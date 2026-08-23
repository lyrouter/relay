"""NT-1 + NT-2 · in-app notifications, the 5-minute window, delivery states.

In-app is S1's *only* reach surface (F-1), so the unread count is not a badge —
it is the whole mechanism by which anyone learns anything happened. That is what
makes the aggregation tests load-bearing rather than cosmetic: a count that goes
to 12 because one ticket moved four times is a count people stop looking at.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from sqlalchemy import select

from relay.app.notifications import (
    AGGREGATION_WINDOW,
    NotificationEvent,
    emit,
    emit_many,
    inbox,
    mark_all_read,
    mark_read,
    unread_count,
)
from relay.context import tenant_scope
from relay.domain.enums import (
    DeliveryState,
    NotificationChannel,
    NotificationType,
    Role,
    UserStatus,
)
from relay.infra.db.models import Notification, NotificationDelivery
from relay.infra.db.session import tenant_session

from .conftest import context_for, requires_db

pytestmark = [requires_db, pytest.mark.db]

NOW = dt.datetime(2026, 8, 24, 10, 0, tzinfo=dt.UTC)


@pytest.fixture
def people(tenant_a, user_factory):
    """Two active users in one tenant: an actor and a recipient."""
    return {
        "actor": user_factory(
            tenant_a, "actor@example.com", role=Role.MEMBER, status=UserStatus.ACTIVE
        ),
        "recipient": user_factory(
            tenant_a, "recipient@example.com", role=Role.MEMBER, status=UserStatus.ACTIVE
        ),
    }


def event(recipient, target, type_=NotificationType.STATUS_CHANGE, **payload):
    return NotificationEvent(
        recipient_id=recipient,
        type=type_,
        target_type="ticket",
        target_id=target,
        payload=payload,
    )


def emit_in(tenant_id, actor_id, events, *, now=NOW):
    with tenant_session(context_for(tenant_id, actor_id)) as session:
        ids = emit_many(session, list(events), now=now)
        session.commit()
        return ids


def deliveries(tenant_id):
    with tenant_session(context_for(tenant_id)) as session:
        return session.scalars(select(NotificationDelivery)).all()


# --------------------------------------------------------------- one event


def test_a_notification_is_stored_and_delivered_in_app(tenant_a, people):
    target = uuid.uuid4()
    emit_in(tenant_a, people["actor"], [event(people["recipient"], target, key="RL-1")])

    rows = deliveries(tenant_a)
    assert len(rows) == 1
    assert rows[0].channel is NotificationChannel.INAPP
    # In-app has nothing to hand to anyone: storing *is* sending.
    assert rows[0].state is DeliveryState.SENT
    assert rows[0].sent_at == NOW

    with tenant_scope(context_for(tenant_a, people["recipient"])):
        assert unread_count(people["recipient"]) == 1
        item = inbox(people["recipient"])[0]
    assert item.target_id == target
    assert item.payload["key"] == "RL-1"
    assert item.folded_count == 1


def test_nobody_is_notified_about_their_own_action(tenant_a, people):
    """An inbox that tells people what they just did trains them to stop
    reading it — and the unread count is the only reach surface S1 has."""
    with tenant_session(context_for(tenant_a, people["actor"])) as session:
        assert emit(session, event(people["actor"], uuid.uuid4()), now=NOW) is None
        session.commit()

    assert deliveries(tenant_a) == []
    with tenant_scope(context_for(tenant_a, people["actor"])):
        assert unread_count(people["actor"]) == 0


def test_a_system_actor_notifies_everyone_including_itself(tenant_a, people):
    """``actor_id`` is None for a system or service-token write, so there is
    nobody to exclude — and the assignee still needs to be told."""
    from relay.context import ActorType, Origin, TenantContext

    ctx = TenantContext(
        tenant_id=tenant_a, actor_id=None, actor_type=ActorType.SYSTEM, origin=Origin.API
    )
    with tenant_session(ctx) as session:
        assert emit(session, event(people["recipient"], uuid.uuid4()), now=NOW) is not None
        session.commit()


# ------------------------------------------------------- the 5-minute window


def test_a_repeat_inside_the_window_folds_instead_of_flooding(tenant_a, people):
    target = uuid.uuid4()
    emit_in(
        tenant_a,
        people["actor"],
        [event(people["recipient"], target, n=i) for i in range(4)],
    )

    rows = deliveries(tenant_a)
    assert len(rows) == 4
    sent = [row for row in rows if row.state is DeliveryState.SENT]
    suppressed = [row for row in rows if row.state is DeliveryState.SUPPRESSED]
    assert len(sent) == 1 and len(suppressed) == 3
    # Every suppressed row points at the one aggregate.
    assert {row.aggregated_into for row in suppressed} == {sent[0].notification_id}

    with tenant_scope(context_for(tenant_a, people["recipient"])):
        # One ticket moving four times costs one unread item, not four.
        assert unread_count(people["recipient"]) == 1
        items = inbox(people["recipient"])
    assert len(items) == 1
    assert items[0].folded_count == 4


def test_the_suppressed_events_are_still_in_the_record(tenant_a, people):
    """Aggregation removes the flooding from the *reach* surface without
    removing the events from history — the rows are there to be read."""
    target = uuid.uuid4()
    emit_in(tenant_a, people["actor"], [event(people["recipient"], target, n=i) for i in range(3)])
    with tenant_session(context_for(tenant_a)) as session:
        assert len(session.scalars(select(Notification)).all()) == 3


def test_a_different_target_is_a_separate_aggregate(tenant_a, people):
    emit_in(
        tenant_a,
        people["actor"],
        [event(people["recipient"], uuid.uuid4()), event(people["recipient"], uuid.uuid4())],
    )
    with tenant_scope(context_for(tenant_a, people["recipient"])):
        assert unread_count(people["recipient"]) == 2


def test_a_different_type_on_the_same_target_is_a_separate_aggregate(tenant_a, people):
    """Being assigned a ticket and that ticket changing status are two different
    things to be told, even inside one window."""
    target = uuid.uuid4()
    emit_in(
        tenant_a,
        people["actor"],
        [
            event(people["recipient"], target, NotificationType.ASSIGNMENT),
            event(people["recipient"], target, NotificationType.STATUS_CHANGE),
        ],
    )
    with tenant_scope(context_for(tenant_a, people["recipient"])):
        assert unread_count(people["recipient"]) == 2


def test_a_different_recipient_gets_their_own_aggregate(tenant_a, people, user_factory):
    third = user_factory(tenant_a, "third@example.com", status=UserStatus.ACTIVE)
    target = uuid.uuid4()
    emit_in(
        tenant_a,
        people["actor"],
        [event(people["recipient"], target), event(third, target)],
    )
    with tenant_scope(context_for(tenant_a, people["recipient"])):
        assert unread_count(people["recipient"]) == 1
    with tenant_scope(context_for(tenant_a, third)):
        assert unread_count(third) == 1


def test_once_the_window_has_passed_a_new_aggregate_opens(tenant_a, people):
    target = uuid.uuid4()
    emit_in(tenant_a, people["actor"], [event(people["recipient"], target)], now=NOW)
    later = NOW + AGGREGATION_WINDOW + dt.timedelta(seconds=1)
    emit_in(tenant_a, people["actor"], [event(people["recipient"], target)], now=later)

    with tenant_scope(context_for(tenant_a, people["recipient"])):
        assert unread_count(people["recipient"]) == 2


def test_the_edge_of_the_window_still_folds(tenant_a, people):
    target = uuid.uuid4()
    emit_in(tenant_a, people["actor"], [event(people["recipient"], target)], now=NOW)
    emit_in(
        tenant_a,
        people["actor"],
        [event(people["recipient"], target)],
        now=NOW + AGGREGATION_WINDOW - dt.timedelta(seconds=1),
    )
    with tenant_scope(context_for(tenant_a, people["recipient"])):
        assert unread_count(people["recipient"]) == 1


def test_a_suppressed_notification_does_not_become_the_next_aggregate(tenant_a, people):
    """Three events over eleven minutes: fold, then a fresh aggregate, then fold
    into *that* one — never into the suppressed row in between."""
    target = uuid.uuid4()
    for offset in (0, 1, 6, 7):
        emit_in(
            tenant_a,
            people["actor"],
            [event(people["recipient"], target)],
            now=NOW + dt.timedelta(minutes=offset),
        )
    rows = deliveries(tenant_a)
    aggregates = [row for row in rows if row.state is DeliveryState.SENT]
    assert len(aggregates) == 2
    for row in rows:
        if row.aggregated_into is not None:
            assert row.aggregated_into in {one.notification_id for one in aggregates}


# ------------------------------------------------------------------- reading


def test_reading_an_aggregate_reads_what_folded_into_it(tenant_a, people):
    """"3 changes to RL-331" *is* the three changes. Leaving the folded rows
    unread would make the count disagree with what the person just saw."""
    target = uuid.uuid4()
    ids = emit_in(
        tenant_a, people["actor"], [event(people["recipient"], target) for _ in range(3)]
    )
    with tenant_scope(context_for(tenant_a, people["recipient"])):
        assert mark_read(ids[0]) is True
        assert unread_count(people["recipient"]) == 0

    with tenant_session(context_for(tenant_a)) as session:
        assert all(row.read_at is not None for row in session.scalars(select(Notification)))


def test_a_notification_addressed_to_somebody_else_cannot_be_marked_read(tenant_a, people):
    ids = emit_in(tenant_a, people["actor"], [event(people["recipient"], uuid.uuid4())])
    # The actor holds a perfectly good session in the right tenant — and still
    # cannot touch a notification that is not theirs.
    with tenant_scope(context_for(tenant_a, people["actor"])):
        assert mark_read(ids[0]) is False
    with tenant_scope(context_for(tenant_a, people["recipient"])):
        assert unread_count(people["recipient"]) == 1


def test_mark_all_read_clears_the_surface(tenant_a, people):
    emit_in(
        tenant_a,
        people["actor"],
        [event(people["recipient"], uuid.uuid4()) for _ in range(3)],
    )
    with tenant_scope(context_for(tenant_a, people["recipient"])):
        assert mark_all_read(people["recipient"]) == 3
        assert unread_count(people["recipient"]) == 0


def test_unread_only_filters_the_inbox(tenant_a, people):
    ids = emit_in(
        tenant_a,
        people["actor"],
        [event(people["recipient"], uuid.uuid4()) for _ in range(2)],
    )
    with tenant_scope(context_for(tenant_a, people["recipient"])):
        mark_read(ids[0])
        assert len(inbox(people["recipient"])) == 2
        assert len(inbox(people["recipient"], unread_only=True)) == 1


def test_the_inbox_is_newest_first(tenant_a, people):
    first = uuid.uuid4()
    second = uuid.uuid4()
    emit_in(tenant_a, people["actor"], [event(people["recipient"], first)], now=NOW)
    emit_in(
        tenant_a,
        people["actor"],
        [event(people["recipient"], second)],
        now=NOW + dt.timedelta(minutes=10),
    )
    with tenant_scope(context_for(tenant_a, people["recipient"])):
        assert [item.target_id for item in inbox(people["recipient"])] == [second, first]


# --------------------------------------------------------------- isolation


def test_another_tenants_notifications_are_invisible(tenant_a, tenant_b, user_factory):
    """Not a notification-layer check — RLS. Worth pinning anyway: the inbox
    query is the one a Phase 2 refactor is most likely to "optimise" into a
    join that loses the policy."""
    mine = user_factory(tenant_a, "mine@example.com", status=UserStatus.ACTIVE)
    theirs = user_factory(tenant_b, "theirs@example.com", status=UserStatus.ACTIVE)
    actor_b = user_factory(tenant_b, "actor@example.com", status=UserStatus.ACTIVE)

    emit_in(tenant_b, actor_b, [event(theirs, uuid.uuid4())])

    with tenant_scope(context_for(tenant_a, mine)):
        assert unread_count(mine) == 0
        assert inbox(mine) == []
