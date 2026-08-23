"""AC-2 + AC-3 · authentication, lockout, sessions, TOTP."""

from __future__ import annotations

import datetime as dt

import pyotp
import pytest
from sqlalchemy import select

from relay.app.accounts.bootstrap import BootstrapRequest, bootstrap_tenant
from relay.app.accounts.login import (
    ABSOLUTE_TIMEOUT,
    IDLE_TIMEOUT,
    LOCKOUT_DURATION,
    MAX_FAILED_ATTEMPTS,
    AccountLocked,
    EmailNotVerified,
    InvalidCredentials,
    LoginRequest,
    LoginUseCase,
    MfaRequired,
)
from relay.app.accounts.sessions import SessionExpired, SessionService
from relay.app.accounts.signup import SignupRequest, SignupUseCase
from relay.app.accounts.totp import InvalidTotpCode, TotpService, admin_mfa_gap
from relay.app.accounts.verification import VerifyEmailUseCase
from relay.app.errors import PermissionDenied
from relay.domain.enums import UserStatus
from relay.infra.db.models import User, UserSession
from relay.infra.db.session import tenant_session
from relay.ports.mail import NullMailPort

from .conftest import context_for, requires_db

pytestmark = [requires_db, pytest.mark.db]

GOOD_PASSWORD = "Corr3ct-Horse-Battery"


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
            admin_password=GOOD_PASSWORD,
        )
    )


@pytest.fixture
def member(gateway, mail):
    """A verified, active Member — the state most tests need to start from."""
    signup = SignupUseCase(mail).execute(
        SignupRequest(email="dev@zerosone.test", password=GOOD_PASSWORD)
    )
    token = mail.sent[-1].text_body.split("token=")[1].split()[0]
    VerifyEmailUseCase().execute(token)
    mail.sent.clear()
    return signup


# ------------------------------------------------------------- credentials


def test_login_succeeds_and_opens_a_session(gateway, member, mail):
    result = LoginUseCase(mail).execute(
        LoginRequest(email="Dev@ZerosOne.test", password=GOOD_PASSWORD, client_ip="10.0.0.1")
    )
    assert result.user_id == member.user_id
    assert result.tenant_id == gateway.tenant_id

    resolved = SessionService().resolve(result.session_token)
    assert resolved.context.tenant_id == gateway.tenant_id
    assert resolved.context.actor_id == member.user_id


def test_unknown_address_and_wrong_password_are_indistinguishable(gateway, member, mail):
    use_case = LoginUseCase(mail)
    with pytest.raises(InvalidCredentials) as unknown:
        use_case.execute(LoginRequest(email="nobody@zerosone.test", password=GOOD_PASSWORD))
    with pytest.raises(InvalidCredentials) as wrong:
        use_case.execute(LoginRequest(email="dev@zerosone.test", password="Wr0ng-Password!"))
    assert str(unknown.value) == str(wrong.value)


def test_only_a_hash_of_the_session_token_is_stored(gateway, member, mail):
    result = LoginUseCase(mail).execute(
        LoginRequest(email="dev@zerosone.test", password=GOOD_PASSWORD)
    )
    with tenant_session(context_for(gateway.tenant_id)) as session:
        stored = session.scalars(select(UserSession.token_hash)).all()
    assert result.session_token not in stored


def test_unverified_email_is_refused_with_the_next_step(gateway, mail):
    """AC-8: refuse **with a resend link**, never a bare 'cannot log in'."""
    SignupUseCase(mail).execute(SignupRequest(email="new@zerosone.test", password=GOOD_PASSWORD))
    with pytest.raises(EmailNotVerified, match="重新发送"):
        LoginUseCase(mail).execute(
            LoginRequest(email="new@zerosone.test", password=GOOD_PASSWORD)
        )


def test_deactivated_account_is_told_why(gateway, member, mail):
    """Distinguishable from a wrong password on purpose: this is the legitimate
    owner, and R-2 makes it a path people will actually hit."""
    with tenant_session(context_for(gateway.tenant_id)) as session:
        session.get(User, member.user_id).status = UserStatus.DEACTIVATED
        session.commit()
    with pytest.raises(PermissionDenied, match="停用"):
        LoginUseCase(mail).execute(
            LoginRequest(email="dev@zerosone.test", password=GOOD_PASSWORD)
        )


