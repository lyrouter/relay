"""AC-3 · optional TOTP, recommended-enforced for Admin.

Self-service signup is what makes this matter more here than it would under
invite-only: **the Admin account becomes the only control point** over who is in
the platform, because nobody hands out accounts any more. An Admin password is
therefore the whole perimeter.

"Recommended for Admin" is implemented as a policy function rather than a hard
gate, because turning it into one would lock out an existing Admin the moment it
ships. :func:`admin_mfa_gap` is what a deployment check or the acceptance
dashboard reads to see whether the recommendation is being followed.

Secrets are stored raw rather than hashed — a TOTP secret is a shared secret by
construction, so it must be readable to verify a code. That makes the `user`
table's protection the whole story, which is worth stating plainly rather than
implying a strength the design does not have.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

import pyotp
from sqlalchemy import select

from relay.app.errors import ApplicationError, NotFound, ValidationFailed
from relay.domain.enums import Role, UserStatus
from relay.infra.db.models import User, UserSession
from relay.infra.db.pre_tenant import PreTenantRepository
from relay.infra.db.session import commit_and_raise, tenant_session
from relay.infra.security.passwords import verify_password

ISSUER = "Relay"

#: One step either side, so a phone clock a few seconds off still works.
VALID_WINDOW = 1


class InvalidTotpCode(ApplicationError):
    code = "invalid_totp"


@dataclass(frozen=True, slots=True)
class TotpEnrollment:
    secret: str
    #: For the QR code. Contains the secret, so it is shown once and never logged.
    provisioning_uri: str


class TotpService:
    def __init__(self) -> None:
        self._pre = PreTenantRepository()

    @staticmethod
    def begin_enrollment(email: str) -> TotpEnrollment:
        """Generate a candidate secret. **Not yet stored.**

        Storing before the user has proved they can produce a code would leave
        accounts holding a second factor nobody can satisfy — the classic way
        to lock people out of their own account while enabling MFA.
        """
        secret = pyotp.random_base32()
        uri = pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=ISSUER)
        return TotpEnrollment(secret=secret, provisioning_uri=uri)

    def confirm_enrollment(
        self, tenant_id: uuid.UUID, user_id: uuid.UUID, secret: str, code: str
    ) -> None:
        """Store the secret only after a code from it verifies."""
        if not pyotp.TOTP(secret).verify(code.strip(), valid_window=VALID_WINDOW):
            raise InvalidTotpCode("动态码不正确，请确认手机时间准确后重试。")
        with tenant_session(_context(tenant_id, user_id)) as session:
            user = session.get(User, user_id)
            if user is None:
                raise NotFound("用户不存在。")
            user.totp_secret = secret
            session.commit()

    def verify_login(self, session_token: str, code: str, *, now: dt.datetime | None = None):
        """Satisfy the second factor for a session opened by ``LoginUseCase``."""
        from relay.app.accounts.sessions import SessionExpired
        from relay.infra.security.tokens import hash_token

        now = now or dt.datetime.now(dt.UTC)
        with self._pre.session() as session:
            record = session.scalars(
                select(UserSession).where(
                    UserSession.token_hash == hash_token(session_token.strip())
                )
            ).first()
            if record is None or record.revoked_at is not None or record.idle_expires_at <= now:
                raise SessionExpired("登录状态已失效，请重新登录。")
            if record.mfa_satisfied:
                return

            user = session.get(User, record.user_id)
            if user is None or user.totp_secret is None:
                raise SessionExpired("登录状态已失效，请重新登录。")

            if not pyotp.TOTP(user.totp_secret).verify(code.strip(), valid_window=VALID_WINDOW):
                # A wrong code ends the half-open session rather than allowing
                # retries: otherwise the session token becomes an oracle for
                # brute-forcing a six-digit code at leisure.
                record.revoked_at = now
                record.revoked_reason = "mfa_failed"
                commit_and_raise(
                    session, InvalidTotpCode("动态码不正确，请重新登录后再试。")
                )

            record.mfa_satisfied = True
            session.flush()

    def disable(
        self, tenant_id: uuid.UUID, user_id: uuid.UUID, current_password: str
    ) -> None:
        """Removing a second factor requires the first one.

        Otherwise a stolen session is enough to strip MFA, which makes having
        it pointless in exactly the scenario it exists for.
        """
        with tenant_session(_context(tenant_id, user_id)) as session:
            user = session.get(User, user_id)
            if user is None:
                raise NotFound("用户不存在。")
            if not verify_password(user.password_hash, current_password):
                raise ValidationFailed("密码不正确。")
            user.totp_secret = None
            session.commit()


def admin_mfa_gap(tenant_id: uuid.UUID) -> list[str]:
    """Active Admins without TOTP.

    The recommendation made checkable. A deployment review or INT-8's dashboard
    can read this; nothing blocks on it, because a hard gate would lock out the
    Admin who is standing there when it ships.
    """
    with tenant_session(_context(tenant_id, None)) as session:
        return list(
            session.scalars(
                select(User.email).where(
                    User.role == Role.ADMIN,
                    User.status == UserStatus.ACTIVE,
                    User.totp_secret.is_(None),
                )
            )
        )


def _context(tenant_id: uuid.UUID, actor_id: uuid.UUID | None):
    from relay.context import ActorType, Origin, TenantContext

    return TenantContext(
        tenant_id=tenant_id, actor_id=actor_id, actor_type=ActorType.USER, origin=Origin.WEB
    )
