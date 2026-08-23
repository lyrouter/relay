"""TKT-3 · the state machine, and TKT-9's frozen key/permalink format.

No database. The graph is a decided value (design §7.2) that ships in API
responses, so these tests are written to fail if somebody *widens* it too —
including the two edges the module deliberately does not have. An added edge
should break a test and send its author to the design doc, which is the whole
mechanism TODO-S1's "change the design doc first" rule relies on.
"""

from __future__ import annotations

import pytest

from relay.domain.enums import TicketStatus
from relay.domain.tickets import (
    TRANSITIONS,
    IllegalTransition,
    ReasonRequired,
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
        (S.TODO, S.IN_PROGRESS),
        (S.IN_PROGRESS, S.IN_REVIEW),
        (S.IN_REVIEW, S.DONE),
        (S.TODO, S.WONT_FIX),
        (S.IN_PROGRESS, S.WONT_FIX),
        (S.IN_REVIEW, S.WONT_FIX),
        (S.BLOCKED, S.WONT_FIX),
        (S.WONT_FIX, S.TODO),
    ],
)
def test_the_declared_edges_are_legal(current, target):
    check_transition(current, target, reason="因为" if target is S.WONT_FIX else None)


@pytest.mark.parametrize("current", [S.TODO, S.IN_PROGRESS, S.IN_REVIEW])
def test_blocked_is_enterable_from_any_active_state(current):
    check_transition(current, S.BLOCKED, reason="等第三方接口")


@pytest.mark.parametrize("current", [S.TODO, S.IN_PROGRESS, S.IN_REVIEW])
def test_blocked_resumes_to_where_it_came_from(current):
    """§7.2: "恢复回原状态". The resume target comes from the history row that
    entered Blocked, so no ``blocked_from`` column can disagree with it."""
    check_transition(S.BLOCKED, current, blocked_from=current)


def test_blocked_does_not_resume_to_somewhere_it_never_was():
    with pytest.raises(IllegalTransition):
        check_transition(S.BLOCKED, S.DONE, blocked_from=S.IN_PROGRESS)


def test_a_blocked_ticket_with_no_history_can_still_be_abandoned():
    """A ticket Blocked by a data import, or whose history was trimmed at 90
    days, must not become unmovable."""
    assert allowed_from(S.BLOCKED, None) == frozenset({S.WONT_FIX})
    check_transition(S.BLOCKED, S.WONT_FIX, reason="不再需要")


# --------------------------------------------------------- reasons and no-ops


@pytest.mark.parametrize("target", [S.BLOCKED, S.WONT_FIX])
def test_blocked_and_wont_fix_require_a_reason(target):
    with pytest.raises(ReasonRequired):
        check_transition(S.TODO, target)
    with pytest.raises(ReasonRequired):
        check_transition(S.TODO, target, reason="   ")
    check_transition(S.TODO, target, reason="有原因")


def test_a_transition_to_the_current_status_is_refused():
    """Not silently ignored: it would write a history row saying nothing
    happened, and through the API it is usually a client that has lost the rev."""
    with pytest.raises(IllegalTransition):
        check_transition(S.TODO, S.TODO)


def test_a_refusal_names_the_legal_moves():
    """Design §2: a user-facing failure gives the next step. "Cannot move to
    Done" without naming what *is* possible makes the caller guess at a graph
    they cannot see."""
    with pytest.raises(IllegalTransition) as refused:
        check_transition(S.TODO, S.DONE)
    assert str(S.IN_PROGRESS) in str(refused.value)


# ------------------------------------------- the two gaps, pinned as they are


def test_done_is_terminal_in_s1():
    """§7.2 gives Won't Fix an explicit reopen and says nothing about Done, and
    the "Reopened" state is deferred.

    This is a **known gap**, not a preference: without a reopen edge, a ticket
    that turns out not to be fixed becomes a duplicate, which is what INT-8's
    counts cannot see through. Adding ``Done → Todo`` should break this test and
    send its author to design §7.2 first — the graph is visible in the public
    API, so widening it quietly is a contract change nobody reviewed.
    """
    assert is_terminal(S.DONE)
    with pytest.raises(IllegalTransition):
        check_transition(S.DONE, S.TODO)


def test_a_review_cannot_send_work_back_yet():
    """The other known gap. Expressing "review rejected" as Blocked would be
    wrong — blocked means waiting on something else, not sent back. Same rule as
    above: widen it in the design doc, not here."""
    with pytest.raises(IllegalTransition):
        check_transition(S.IN_REVIEW, S.IN_PROGRESS)


def test_every_status_has_an_entry():
    """Total over the enum: a status missing from the graph would raise KeyError
    from inside a transition rather than be refused cleanly."""
    assert set(TRANSITIONS) == set(TicketStatus)


def test_only_done_is_terminal():
    terminal = {status for status in TicketStatus if is_terminal(status)}
    assert terminal == {S.DONE}


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
