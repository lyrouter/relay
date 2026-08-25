"""AC-4 · the Admin-only account operations, at the service layer.

What these tests are really pinning down is that the four rules hold *through*
the service, not just in the capability table: the role is re-read per call, a
target in another tenant is invisible rather than forbidden, a tenant cannot be
left unadministered, and deactivation actually ends sessions.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from relay.app.accounts.administration import LAST_ADMIN, NOT_PENDING, AdminService
from relay.app.accounts.bootstrap import BootstrapRequest, bootstrap_tenant
from relay.app.accounts.login import LoginRequest, LoginUseCase
from relay.app.accounts.sessions import SessionExpired, SessionService
from relay.app.accounts.signup import SignupRequest, SignupUseCase
from relay.app.accounts.verification import VerifyEmailUseCase
from relay.app.errors import Conflict, NotFound, PermissionDenied
from relay.context import tenant_scope
from relay.domain.enums import Role, UserStatus
from relay.infra.db.models import AuditLog, User
from relay.infra.db.session import tenant_session
from relay.ports.mail import NullMailPort

from .conftest import context_for, requires_db

pytestmark = [requires_db, pytest.mark.db]

PASSWORD = "Corr3ct-Horse-Battery"


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


@pytest.fixture
def member(gateway, mail):
    """A verified, active Member, arrived by self-service signup."""
    signup = SignupUseCase(mail).execute(
        SignupRequest(email="dev@zerosone.test", password=PASSWORD)
    )
    token = mail.sent[-1].text_body.split("token=")[1].split()[0]
    VerifyEmailUseCase().execute(token)
    mail.sent.clear()
    return signup


def as_admin(gateway):
    return tenant_scope(context_for(gateway.tenant_id, gateway.admin_user_id))


def role_of(tenant_id: uuid.UUID, user_id: uuid.UUID) -> Role:
    with tenant_session(context_for(tenant_id)) as session:
        return session.get(User, user_id).role


def status_of(tenant_id: uuid.UUID, user_id: uuid.UUID) -> UserStatus:
    with tenant_session(context_for(tenant_id)) as session:
        return session.get(User, user_id).status


def audit_actions(tenant_id: uuid.UUID) -> list[str]:
    with tenant_session(context_for(tenant_id)) as session:
        return list(session.scalars(select(AuditLog.action)))


# ------------------------------------------------------------- role changes


def test_the_review_list_includes_emails_and_deactivated_accounts(gateway, member):
    """GET /web/users is the picker and hides both. This list is the review."""
    with as_admin(gateway):
        AdminService().deactivate_user(member.user_id)
        rows = AdminService().list_users()
    emails = {one.email for one in rows}
    assert "dev@zerosone.test" in emails
    assert "admin@zerosone.test" in emails
    by_email = {one.email: one for one in rows}
    assert by_email["dev@zerosone.test"].status is UserStatus.DEACTIVATED
    pending_rank = [one.status for one in rows]
    assert pending_rank.index(UserStatus.ACTIVE) < pending_rank.index(UserStatus.DEACTIVATED)


def test_a_member_cannot_list_accounts_for_review(gateway, member):
    with tenant_scope(context_for(gateway.tenant_id, member.user_id)):
        with pytest.raises(PermissionDenied):
            AdminService().list_users()


def test_an_admin_changes_a_role_and_it_is_audited(gateway, member):
    with as_admin(gateway):
        AdminService().change_role(member.user_id, Role.GUEST)

    assert role_of(gateway.tenant_id, member.user_id) is Role.GUEST
    assert "account.role_changed" in audit_actions(gateway.tenant_id)


def test_the_audit_row_names_the_actor_and_both_roles(gateway, member):
    with as_admin(gateway):
        AdminService().change_role(member.user_id, Role.GUEST)

    with tenant_session(context_for(gateway.tenant_id)) as session:
        row = session.scalars(
            select(AuditLog).where(AuditLog.action == "account.role_changed")
        ).one()
    assert row.actor_id == gateway.admin_user_id
    assert row.target_id == str(member.user_id)
    assert row.before == {"role": "member"} and row.after == {"role": "guest"}


def test_an_unchanged_role_writes_nothing(gateway, member):
    with as_admin(gateway):
        AdminService().change_role(member.user_id, Role.MEMBER)
    assert "account.role_changed" not in audit_actions(gateway.tenant_id)


def test_a_member_cannot_change_roles(gateway, member):
    with tenant_scope(context_for(gateway.tenant_id, member.user_id)):
        with pytest.raises(PermissionDenied):
            AdminService().change_role(gateway.admin_user_id, Role.MEMBER)
    assert role_of(gateway.tenant_id, gateway.admin_user_id) is Role.ADMIN


def test_a_demoted_admin_cannot_administer_on_their_next_call(gateway, member):
    """The reason ``UserSession`` does not cache the role.

    Promote, demote, then try again with the same context — which is what a
    still-open browser session looks like. A cached role would let the demoted
    admin keep going until their session expired.
    """
    with as_admin(gateway):
        AdminService().change_role(member.user_id, Role.ADMIN)

    with tenant_scope(context_for(gateway.tenant_id, member.user_id)):
        service = AdminService()
        service.change_role(member.user_id, Role.MEMBER)  # demotes themselves
        with pytest.raises(PermissionDenied):
            service.change_role(gateway.admin_user_id, Role.GUEST)


def test_a_deactivated_actor_cannot_administer(gateway, member):
    with as_admin(gateway):
        AdminService().change_role(member.user_id, Role.ADMIN)
        AdminService().deactivate_user(member.user_id)

    with tenant_scope(context_for(gateway.tenant_id, member.user_id)):
        with pytest.raises(PermissionDenied):
            AdminService().change_role(gateway.admin_user_id, Role.GUEST)


# ----------------------------------------------------- the last-admin guard


def test_the_last_admin_cannot_be_demoted(gateway, member):
    with as_admin(gateway):
        with pytest.raises(Conflict) as refused:
            AdminService().change_role(gateway.admin_user_id, Role.MEMBER)
    assert refused.value.message == LAST_ADMIN
    assert role_of(gateway.tenant_id, gateway.admin_user_id) is Role.ADMIN


def test_the_last_admin_cannot_be_deactivated(gateway, member):
    """Without SSO and with bootstrap refusing a second Admin, this is the door
    that cannot be reopened from inside the product."""
    with as_admin(gateway):
        with pytest.raises(Conflict):
            AdminService().deactivate_user(gateway.admin_user_id)
    assert status_of(gateway.tenant_id, gateway.admin_user_id) is UserStatus.ACTIVE


def test_an_admin_can_step_down_once_there_is_another(gateway, member):
    with as_admin(gateway):
        service = AdminService()
        service.change_role(member.user_id, Role.ADMIN)
        service.change_role(gateway.admin_user_id, Role.MEMBER)

    assert role_of(gateway.tenant_id, gateway.admin_user_id) is Role.MEMBER
    assert role_of(gateway.tenant_id, member.user_id) is Role.ADMIN


def test_a_deactivated_admin_does_not_count_as_the_remaining_one(gateway, member, mail):
    """The count is of *active* Admins. A deactivated one cannot log in to
    administer anything, so counting them would leave the tenant stuck with a
    door it cannot open."""
    second = SignupUseCase(mail).execute(
        SignupRequest(email="second@zerosone.test", password=PASSWORD)
    )
    token = mail.sent[-1].text_body.split("token=")[1].split()[0]
    VerifyEmailUseCase().execute(token)

    with as_admin(gateway):
        service = AdminService()
        service.change_role(second.user_id, Role.ADMIN)
        service.deactivate_user(second.user_id)
        with pytest.raises(Conflict):
            service.change_role(gateway.admin_user_id, Role.MEMBER)


# ------------------------------------------------------- cross-tenant (MT-6)


def test_another_tenants_user_is_not_found_rather_than_forbidden(gateway):
    """§4.5: 404, not 403. A 403 would confirm the account exists."""
    other = bootstrap_tenant(
        BootstrapRequest(
            tenant_name="其他团队",
            tenant_slug="other",
            admin_email="admin@other.test",
            admin_password=PASSWORD,
        )
    )
    with as_admin(gateway):
        with pytest.raises(NotFound):
            AdminService().change_role(other.admin_user_id, Role.GUEST)

    assert role_of(other.tenant_id, other.admin_user_id) is Role.ADMIN


def test_a_missing_user_and_another_tenants_user_look_identical(gateway):
    other = bootstrap_tenant(
        BootstrapRequest(
            tenant_name="其他团队",
            tenant_slug="other",
            admin_email="admin@other.test",
            admin_password=PASSWORD,
        )
    )
    with as_admin(gateway):
        service = AdminService()
        with pytest.raises(NotFound) as absent:
            service.change_role(uuid.uuid4(), Role.GUEST)
        with pytest.raises(NotFound) as elsewhere:
            service.change_role(other.admin_user_id, Role.GUEST)
    assert absent.value.message == elsewhere.value.message


# ---------------------------------------------------------------- approval


@pytest.fixture
def approval_tenant(mail):
    """A tenant whose domain is allowlisted with ``auto_join=false`` (AC-1)."""
    return bootstrap_tenant(
        BootstrapRequest(
            tenant_name="需审批团队",
            tenant_slug="approval",
            admin_email="admin@approval.test",
            admin_password=PASSWORD,
            auto_join=False,
        )
    )


def test_an_admin_approves_a_pending_signup(approval_tenant, mail):
    signup = SignupUseCase(mail).execute(
        SignupRequest(email="new@approval.test", password=PASSWORD)
    )
    assert status_of(approval_tenant.tenant_id, signup.user_id) is UserStatus.PENDING

    with tenant_scope(context_for(approval_tenant.tenant_id, approval_tenant.admin_user_id)):
        AdminService().approve_pending_user(signup.user_id)

    assert status_of(approval_tenant.tenant_id, signup.user_id) is UserStatus.ACTIVE
    assert "account.approved" in audit_actions(approval_tenant.tenant_id)


def test_approval_alone_does_not_let_an_unverified_account_in(approval_tenant, mail):
    """Approval and verification are independent, and both are required.

    Approval is the Admin's judgment about the person; verification is proof
    they hold the address that judgment was based on.
    """
    signup = SignupUseCase(mail).execute(
        SignupRequest(email="new@approval.test", password=PASSWORD)
    )
    with tenant_scope(context_for(approval_tenant.tenant_id, approval_tenant.admin_user_id)):
        AdminService().approve_pending_user(signup.user_id)

    from relay.app.accounts.login import EmailNotVerified

    with pytest.raises(EmailNotVerified):
        LoginUseCase(mail).execute(
            LoginRequest(email="new@approval.test", password=PASSWORD)
        )


def test_approving_an_active_account_is_a_conflict(gateway, member):
    with as_admin(gateway):
        with pytest.raises(Conflict) as refused:
            AdminService().approve_pending_user(member.user_id)
    assert refused.value.message == NOT_PENDING


# ------------------------------------------------- deactivation (R-2 · AC-8)


def test_deactivation_ends_live_sessions(gateway, member, mail):
    """R-2's mechanism. Without SSO, this is the only thing that removes access."""
    login = LoginUseCase(mail).execute(
        LoginRequest(email="dev@zerosone.test", password=PASSWORD, client_ip="10.0.0.1")
    )
    SessionService().resolve(login.session_token)  # live before

    with as_admin(gateway):
        ended = AdminService().deactivate_user(member.user_id)

    assert ended == 1
    with pytest.raises(SessionExpired):
        SessionService().resolve(login.session_token)


