"""LOG-8 · the search use case (design §6.4).

Thin on purpose. ``PgroongaSearch`` already applies the share-level filter in
SQL, so this layer is not where the rule lives — it is where the *call* lives, so
that the HTTP layer talks to a use case rather than to an adapter (§8.1), and so
that two things nobody wants in a router have somewhere to be:

* **the capability check.** ``CONTENT_VIEW``, once, before the query.
* **the S-19 audit.** A search snippet of a colleague's private draft is a read of
  it. Recording it here rather than inside the adapter keeps the privilege rule
  in the application layer and leaves ``SearchPort`` implementable by anything
  that can match text — which is the substitution the port exists to keep cheap.

The port takes no tenant and no reader: an implementation reads the ambient
``TenantContext``, so there is no signature a caller can get wrong. The cost is
that the audit needs a second session after the search returns, which is honest
— the row is written because the read happened, not before.
"""

from __future__ import annotations

from relay.app.authz import actor_principal, require
from relay.app.logs import read_audit
from relay.domain.permissions import Capability
from relay.infra.db.session import tenant_session
from relay.ports.search import SearchPort, SearchResults


class SearchUseCase:
    """Runs inside an established ``TenantContext``."""

    def __init__(self, port: SearchPort) -> None:
        self._port = port

    def execute(
        self, query: str, kinds: tuple[str, ...] = (), limit: int = 20
    ) -> SearchResults:
        with tenant_session() as session:
            require(actor_principal(session), Capability.CONTENT_VIEW)

        results = self._port.search(query, kinds=kinds, limit=limit)

        log_ids = [hit.id for hit in results.hits if hit.kind == "log"]
        if log_ids:
            with tenant_session() as session:
                # The principal is rebuilt rather than reused: it is a different
                # transaction, and re-reading the row is the rule everywhere else
                # (a demotion between the two would be a demotion, not a race to
                # win).
                actor = actor_principal(session)
                if read_audit.record_by_id(session, actor, log_ids, via="search"):
                    session.commit()
        return results