# ----------------------------------------------------------------- lockout


def test_lockout_after_repeated_failures(gateway, member, mail):
    use_case = LoginUseCase(mail)
    for _ in range(MAX_FAILED_ATTEMPTS):
        with pytest.raises(InvalidCredentials):
            use_case.execute(LoginRequest(email="dev@zerosone.test", password="Wr0ng-Password!"))

    with pytest.raises(AccountLocked, match="锁定"):
        use_case.execute(LoginRequest(email="dev@zerosone.test", password=GOOD_PASSWORD))


def test_lockout_expires_rather_than_persisting(gateway, member, mail):
    """A permanent lock hands anyone who knows a colleague's address the ability
    to keep them out indefinitely."""
    use_case = LoginUseCase(mail)
    for _ in range(MAX_FAILED_ATTEMPTS):
        with pytest.raises(InvalidCredentials):
            use_case.execute(LoginRequest(email="dev@zerosone.test", password="Wr0ng-Password!"))

    later = dt.datetime.now(dt.UTC) + LOCKOUT_DURATION + dt.timedelta(seconds=1)
    result = use_case.execute(
        LoginRequest(email="dev@zerosone.test", password=GOOD_PASSWORD), now=later
    )
    assert result.session_token


def test_a_successful_login_clears_the_failure_count(gateway, member, mail):
    use_case = LoginUseCase(mail)
    for _ in range(MAX_FAILED_ATTEMPTS - 1):
        with pytest.raises(InvalidCredentials):
            use_case.execute(LoginRequest(email="dev@zerosone.test", password="Wr0ng-Password!"))
    use_case.execute(LoginRequest(email="dev@zerosone.test", password=GOOD_PASSWORD))

    with tenant_session(context_for(gateway.tenant_id)) as session:
        assert session.get(User, member.user_id).failed_login_count == 0


# ---------------------------------------------------------------- sessions


def test_idle_timeout_ends_the_session(gateway, member, mail):
    result = LoginUseCase(mail).execute(
        LoginRequest(email="dev@zerosone.test", password=GOOD_PASSWORD)
    )
    later = dt.datetime.now(dt.UTC) + IDLE_TIMEOUT + dt.timedelta(minutes=1)
    with pytest.raises(SessionExpired):
        SessionService().resolve(result.session_token, now=later)


def test_activity_slides_the_idle_window_but_not_past_the_absolute_one(gateway, member, mail):
    service = SessionService()
    result = LoginUseCase(mail).execute(
        LoginRequest(email="dev@zerosone.test", password=GOOD_PASSWORD)
    )
    # Active every few hours for a week: idle never trips...
    now = dt.datetime.now(dt.UTC)
    for hours in range(4, 24 * 7, 4):
        service.resolve(result.session_token, now=now + dt.timedelta(hours=hours))
    # ...but the absolute deadline still ends it.
    with pytest.raises(SessionExpired):
        service.resolve(result.session_token, now=now + ABSOLUTE_TIMEOUT + dt.timedelta(minutes=1))


def test_logout_ends_the_session(gateway, member, mail):
    service = SessionService()
    result = LoginUseCase(mail).execute(
        LoginRequest(email="dev@zerosone.test", password=GOOD_PASSWORD)
    )
    service.logout(result.session_token)
    with pytest.raises(SessionExpired):
        service.resolve(result.session_token)


def test_deactivating_an_account_kills_live_sessions(gateway, member, mail):
    """R-2's actual mechanism. Without SSO this is the only thing that removes
    access, so a live session must not survive it."""
    service = SessionService()
    result = LoginUseCase(mail).execute(
        LoginRequest(email="dev@zerosone.test", password=GOOD_PASSWORD)
    )
    service.resolve(result.session_token)  # works now

    with tenant_session(context_for(gateway.tenant_id)) as session:
        session.get(User, member.user_id).status = UserStatus.DEACTIVATED
        session.commit()

    with pytest.raises(SessionExpired):
        service.resolve(result.session_token)


def test_revoke_all_can_spare_the_current_session(gateway, member, mail):
    service = SessionService()
    keep = LoginUseCase(mail).execute(
        LoginRequest(email="dev@zerosone.test", password=GOOD_PASSWORD)
    )
    drop = LoginUseCase(mail).execute(
        LoginRequest(email="dev@zerosone.test", password=GOOD_PASSWORD)
    )
    kept = service.resolve(keep.session_token)

    ended = service.revoke_all_for_user(
        member.user_id, "password_change", except_session_id=kept.session_id
    )
    assert ended == 1
    service.resolve(keep.session_token)
    with pytest.raises(SessionExpired):
        service.resolve(drop.session_token)


