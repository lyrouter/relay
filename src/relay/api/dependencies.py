"""The session dependency, and the four rules every route inherits from it.

This is the "先立规矩" half of the HTTP layer: it exists once so that no route
decides for itself how authentication, tenancy, CSRF or the client address work.

**1 · The tenant context is established here and nowhere else.** Resolving the
cookie yields a ``TenantContext``; this dependency enters it for the life of the
request, and every use case below reads it from the ambient contextvar. A route
that forgot to depend on it does not get an unfiltered query — it gets
``MissingTenantContext``, because a ``TenantSession`` refuses to open without one.

**2 · The dependency is ``async`` and that is load-bearing.** FastAPI runs a
*sync* generator dependency in a worker thread, which gets a **copy** of the
context — so a ``ContextVar`` set there would be invisible to the endpoint, and
every request would fail with ``MissingTenantContext``. An async dependency runs
in the request's own task, and the endpoint (sync, run in a threadpool) inherits
a copy of *that* context, which includes the tenant. Blocking work is therefore
pushed to the threadpool explicitly with ``run_in_threadpool``, rather than the
dependency being made sync and the context quietly lost.

**3 · The session token lives in an HttpOnly cookie.** Not in ``localStorage``
behind an ``Authorization`` header: the token is a bearer credential for eight
hours, and the difference between the two choices is whether one XSS bug is a
stolen session. The cost of a cookie is CSRF, paid below.

**4 · A half-open session is not a session.** AC-3 opens a session *before* the
second factor is verified, so ``require_session`` refuses one where
``mfa_satisfied`` is false — with ``mfa_required``, which is a different answer
from "log in again". Only the TOTP route takes such a session, through
:func:`half_open_session`.
"""

from __future__ import annotations

import ipaddress
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request, Response
from fastapi.concurrency import run_in_threadpool

from relay.app.accounts.login import ABSOLUTE_TIMEOUT
from relay.app.accounts.sessions import (
    MFA_OUTSTANDING,
    MfaNotSatisfied,
    ResolvedSession,
    SessionExpired,
    SessionService,
)
from relay.app.errors import PermissionDenied
from relay.config import settings
from relay.context import Origin, tenant_scope

#: One name, set in one place. Prefixed so it is obvious in a browser inspector
#: which application owns it.
SESSION_COOKIE = "relay_session"

SESSION_MISSING = "请先登录。"

#: Methods that may change something. Everything else is a read, and a read
#: cannot be forged into an action.
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

CSRF_REFUSED = "请求来源不被信任，请从 Relay 页面内操作。"


class UntrustedOrigin(PermissionDenied):
    """A refused ``Origin``: the same 403 as any other refusal, its own message
    and its own name in a traceback."""


async def require_session(request: Request) -> AsyncIterator[ResolvedSession]:
    """Resolve the cookie, establish the tenant scope, and hold it for the request.

    Order matters: CSRF is checked **before** the token is resolved, so a forged
    cross-site request cannot even slide somebody's idle timeout.
    """
    _check_origin(request)
    resolved = await _resolve(request)
    if not resolved.mfa_satisfied:
        raise MfaNotSatisfied(MFA_OUTSTANDING)
    with tenant_scope(resolved.context):
        yield resolved


async def half_open_session(request: Request) -> AsyncIterator[ResolvedSession]:
    """Like :func:`require_session`, but accepts a session awaiting its second
    factor. **Only** the TOTP verification route may depend on this.

    Kept a separate function rather than a flag: a parameter that switches the
    MFA gate off is a parameter somebody copies onto a route that should not
    have it, and it would not look wrong in a diff.
    """
    _check_origin(request)
    resolved = await _resolve(request)
    with tenant_scope(resolved.context):
        yield resolved


async def _resolve(request: Request) -> ResolvedSession:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        # The same error a stale cookie gets: whether a token was ever presented
        # is not something the response should teach.
        raise SessionExpired(SESSION_MISSING)
    return await run_in_threadpool(SessionService().resolve, token, origin=Origin.WEB)


def _check_origin(request: Request) -> None:
    """CSRF, by the cheapest defence that actually holds.

    Two layers: the cookie is ``SameSite=Lax``, so a browser will not attach it
    to a cross-site POST at all; and every state-changing request must carry an
    ``Origin`` this deployment recognises. Browsers send ``Origin`` on all
    non-GET requests, so the check costs the frontend nothing.

    A request with **no** ``Origin`` is allowed: that is curl, a server-side
    caller or a test, none of which a third-party page can cause. Refusing it
    would break every non-browser client to defend against something a browser
    cannot do.
    """
    if request.method not in UNSAFE_METHODS:
        return
    origin = request.headers.get("origin")
    if origin is None:
        return
    if origin.rstrip("/") not in settings.allowed_origins:
        raise UntrustedOrigin(CSRF_REFUSED)


def client_ip(request: Request) -> str:
    """The address the signup and login throttles count against.

    ``X-Forwarded-For`` is believed **only** when the immediate peer is a
    configured trusted proxy, and then only its last hop — the entries before
    that are whatever the client wrote. Design's note on
    :class:`~relay.app.accounts.signup.SignupRequest` asks the API layer for a
    *trustworthy* value, and a header nobody vouched for is the opposite: one
    caller could spend every other caller's attempts, or dodge their own limit
    by changing a string.
    """
    peer = request.client.host if request.client else ""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded and peer in settings.trusted_proxy_addresses:
        hops = [one.strip() for one in forwarded.split(",") if one.strip()]
        if hops and _is_address(hops[-1]):
            return hops[-1]
    return peer


def set_session_cookie(response: Response, token: str) -> None:
    """Write the session cookie with the four attributes that matter.

    ``max_age`` is the **absolute** session lifetime (AC-2's second clock), not
    the idle one: a cookie that expired on the idle timeout would log people out
    of a tab they were about to use, while the server-side idle check still
    holds either way. The server is the authority on both clocks; the cookie
    just stops carrying a token that can no longer work.
    """
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=int(ABSOLUTE_TIMEOUT.total_seconds()),
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")


def _is_address(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


#: The three names routes actually use. ``Annotated`` rather than a ``Depends``
#: default so that a route signature reads as types, and so the dependency is
#: declared once here instead of being retyped — and mistyped — per route.
Session = Annotated[ResolvedSession, Depends(require_session)]
HalfOpenSession = Annotated[ResolvedSession, Depends(half_open_session)]
ClientIp = Annotated[str, Depends(client_ip)]
