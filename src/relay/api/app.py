"""The FastAPI application factory.

Assembles three things and nothing else: the error shape (§8.6), the routers, and
a liveness probe. Everything a route needs beyond that comes from
:mod:`relay.api.dependencies`.

**Two surfaces, one application, different contracts.** ``/web`` is the SPA's own
API and ships with the frontend that consumes it; it is versionless and can change
field names in the same commit as its consumer. ``/api/v1`` is the frozen public
contract (API-1…6) and inherits — rather than re-invents — the error shape, the
pagination convention and the tenant seam that ``/web`` established (§8.9). Inside
it, ``/logs`` and ``/search`` are **claimed but not implemented** (§8.3): naming
them now is what stops a second set of conventions from growing later.

**A factory, not a module-level ``app``.** Settings are read per instance, so a
test can point ``RELAY_BLOB_ROOT`` somewhere else and build a second application
without reloading the package. ``uvicorn relay.api.app:create_app --factory`` is
the deployment form.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from relay.api import problems, wiring
from relay.api.v1 import ROUTERS as V1_ROUTERS
from relay.api.web import (
    admin,
    attachments,
    auth,
    logs,
    meta,
    notifications,
    search,
    session,
    spaces,
    tickets,
    tokens,
)

#: Bumped when the *web* surface changes shape. Not a contract version — ``/web``
#: is versionless on purpose (see ``relay.api.web``) — just something a support
#: conversation can pin down.
VERSION = "0.1.0"

DESCRIPTION = """\
Relay S1 · the workbench HTTP surface.

`/web/*` is the Vue frontend's own API: it ships with the frontend and is
versionless.

`/api/v1/*` is the **frozen** public ticket API. Authenticate with
`Authorization: Bearer rly_…`; the tenant is derived from the token and a
`tenant_id` in a request is a 400. Inside v1 only additive change is allowed —
consumers must tolerate unknown enum values — and every breaking change goes to
v2 with 90 days of overlap.
"""


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """One job: say the configuration mistakes out loud, at startup.

    Every warning ``check_configuration`` emits describes a deployment that
    *works* — mail silently unsent, links signed with a published key, a cookie
    without ``Secure``. None of them can be raised (that would make the
    development default unusable and the container un-startable), so the only
    thing standing between them and production is somebody reading a log line.
    """
    wiring.check_configuration()
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="Relay",
        version=VERSION,
        description=DESCRIPTION,
        # The generated schema is what the frontend's TS types come from
        # (API-5), so the paths are stable and documented rather than internal.
        openapi_url="/openapi.json",
        docs_url="/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    problems.install(app)

    for router in (
        auth.router,
        session.router,
        logs.router,
        attachments.router,
        tickets.router,
        meta.router,
        notifications.router,
        search.router,
        spaces.router,
        admin.router,
        tokens.router,
        *V1_ROUTERS,
    ):
        app.include_router(router)

    if wiring.blob_delivery_is_local():
        # LOG-5 · S-25. Only the filesystem carrier serves attachment bytes from
        # this application; with MinIO the browser fetches the object directly,
        # and a route that needs ``verify``/``open`` has nothing to call. The
        # carrier decides whether the URL exists at all rather than whether it
        # works — see ``relay.api.wiring.blob_delivery_is_local``.
        app.include_router(attachments.local_delivery_router)

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        """Liveness only — deliberately **not** a database check.

        A probe that queries PostgreSQL turns a slow database into a rolling
        restart, which is how a degraded system becomes an outage. Readiness
        (can this instance serve?) is a separate question and not one S1 has an
        answer for.
        """
        return {"status": "ok", "version": VERSION}

    return app
