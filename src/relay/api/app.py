"""The FastAPI application factory.

Assembles three things and nothing else: the error shape (§8.6), the routers, and
a liveness probe. Everything a route needs beyond that comes from
:mod:`relay.api.dependencies`.

**Two surfaces, one application, different contracts.** ``/web`` is the SPA's own
API and ships with the frontend that consumes it. ``/api/v1`` is the frozen public
contract (API-1/2/3) and is **not mounted yet** — the namespace is reserved here
so that when it lands, the error handler, the session/token seam and the
pagination convention are already the same ones ``/web`` uses. §8.3 makes the
same point about reserving ``/logs`` and ``/search`` inside ``/api/v1``: claiming
the name early is what stops two conventions from growing.

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
)

#: Bumped when the *web* surface changes shape. Not a contract version — ``/web``
#: is versionless on purpose (see ``relay.api.web``) — just something a support
#: conversation can pin down.
VERSION = "0.1.0"

DESCRIPTION = """\
Relay S1 · the workbench HTTP surface.

`/web/*` is the Vue frontend's own API: it ships with the frontend and is
versionless. The frozen public ticket API (`/api/v1`) is a separate surface and
is not mounted yet.
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
    ):
        app.include_router(router)

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
