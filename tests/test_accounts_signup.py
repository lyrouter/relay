"""AC-9 + AC-1 · residency, bootstrap, signup, verification.

Written around the decisions rather than the code paths: S-3 (refuse, do not
park), S-4 (no "first registrant becomes Admin"), mandatory verification, and
the enumeration properties that make a public signup endpoint safe to expose.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import select

from relay.app.accounts.bootstrap import (
    BootstrapError,
    BootstrapRequest,
    add_allowed_domains,
    bootstrap_tenant,
)
from relay.app.accounts.signup import (
    ACCEPTED_MESSAGE,
    PENDING_MESSAGE,
    SignupRequest,
    SignupUseCase,
)
from relay.app.accounts.verification import (
    RESEND_MESSAGE,
    ResendVerificationUseCase,
    VerifyEmailUseCase,
)
from relay.app.errors import RateLimited, ValidationFailed
from relay.domain.enums import Role, UserStatus
from relay.domain.passwords import WeakPassword
from relay.domain.residency import REFUSAL_MESSAGE, ResidencyOutcome
from relay.infra.db.models import AuditLog, EmailVerification, User
from relay.infra.db.session import tenant_session
from relay.ports.mail import NullMailPort

from .conftest import context_for, requires_db

pytestmark = [requires_db, pytest.mark.db]

GOOD_PASSWORD = "Corr3ct-Horse-Battery"


@pytest.fixture
def gateway():
    """A bootstrapped tenant, the way a real deployment gets one."""
    return bootstrap_tenant(
        BootstrapRequest(
            tenant_name="AI 网关团队",
            tenant_slug="gateway",
            admin_email="admin@zerosone.test",
            admin_password=GOOD_PASSWORD,
        )
    )


@pytest.fixture
def mail():
    return NullMailPort()


def _link_token(mail: NullMailPort) -> str:
    body = mail.sent[-1].text_body
    return body.split("token=")[1].split()[0]


# ------------------------------------------------------------------ AC-9


def test_bootstrap_creates_tenant_admin_and_allowlist(gateway):
    assert gateway.created
    assert gateway.domains == ("zerosone.test",)
    with tenant_session(context_for(gateway.tenant_id)) as session:
        admin = session.get(User, gateway.admin_user_id)
    assert admin.role is Role.ADMIN
    assert admin.status is UserStatus.ACTIVE
    # Verified by construction: created by whoever holds deployment credentials,
    # not by someone claiming an address.
    assert admin.email_verified_at is not None


def test_bootstrap_is_idempotent(gateway):
    again = bootstrap_tenant(
        BootstrapRequest(
            tenant_name="AI 网关团队",
            tenant_slug="gateway",
            admin_email="admin@zerosone.test",
            admin_password=GOOD_PASSWORD,
        )
    )
    assert not again.created
    assert again.tenant_id == gateway.tenant_id
    assert again.admin_user_id == gateway.admin_user_id


def test_bootstrap_refuses_to_add_a_second_admin_to_an_existing_tenant(gateway):
    """S-4's real content. If re-running with a different address quietly added
    an Admin, "deploy-time init" would be a takeover primitive."""
    with pytest.raises(BootstrapError, match="not its admin"):
        bootstrap_tenant(
            BootstrapRequest(
                tenant_name="x",
                tenant_slug="gateway",
                admin_email="attacker@zerosone.test",
                admin_password=GOOD_PASSWORD,
            )
        )


def test_a_domain_belongs_to_exactly_one_tenant(gateway):
    """S-3. Two tenants claiming one domain would make residency ambiguous, and
    residency is the only credential a self-signup has."""
    with pytest.raises(BootstrapError, match="one-to-one"):
        bootstrap_tenant(
            BootstrapRequest(
                tenant_name="other",
                tenant_slug="other",
                admin_email="boss@zerosone.test",
                admin_password=GOOD_PASSWORD,
            )
        )


def test_bootstrap_rejects_a_weak_admin_password(tenant_a):
    with pytest.raises(WeakPassword):
        bootstrap_tenant(
            BootstrapRequest(
                tenant_name="weak",
                tenant_slug="weak",
                admin_email="admin@weak.test",
                admin_password="password",
            )
        )


def test_bootstrap_rejects_a_slug_that_cannot_live_in_a_url(tenant_a):
    """S-12: the slug appears in every permalink, and permalinks freeze on
    release."""
    with pytest.raises(BootstrapError, match="permalink"):
        bootstrap_tenant(
            BootstrapRequest(
                tenant_name="bad",
                tenant_slug="网关 team",
                admin_email="admin@bad.test",
                admin_password=GOOD_PASSWORD,
            )
        )


def test_bootstrap_is_audited(gateway):
    with tenant_session(context_for(gateway.tenant_id)) as session:
        actions = session.scalars(select(AuditLog.action)).all()
    assert "system_repository.bootstrap_tenant" in actions


def test_add_allowed_domains_opens_signup_for_those_addresses(gateway, mail):
    """The follow-up to bootstrap: a company domain listed after day one."""
    refused = SignupUseCase(mail).execute(
        SignupRequest(email="wangli@lyrouter.com", password=GOOD_PASSWORD, client_ip="10.0.0.4")
    )
    assert refused.outcome is ResidencyOutcome.REFUSED
    assert refused.message == REFUSAL_MESSAGE

    result = add_allowed_domains(
        "gateway", ("LYROUTER.COM", "someone@arraynetworks.com.cn")
    )
    assert result.tenant_id == gateway.tenant_id
    assert result.added == ("lyrouter.com", "arraynetworks.com.cn")
    assert result.already_present == ()

    first = SignupUseCase(mail).execute(
        SignupRequest(email="wangli@lyrouter.com", password=GOOD_PASSWORD, client_ip="10.0.0.5")
    )
    assert first.outcome is ResidencyOutcome.AUTO_JOIN
    second = SignupUseCase(mail).execute(
        SignupRequest(
            email="dev@arraynetworks.com.cn", password=GOOD_PASSWORD, client_ip="10.0.0.6"
        )
    )
    assert second.outcome is ResidencyOutcome.AUTO_JOIN


def test_add_allowed_domains_is_idempotent(gateway):
    first = add_allowed_domains("gateway", ("lyrouter.com",))
    again = add_allowed_domains("gateway", ("lyrouter.com", "arraynetworks.com.cn"))
    assert first.added == ("lyrouter.com",)
    assert again.already_present == ("lyrouter.com",)
    assert again.added == ("arraynetworks.com.cn",)


def test_add_allowed_domains_refuses_a_domain_owned_by_another_tenant(gateway):
    bootstrap_tenant(
        BootstrapRequest(
            tenant_name="other",
            tenant_slug="other",
            admin_email="boss@other.test",
            admin_password=GOOD_PASSWORD,
        )
    )
    with pytest.raises(BootstrapError, match="one-to-one"):
        add_allowed_domains("gateway", ("other.test",))


def test_add_allowed_domains_refuses_an_unknown_tenant(gateway):
    with pytest.raises(BootstrapError, match="no tenant"):
        add_allowed_domains("missing", ("lyrouter.com",))


def test_add_allowed_domains_is_audited(gateway):
    add_allowed_domains("gateway", ("lyrouter.com",))
    with tenant_session(context_for(gateway.tenant_id)) as session:
        actions = session.scalars(select(AuditLog.action)).all()
    assert "system_repository.add_allowed_domains" in actions


# ------------------------------------------------------------------ AC-1


def test_allowlisted_domain_joins_directly(gateway, mail):
    result = SignupUseCase(mail).execute(
        SignupRequest(email="Dev@ZerosOne.test", password=GOOD_PASSWORD, client_ip="10.0.0.1")
    )
    assert result.outcome is ResidencyOutcome.AUTO_JOIN
    assert result.tenant_id == gateway.tenant_id

    with tenant_session(context_for(gateway.tenant_id)) as session:
        user = session.get(User, result.user_id)
    assert user.email == "dev@zerosone.test", "email must be normalised before storage"
    assert user.role is Role.MEMBER
    assert user.email_verified_at is None, "verification is mandatory, not implied"
    assert len(mail.sent) == 1


def test_unlisted_domain_is_refused_not_parked(gateway, mail):
    """S-3. A pending pool for unknown domains is an unbounded queue of
    strangers that someone eventually clears in bulk."""
    result = SignupUseCase(mail).execute(
        SignupRequest(email="outsider@elsewhere.test", password=GOOD_PASSWORD)
    )
    assert result.outcome is ResidencyOutcome.REFUSED
    assert result.message == REFUSAL_MESSAGE
    assert "管理员" in result.message, "a refusal must name the next step"
    assert result.user_id is None
    assert mail.sent == []


def test_auto_join_false_creates_a_pending_user(mail):
    tenant = bootstrap_tenant(
        BootstrapRequest(
            tenant_name="Careful",
            tenant_slug="careful",
            admin_email="admin@careful.test",
            admin_password=GOOD_PASSWORD,
            auto_join=False,
        )
    )
    result = SignupUseCase(mail).execute(
        SignupRequest(email="dev@careful.test", password=GOOD_PASSWORD)
    )
    assert result.outcome is ResidencyOutcome.PENDING
    assert result.message == PENDING_MESSAGE
    with tenant_session(context_for(tenant.tenant_id)) as session:
        assert session.get(User, result.user_id).status is UserStatus.PENDING


def test_signup_does_not_reveal_whether_the_address_is_taken(gateway, mail):
    """The endpoint is unauthenticated. Distinguishing "created" from "already
    exists" turns it into an account oracle for any allowlisted domain."""
    use_case = SignupUseCase(mail)
    first = use_case.execute(SignupRequest(email="dev@zerosone.test", password=GOOD_PASSWORD))
    second = use_case.execute(SignupRequest(email="dev@zerosone.test", password=GOOD_PASSWORD))

    assert first.message == second.message == ACCEPTED_MESSAGE
    assert second.user_id is None
    with tenant_session(context_for(gateway.tenant_id)) as session:
        count = len(session.scalars(select(User).where(User.email == "dev@zerosone.test")).all())
    assert count == 1


