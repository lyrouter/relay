"""Who am I, and who else is here — the two reads every screen starts with.

Neither is a feature in the plan, which is why they did not exist until the HTTP
layer needed them. Both are here rather than in a router because the API layer
does not touch the repository (§8.1), and because "what may this person do?" is a
question with one right answer and several plausible wrong ones.

:func:`me` returns the caller's **capabilities** along with their role. That is
deliberate and it is the same reasoning
:func:`relay.domain.permissions.token_request_refusal` gives for returning a
message instead of raising: a UI that offers an action the service layer will
refuse is its own kind of bug. The frontend hides what the caller cannot do by
asking, not by re-deriving the §5.4 matrix in TypeScript — where it would drift.

:func:`members` is the directory behind an assignee picker and mention
autocomplete. It returns the **mention handle** (the email local part, which is
what ``@lisa`` resolves against) and not the address. The handle is unavoidable —
mentions are typed with it — but the domain half carries nothing the UI needs.
Note that the public API's ``/meta/users`` (API-2) is stricter still: id and
display name only, never an email, not even the local part.

:func:`update_display_name` and :func:`change_password` are the two writes a
person can make to **their own** account. Email and role are not among them:
email is the residency credential (AC-9), and role is an Admin decision (AC-4).
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

from sqlalchemy import func, select

from relay.app import audit
from relay.app.accounts.sessions import SessionService
from relay.app.authz import actor_principal, require
from relay.app.errors import NotFound, ValidationFailed
from relay.domain import passwords
from relay.domain.enums import Role, UserStatus
from relay.domain.permissions import Capability
from relay.infra.db.models import Tenant, User
from relay.infra.db.session import tenant_session
from relay.infra.security.passwords import hash_password, verify_password

TENANT_MISSING = "找不到该租户。"

#: Matches ``User.display_name``'s column. Enforced here so a 500 from the
#: database is not the first the caller hears of the limit.
DISPLAY_NAME_MAX = 200


@dataclass(frozen=True, slots=True)
class MeView:
    user_id: uuid.UUID
    email: str
    display_name: str
    role: Role
    #: AC-3. Whether *this* account has TOTP enrolled — never whether anyone
    #: else does.
    mfa_enrolled: bool
    tenant_id: uuid.UUID
    tenant_slug: str
    tenant_name: str
    #: Design §2: timestamps are stored UTC and rendered in the tenant's zone.
    #: The renderer is the frontend, so it needs the zone.
    timezone: str
    capabilities: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MemberView:
    user_id: uuid.UUID
    display_name: str
    #: The mention handle: ``@lisa`` for ``lisa@zerosone.test`` (TKT-4).
    handle: str
    role: Role
    status: UserStatus


def me() -> MeView:
    """The caller, their tenant, and what they may do.

    No capability required — it describes the caller to themselves. A session
    that cannot answer this question is a session that cannot render a page.
    """
    with tenant_session() as session:
        actor = actor_principal(session)
        user = session.get(User, actor.user_id)
        tenant = session.get(Tenant, actor.tenant_id)
        if user is None or tenant is None:
            # ``actor_principal`` already refused a missing user; a missing
            # tenant means the row is gone under RLS, which is not recoverable
            # from here.
            raise NotFound(TENANT_MISSING)
        return MeView(
            user_id=user.id,
            email=user.email,
            display_name=user.display_name,
            role=user.role,
            mfa_enrolled=user.totp_secret is not None,
            tenant_id=tenant.id,
            tenant_slug=tenant.slug,
            tenant_name=tenant.name,
            timezone=tenant.timezone,
            capabilities=tuple(sorted(str(one) for one in actor.capabilities)),
        )


def members(limit: int = 200) -> list[MemberView]:
    """Everyone in the tenant who can still be picked, newest last.

    Deactivated accounts are **excluded**: assigning a ticket to somebody who
    left is a mistake the picker should not offer (and ``_check_assignee``
    refuses it anyway, one layer down). Pending accounts are included — they
    exist and an Admin has to be able to see them to approve them.
    """
    with tenant_session() as session:
        require(actor_principal(session), Capability.CONTENT_VIEW)
        rows = session.execute(
            select(
                User.id,
                User.display_name,
                func.split_part(User.email, "@", 1),
                User.role,
                User.status,
            )
            .where(User.status != UserStatus.DEACTIVATED)
            .order_by(User.display_name.asc())
            .limit(limit)
        ).all()
        return [
            MemberView(
                user_id=row[0],
                display_name=row[1],
                handle=row[2],
                role=row[3],
                status=row[4],
            )
            for row in rows
        ]


def update_display_name(display_name: str) -> MeView:
    """Rename the caller. Email and role stay where they are.

    No capability required: this is the caller's own row, and a session that
    can render a page can also put a name on it. An empty name after stripping
    is refused rather than falling back to the email local part — that fallback
    is a create-time convenience, not something an edit should silently undo.
    """
    cleaned = display_name.strip()
    if not cleaned:
        raise ValidationFailed("显示名不能为空。")
    if len(cleaned) > DISPLAY_NAME_MAX:
        raise ValidationFailed(f"显示名不能超过 {DISPLAY_NAME_MAX} 个字符。")

    with tenant_session() as session:
        user = _caller(session)
        if user.display_name != cleaned:
            before = user.display_name
            user.display_name = cleaned
            audit.record(
                session,
                "account.display_name_changed",
                target_type="user",
                target_id=user.id,
                before={"display_name": before},
                after={"display_name": cleaned},
            )
            session.commit()
    return me()


def change_password(
    current_password: str,
    new_password: str,
    *,
    except_session_id: uuid.UUID,
    sessions: SessionService | None = None,
) -> int:
    """Replace the caller's password and end every other live session.

    The current password is required so a stolen session cannot rotate the
    credential. Other sessions die — ``except_session_id`` is the one that just
    proved it still knows the old password — because otherwise a laptop left
    logged in would keep working after the owner thought they had locked it.
    ``SessionService.revoke_all_for_user`` already named this case; this is
    the call it was waiting for.

    Returns how many other sessions were ended, so the UI can say so.
    """
    sessions = sessions or SessionService()
    with tenant_session() as session:
        user = _caller(session)
        if not verify_password(user.password_hash, current_password):
            # 422, not 401: the session is still good. A 401 would bounce the
            # SPA to login over a typo, which is not the next step.
            raise ValidationFailed("当前密码不正确。")
        if current_password == new_password:
            raise ValidationFailed("新密码不能与当前密码相同。")
        try:
            passwords.validate(new_password, email=user.email)
        except passwords.WeakPassword as exc:
            raise ValidationFailed(str(exc)) from exc

        user.password_hash = hash_password(new_password)
        user.password_changed_at = dt.datetime.now(dt.UTC)
        # A successful change is also proof they can still authenticate, so a
        # leftover lockout from earlier failed logins should not survive it.
        user.failed_login_count = 0
        user.locked_until = None
        audit.record(
            session,
            "account.password_changed",
            target_type="user",
            target_id=user.id,
        )
        user_id = user.id
        session.commit()

    return sessions.revoke_all_for_user(
        user_id, "password_change", except_session_id=except_session_id
    )


def _caller(session) -> User:
    """The stored row for the person making this request.

    ``actor_principal`` already refused a missing or inactive user; looking the
    row up again is so the caller can mutate it in this transaction, not so we
    can disagree with that check.
    """
    actor = actor_principal(session)
    if actor.user_id is None:
        # A service token has no profile. /web never hands one of those to
        # these functions; this is the wall if a caller ever does.
        raise NotFound(TENANT_MISSING)
    user = session.get(User, actor.user_id)
    if user is None:
        raise NotFound(TENANT_MISSING)
    return user
