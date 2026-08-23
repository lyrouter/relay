"""TKT-3 · the ticket state machine (design §7.2).

**Frozen from release.** The status values ship in API responses, webhook
payloads and external systems' stored data, so renaming one is a v2-level change
(§8.6) — and the transition graph is on the same footing the moment
``POST /tickets/{key}/transitions`` exists.

The graph, exactly as §7.2 draws it::

    Todo ──▶ In Progress ──▶ In Review ──▶ Done
      │           │              │
      └──────▶ Blocked ◀─────────┘     Blocked is enterable from any active
      └──────▶ Won't Fix               state and **resumes to the one it came
                                       from**; Won't Fix reopens to Todo.

Blocked resumes to where it came from, which is why no ``blocked_from`` column
exists: ``ticket_status_history`` already records ``from_status`` on the row that
entered Blocked, so the resume target is a fact about history rather than a
second copy of it that can disagree.

**Two edges the design does not draw, and this module therefore does not
invent:**

* ``Done → Todo`` — reopening a ticket that turned out not to be fixed. §7.2
  gives Won't Fix an explicit reopen and says nothing about Done, and the
  "Reopened" state is deferred. Without the edge people file a duplicate, which
  is the thing INT-8's counts cannot see through.
* ``In Review → In Progress`` — a review that sends work back. Expressing it as
  Blocked would be wrong: blocked means waiting on something else, not
  rejected.

Both are real gaps, both are visible in the public API, and both are decided
values in the plan — so they belong in a design-doc change, not in a quiet
addition here (TODO-S1: "if one is wrong, change it in the design doc first").
Raised in the S1 open items instead.
"""

from __future__ import annotations

from relay.domain.enums import STATUSES_REQUIRING_REASON, TicketStatus

#: TKT-9 · S-12. **Frozen on release**: the key and the permalink both end up in
#: API responses, webhook payloads and external systems' stored rows, so a
#: change here is a breaking change for somebody else's database (§8.6).
TICKET_KEY_PREFIX = "RL-"


def ticket_key(number: int) -> str:
    return f"{TICKET_KEY_PREFIX}{number}"


def permalink(base_url: str, tenant_slug: str, number: int) -> str:
    """The canonical form, **tenant segment included from day one** (S-12).

    With one tenant the UI may hide the segment, but the router has to support
    it now: shipping ``/t/331`` first and adding the segment later makes the
    second tenant a breaking change for every stored link.
    """
    return f"{base_url.rstrip('/')}/{tenant_slug}/t/{number}"

#: Statuses that count as "work is under way", i.e. the ones Blocked can be
#: entered from and resumed to.
ACTIVE_STATUSES: frozenset[TicketStatus] = frozenset(
    {TicketStatus.TODO, TicketStatus.IN_PROGRESS, TicketStatus.IN_REVIEW}
)

#: The declared graph. ``BLOCKED``'s resume target is computed, so its entry
#: holds only the edges that do not depend on history.
TRANSITIONS: dict[TicketStatus, frozenset[TicketStatus]] = {
    TicketStatus.TODO: frozenset(
        {TicketStatus.IN_PROGRESS, TicketStatus.BLOCKED, TicketStatus.WONT_FIX}
    ),
    TicketStatus.IN_PROGRESS: frozenset(
        {TicketStatus.IN_REVIEW, TicketStatus.BLOCKED, TicketStatus.WONT_FIX}
    ),
    TicketStatus.IN_REVIEW: frozenset(
        {TicketStatus.DONE, TicketStatus.BLOCKED, TicketStatus.WONT_FIX}
    ),
    TicketStatus.BLOCKED: frozenset({TicketStatus.WONT_FIX}),
    #: Terminal in S1 — see the module note.
    TicketStatus.DONE: frozenset(),
    TicketStatus.WONT_FIX: frozenset({TicketStatus.TODO}),
}


class IllegalTransition(ValueError):
    """Carries the legal moves, not just the refusal.

    The cross-cutting constraint from design §2: a user-facing failure names the
    next step. "Cannot move to Done" without saying what *is* possible makes the
    caller guess at a graph they cannot see.
    """


class ReasonRequired(ValueError):
    """TKT-3: Blocked and Won't Fix require a written reason."""


def allowed_from(current: TicketStatus, blocked_from: TicketStatus | None = None) -> frozenset[
    TicketStatus
]:
    """Legal next statuses. ``blocked_from`` is the resume target when Blocked.

    A Blocked ticket whose history has been trimmed (or that was Blocked by a
    data import) has no resume target; it can still be abandoned to Won't Fix,
    so the ticket never becomes unmovable.
    """
    allowed = TRANSITIONS[current]
    if current is TicketStatus.BLOCKED and blocked_from is not None:
        return allowed | {blocked_from}
    return allowed


def check_transition(
    current: TicketStatus,
    target: TicketStatus,
    *,
    reason: str | None = None,
    blocked_from: TicketStatus | None = None,
) -> None:
    """Raise unless the move is legal and carries what it needs.

    A no-op transition (``current is target``) is refused rather than ignored: it
    would write a history row saying nothing happened, and through the API it is
    almost always a client that has lost track of the ticket's ``rev``.
    """
    if target is current:
        raise IllegalTransition(f"工单已处于「{current}」状态。")

    allowed = allowed_from(current, blocked_from)
    if target not in allowed:
        legal = "、".join(sorted(str(status) for status in allowed)) or "无"
        raise IllegalTransition(f"不能从「{current}」流转到「{target}」。可选：{legal}。")

    if target in STATUSES_REQUIRING_REASON and not (reason or "").strip():
        raise ReasonRequired(f"流转到「{target}」必须填写原因。")


def is_terminal(status: TicketStatus) -> bool:
    return not TRANSITIONS[status]
