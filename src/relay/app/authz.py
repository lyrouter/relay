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
from relay.context import current_context
from relay.domain.enums import Role, TokenScope, UserStatus
from relay.domain.permissions import Capability, effective_capabilities
from relay.infra.db.models import User

#: One message for every refused capability. A caller learning *which* power
#: they lack learns the shape of the permission model; the next step is the same
#: either way, which is to ask an Admin.
NOT_PERMITTED = "你的角色没有执行该操作的权限，如有需要请联系管理员。"

ACTOR_NOT_ACTIVE = "你的账号已停用或尚未生效，无法执行该操作。"


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

    @property
    def capabilities(self) -> frozenset[Capability]:
        return effective_capabilities(self.role, self.scopes)

    def can(self, capability: Capability) -> bool:
        return capability in self.capabilities


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
    """
    ctx = current_context()
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
