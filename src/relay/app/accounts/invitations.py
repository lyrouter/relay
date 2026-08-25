"""AC-1 · invitations, the secondary path (§5.2).

Self-service signup is the primary route and a rule; this is the exception, and
it is deliberately the only way past that rule. Two halves, in two different
worlds:

* **inviting** happens inside a tenant, by an Admin, and is audited. It is user
  management (§5.4 row 1), so it needs ``USER_MANAGE`` — a Member who could
  invite would be a Member who can add anyone to the tenant, which is the whole
  residency rule undone from the inside.
* **accepting** happens with no session and no tenant, because the token is all
  the caller has. Same pre-tenant path as signup and verification.

**An invitation is not checked against the domain allowlist, and that is the
point.** The refusal AC-1 gives an unknown domain says "contact your
administrator for an invite"; a route that then refused the invite for the same
reason would be a dead end wearing a next step. So the epic's "cannot create an
account by any route" means no *self-service* route: an invitation is an Admin
deliberately naming one person, which is exactly the judgment the allowlist
exists to avoid having to make at scale — not to prevent.

Holding the token verifies the address by itself: it arrived in that mailbox and
nothing else can have read it, which is the same proof
:mod:`relay.app.accounts.verification` extracts from a verification link. So an
invited account starts ACTIVE and verified, and can log in immediately.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

from sqlalchemy import select

from relay.app import audit
from relay.app.authz import actor_principal, require
from relay.app.errors import Conflict, ValidationFailed
from relay.context import ActorType, Origin, TenantContext, current_context, tenant_scope
from relay.domain import passwords
from relay.domain.enums import Role, UserStatus
from relay.domain.permissions import Capability
from relay.domain.residency import email_domain, normalize_email
from relay.infra.db.models import Invitation, User
from relay.infra.db.pre_tenant import PreTenantRepository
from relay.infra.db.session import tenant_session
from relay.infra.security.passwords import hash_password
from relay.infra.security.tokens import generate_token, hash_token
from relay.ports.mail import MailPort, OutboundMail

#: Longer than the 24h verification TTL: a verification link is clicked by
#: someone who just typed their password, an invitation waits for a person to
#: notice a mail they were not expecting.
INVITATION_TTL = dt.timedelta(days=7)

#: One message for expired, already used, never existed, and "the address has an
#: account now". A caller holding a bad token learns nothing about which.
INVALID_INVITATION = "邀请链接无效或已过期，请联系邀请你的管理员重新发送。"

ALREADY_A_MEMBER = "该邮箱在本租户已有账号。"


@dataclass(frozen=True, slots=True)
class AcceptedInvitation:
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    role: Role


class InviteUserUseCase:
    """Runs inside an established ``TenantContext``."""

    def __init__(self, mail: MailPort, base_url: str = "https://relay.internal") -> None:
        self._mail = mail
        self._base_url = base_url.rstrip("/")

    def execute(
        self, email: str, role: Role = Role.MEMBER, *, now: dt.datetime | None = None
    ) -> uuid.UUID:
        """Invite one address at one role. Returns the invitation id.

        ``role`` may be ``ADMIN``: an Admin inviting another Admin is the
        in-product way to get a second one, which matters because
        ``bootstrap_tenant`` refuses to add one and AC-4 refuses to remove the
        last. Promoting an existing member does the same job — this is for the
        person who is not here yet.
        """
        now = now or dt.datetime.now(dt.UTC)
        address = normalize_email(email)
        try:
            email_domain(address)
        except ValueError as exc:
            raise ValidationFailed("请填写有效的邮箱地址。") from exc

        ctx = current_context()
        token = generate_token()
        with tenant_session() as session:
            actor = actor_principal(session)
            require(actor, Capability.USER_MANAGE)

            if session.scalar(select(User.id).where(User.email == address)) is not None:
                # Not the enumeration-safe message: the caller is an
                # authenticated Admin of this tenant, who is entitled to know
                # that the person already has an account — and needs to, or the
                # invitation appears to have been sent and never arrives.
                raise Conflict(ALREADY_A_MEMBER)

            invitation = Invitation(
                tenant_id=ctx.tenant_id,
                email=address,
                role=role,
                token_hash=hash_token(token),
                invited_by=actor.user_id,
                expires_at=now + INVITATION_TTL,
            )
            session.add(invitation)
            session.flush()
            audit.record(
                session,
                "account.invited",
                target_type="invitation",
                target_id=invitation.id,
                after={"email": address, "role": str(role)},
            )
            session.commit()
            invitation_id = invitation.id

        # Outside the transaction: a mail sent for an invitation the database
        # then rolled back is a link to nothing.
        self._send(address, token)
        return invitation_id

    def _send(self, address: str, token: str) -> None:
        link = f"{self._base_url}/invite?token={token}"
        self._mail.send(
            OutboundMail(
                to=address,
                subject="你被邀请加入 Relay",
                text_body=(
                    "管理员邀请你加入 Relay。\n\n"
                    f"请在 7 天内打开下面的链接设置密码并激活账号：\n{link}\n\n"
                    "如果你不认识邀请方，忽略这封邮件即可 —— 未使用的邀请会自动过期。\n"
                ),
            )
        )


class AcceptInvitationUseCase:
    """Consumes an invitation token. No session, no tenant — see the module note."""

    def __init__(self) -> None:
        self._pre = PreTenantRepository()

    def execute(
        self,
        token: str,
        password: str,
        display_name: str = "",
        *,
        now: dt.datetime | None = None,
    ) -> AcceptedInvitation:
        now = now or dt.datetime.now(dt.UTC)
        token_hash = hash_token(token.strip())

        with self._pre.session() as session:
            invitation = session.scalars(
                select(Invitation).where(Invitation.token_hash == token_hash)
            ).first()
            if (
                invitation is None
                or invitation.accepted_at is not None
                or invitation.expires_at <= now
            ):
                raise ValidationFailed(INVALID_INVITATION)

            # Validated only once the token is known good, so that a weak
            # password on a dead link fails for the reason that matters.
            try:
                passwords.validate(password, email=invitation.email)
            except passwords.WeakPassword as exc:
                raise ValidationFailed(str(exc)) from exc

            if self._pre.email_taken(session, invitation.tenant_id, invitation.email):
                # The address registered by another route, or a second
                # invitation was accepted first. Same message: the invitation is
                # spent either way, and the difference is not the invitee's
                # business.
                raise ValidationFailed(INVALID_INVITATION)

            user = User(
                tenant_id=invitation.tenant_id,
                email=invitation.email,
                password_hash=hash_password(password),
                status=UserStatus.ACTIVE,
                role=invitation.role,
                display_name=display_name.strip() or invitation.email.split("@")[0],
                # Holding the token *is* the proof of address. Requiring a
                # second round trip would only prove it twice.
                email_verified_at=now,
                password_changed_at=now,
            )
            session.add(user)
            session.flush()
            invitation.accepted_at = now

            # The invite was audited under the Admin who sent it; this row
            # closes the loop — who actually turned up, and when.
            with tenant_scope(
                TenantContext(
                    tenant_id=invitation.tenant_id,
                    actor_id=user.id,
                    actor_type=ActorType.USER,
                    origin=Origin.WEB,
                )
            ):
                audit.record(
                    session,
                    "account.invitation_accepted",
                    target_type="user",
                    target_id=user.id,
                    after={"invitation": str(invitation.id), "role": str(invitation.role)},
                )

            return AcceptedInvitation(
                user_id=user.id, tenant_id=invitation.tenant_id, role=invitation.role
            )