def test_every_session_failure_says_the_same_thing(gateway, member, mail):
    """Missing, revoked, idled out and aged out are one message: learning which
    applies tells the caller whether the token was ever real."""
    service = SessionService()
    result = LoginUseCase(mail).execute(
        LoginRequest(email="dev@zerosone.test", password=GOOD_PASSWORD)
    )
    service.logout(result.session_token)

    with pytest.raises(SessionExpired) as revoked:
        service.resolve(result.session_token)
    with pytest.raises(SessionExpired) as bogus:
        service.resolve("not-a-real-token")
    assert str(revoked.value) == str(bogus.value)


# ----------------------------------------------------- unfamiliar network


def test_first_login_does_not_raise_an_alert(gateway, member, mail):
    """Alerting on the very first login makes a new user's first experience a
    security warning about themselves."""
    result = LoginUseCase(mail).execute(
        LoginRequest(email="dev@zerosone.test", password=GOOD_PASSWORD, client_ip="10.0.0.1")
    )
    assert not result.unfamiliar_network
    assert mail.sent == []


def test_same_network_does_not_alert(gateway, member, mail):
    use_case = LoginUseCase(mail)
    use_case.execute(
        LoginRequest(email="dev@zerosone.test", password=GOOD_PASSWORD, client_ip="10.0.0.1")
    )
    result = use_case.execute(
        LoginRequest(email="dev@zerosone.test", password=GOOD_PASSWORD, client_ip="10.0.0.87")
    )
    assert not result.unfamiliar_network, "a /24 neighbour is the same network"
    assert mail.sent == []


def test_new_network_alerts_by_mail(gateway, member, mail):
    """By mail, not in-app: an in-app alert is visible to whoever holds the
    session, including the person it is warning about."""
    use_case = LoginUseCase(mail)
    use_case.execute(
        LoginRequest(email="dev@zerosone.test", password=GOOD_PASSWORD, client_ip="10.0.0.1")
    )
    result = use_case.execute(
        LoginRequest(email="dev@zerosone.test", password=GOOD_PASSWORD, client_ip="203.0.113.9")
    )
    assert result.unfamiliar_network
    assert len(mail.sent) == 1
    assert "新网络" in mail.sent[0].subject
    assert "203.0.113.9" in mail.sent[0].text_body


def test_an_unparseable_address_does_not_alert(gateway, member, mail):
    """Alerting because a proxy sent a malformed header trains people to ignore
    the alert."""
    use_case = LoginUseCase(mail)
    use_case.execute(
        LoginRequest(email="dev@zerosone.test", password=GOOD_PASSWORD, client_ip="10.0.0.1")
    )
    result = use_case.execute(
        LoginRequest(email="dev@zerosone.test", password=GOOD_PASSWORD, client_ip="unknown")
    )
    assert not result.unfamiliar_network


# -------------------------------------------------------------- AC-3 TOTP


def test_totp_enrollment_stores_nothing_until_a_code_verifies(gateway, member, mail):
    """Storing first would leave accounts holding a factor nobody can satisfy —
    the classic way to lock people out while enabling MFA."""
    service = TotpService()
    enrollment = service.begin_enrollment("dev@zerosone.test")
    with tenant_session(context_for(gateway.tenant_id)) as session:
        assert session.get(User, member.user_id).totp_secret is None

    with pytest.raises(InvalidTotpCode):
        service.confirm_enrollment(gateway.tenant_id, member.user_id, enrollment.secret, "000000")
    with tenant_session(context_for(gateway.tenant_id)) as session:
        assert session.get(User, member.user_id).totp_secret is None

    service.confirm_enrollment(
        gateway.tenant_id, member.user_id, enrollment.secret, pyotp.TOTP(enrollment.secret).now()
    )
    with tenant_session(context_for(gateway.tenant_id)) as session:
        assert session.get(User, member.user_id).totp_secret == enrollment.secret


