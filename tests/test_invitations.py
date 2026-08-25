"""AC-1 · invitations, the secondary path.

The rule under test is the awkward one: an invitation is **not** checked against
the domain allowlist. That is what makes "contact your administrator for an
invite" a real next step rather than a polite dead end, and it is why the AC's
"cannot create an account by any route" has to mean *no self-service route* —
which ``test_the_allowlist_still_refuses_the_same_address_by_signup`` pins from
both sides at once.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import select

from relay.app.accounts.bootstrap import BootstrapRequest, bootstrap_tenant
from relay.app.accounts.invitations import (
    ALREADY_A_MEMBER,
    INVALID_INVITATION,
    INVITATION_TTL,
    AcceptInvitationUseCase,
    InviteUserUseCase,
)
from relay.app.accounts.login import LoginRequest, LoginUseCase
from relay.app.accounts.signup import SignupRequest, SignupUseCase
from relay.app.errors import Conflict, PermissionDenied, ValidationFailed
from relay.context import tenant_scope
from relay.domain.enums import Role, UserStatus
from relay.domain.residency import ResidencyOutcome
from relay.infra.db.models import AuditLog, Invitation, User
from relay.infra.db.session import tenant_session
from relay.infra.security.tokens import hash_token
from relay.ports.mail import NullMailPort

from .conftest import context_for, requires_db

pytestmark = [requires_db, pytest.mark.db]

PASSWORD = "Corr3ct-Horse-Battery"
#: Not in the allowlist. The whole point of the secondary path.
OUTSIDER = "contractor@agency.example"


@pytest.fixture
def mail():
    return NullMailPort()


@pytest.fixture
def gateway():
    return bootstrap_tenant(
        BootstrapRequest(
            tenant_name="AI 网关团队",
            tenant_slug="gateway",
            admin_email="admin@zerosone.test",
            admin_password=PASSWORD,
        )
    )


def as_admin(gateway):
    return tenant_scope(context_for(gateway.tenant_id, gateway.admin_user_id))


def link_token(mail: NullMailPort) -> str:
    return mail.sent[-1].text_body.split("token=")[1].split()[0]


def invite(gateway, mail, email: str = OUTSIDER, role: Role = Role.MEMBER) -> str:
    with as_admin(gateway):
        InviteUserUseCase(mail).execute(email, role)
    return link_token(mail)


# ---------------------------------------------------------------- the happy path


def test_an_invited_outsider_joins_and_can_log_in_immediately(gateway, mail):
    """No verification round trip: holding the token *is* the proof of address.

    It arrived in that mailbox and nothing else can have read it — the same
    proof a verification link carries, so asking for it twice buys nothing.
    """
    token = invite(gateway, mail)
    accepted = AcceptInvitationUseCase().execute(token, PASSWORD)

    assert accepted.tenant_id == gateway.tenant_id
    assert accepted.role is Role.MEMBER

    with tenant_session(context_for(gateway.tenant_id)) as session:
        user = session.get(User, accepted.user_id)
        assert user.status is UserStatus.ACTIVE
        assert user.email_verified_at is not None

    login = LoginUseCase(mail).execute(LoginRequest(email=OUTSIDER, password=PASSWORD))
    assert login.user_id == accepted.user_id


def test_the_invited_role_is_what_the_admin_chose(gateway, mail):
    token = invite(gateway, mail, role=Role.GUEST)
    assert AcceptInvitationUseCase().execute(token, PASSWORD).role is Role.GUEST


def test_an_admin_can_be_invited(gateway, mail):
    """The in-product way to get a second Admin. ``bootstrap_tenant`` refuses to
    add one and AC-4 refuses to remove the last, so this route matters."""
    token = invite(gateway, mail, role=Role.ADMIN)
    assert AcceptInvitationUseCase().execute(token, PASSWORD).role is Role.ADMIN


def test_the_allowlist_still_refuses_the_same_address_by_signup(gateway, mail):
    """Both halves of the rule, in one test.

    The invitation admits this address; self-service signup for the same address
    is still refused. "No route" means no *self-service* route — an invitation is
    an Admin naming one person, which is the judgment the allowlist exists to
    avoid having to make at scale, not to forbid.
    """
    refused = SignupUseCase(mail).execute(SignupRequest(email=OUTSIDER, password=PASSWORD))
    assert refused.outcome is ResidencyOutcome.REFUSED
    assert refused.user_id is None

    token = invite(gateway, mail)
    assert AcceptInvitationUseCase().execute(token, PASSWORD).user_id is not None


# ------------------------------------------------------------------- who may


def test_a_member_cannot_invite(gateway, mail):
    """A Member who could invite is a Member who can add anyone to the tenant —
    the residency rule undone from the inside."""
    token = invite(gateway, mail, role=Role.MEMBER)
    joined = AcceptInvitationUseCase().execute(token, PASSWORD)

    with tenant_scope(context_for(gateway.tenant_id, joined.user_id)):
        with pytest.raises(PermissionDenied):
            InviteUserUseCase(mail).execute("someone@agency.example")


def test_inviting_an_existing_account_is_refused_plainly(gateway, mail):
    """The caller is an authenticated Admin of this tenant, so this message is
    specific: an enumeration-safe "sent!" would look like a mail that never
    arrives."""
    token = invite(gateway, mail)
    AcceptInvitationUseCase().execute(token, PASSWORD)

    with as_admin(gateway):
        with pytest.raises(Conflict) as refused:
            InviteUserUseCase(mail).execute(OUTSIDER)
    assert refused.value.message == ALREADY_A_MEMBER


def test_a_malformed_address_is_refused(gateway, mail):
    with as_admin(gateway):
        with pytest.raises(ValidationFailed):
            InviteUserUseCase(mail).execute("not-an-address")


# ------------------------------------------------------------- bad tokens


def test_expired_used_and_bogus_tokens_are_indistinguishable(gateway, mail):
    used = invite(gateway, mail, email="one@agency.example")
    AcceptInvitationUseCase().execute(used, PASSWORD)

    expired = invite(gateway, mail, email="two@agency.example")
    later = dt.datetime.now(dt.UTC) + INVITATION_TTL + dt.timedelta(minutes=1)

    messages = set()
    for token, when in ((used, None), (expired, later), ("not-a-token", None)):
        with pytest.raises(ValidationFailed) as refused:
            AcceptInvitationUseCase().execute(token, PASSWORD, now=when)
        messages.add(refused.value.message)

    assert messages == {INVALID_INVITATION}


def test_an_invitation_is_single_use(gateway, mail):
    token = invite(gateway, mail)
    AcceptInvitationUseCase().execute(token, PASSWORD)
    with pytest.raises(ValidationFailed):
        AcceptInvitationUseCase().execute(token, PASSWORD)


def test_a_second_invitation_dies_once_the_first_is_accepted(gateway, mail):
    """Two live invitations for one address are allowed — an Admin who resends
    should not have to find the old mail. Whichever is used first wins, and the
    other fails as a spent link rather than creating a second account."""
    first = invite(gateway, mail)
    second = invite(gateway, mail)
    AcceptInvitationUseCase().execute(first, PASSWORD)

    with pytest.raises(ValidationFailed) as refused:
        AcceptInvitationUseCase().execute(second, PASSWORD)
    assert refused.value.message == INVALID_INVITATION

    with tenant_session(context_for(gateway.tenant_id)) as session:
        assert len(session.scalars(select(User).where(User.email == OUTSIDER)).all()) == 1


def test_the_password_policy_applies_to_an_accepted_invitation(gateway, mail):
    """And it is checked *after* the token, so a weak password on a dead link
    fails for the reason that matters."""
    token = invite(gateway, mail)
    with pytest.raises(ValidationFailed, match="至少"):
        AcceptInvitationUseCase().execute(token, "short")
    with pytest.raises(ValidationFailed, match="无效"):
        AcceptInvitationUseCase().execute("not-a-token", "short")


# ------------------------------------------------------- storage and audit


def test_only_the_hash_is_stored(gateway, mail):
    token = invite(gateway, mail)
    with tenant_session(context_for(gateway.tenant_id)) as session:
        row = session.scalars(select(Invitation)).one()
    assert row.token_hash == hash_token(token)
    assert token not in row.token_hash


def test_both_halves_are_audited(gateway, mail):
    token = invite(gateway, mail)
    accepted = AcceptInvitationUseCase().execute(token, PASSWORD)

    with tenant_session(context_for(gateway.tenant_id)) as session:
        rows = {row.action: row for row in session.scalars(select(AuditLog)).all()}

    assert rows["account.invited"].actor_id == gateway.admin_user_id
    # The other half closes the loop: who actually turned up, and when.
    assert rows["account.invitation_accepted"].actor_id == accepted.user_id


def test_the_invitation_records_who_sent_it(gateway, mail):
    invite(gateway, mail)
    with tenant_session(context_for(gateway.tenant_id)) as session:
        assert session.scalars(select(Invitation)).one().invited_by == gateway.admin_user_id
