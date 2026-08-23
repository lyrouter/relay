"""SearchPort (§4.4 · LOG-8) — and the one rule MT-5 leaves behind.

S1 has nothing to isolate in a vector store because there is no
``knowledge_unit`` table yet. What S1 *does* owe Phase 2 is the rule, written
somewhere a future implementer will actually read:

    **Any index Relay searches — full text or vector — lives in the same
    PostgreSQL database as the business tables and under the same RLS policy.**

pgvector is same-database by decision (D-0), so a vector table is an ordinary
table and the tenant policy applies to it for free. The failure mode this rule
exists to prevent is convenience: standing up an external vector service during
RAG because it is faster to start, and thereby creating a second isolation
mechanism that nobody negative-tests.

LOG-8 implements this over PG FTS + pgroonga (F-2: confirmed installable, so the
zhparser fallback is moot). ``SearchPort`` earns its keep by making a future
switch to a real external engine a port swap rather than a rewrite — at which
point the rule above becomes a hard question, not a footnote.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class SearchHit:
    kind: str  # "log" | "ticket"
    id: uuid.UUID
    title: str
    snippet: str
    score: float


@dataclass(frozen=True, slots=True)
class SearchResults:
    hits: tuple[SearchHit, ...]
    total: int


class SearchPort(Protocol):
    """Tenant scope is **not** a parameter here, deliberately.

    An implementation reads it from the ambient ``TenantContext``, the same way
    every other query does, so there is no signature a caller could get wrong.
    """

    def search(self, query: str, kinds: tuple[str, ...] = (), limit: int = 20) -> SearchResults:
        ...

    def index_log(self, log_id: uuid.UUID) -> None:
        ...

    def index_ticket(self, ticket_id: uuid.UUID) -> None:
        ...
