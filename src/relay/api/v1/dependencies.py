"""API-1/3/5 · what every ``/api/v1`` route inherits before its own body runs.

Five rules live here so that no route decides any of them for itself.

**1 · The tenant comes from the token, never from the request** (§8.2). A
``tenant_id`` in a query string or a JSON body is a **400** — not a 422, and not a
hint we helpfully ignore: a caller sending one believes it selects the tenant, and
silently ignoring it means they think they wrote to tenant B while they wrote to
tenant A. Refusing is the only answer that cannot be misread.

**2 · The dependency is ``async``, and that is load-bearing.** Identical mechanics
to the session dependency (``relay.api.dependencies``): FastAPI runs a *sync*
generator dependency in a worker thread, whose ``ContextVar`` is a copy, so the
``TenantContext`` set there would be invisible to the endpoint and every request
would raise ``MissingTenantContext``. Blocking work therefore goes through
``run_in_threadpool`` explicitly.

**3 · Scopes are checked here, not only in the application layer.** The capability
table is coarse — ``tickets:read`` and ``meta:read`` both grant ``CONTENT_VIEW`` —
so a token holding only ``meta:read`` would otherwise be able to read tickets
through the use case. §8.2 promises four scopes that *mean* something, so each
route names the one it needs.

**4 · Rate limits are counted for every request and reported on every response**
(S-14). ``X-RateLimit-*`` on success too, not just on the 429: instrumentation is
what makes the "tighten after two weeks" half of that decision possible, and a
client can only slow itself down if it can see its budget.

**5 · Idempotency is a route decorator's job, not a route's** (API-3). ``POST``
routes ask :func:`idempotent` for a claim; the replay path returns the first
response verbatim without re-running the use case.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable
from typing import Annotated, Any

from fastapi import Depends, Request, Response
from fastapi.concurrency import run_in_threadpool

from relay.app import api_rate_limit, idempotency
from relay.app.api_tokens import TENANT_IN_REQUEST, ApiTokenService, AuthenticatedToken
from relay.app.errors import PermissionDenied, TenantInRequest
from relay.context import tenant_scope
from relay.domain.enums import TokenScope

#: The one refusal for a missing or unusable ``Authorization`` header. Same
#: message as a revoked token, on purpose (see ``InvalidToken``).
CREDENTIALS_MISSING = "缺少 API token。请带上 Authorization: Bearer <token>。"

SCOPE_REFUSED = "该 token 的权限范围不包含此操作。"

#: Query parameters that would be an attempt to select a tenant. ``tenant`` and
#: ``tenant_slug`` are here too: the rule is about the *intent*, and a caller who
#: sends ``?tenant=other`` has the same misunderstanding as one who sends an id.
TENANT_SELECTORS = frozenset({"tenant_id", "tenant", "tenant_slug"})


class MissingCredentials(PermissionDenied):
    """No bearer token at all. ``invalid_token`` so it answers 401 like the rest."""

    code = "invalid_token"


class ScopeRefused(PermissionDenied):
    """The token authenticated but is not scoped for this operation.

    403 rather than 401: the credential is valid, so re-authenticating changes
    nothing. What has to change is the token's scopes, which needs a person.
    """

    code = "permission_denied"


async def require_token(request: Request, response: Response) -> AsyncIterator[AuthenticatedToken]:
    """Authenticate, rate-limit, and hold the tenant scope for the request.

    Order is deliberate: the tenant-selector check runs **first**, because a
    request that misunderstands tenancy should be refused before it can consume
    quota or slide anything's clock.
    """
    await _refuse_tenant_selectors(request)

    presented = _bearer(request)
    token = await run_in_threadpool(ApiTokenService.authenticate, presented)

    decision = await run_in_threadpool(
        api_rate_limit.consume, token.token_id, request.method
    )
    # Set on the injected response: FastAPI merges these headers into whatever
    # the route returns. On a 429 the exception handler adds ``Retry-After``
    # instead, from the error itself.
    response.headers["X-RateLimit-Limit"] = str(decision.limit)
    response.headers["X-RateLimit-Remaining"] = str(decision.remaining)
    response.headers["X-RateLimit-Reset"] = str(decision.reset_after)

    with tenant_scope(token.context):
        yield token


def scoped(*needed: TokenScope) -> Callable[..., AuthenticatedToken]:
    """A dependency requiring ``needed`` — any one of them — on the token.

    "Any" rather than "all" because the scopes are coarse and a route usually has
    one natural scope with one alias (commenting is allowed by ``comments:write``
    and, for a token that can write tickets outright, by ``tickets:write``).
    Routes that need a conjunction do not exist in S1, and inventing the
    machinery for them would be the fine-grained authorization §8.2 rules out.

    Kept as a factory so the requirement is visible in the route signature. A
    route that forgets it does not get a permissive default — it gets no token at
    all, because :func:`require_token` is only reachable through this.
    """

    def dependency(
        token: Annotated[AuthenticatedToken, Depends(require_token)],
    ) -> AuthenticatedToken:
        if not token.scopes & frozenset(needed):
            raise ScopeRefused(
                SCOPE_REFUSED,
                detail={"required": sorted(str(one) for one in needed)},
            )
        return token

    return dependency


#: Ready-made annotations, so a route reads as a type. One per scope the surface
#: actually uses.
TicketsRead = Annotated[AuthenticatedToken, Depends(scoped(TokenScope.TICKETS_READ))]
TicketsWrite = Annotated[AuthenticatedToken, Depends(scoped(TokenScope.TICKETS_WRITE))]
CommentsWrite = Annotated[
    AuthenticatedToken,
    Depends(scoped(TokenScope.COMMENTS_WRITE, TokenScope.TICKETS_WRITE)),
]
MetaRead = Annotated[
    AuthenticatedToken, Depends(scoped(TokenScope.META_READ, TokenScope.TICKETS_READ))
]
#: Webhook management is Admin-only and is enforced by the *capability* check in
#: the use case, not by a scope: no scope maps to ``WEBHOOK_MANAGE``, which is
#: what makes a service token unable to administer the tenant however it was
#: created (see ``effective_capabilities``). So the route only needs a token.
AnyToken = Annotated[AuthenticatedToken, Depends(require_token)]


async def _refuse_tenant_selectors(request: Request) -> None:
    """Rule 1. Query parameters and top-level body keys.

    Top-level only, and that boundary is a choice: ``ai_context`` is
    caller-defined JSON validated against the tenant's own field config (§7.3),
    and a field legitimately named ``tenant_id`` inside it is the caller's data,
    not an attempt to route the request. Refusing nested keys would break a
    payload that is doing nothing wrong.
    """
    for name in request.query_params:
        if name.lower() in TENANT_SELECTORS:
            raise TenantInRequest(TENANT_IN_REQUEST, detail={"parameter": name})

    if request.method in {"POST", "PUT", "PATCH"}:
        content_type = request.headers.get("content-type", "")
        if content_type.startswith("application/json"):
            # ``request.json()`` caches the body, so the route's own Pydantic
            # parsing still sees it. Malformed JSON is left alone here: it is
            # FastAPI's 422 to raise, with the field detail this cannot produce.
            try:
                body = await request.json()
            except (ValueError, UnicodeDecodeError):
                return
            if isinstance(body, dict):
                for name in body:
                    if str(name).lower() in TENANT_SELECTORS:
                        raise TenantInRequest(TENANT_IN_REQUEST, detail={"field": name})


def _bearer(request: Request) -> str:
    header = request.headers.get("authorization", "")
    scheme, _, credential = header.partition(" ")
    if scheme.lower() != "bearer" or not credential.strip():
        raise MissingCredentials(CREDENTIALS_MISSING)
    return credential.strip()


def idempotent(
    request: Request, key: str | None, body: Any
) -> tuple[str | None, idempotency.Replay | None]:
    """Claim the ``Idempotency-Key`` for this request, if one was sent.

    Returns the normalised key (or None) and a replay to return instead of doing
    the work. The key is optional by design: §8.3 offers it on ``POST`` and the
    business-level ``external_ref`` dedupe is what protects the paths that matter
    even when a caller sends neither.
    """
    normalised = idempotency.key_of(key)
    if normalised is None:
        return None, None
    replay = idempotency.begin(
        normalised, request.method, request.url.path, body
    )
    return normalised, replay


def ticket_url(tenant_slug: str, number: int) -> str:
    """The permalink §8.3 requires in every ticket response.

    **The tenant segment is not optional** (S-12). With one tenant the UI may hide
    it, but the API carries it from day one: the first consumer is the gateway
    WebUI, which stores this URL against its feedback records, and adding the
    segment later would be a breaking change in somebody else's database.
    """
    from relay.config import settings

    return f"{settings.public_base_url.rstrip('/')}/{tenant_slug}/t/{number}"


def as_uuid(candidate: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(candidate)
    except (ValueError, AttributeError):
        return None
