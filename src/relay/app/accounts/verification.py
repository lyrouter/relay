"""AC-1 / AC-8 · email verification and resend.

Two rules from the degradation matrix, and both are about giving the next step:

* an unverified login is **refused with a resend link**, never a bare "cannot
  log in";
* a resend is rate-limited but never says whether the address exists.

The token is single-use (``consumed_at``) and hash-stored, so a database dump
contains no working verification links.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

from sqlalchemy import select

from relay.app.accounts import throttle
from relay.app.accounts.signup import VERIFICATION_TTL
from relay.app.errors import ValidationFailed
from relay.domain.enums import UserStatus
from relay.domain.residency import normalize_email
from relay.infra.db.models import EmailVerification, User
from relay.infra.db.pre_tenant import PreTenantRepository
from relay.infra.security.tokens import generate_token, hash_token
from relay.ports.mail import MailPort, OutboundMail

#: Same answer whether or not the address exists.
RESEND_MESSAGE = "如果该邮箱存在且尚未验证，我们已重新发送验证邮件。"

INVALID_TOKEN_MESSAGE = "验证链接无效或已过期。可以在登录页重新发送一封。"


@dataclass(frozen=True, slots=True)
class VerificationResult:
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    #: True when the account is now usable. False for an approved-pending user,
    #: who has verified their address but still waits on an Admin.
    activated: bool
    message: str


class VerifyEmailUseCase:
    """Consumes a verification token.

    Runs on the pre-tenant connection because the token is all the caller has —
    there is no session, and therefore no tenant, until it resolves.
    """

    def __init__(self) -> None:
        self._pre = PreTenantRepository()

    def execute(self, token: str, *, now: dt.datetime | None = None) -> VerificationResult:
        now = now or dt.datetime.now(dt.UTC)
        token_hash = hash_token(token.strip())

        with self._pre.session() as session:
            record = session.scalars(
                select(EmailVerification).where(EmailVerification.token_hash == token_hash)
            ).first()
            if record is None or record.consumed_at is not None or record.expires_at <= now:
                # One message for missing, spent and expired. A caller learning
                # "expired" learns the token was once real.
                raise ValidationFailed(INVALID_TOKEN_MESSAGE)

            user = session.get(User, record.user_id)
            if user is None:
                raise ValidationFailed(INVALID_TOKEN_MESSAGE)

            record.consumed_at = now
            user.email_verified_at = now

            # A PENDING user has verified their address but still needs Admin
            # approval (auto_join=false). Verification must not skip that.
            if user.status is UserStatus.PENDING:
                message = "邮箱已验证。你的账号仍在等待管理员审批。"
                activated = False
            else:
                user.status = UserStatus.ACTIVE
                message = "邮箱已验证，现在可以登录了。"
                activated = True

            session.flush()
            throttle.reset(session, throttle.VERIFICATION_RESEND, user.email)
            return VerificationResult(user.id, user.tenant_id, activated, message)


class ResendVerificationUseCase:
    def __init__(self, mail: MailPort, base_url: str = "https://relay.internal") -> None:
        self._mail = mail
        self._base_url = base_url.rstrip("/")
        self._pre = PreTenantRepository()

    def execute(self, email: str, *, now: dt.datetime | None = None) -> str:
        """Always returns the same message. Raises only when rate-limited."""
        now = now or dt.datetime.now(dt.UTC)
        email = normalize_email(email)

        with self._pre.session() as session:
            # Consumed before the lookup, so probing costs an attempt whether or
            # not the address exists.
            throttle.check_and_consume(session, throttle.VERIFICATION_RESEND, email, now=now)

            user = session.scalars(
                select(User).where(User.email == email, User.email_verified_at.is_(None))
            ).first()
            if user is None:
                return RESEND_MESSAGE

            # Invalidate outstanding tokens: two live links to one account means
            # a link forwarded by mistake stays usable after the user re-sends.
            for old in session.scalars(
                select(EmailVerification).where(
                    EmailVerification.user_id == user.id,
                    EmailVerification.consumed_at.is_(None),
                )
            ):
                old.consumed_at = now

            token = generate_token()
            session.add(
                EmailVerification(
                    tenant_id=user.tenant_id,
                    user_id=user.id,
                    token_hash=hash_token(token),
                    expires_at=now + VERIFICATION_TTL,
                )
            )
            session.flush()

        self._mail.send(
            OutboundMail(
                to=email,
                subject="验证你的 Relay 邮箱",
                text_body=(
                    f"请在 24 小时内打开下面的链接完成验证：\n"
                    f"{self._base_url}/verify?token={token}\n\n"
                    "之前发出的验证链接已失效。\n"
                ),
            )
        )
        return RESEND_MESSAGE
