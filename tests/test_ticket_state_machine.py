"""TKT-3 · the state machine, and TKT-9's frozen key/permalink format.

No database. The graph is a decided value (design §7.2 · clarification 2.2) that
ships in API responses, so these tests are written to fail if somebody *widens*
it too: an added edge breaks ``test_the_graph_is_exactly_what_the_design_draws``
and sends its author to the design doc.
"""

from __future__ import annotations

import pytest

from relay.domain.enums import TicketStatus
from relay.domain.tickets import (
    TRANSITIONS,
    IllegalTransition,
    allowed_from,
    check_transition,
    is_terminal,
    permalink,
    ticket_key,
)

from .test_frozen_contract import FROZEN_PERMALINK_TEMPLATE

S = TicketStatus


# ------------------------------------------------------------- the drawn graph


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (S.NEW, S.ASSIGN),
        (S.NEW, S.WORKING),
        (S.ASSIGN, S.WORKING),
        (S.WORKING, S.RESOLVED),
        (S.RESOLVED, S.CLOSED),
        (S.RESOLVED, S.REOPEN),
        (S.REOPEN, S.ASSIGN),
        (S.REOPEN, S.WORKING),
    ],
)
def test_the_declared_edges_are_legal(current, target):
    check_transition(current, target)


def test_closed_is_terminal():
    assert allowed_from(S.CLOSED) == frozenset()
    assert is_terminal(S.CLOSED)
    with pytest.raises(IllegalTransition):
        check_transition(S.CLOSED, S.REOPEN)


def test_a_transition_to_the_current_status_is_refused():
    """Not silently ignored: it would write a history row saying nothing
    happened, and through the API it is usually a client that has lost the rev."""
    with pytest.raises(IllegalTransition):
        check_transition(S.NEW, S.NEW)


def test_a_refusal_names_the_legal_moves():
    """Design §2: a user-facing failure gives the next step."""
    with pytest.raises(IllegalTransition) as refused:
        check_transition(S.NEW, S.RESOLVED)
    assert str(S.ASSIGN) in str(refused.value) or str(S.WORKING) in str(refused.value)


def test_resolved_can_reopen_or_close():
    check_transition(S.RESOLVED, S.REOPEN)
    check_transition(S.RESOLVED, S.CLOSED)
    assert not is_terminal(S.RESOLVED)


def test_reopening_needs_no_reason():
    check_transition(S.RESOLVED, S.REOPEN)


def test_every_status_has_an_entry():
    """Total over the enum: a status missing from the graph would raise KeyError
    from inside a transition rather than be refused cleanly."""
    assert set(TRANSITIONS) == set(TicketStatus)


def test_only_closed_is_terminal():
    assert [status for status in TicketStatus if is_terminal(status)] == [S.CLOSED]


def test_the_graph_is_exactly_what_the_design_draws():
    """The gate. Written out edge by edge, deliberately duplicating
    ``TRANSITIONS`` rather than deriving from it: a test that read the graph it
    is checking would pass through any change to it, which is the vacuous-gate
    problem API-5 calls out about the OpenAPI snapshot.
    """
    assert {status: set(targets) for status, targets in TRANSITIONS.items()} == {
        S.NEW: {S.ASSIGN, S.WORKING},
        S.ASSIGN: {S.WORKING},
        S.WORKING: {S.RESOLVED},
        S.RESOLVED: {S.CLOSED, S.REOPEN},
        S.REOPEN: {S.ASSIGN, S.WORKING},
        S.CLOSED: set(),
    }


# ----------------------------------------------------- TKT-9 · frozen formats


def test_the_ticket_key_is_rl_prefixed():
    assert ticket_key(331) == "RL-331"


def test_the_permalink_carries_the_tenant_segment_from_day_one():
    """S-12. Shipping ``/t/331`` first would make the second tenant a breaking
    change for every stored link, so the segment is not optional.

    Checked against the literal in test_frozen_contract rather than against the
    function's own format string — a test that derived the expectation from the
    code would pass through any change to it.
    """
    assert permalink("https://relay.internal", "gateway", 331) == (
        FROZEN_PERMALINK_TEMPLATE.format(tenant_slug="gateway", number=331)
    )


def test_a_trailing_slash_on_the_base_url_does_not_double():
    assert permalink("https://relay.internal/", "gateway", 7).count("//") == 1
