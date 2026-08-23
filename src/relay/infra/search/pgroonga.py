"""LOG-8 · full-text search over PG FTS + pgroonga (F-2, no separate service).

Covers §6.4's surface: **log titles + log bodies + ticket titles.** Same database,
same policies — which is the rule ``SearchPort`` was created to write down for
RAG (see :mod:`relay.ports.search`). Nothing here reaches outside PostgreSQL, so
there is no second isolation mechanism to negative-test.

Three decisions worth knowing about before reading the SQL:

* **``&@``, not ``&@~``.** The second operator accepts pgroonga's query syntax —
  ``OR``, parentheses, prefixes. Handing a user's raw input to it means a stray
  bracket is either an error or a silently different search. ``&@`` treats the
  input as terms and is what a search box should do. Query syntax can be exposed
  later behind an explicit flag; it cannot be un-exposed.
* **Results are filtered by share level, not just by tenant.** RLS gets the
  tenant right on its own; a search that returned a colleague's private log
  because pgroonga matched it would be a leak that no policy catches. The
  visibility clause is the same one the log list uses
  (:func:`relay.infra.db.visibility.visible_logs_predicate`), so there is one
  rule and not two.
* **Ranked by recency, not by relevance.** ``pgroonga_score`` returns 0 for an
  index built without a scorer, and configuring one is real work for a corpus
  this size. "What did we write about this lately" is also what people actually
  want from a log search. The ``score`` field is populated from
  ``pgroonga_score`` anyway so that a future scorer needs no signature change,
  and it is documented as informational rather than as the sort key.

Indexing is a no-op: PostgreSQL maintains the index. The methods exist on the
port because an external engine would need them, and that is exactly the
substitution the port is there to keep cheap.
"""

from __future__ import annotations

import uuid

from sqlalchemy import literal_column, or_, select

from relay.context import current_context
from relay.domain.enums import Role, UserStatus
from relay.infra.db.models import Log, Ticket, User
from relay.infra.db.session import tenant_session
from relay.infra.db.visibility import visible_logs_predicate
from relay.ports.search import SearchHit, SearchResults

#: Characters around the first match. Long enough to show a sentence, short
#: enough that twenty hits do not ship a book.
SNIPPET_WINDOW = 160

KINDS = ("log", "ticket")


class PgroongaSearch:
    """Implements :class:`relay.ports.search.SearchPort`."""

    def search(
        self, query: str, kinds: tuple[str, ...] = (), limit: int = 20
    ) -> SearchResults:
        term = (query or "").strip()
        if not term:
            # An empty search is not "everything": a search box that returns the
            # corpus on an accidental Enter is how people find things they were
            # not looking for.
            return SearchResults(hits=(), total=0)

        wanted = tuple(kind for kind in (kinds or KINDS) if kind in KINDS)
        hits: list[SearchHit] = []

        with tenant_session() as session:
            role, user_id = _reader(session)
            if "log" in wanted:
                hits.extend(_log_hits(session, term, role, user_id, limit))
            if "ticket" in wanted:
                hits.extend(_ticket_hits(session, term, limit))

        # Interleaved by recency across both kinds, then truncated. Sorting the
        # merged list rather than each kind separately means a busy ticket board
        # cannot push every log off the first page.
        hits.sort(key=lambda hit: hit.score, reverse=True)
        return SearchResults(hits=tuple(hits[:limit]), total=len(hits))

    def index_log(self, log_id: uuid.UUID) -> None:
        """No-op: PostgreSQL maintains the index on write.

        Kept because the port declares it, and because an external engine — the
        substitution ``SearchPort`` exists to make cheap — would need exactly
        this call at exactly these sites.
        """

    def index_ticket(self, ticket_id: uuid.UUID) -> None:
        """No-op, for the same reason as :meth:`index_log`."""


def _reader(session) -> tuple[Role | None, uuid.UUID | None]:
    """The actor's role, read fresh — the same rule as ``actor_principal``.

    Done here rather than by taking a Principal parameter because
    ``SearchPort.search`` deliberately has no such argument: an implementation
    reads the ambient context, so there is no signature a caller can get wrong.
    """
    ctx = current_context()
    if ctx.actor_id is None:
        return None, None
    user = session.get(User, ctx.actor_id)
    if user is None or user.status is not UserStatus.ACTIVE:
        return None, None
    return user.role, user.id


def _log_hits(
    session, term: str, role: Role | None, user_id: uuid.UUID | None, limit: int
) -> list[SearchHit]:
    matches = or_(Log.title.op("&@")(term), Log.body.op("&@")(term))
    rows = session.execute(
        select(Log.id, Log.title, Log.body, Log.updated_at, _score(Log))
        .where(matches, visible_logs_predicate(user_id, role))
        .order_by(Log.updated_at.desc())
        .limit(limit)
    ).all()
    return [
        SearchHit(
            kind="log",
            id=row.id,
            title=row.title,
            snippet=_snippet(row.body, term),
            score=_recency_score(row.updated_at),
        )
        for row in rows
    ]


def _ticket_hits(session, term: str, limit: int) -> list[SearchHit]:
    """Titles only (§6.4).

    Ticket descriptions are out of the index on purpose: they are largely stack
    traces and pasted logs, so indexing them would double the index for the
    half of the corpus with the worst signal.

    No share-level clause, because a ticket has no share level — it is
    tenant-wide by construction, which is open item T-2 rather than an omission
    here.
    """
    rows = session.execute(
        select(Ticket.id, Ticket.number, Ticket.title, Ticket.description, Ticket.updated_at)
        .where(Ticket.title.op("&@")(term))
        .order_by(Ticket.updated_at.desc())
        .limit(limit)
    ).all()
    return [
        SearchHit(
            kind="ticket",
            id=row.id,
            title=row.title,
            snippet=_snippet(row.description, term),
            score=_recency_score(row.updated_at),
        )
        for row in rows
    ]


def _score(model):
    """``pgroonga_score`` for the row, as an informational column.

    Returns 0 for an index built without a scorer, which is why it is not the
    sort key. Selected anyway so that turning on real relevance ranking later is
    a change to the ORDER BY and not to this module's shape.
    """
    return literal_column("pgroonga_score(tableoid, ctid)").label("pgroonga_score")


def _recency_score(updated_at) -> float:
    """The actual sort key, as a float so the field means one thing.

    Documented as recency rather than relevance — see the module docstring. A
    caller sorting by ``score`` gets newest-first, which is the honest behaviour
    of this implementation.
    """
    return float(updated_at.timestamp()) if updated_at is not None else 0.0


def _snippet(text: str, term: str) -> str:
    """A window around the first occurrence of any word in the query.

    Approximate on purpose: pgroonga tokenises, so the term that actually
    matched is not knowable from here without asking it. Falling back to the head
    of the text is right for the CJK case, where a match often has no ASCII
    boundary to find.
    """
    body = (text or "").strip()
    if not body:
        return ""
    lowered = body.lower()
    for word in term.lower().split():
        position = lowered.find(word)
        if position >= 0:
            start = max(0, position - SNIPPET_WINDOW // 3)
            return _ellipsise(body, start, start + SNIPPET_WINDOW)
    return _ellipsise(body, 0, SNIPPET_WINDOW)


def _ellipsise(body: str, start: int, end: int) -> str:
    piece = body[start:end]
    if start > 0:
        piece = "…" + piece
    if end < len(body):
        piece = piece + "…"
    return piece
