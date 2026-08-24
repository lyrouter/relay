"""RFC 9457 ``application/problem+json`` — one error shape, no exceptions.

Pulled forward out of API-5 because every route needs it and retrofitting an
error format means touching all of them again. Design §8.6 is blunt about why it
cannot be left to FastAPI's defaults: the framework emits ``{"detail": ...}`` and
validation failures come back in a third shape, so an API that skips this step
has **two** error formats — and that is the first thing an integrator hits.

Four handlers, because there are four ways a request fails:

* :class:`relay.app.errors.ApplicationError` — the use case refused. The status
  comes from the error's ``code``, which is the machine-discriminable half of
  the problem document (§8.6's stable ``type``).
* ``RequestValidationError`` — Pydantic. **Stays 422** (§8.6: keep the status,
  normalise the body) and the field errors become ``errors[]``.
* ``HTTPException`` — **Starlette's**, not FastAPI's subclass of it. That
  distinction is the difference between covering an unmatched route and not:
  a 404 for a path nobody registered is raised by the router, and registering
  the handler on the subclass leaves exactly that case answering
  ``{"detail": "Not Found"}`` — the second error format §8.6 is about, in the
  one response an integrator hits before any other.
* anything else — a bug. 500 with **no detail**: an exception message is the one
  place a stack trace leaks a query, a path, or a row.

``type`` is a URI rather than a bare string because §8.6 says stable and
machine-discriminable, and a URI is the thing consumers can pin without also
pinning our prose. It does not have to resolve; it has to be unique and never
change.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from relay.app.errors import ApplicationError, RateLimited

logger = logging.getLogger("relay.api")

CONTENT_TYPE = "application/problem+json"

#: Where a ``type`` URI points. Frozen once published — a consumer matching on
#: it has our error taxonomy in a constant somewhere.
PROBLEM_BASE = "https://relay.internal/problems/"

#: ``ApplicationError.code`` → HTTP status. The interesting rows:
#:
#: * ``validation_failed`` is **422**, matching FastAPI's default for schema
#:   failures (§8.6) so that "the request was understood and refused" has one
#:   status whether the refusal came from Pydantic or from a use case;
#: * ``not_found`` covers MT-6's cross-tenant rule and LOG-6's unreadable log —
#:   both are 404 by design, never 403, because the caller must not learn the
#:   resource exists;
#: * ``account_locked`` is **423 Locked**, which is what it is. 401 would invite
#:   a client to retry credentials that cannot work for fifteen minutes;
#: * ``mfa_required`` is not here on purpose — the login route answers 200 with
#:   ``mfa_required: true``, because a second factor is the next step in a flow
#:   rather than a failed request. A 401 there would trip every SPA's global
#:   "session expired, go to login" handler, which is where the user already is.
STATUS_BY_CODE: dict[str, int] = {
    "not_found": 404,
    "permission_denied": 403,
    "conflict": 409,
    "validation_failed": 422,
    "rate_limited": 429,
    "session_expired": 401,
    "invalid_credentials": 401,
    "invalid_totp": 401,
    "account_locked": 423,
    "email_not_verified": 403,
    "mfa_required": 401,
}

#: Anything an ``ApplicationError`` subclass adds without updating the table.
#: 400 rather than 500: the use case refused deliberately, so it is the caller's
#: request that is wrong, and a new refusal should not read as an outage.
FALLBACK_STATUS = 400


def problem(
    *,
    status: int,
    code: str,
    title: str,
    detail: str | None = None,
    errors: list[dict[str, Any]] | None = None,
    extra: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "type": f"{PROBLEM_BASE}{code}",
        "title": title,
        "status": status,
    }
    if detail:
        body["detail"] = detail
    if errors:
        body["errors"] = errors
    if extra:
        body.update(extra)
    return JSONResponse(body, status_code=status, media_type=CONTENT_TYPE, headers=headers)


def install(app: FastAPI) -> None:
    """Register the four handlers on ``app``."""

    @app.exception_handler(ApplicationError)
    async def _application_error(request: Request, exc: ApplicationError) -> JSONResponse:
        status = STATUS_BY_CODE.get(exc.code, FALLBACK_STATUS)
        headers = None
        extra = dict(exc.detail) if exc.detail else None
        if isinstance(exc, RateLimited):
            # §8.6 requires Retry-After on a 429. The seconds are in the error
            # because only the use case knows the window it just consumed.
            headers = {"Retry-After": str(exc.retry_after_seconds)}
        return problem(
            status=status,
            code=exc.code,
            # ``message`` is user-facing and names the next step (design §2), so
            # it is the title rather than being buried in ``detail``.
            title=exc.message,
            extra=extra,
            headers=headers,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return problem(
            status=422,
            code="validation_failed",
            title="请求内容不符合要求，请检查后重试。",
            errors=[
                {
                    # ``loc`` includes the source ("body", "query"); joining it
                    # is what makes a field pointer readable by a human reading
                    # a log as well as by a form binding it.
                    "field": ".".join(str(part) for part in error.get("loc", ())),
                    "message": error.get("msg", ""),
                    "type": error.get("type", ""),
                }
                for error in exc.errors()
            ],
        )

    @app.exception_handler(HTTPException)
    async def _http_error(request: Request, exc: HTTPException) -> JSONResponse:
        return problem(
            status=exc.status_code,
            code=_code_for(exc.status_code),
            title=str(exc.detail),
            headers=dict(exc.headers) if exc.headers else None,
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        # Logged with the traceback, answered without it. The message of an
        # unexpected exception is the one place a SQL fragment or a filesystem
        # path escapes into a response body.
        logger.exception("unhandled error on %s %s", request.method, request.url.path)
        return problem(
            status=500,
            code="internal_error",
            title="服务出现意外错误，请稍后重试。如持续出现请联系管理员。",
        )


#: Statuses FastAPI raises on its own behalf. Everything else gets a code
#: derived from the number, so a new status cannot arrive as ``None``.
_CODES_BY_STATUS = {
    400: "bad_request",
    401: "session_expired",
    403: "permission_denied",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    415: "unsupported_media_type",
    422: "validation_failed",
    429: "rate_limited",
}


def _code_for(status: int) -> str:
    return _CODES_BY_STATUS.get(status, f"http_{status}")