def test_weak_password_is_refused_before_the_throttle_is_charged(gateway, mail):
    """A typo should not cost an attempt."""
    use_case = SignupUseCase(mail)
    for _ in range(20):
        with pytest.raises(ValidationFailed):
            use_case.execute(
                SignupRequest(
                    email="dev@zerosone.test", password="short", client_ip="10.0.0.9"
                )
            )
    # Still able to sign up afterwards: nothing was consumed.
    result = use_case.execute(
        SignupRequest(email="dev@zerosone.test", password=GOOD_PASSWORD, client_ip="10.0.0.9")
    )
    assert result.outcome is ResidencyOutcome.AUTO_JOIN


def test_signup_is_rate_limited_per_ip_including_refusals(gateway, mail):
    """Refusals are the signal an enumerator is after, so they must count."""
    use_case = SignupUseCase(mail)
    for i in range(10):
        use_case.execute(
            SignupRequest(
                email=f"probe{i}@elsewhere.test", password=GOOD_PASSWORD, client_ip="10.0.0.2"
            )
        )
    with pytest.raises(RateLimited) as excinfo:
        use_case.execute(
            SignupRequest(
                email="probe99@elsewhere.test", password=GOOD_PASSWORD, client_ip="10.0.0.2"
            )
        )
    assert excinfo.value.retry_after_seconds > 0


