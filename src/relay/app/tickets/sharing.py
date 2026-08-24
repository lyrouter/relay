"""Who may read a ticket (decision S-21).

Tickets carry no share level, and for an Admin or a Member that is still the
whole story: a ticket is tenant-wide by construction — L3 — because a team's
board is the team's board, and giving every ticket an ACL is the fine-grained
authorization §5.4 rules out.

**A Guest is the exception, and it is a decision rather than a fallout.** The
role exists for outside contractors, and "add the contractor so they can pick up
their two tickets" must not hand over the board — the same shape as S-6, where a
Guest in a space still does not reach L2. So:

    a Guest reads a ticket only if they are its **assignee or reporter**.

Two consequences worth stating, because both are visible in the product:

* **A Guest's board is their own work, not a filtered team board.** They cannot
  browse, and they cannot count how many tickets exist. Refusal is ``NotFound``,
  never ``PermissionDenied`` — a ticket a Guest may not read is one they should
  not learn exists, the same reasoning MT-6 applies across tenants and LOG-6
  applies inside one.
* **Reporter is in the rule even though a Guest cannot file today** (no
  ``TICKET_WRITE``). It costs nothing now and is what makes the rule still
  correct on the day a Guest is allowed to report a bug — as opposed to a rule
  that says "assignee" and quietly hides the reporter's own ticket from them.

A **service token has no role** (``role is None``) and reads the whole board:
its scopes are its authority (§8.2), and reading tickets is what the public API
exists for. That is the opposite of the log rule, where a role-less principal
reaches nothing — logs are not on the S1 API surface at all (§8.3), so "nothing"
is the conservative answer there and "the board" is the correct one here.

``relay.infra.db.visibility.visible_tickets_predicate`` is the SQL mirror of this
function, for the list and for search. ``tests/test_tickets.py`` cross-checks the
two for every (ticket, reader) pair: two implementations of one rule is how a
board becomes visible to somebody it should not be, and neither half shows the
drift in a diff.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from relay.domain.enums import Role


@dataclass(frozen=True, slots=True)
class TicketReader:
    """Who is asking, reduced to what the decision needs.

    ``role`` is None for a service token, which has no user and therefore no
    role.
    """

    user_id: uuid.UUID | None
    role: Role | None


def can_read_ticket(
    *,
    reader: TicketReader,
    assignee_id: uuid.UUID | None,
    reporter_id: uuid.UUID | None,
) -> bool:
    """The single place the ticket read question is answered.

    Kept a pure function for the same reason ``logs.sharing.can_read`` is: the
    truth table is the part worth testing exhaustively, and it should be
    testable without a database.
    """
    if reader.role is not Role.GUEST:
        # Admin, Member, and a role-less service principal: tenant-wide.
        return True
    if reader.user_id is None:
        return False
    return reader.user_id in {one for one in (assignee_id, reporter_id) if one is not None}
