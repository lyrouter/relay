"""TKT-3 · the ticket state machine (design §7.2 · clarification 2.2).

**Frozen from release.** The status values ship in API responses, webhook
payloads and external systems' stored data, so renaming one is a v2-level change
(§8.6) — and the transition graph is on the same footing the moment
``POST /tickets/{key}/transitions`` exists.

Clarification 2.2 replaced the engineering board statuses with a single graph
shared by investigation tickets and gateway-synced support copies::

    New ──▶ Assign ──▶ Working ──▶ Resolved ──▶ Closed
                              │         │
                              │         └──────▶ Reopen ──▶ Assign | Working
                              └────────────────────────────┘

``closed`` is terminal and irreversible. ``reopen`` is a status, not a required
stop before close: from ``resolved`` you may go to ``closed`` or to ``reopen``.

The platform's ``awaiting`` still has no Relay peer; gateway sync maps it to
``working`` (see ``markdown/platform-support-ticket-gap.md`` §4.3). Tenant
close / reopen windows stay on the gateway; Relay only enforces this graph.
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


#: Statuses that still count as open work on the board / 此刻 feed.
ACTIVE_STATUSES: frozenset[TicketStatus] = frozenset(
    {
        TicketStatus.NEW,
        TicketStatus.ASSIGN,
        TicketStatus.WORKING,
        TicketStatus.REOPEN,
    }
)

#: The declared graph. ``closed`` has no outbound edges — that is the terminal.
TRANSITIONS: dict[TicketStatus, frozenset[TicketStatus]] = {
    TicketStatus.NEW: frozenset({TicketStatus.ASSIGN, TicketStatus.WORKING}),
    TicketStatus.ASSIGN: frozenset({TicketStatus.WORKING}),
    TicketStatus.WORKING: frozenset({TicketStatus.RESOLVED}),
    TicketStatus.RESOLVED: frozenset({TicketStatus.CLOSED, TicketStatus.REOPEN}),
    TicketStatus.REOPEN: frozenset({TicketStatus.ASSIGN, TicketStatus.WORKING}),
    TicketStatus.CLOSED: frozenset(),
}


class IllegalTransition(ValueError):
    """Carries the legal moves, not just the refusal.

    The cross-cutting constraint from design §2: a user-facing failure names the
    next step. "Cannot move to Done" without saying what *is* possible makes the
    caller guess at a graph they cannot see.
    """


class ReasonRequired(ValueError):
    """A target status that requires a written reason was requested without one."""


def allowed_from(current: TicketStatus) -> frozenset[TicketStatus]:
    """Legal next statuses for ``current``."""
    return TRANSITIONS[current]


def check_transition(
    current: TicketStatus,
    target: TicketStatus,
    *,
    reason: str | None = None,
) -> None:
    """Raise unless the move is legal and carries what it needs.

    A no-op transition (``current is target``) is refused rather than ignored: it
    would write a history row saying nothing happened, and through the API it is
    almost always a client that has lost track of the ticket's ``rev``.
    """
    if target is current:
        raise IllegalTransition(f"工单已处于「{current}」状态。")

    allowed = allowed_from(current)
    if target not in allowed:
        legal = "、".join(sorted(str(status) for status in allowed)) or "无"
        raise IllegalTransition(f"不能从「{current}」流转到「{target}」。可选：{legal}。")

    if target in STATUSES_REQUIRING_REASON and not (reason or "").strip():
        raise ReasonRequired(f"流转到「{target}」必须填写原因。")


def is_terminal(status: TicketStatus) -> bool:
    return not TRANSITIONS[status]