def test_invalid_email_is_rejected_with_guidance(gateway, mail):
    with pytest.raises(ValidationFailed, match="邮箱"):
        SignupUseCase(mail).execute(SignupRequest(email="not-an-email", password=GOOD_PASSWORD))


# --------------------------------------------------------- verification


def test_verification_activates_the_account(gateway, mail):
    signup = SignupUseCase(mail).execute(
        SignupRequest(email="dev@zerosone.test", password=GOOD_PASSWORD)
    )
    result = VerifyEmailUseCase().execute(_link_token(mail))
    assert result.user_id == signup.user_id
    assert result.activated
    with tenant_session(context_for(gateway.tenant_id)) as session:
        assert session.get(User, signup.user_id).email_verified_at is not None


def test_verification_token_is_single_use(gateway, mail):
    SignupUseCase(mail).execute(SignupRequest(email="dev@zerosone.test", password=GOOD_PASSWORD))
    token = _link_token(mail)
    VerifyEmailUseCase().execute(token)
    with pytest.raises(ValidationFailed):
        VerifyEmailUseCase().execute(token)


def test_expired_token_is_indistinguishable_from_a_bogus_one(gateway, mail):
    """Learning "expired" tells the caller the token was once real."""
    SignupUseCase(mail).execute(SignupRequest(email="dev@zerosone.test", password=GOOD_PASSWORD))
    token = _link_token(mail)
    later = dt.datetime.now(dt.UTC) + dt.timedelta(hours=25)

    with pytest.raises(ValidationFailed) as expired:
        VerifyEmailUseCase().execute(token, now=later)
    with pytest.raises(ValidationFailed) as bogus:
        VerifyEmailUseCase().execute("obviously-not-a-token", now=later)
    assert str(expired.value) == str(bogus.value)


