"""AC-1 · self-service signup (S-3).

The flip from invite-only to self-service is what turns "who gets into the
platform" from a human decision into a rule, and this is where the rule runs.
Three outcomes, from :mod:`relay.domain.residency`: join, wait for approval, or
be refused — refused meaning *refused*, not parked in a pending pool.

**Email verification is mandatory.** The domain is the only residency credential
a self-signup has, so an unverified account means anyone can walk in with a
fabricated same-domain address. Token TTL 24h, single-use, hash-stored.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

from relay.app.accounts import throttle
from relay.app.errors import ValidationFailed
from relay.domain import passwords
from relay.domain.enums import Role, UserStatus
from relay.domain.residency import (
    ResidencyOutcome,
    email_domain,
    normalize_email,
    resolve,
)
from relay.infra.db.models import EmailVerification, User
from relay.infra.db.pre_tenant import PreTenantRepository
from relay.infra.security.passwords import hash_password
from relay.infra.security.tokens import generate_token, hash_token
from relay.ports.mail import MailPort, OutboundMail

VERIFICATION_TTL = dt.timedelta(hours=24)

#: One message for "registered" and for "already registered" alike. Signup is
#: unauthenticated, so distinguishing them would turn the endpoint into an
#: account-existence oracle for any allowlisted domain.
ACCEPTED_MESSAGE = "如果该邮箱可以注册，我们已经发送了一封验证邮件，请查收（24 小时内有效）。"

PENDING_MESSAGE = "注册已提交，等待管理员审批。审批通过后你会收到通知。"


@dataclass(frozen=True, slots=True)
class SignupRequest:
    email: str
    password: str
    display_name: str = ""
    #: For the per-IP limit. The caller (API layer) is responsible for producing
    #: a trustworthy value — behind a proxy that means the real client address,
    #: not whatever ``X-Forwarded-For`` says.
    client_ip: str = ""


@dataclass(frozen=True, slots=True)
class SignupResult:
    outcome: ResidencyOutcome
    message: str
    #: None when refused, and None when the address was already taken — the
    #: caller must not be able to tell those apart from the outside.
    user_id: uuid.UUID | None = None
    tenant_id: uuid.UUID | None = None


class SignupUseCase:
    def __init__(self, mail: MailPort, base_url: str = "https://relay.internal") -> None:
        self._mail = mail
        self._base_url = base_url.rstrip("/")
        self._pre = PreTenantRepository()

    def execute(self, request: SignupRequest, *, now: dt.datetime | None = None) -> SignupResult:
        now = now or dt.datetime.now(dt.UTC)
        email = normalize_email(request.email)

        try:
            domain = email_domain(email)
        except ValueError as exc:
            raise ValidationFailed("请填写有效的邮箱地址。") from exc

        # Password policy before anything expensive, and before the throttle is
        # consumed — a typo in the password should not cost an attempt.
        passwords.validate(request.password, email=email)

        with self._pre.session() as session:
            if request.client_ip:
                throttle.check_and_consume(
                    session, throttle.SIGNUP_PER_IP, request.client_ip, now=now
                )
            throttle.check_and_consume(session, throttle.SIGNUP_PER_DOMAIN, domain, now=now)

            residency = resolve(email, self._resolve_allowlist(session, domain))
            if residency.outcome is ResidencyOutcome.REFUSED:
                # No pending pool (S-3). The message names the next step.
                return SignupResult(residency.outcome, residency.message or "")

            found = self._pre.resolve_domain(session, domain)
            assert found is not None  # resolve() already established this
            tenant_id = found.tenant_id

            if self._pre.email_taken(session, tenant_id, email):
                # Deliberately indistinguishable from success. See ACCEPTED_MESSAGE.
                return SignupResult(residency.outcome, ACCEPTED_MESSAGE)

            status = (
                UserStatus.ACTIVE
                if residency.outcome is ResidencyOutcome.AUTO_JOIN
                else UserStatus.PENDING
            )
            user = User(
                tenant_id=tenant_id,
                email=email,
                password_hash=hash_password(request.password),
                status=status,
                role=residency.role or Role.MEMBER,
                display_name=request.display_name.strip() or email.split("@")[0],
                password_changed_at=now,
            )
            session.add(user)
            session.flush()

            token = self._issue_verification(session, user, now=now)

        # Sent outside the transaction: a mail that goes out for a signup the
        # database then rolled back is a link to nothing.
        self._send_verification(email, found.allowlist.domain, token)

        if residency.outcome is ResidencyOutcome.PENDING:
            return SignupResult(residency.outcome, PENDING_MESSAGE, user.id, tenant_id)
        return SignupResult(residency.outcome, ACCEPTED_MESSAGE, user.id, tenant_id)

    def _resolve_allowlist(self, session, domain: str):
        found = self._pre.resolve_domain(session, domain)
        return found.allowlist if found else None

    @staticmethod
    def _issue_verification(session, user: User, *, now: dt.datetime) -> str:
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
        return token

    def _send_verification(self, email: str, domain: str, token: str) -> None:
        link = f"{self._base_url}/verify?token={token}"
        self._mail.send(
            OutboundMail(
                to=email,
                subject="验证你的 Relay 邮箱",
                text_body=(
                    f"你在 Relay 用 {email} 注册了账号（域名 {domain}）。\n\n"
                    f"请在 24 小时内打开下面的链接完成验证：\n{link}\n\n"
                    "如果不是你本人操作，忽略这封邮件即可 —— 未验证的账号无法登录。\n"
                ),
            )
        )
