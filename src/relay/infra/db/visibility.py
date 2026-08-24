"""The read rules, expressed as SQL: LOG-6 share levels (§6.3) and S-21 tickets.

Each function here is the **mirror** of a pure function in the application
layer — :func:`relay.app.logs.sharing.can_read` and
:func:`relay.app.tickets.sharing.can_read_ticket` — not a second
authority. The pure function is where the rule is stated and exhaustively
tested; this exists because filtering in Python would mean reading every log in
the tenant to return five, and because two consumers need the same filter: the
log list and full-text search.

It lives in the repository layer rather than beside the rule because it is SQL
over mapped tables, and because the search adapter needs it without the app
layer needing to hand it over. ``tests/test_logs.py`` cross-checks the two
implementations against each other for every (log, reader) pair — the drift
between them is a leak or an invisible document, and neither shows up in a diff.

Takes primitives rather than a ``Principal`` so that nothing here has to import
the application layer.
"""

from __future__ import annotations

import uuid

from sqlalchemy import and_, or_, select

from relay.domain.enums import Role, ShareLevel
from relay.domain.permissions import role_reaches_share_level
from relay.infra.db.models import Log, LogShareGrant, SpaceMember, Ticket


def visible_logs_predicate(user_id: uuid.UUID | None, role: Role | None):
    """A WHERE clause selecting the logs this reader may see.

    The tenant is *not* part of it: RLS has already applied that, and §6.3 is
    explicit that cross-tenant invisibility is MT's guarantee and not LOG's
    judgment. Adding a redundant tenant filter here would invite the reading
    that this clause is what provides isolation.
    """
    if role is None or user_id is None:
        # A service token has no role and therefore no share-level reach. It can
        # still be given ticket scopes; logs are not part of the S1 API surface
        # (§8.3 reserves /logs without implementing it), so "nothing" is the
        # correct and conservative answer rather than an oversight.
        return Log.id.is_(None)

    mine = Log.author_id == user_id
    if role is Role.ADMIN:
        # §6.3: L0 is "仅作者 + Admin", so an Admin reaches every level.
        return or_(mine, Log.id.is_not(None))

    clauses = [mine, Log.share_level == ShareLevel.TENANT]

    if role_reaches_share_level(role, ShareLevel.NAMED):
        clauses.append(
            and_(
                Log.share_level == ShareLevel.NAMED,
                Log.id.in_(select(LogShareGrant.log_id).where(LogShareGrant.user_id == user_id)),
            )
        )
    # S-6 lands here: a Guest's role does not reach L2, so the space clause is
    # never added for them and no membership can talk the query into it.
    if role_reaches_share_level(role, ShareLevel.SPACE):
        clauses.append(
            and_(
                Log.share_level == ShareLevel.SPACE,
                Log.space_id.is_not(None),
                Log.space_id.in_(
                    select(SpaceMember.space_id).where(SpaceMember.user_id == user_id)
                ),
            )
        )
    return or_(*clauses)


def visible_tickets_predicate(user_id: uuid.UUID | None, role: Role | None):
    """A WHERE clause selecting the tickets this reader may see (S-21).

    The mirror of :func:`relay.app.tickets.sharing.can_read_ticket`. Same
    omission as above and for the same reason: the tenant is RLS's job, not this
    clause's.

    Note the asymmetry with logs for a **role-less principal** — a service token
    reads the whole board here, because that is what the public API is for
    (§8.2), while it reaches no log at all because logs are not on the S1 API
    surface (§8.3).
    """
    if role is not Role.GUEST:
        return Ticket.id.is_not(None)
    if user_id is None:
        return Ticket.id.is_(None)
    # A Guest sees their own work, not a filtered board — so this is an OR over
    # two columns rather than a status or label filter.
    return or_(Ticket.assignee_id == user_id, Ticket.reporter_id == user_id)
