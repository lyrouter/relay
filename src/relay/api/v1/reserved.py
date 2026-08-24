"""§8.3 · ``/logs`` and ``/search``: claimed now, implemented later.

These answer **501** with a problem document. That is not a placeholder for its
own sake — it is the cheapest way to stop a second set of conventions from
growing. When the log API lands it will need pagination, an error shape and an
auth seam; if the namespace is unclaimed until then, whoever builds it starts from
a blank page and picks their own, and the API ends up with two ways to page and
two ways to fail. Naming the route now means the next person edits an endpoint
that already sits inside the conventions.

**501, not 404.** A 404 says "no such thing, look elsewhere"; 501 says "this is
ours, it does not exist yet". An integrator who gets a 404 files a bug or builds a
workaround. One who gets this reads the message and asks when it ships.

They are excluded from the OpenAPI document on purpose: publishing an endpoint
that cannot serve a request would put a shape into the frozen v1 contract before
anybody has designed it, which is exactly backwards.
"""

from __future__ import annotations

from fastapi import APIRouter

from relay.api.v1.dependencies import AnyToken
from relay.app.errors import ApplicationError

router = APIRouter(prefix="/api/v1", tags=["reserved (v1)"], include_in_schema=False)

RESERVED = (
    "该命名空间已预留但 S1 未实现（设计 §8.3）。日志 API 与搜索 API 会沿用与 "
    "/api/v1/tickets 相同的分页与错误约定。"
)


class NotImplementedYet(ApplicationError):
    """A route that exists to hold its name. ``501`` via the code table."""

    code = "not_implemented"


@router.api_route(
    "/logs/{rest:path}", methods=["GET", "POST", "PATCH", "PUT", "DELETE"]
)
def logs_reserved(rest: str, token: AnyToken) -> None:
    """The log API (LOG-*) is not in S1's public surface.

    Requiring a token even here is deliberate: an unauthenticated 501 would tell
    anybody on the network which namespaces we plan to build.
    """
    raise NotImplementedYet(RESERVED, detail={"namespace": "/api/v1/logs"})


@router.api_route("/search", methods=["GET", "POST"])
def search_reserved(token: AnyToken) -> None:
    """Search (LOG-8) is in the product but not in the public contract.

    It is behind ``SearchPort`` with pgroonga underneath, and its result shape is
    the thing most likely to change once real usage arrives — publishing it into a
    frozen v1 now would be the expensive kind of premature.
    """
    raise NotImplementedYet(RESERVED, detail={"namespace": "/api/v1/search"})