def _enroll(service: TotpService, gateway, user_id) -> str:
    enrollment = service.begin_enrollment("dev@zerosone.test")
    service.confirm_enrollment(
        gateway.tenant_id, user_id, enrollment.secret, pyotp.TOTP(enrollment.secret).now()
    )
    return enrollment.secret


def test_login_with_totp_requires_the_second_factor(gateway, member, mail):
    service = TotpService()
    secret = _enroll(service, gateway, member.user_id)

    with pytest.raises(MfaRequired) as exc:
        LoginUseCase(mail).execute(
            LoginRequest(email="dev@zerosone.test", password=GOOD_PASSWORD)
        )
    half_open = exc.value.session_token

    resolved = SessionService().resolve(half_open)
    assert not resolved.mfa_satisfied, "the session must not be usable before the code"

    service.verify_login(half_open, pyotp.TOTP(secret).now())
    assert SessionService().resolve(half_open).mfa_satisfied


def test_a_wrong_code_ends_the_half_open_session(gateway, member, mail):
    """Otherwise the session token is an oracle for brute-forcing six digits at
    leisure."""
    service = TotpService()
    _enroll(service, gateway, member.user_id)
    with pytest.raises(MfaRequired) as exc:
        LoginUseCase(mail).execute(
            LoginRequest(email="dev@zerosone.test", password=GOOD_PASSWORD)
        )

    with pytest.raises(InvalidTotpCode):
        service.verify_login(exc.value.session_token, "000000")
    with pytest.raises(SessionExpired):
        SessionService().resolve(exc.value.session_token)


def test_disabling_totp_requires_the_password(gateway, member, mail):
    """A stolen session must not be enough to strip the second factor."""
    from relay.app.errors import ValidationFailed

    service = TotpService()
    _enroll(service, gateway, member.user_id)
    with pytest.raises(ValidationFailed):
        service.disable(gateway.tenant_id, member.user_id, "Wr0ng-Password!")
    service.disable(gateway.tenant_id, member.user_id, GOOD_PASSWORD)
    with tenant_session(context_for(gateway.tenant_id)) as session:
        assert session.get(User, member.user_id).totp_secret is None


def test_admin_mfa_gap_reports_admins_without_totp(gateway, member):
    """AC-3's recommendation, made checkable. Self-service signup makes the
    Admin account the only control point over who is in."""
    assert admin_mfa_gap(gateway.tenant_id) == ["admin@zerosone.test"]

    service = TotpService()
    enrollment = service.begin_enrollment("admin@zerosone.test")
    service.confirm_enrollment(
        gateway.tenant_id,
        gateway.admin_user_id,
        enrollment.secret,
        pyotp.TOTP(enrollment.secret).now(),
    )
    assert admin_mfa_gap(gateway.tenant_id) == []


def test_an_expired_session_records_why_it_ended(gateway, member, mail):
    """`revoked_reason` exists for the investigation where guessing is
    expensive, so it has to actually be written — and it is written on a code
    path that raises, which is where it gets discarded by accident."""
    result = LoginUseCase(mail).execute(
        LoginRequest(email="dev@zerosone.test", password=GOOD_PASSWORD)
    )
    later = dt.datetime.now(dt.UTC) + IDLE_TIMEOUT + dt.timedelta(minutes=1)
    with pytest.raises(SessionExpired):
        SessionService().resolve(result.session_token, now=later)

    with tenant_session(context_for(gateway.tenant_id)) as session:
        record = session.scalars(select(UserSession)).one()
    assert record.revoked_at is not None
    assert record.revoked_reason == "idle"


def test_a_rate_limit_block_is_persisted_not_just_reported(gateway, mail):
    """Same class of bug: the block is written and then raised past, so the
    limiter reports a block it never stored."""
    from relay.app.errors import RateLimited
    from relay.infra.db.models import Throttle
    from relay.infra.db.pre_tenant import PreTenantRepository

    use_case = SignupUseCase(mail)
    for i in range(11):
        try:
            use_case.execute(
                SignupRequest(
                    email=f"probe{i}@elsewhere.test",
                    password=GOOD_PASSWORD,
                    client_ip="198.51.100.7",
                )
            )
        except RateLimited:
            break

    with PreTenantRepository().session() as session:
        row = session.scalars(select(Throttle).where(Throttle.bucket == "signup_ip")).one()
    assert row.blocked_until is not None, "the block was rolled back by its own exception"