def test_only_a_hash_of_the_token_is_stored(gateway, mail):
    SignupUseCase(mail).execute(SignupRequest(email="dev@zerosone.test", password=GOOD_PASSWORD))
    token = _link_token(mail)
    with tenant_session(context_for(gateway.tenant_id)) as session:
        stored = session.scalars(select(EmailVerification.token_hash)).all()
    assert token not in stored
    assert all(len(h) == 64 for h in stored)


def test_verification_does_not_skip_admin_approval(mail):
    """A pending user verifying their address proves the mailbox, not the
    approval."""
    tenant = bootstrap_tenant(
        BootstrapRequest(
            tenant_name="Careful",
            tenant_slug="careful",
            admin_email="admin@careful.test",
            admin_password=GOOD_PASSWORD,
            auto_join=False,
        )
    )
    signup = SignupUseCase(mail).execute(
        SignupRequest(email="dev@careful.test", password=GOOD_PASSWORD)
    )
    result = VerifyEmailUseCase().execute(_link_token(mail))
    assert not result.activated
    assert "审批" in result.message
    with tenant_session(context_for(tenant.tenant_id)) as session:
        user = session.get(User, signup.user_id)
    assert user.status is UserStatus.PENDING
    assert user.email_verified_at is not None


def test_resend_invalidates_the_previous_link(gateway, mail):
    SignupUseCase(mail).execute(SignupRequest(email="dev@zerosone.test", password=GOOD_PASSWORD))
    first_token = _link_token(mail)

    ResendVerificationUseCase(mail).execute("dev@zerosone.test")
    second_token = _link_token(mail)
    assert first_token != second_token

    with pytest.raises(ValidationFailed):
        VerifyEmailUseCase().execute(first_token)
    assert VerifyEmailUseCase().execute(second_token).activated


def test_resend_says_the_same_thing_for_an_unknown_address(gateway, mail):
    assert ResendVerificationUseCase(mail).execute("nobody@zerosone.test") == RESEND_MESSAGE
    assert mail.sent == []


def test_resend_is_rate_limited(gateway, mail):
    SignupUseCase(mail).execute(SignupRequest(email="dev@zerosone.test", password=GOOD_PASSWORD))
    use_case = ResendVerificationUseCase(mail)
    for _ in range(3):
        use_case.execute("dev@zerosone.test")
    with pytest.raises(RateLimited, match="验证邮件"):
        use_case.execute("dev@zerosone.test")
