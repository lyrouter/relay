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
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select

from relay.app.authz import actor_principal, require
from relay.app.errors import NotFound
from relay.domain.enums import Role, UserStatus
from relay.domain.permissions import Capability
from relay.infra.db.models import Tenant, User
from relay.infra.db.session import tenant_session

TENANT_MISSING = "找不到该租户。"


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
