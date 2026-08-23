"""AC-2 · authentication, lockout, sessions (S-5).

Four things the task asks for, and the reasoning that shaped each:

* **password policy** — in :mod:`relay.domain.passwords`. The 90-day rule
  *reminds and lets you in* (S-5); forced rotation produces `Summer2026!!`.
* **failed-login lockout** — per account, time-boxed. Locking permanently turns
  a nuisance into a denial-of-service against a colleague.
* **session timeout** — two clocks, idle and absolute (see the model).
* **unfamiliar-location alert** — unfamiliar *network*, since geolocation is a
  dependency AC-2 does not have. See :mod:`relay.domain.networks`.

Enumeration is handled the same way signup handles it: every failure that is not
a lockout returns one message, and a login for an address that does not exist
still spends a password verification (``fake_verify``) so the two cannot be told
apart with a stopwatch.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

from sqlalchemy import select

from relay.app.errors import ApplicationError, PermissionDenied
from relay.domain import passwords
from relay.domain.enums import UserStatus
from relay.domain.networks import is_unfamiliar, network_key
from relay.domain.residency import normalize_email
from relay.infra.db.models import User, UserSession
from relay.infra.db.pre_tenant import PreTenantRepository
from relay.infra.db.session import commit_and_raise
from relay.infra.security.passwords import fake_verify, hash_password, needs_rehash, verify_password
from relay.infra.security.tokens import generate_token, hash_token
from relay.ports.mail import MailPort, OutboundMail

#: Not decided values in the plan — chosen here and easy to move.
IDLE_TIMEOUT = dt.timedelta(hours=8)
ABSOLUTE_TIMEOUT = dt.timedelta(days=7)
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_DURATION = dt.timedelta(minutes=15)

#: One message for wrong password, unknown address, and wrong tenant.
INVALID_CREDENTIALS = "邮箱或密码不正确。"


class InvalidCredentials(ApplicationError):
    code = "invalid_credentials"


class AccountLocked(ApplicationError):
    code = "account_locked"


class EmailNotVerified(ApplicationError):
    """AC-8: refused **with a resend link**. Always give the next step."""

    code = "email_not_verified"


class MfaRequired(ApplicationError):
    """AC-3. Carries a session id that is authenticated but not yet usable."""

    code = "mfa_required"

    def __init__(self, message: str, *, session_token: str) -> None:
        super().__init__(message)
        self.session_token = session_token


@dataclass(frozen=True, slots=True)
class LoginRequest:
    email: str
    password: str
    client_ip: str = ""
    user_agent: str = ""


@dataclass(frozen=True, slots=True)
class LoginResult:
    session_token: str
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    #: S-5: surfaced to the UI as a nudge, never as a block.
    password_reminder: bool = False
    unfamiliar_network: bool = False


class LoginUseCase:
    def __init__(self, mail: MailPort) -> None:
        self._mail = mail
        self._pre = PreTenantRepository()

    def execute(self, request: LoginRequest, *, now: dt.datetime | None = None) -> LoginResult:
        now = now or dt.datetime.now(dt.UTC)
        email = normalize_email(request.email)

        with self._pre.session() as session:
            user = session.scalars(select(User).where(User.email == email)).first()

            if user is None:
                # Spend the same time as a real verification so that "does this
                # account exist?" cannot be answered by timing.
                fake_verify()
                raise InvalidCredentials(INVALID_CREDENTIALS)

            if user.locked_until and user.locked_until > now:
                remaining = int((user.locked_until - now).total_seconds() // 60) + 1
                raise AccountLocked(
                    f"账号已因多次登录失败被临时锁定，请 {remaining} 分钟后再试，"
                    "或联系管理员重置密码。"
                )

            if not verify_password(user.password_hash, request.password):
                user.failed_login_count += 1
                if user.failed_login_count >= MAX_FAILED_ATTEMPTS:
                    # Time-boxed. A permanent lock hands anyone who knows a
                    # colleague's address the ability to keep them out.
                    user.locked_until = now + LOCKOUT_DURATION
                    user.failed_login_count = 0
                # commit_and_raise, not raise: the counter is the whole lockout
                # mechanism, and raising past the context manager would discard
                # it on every attempt — leaving code that reads as though it
                # locks accounts and never does.
                commit_and_raise(session, InvalidCredentials(INVALID_CREDENTIALS))

            if user.email_verified_at is None:
                raise EmailNotVerified(
                    "邮箱尚未验证，无法登录。可以在登录页点击「重新发送验证邮件」。"
                )
            if user.status is UserStatus.PENDING:
                raise PermissionDenied("账号仍在等待管理员审批。")
            if user.status is UserStatus.DEACTIVATED:
                # Distinguishable from a wrong password on purpose: the person
                # is the legitimate owner and needs to know why, not to keep
                # retrying. R-2 makes this a path people will actually hit.
                raise PermissionDenied("账号已停用，请联系管理员。")

            known = set(
                session.scalars(
                    select(UserSession.ip_address).where(UserSession.user_id == user.id)
                ).all()
            )
            unfamiliar = is_unfamiliar(request.client_ip, {network_key(ip) for ip in known})

            user.failed_login_count = 0
            user.locked_until = None
            user.last_login_at = now

            # Transparent upgrade if the Argon2 cost has been raised since this
            # password was last set. No migration, no forced reset.
            if needs_rehash(user.password_hash):
                user.password_hash = hash_password(request.password)

            mfa_pending = user.totp_secret is not None
            token = self._open_session(
                session, user, request, now=now, mfa_satisfied=not mfa_pending
            )
            reminder = passwords.needs_reminder(user.password_changed_at, now)
            user_id, tenant_id, user_email = user.id, user.tenant_id, user.email

        if unfamiliar:
            self._alert_unfamiliar_login(user_email, request, now)

        if mfa_pending:
            raise MfaRequired("请输入两步验证动态码。", session_token=token)

        return LoginResult(token, user_id, tenant_id, reminder, unfamiliar)

    @staticmethod
    def _open_session(
        session,
        user: User,
        request: LoginRequest,
        *,
        now: dt.datetime,
        mfa_satisfied: bool,
    ) -> str:
        token = generate_token()
        session.add(
            UserSession(
                tenant_id=user.tenant_id,
                user_id=user.id,
                token_hash=hash_token(token),
                idle_expires_at=now + IDLE_TIMEOUT,
                absolute_expires_at=now + ABSOLUTE_TIMEOUT,
                last_seen_at=now,
                ip_address=request.client_ip or None,
                user_agent=(request.user_agent or None) and request.user_agent[:512],
                mfa_satisfied=mfa_satisfied,
            )
        )
        session.flush()
        return token

    def _alert_unfamiliar_login(
        self, email: str, request: LoginRequest, now: dt.datetime
    ) -> None:
        """Sent by mail, not in-app.

        F-1 makes S1 in-app-only for *notifications*, and this is not one: an
        in-app security alert is visible to whoever is holding the session,
        including the person it is warning about. Mail reaches the account
        owner instead. Same reasoning as verification mail (F-5).
        """
        self._mail.send(
            OutboundMail(
                to=email,
                subject="Relay：检测到来自新网络的登录",
                text_body=(
                    f"你的 Relay 账号刚刚从一个新的网络登录：\n\n"
                    f"  时间：{now:%Y-%m-%d %H:%M} UTC\n"
                    f"  地址：{request.client_ip or '未知'}\n"
                    f"  客户端：{request.user_agent or '未知'}\n\n"
                    "如果是你本人，忽略这封邮件。\n"
                    "如果不是，请立即修改密码 —— 改密码会同时终止所有已登录的会话。\n"
                ),
            )
        )
