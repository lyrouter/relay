"""Cross-cutting request context.

Deliberately at the package root rather than inside ``relay.app``: both the
application layer (which establishes the context) and the infrastructure layer
(which turns it into ``SET LOCAL app.tenant_id``) need it, and neither should
import the other.

The cross-cutting constraint from design §2 that this module encodes: a request establishes a
``TenantContext`` on entry to the application layer and everything below it
carries that context. A repository reached with no context **raises**; it never
degrades into an all-tenant query.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from enum import StrEnum

from relay.domain.enums import TokenScope


class ActorType(StrEnum):
    """Who performed a write. Design §8.4: GH sync's first loop guard reads this."""

    USER = "user"
    INTEGRATION = "integration"
    SYSTEM = "system"


class Origin(StrEnum):
    """Which surface the write came through."""

    WEB = "web"
    API = "api"
    SYSTEM = "system"


class MissingTenantContext(RuntimeError):
    """Raised when data access is attempted with no tenant established.

    This is the failure mode we *want*. The alternative — quietly querying
    every tenant — is the bug that multi-tenancy exists to prevent.
    """


@dataclass(frozen=True, slots=True)
class TenantContext:
    tenant_id: uuid.UUID
    actor_id: uuid.UUID | None = None
    actor_type: ActorType = ActorType.SYSTEM
    origin: Origin = Origin.SYSTEM
    #: API-1 · the scopes of the token this request arrived on.
    #:
    #: ``None`` means "not a token" — a browser session or a scheduled job. An
    #: **empty** set means a token granted nothing, which is a different thing
    #: and must not collapse into the session case
    #: (:func:`relay.domain.permissions.effective_capabilities` keeps them
    #: apart).
    #:
    #: It lives on the context rather than being threaded through every use case
    #: for one reason: ``actor_principal`` builds the caller's Principal from the
    #: ambient context, so a use case does not know — and must not need to know —
    #: whether it was reached from the UI or from ``/api/v1``. That is §8.1's
    #: "the API is not a second implementation", made mechanical.
    scopes: frozenset[TokenScope] | None = None


_current: ContextVar[TenantContext | None] = ContextVar("relay_tenant_context", default=None)


def current_context() -> TenantContext:
    ctx = _current.get()
    if ctx is None:
        raise MissingTenantContext(
            "No TenantContext established. Data access outside a tenant scope is refused; "
            "use relay.context.tenant_scope(...) or the SystemRepository escape hatch."
        )
    return ctx


def current_context_or_none() -> TenantContext | None:
    return _current.get()


@contextmanager
def tenant_scope(ctx: TenantContext) -> Iterator[TenantContext]:
    token = _current.set(ctx)
    try:
        yield ctx
    finally:
        _current.reset(token)