def test_a_deactivated_account_cannot_log_in_again(gateway, member, mail):
    with as_admin(gateway):
        AdminService().deactivate_user(member.user_id)

    with pytest.raises(PermissionDenied):
        LoginUseCase(mail).execute(
            LoginRequest(email="dev@zerosone.test", password=PASSWORD)
        )


def test_deactivating_twice_is_a_no_op(gateway, member):
    with as_admin(gateway):
        service = AdminService()
        service.deactivate_user(member.user_id)
        assert service.deactivate_user(member.user_id) == 0


def test_reactivation_restores_login_but_not_sessions(gateway, member, mail):
    login = LoginUseCase(mail).execute(
        LoginRequest(email="dev@zerosone.test", password=PASSWORD)
    )
    with as_admin(gateway):
        service = AdminService()
        service.deactivate_user(member.user_id)
        service.reactivate_user(member.user_id)

    # The account works again...
    fresh = LoginUseCase(mail).execute(
        LoginRequest(email="dev@zerosone.test", password=PASSWORD)
    )
    assert fresh.session_token
    # ...but revocation is not reversible.
    with pytest.raises(SessionExpired):
        SessionService().resolve(login.session_token)


def test_reactivating_an_active_account_is_a_conflict(gateway, member):
    with as_admin(gateway):
        with pytest.raises(Conflict):
            AdminService().reactivate_user(member.user_id)
