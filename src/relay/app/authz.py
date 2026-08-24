"""AC-4 · where the capability table meets a request.

"Checked at the service layer" (§5.4) is a decision about *where*, and it needs
somewhere to actually be. This module is that somewhere: a :class:`Principal`
carrying the caller's role and, if they came in on a token, its scopes — plus
:func:`require`, which raises.

The one rule to keep in mind when using it: **build the Principal from the
stored user row, per call.** ``UserSession`` deliberately does not cache the
role, so a demotion takes effect on the demoted user's next request rather than
whenever their session happens to expire. Constructing a Principal from
anything longer-lived than the current transaction throws that away.

Guest handling is why the failure type matters here. :class:`PermissionDenied`
is right for a capability — "you may not change roles" tells the caller nothing
they should not know. Whether a *resource* exists is a different question, and
the answer for another tenant's row is ``NotFound`` (MT-6, §4.5); a service that
looks the target up under RLS gets that behaviour for free, because the row is
simply not there.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from relay.app.errors import PermissionDenied
from relay.context import ActorType, Origin, current_context
from relay.domain.enums import Role, TokenScope, UserStatus
from relay.domain.permissions import Capability, effective_capabilities
from relay.infra.db.models import User

#: One message for every refused capability. A caller learning *which* power
#: they lack learns the shape of the permission model; the next step is the same
#: either way, which is to ask an Admin.
NOT_PERMITTED = "你的角色没有执行该操作的权限，如有需要请联系管理员。"

ACTOR_NOT_ACTIVE = "你的账号已停用或尚未生效，无法执行该操作。"

#: S-20 · what a scheduled run may do. **A closed list, not "whatever an Admin
#: can do"**: adding a capability here is a line in a review, which is the only
#: control there is over an identity that authorizes itself (see
#: :func:`system_principal`). Today one job needs one capability.
SYSTEM_CAPABILITIES: frozenset[Capability] = frozenset({Capability.USER_MANAGE})

SYSTEM_NOT_FOR_REQUESTS = "系统身份不能用来服务请求。"
SYSTEM_HAS_NO_USER = "系统身份不能借用某个用户的身份。"


@dataclass(frozen=True, slots=True)
class Principal:
    """Who is acting, for authorization purposes only.

    ``role`` is None for a service token: it has no user, and therefore no role,
    so its scopes are the whole of its authority.
    """

    tenant_id: uuid.UUID
    user_id: uuid.UUID | None
    role: Role | None
    #: None means "not a token" — a browser session. An **empty** set means a
    #: token that was granted nothing, which is not the same thing and must not
    #: collapse into the session case.
    scopes: frozenset[TokenScope] | None = None
    #: S-20 · set **only** for the system actor, which has no role to derive
    #: capabilities from. Anything else leaves it None and goes through the
    #: role/scope table, so this cannot become a way to hand a user extra
    #: powers at a call site.
    granted: frozenset[Capability] | None = None

    @property
    def capabilities(self) -> frozenset[Capability]:
        if self.granted is not None:
            return self.granted
        return effective_capabilities(self.role, self.scopes)

    def can(self, capability: Capability) -> bool:
        return capability in self.capabilities


def system_principal() -> Principal:
    """S-20 · the identity a scheduled job runs as.

    The 90-day version purge needs ``USER_MANAGE`` and a scheduler has no
    session, so something had to give. The three candidates were: let the job
    impersonate an Admin, route it through ``SystemRepository``, or give the
    scheduler an identity of its own. This is the third.

    **Be clear about what it does and does not buy.** The capability set is the
    job's own declaration, so this is not a second opinion about whether the job
    may run — an identity that authorizes itself never is. What it buys is:

    * **honest attribution.** ``audit_log`` says ``system``, not the name of
      whichever Admin's account was borrowed. A row that names a person who was
      asleep is worse than no row, because it is evidence of the wrong thing.
    * **a wall between this and a request.** ``origin`` must be ``SYSTEM``, so a
      web or API request cannot run as system even if a bug arranged the actor
      type. The HTTP layer never builds one — sessions resolve to
      ``ActorType.USER`` — and this is the check that holds if that ever changes.
    * **a reviewable list.** :data:`SYSTEM_CAPABILITIES` is greppable and short;
      "the scheduler can do anything an Admin can" is neither.

    Not ``SystemRepository``: that is the cross-tenant BYPASSRLS path, and the
    purge is an ordinary in-tenant operation that should stay under RLS. Using a
    cross-tenant channel to do per-tenant work is how a bug becomes a leak.
    """
    ctx = current_context()
    if ctx.actor_type is not ActorType.SYSTEM:
        raise PermissionDenied(SYSTEM_NOT_FOR_REQUESTS)
    if ctx.origin is not Origin.SYSTEM:
        # A system actor arriving over the web is either a bug or an attempt.
        raise PermissionDenied(SYSTEM_NOT_FOR_REQUESTS)
    if ctx.actor_id is not None:
        raise PermissionDenied(SYSTEM_HAS_NO_USER)
    return Principal(
        tenant_id=ctx.tenant_id, user_id=None, role=None, granted=SYSTEM_CAPABILITIES
    )


def actor_principal(session) -> Principal:
    """Build the caller's Principal from their stored row, in this transaction.

    The one way a service should get a Principal for a browser session, and the
    reason it takes a session rather than caching anything: reading the role here
    is what makes a demotion effective on the demoted user's *next* call. It also
    catches the actor who was deactivated while holding a live session — which
    ``SessionService`` refuses too, one layer up, but a service that only ever
    ran under a resolved session would be relying on that by accident.

    The lookup runs under RLS, so an actor id from outside the tenant is simply
    absent and refused.

    A **system** actor has no row to read, so it is dispatched to
    :func:`system_principal` — that way a use case does not need to know whether
    a person or the scheduler called it (S-20).
    """
    ctx = current_context()
    if ctx.actor_type is ActorType.SYSTEM:
        return system_principal()
    if ctx.actor_id is None:
        raise PermissionDenied(ACTOR_NOT_ACTIVE)
    user = session.get(User, ctx.actor_id)
    if user is None or user.status is not UserStatus.ACTIVE:
        raise PermissionDenied(ACTOR_NOT_ACTIVE)
    return Principal(tenant_id=ctx.tenant_id, user_id=user.id, role=user.role)


def require(principal: Principal, capability: Capability) -> None:
    """Raise unless ``principal`` holds ``capability``.

    Kept as a free function taking the Principal rather than a method on it so
    that a call site reads as an assertion about the request — and so that
    grepping for ``require(`` finds every place a permission is enforced.
    """
    if not principal.can(capability):
        raise PermissionDenied(NOT_PERMITTED, detail={"capability": str(capability)})
