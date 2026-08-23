"""NT-1 + NT-2 · in-app notifications, aggregation, delivery state machine.

F-1: **in-app only in S1**, and §9 insists the consequence be said out loud —
in-app notification requires people to come to the platform, so the unread count
*is* the reach surface rather than a badge next to one. That is why the unread
query has its own index and why aggregation matters: five status changes on one
ticket inside a minute must not cost five unread items, or the surface people
depend on becomes the surface they learn to ignore.

Three structural choices, each about not needing a rewrite later:

* **The delivery row exists even though in-app has nothing to deliver.** For
  in-app, storing *is* sending, so a delivery goes straight to ``SENT``. The row
  and its state machine are here for NT-3 (email, ~0.5 pd away) and for BOT's
  WeCom channel, which then add a channel instead of changing domain logic.
* **Aggregation is derived, not counted into a column.** The fold count is the
  number of ``SUPPRESSED`` deliveries pointing at the aggregate, so a suppressed
  notification is still a row in the recipient's history — the flooding is
  removed from the *reach* surface without removing the events from the record.
* **:func:`emit` takes the caller's session.** A notification describing a
  status change that rolled back is worse than no notification, and the reverse
  — a committed change nobody was told about — is the silent-back-door failure
  §7.5 is about. One transaction, both facts.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field

from sqlalchemy import func, select

from relay.context import current_context
from relay.domain.enums import DeliveryState, NotificationChannel, NotificationType
from relay.infra.db.models import Notification, NotificationDelivery
from relay.infra.db.session import tenant_session

#: NT-2. Same window BOT will reuse for WeCom, which is why it is a constant
#: rather than a literal at the call site.
AGGREGATION_WINDOW = dt.timedelta(minutes=5)

#: S1's only channel (F-1). Declared as a tuple so NT-3 is an edit here plus a
#: worker, not a rewrite of everything that calls :func:`emit`.
CHANNELS: tuple[NotificationChannel, ...] = (NotificationChannel.INAPP,)


@dataclass(frozen=True, slots=True)
class NotificationEvent:
    """One thing that happened, addressed to one person.

    ``target_type`` / ``target_id`` are the aggregation key as well as the link
    the UI follows: "3 changes to RL-331" only makes sense if the notification
    knows which ticket it is about.
    """

    recipient_id: uuid.UUID
    type: NotificationType
    target_type: str
    target_id: uuid.UUID
    payload: dict = field(default_factory=dict)

    def stored_payload(self) -> dict:
        # target_* live in the payload rather than in columns of their own: the
        # MT-1 entity list is fixed, and a JSONB key costs no migration. The
        # aggregation query indexes on recipient + type first, so the JSONB
        # comparison only ever runs over one person's recent notifications.
        return {**self.payload, "target_type": self.target_type, "target_id": str(self.target_id)}


@dataclass(frozen=True, slots=True)
class InboxItem:
    notification_id: uuid.UUID
    type: NotificationType
    target_type: str
    target_id: uuid.UUID
    payload: dict
    created_at: dt.datetime | None
    read_at: dt.datetime | None
    #: 1 plus however many later notifications folded into this one.
    folded_count: int


def emit(session, event: NotificationEvent, *, now: dt.datetime | None = None) -> uuid.UUID | None:
    """Record a notification on the caller's transaction. Returns its id.

    Returns None when there is nothing to tell anyone: a notification addressed
    to the actor who caused it. People do not need to be told what they just did,
    and an inbox that says otherwise trains them to stop reading it.

    A second event with the same (recipient, type, target) inside the window is
    still stored — the history stays complete — but its delivery is
    ``SUPPRESSED`` and points at the aggregate, so it does not add to the unread
    count.
    """
    now = now or dt.datetime.now(dt.UTC)
    ctx = current_context()
    if ctx.actor_id is not None and event.recipient_id == ctx.actor_id:
        return None

    # Looked up before the insert, so there is no question of the new row
    # becoming its own aggregate.
    aggregate_id = _open_aggregate(session, event, now=now)

    notification = Notification(
        tenant_id=ctx.tenant_id,
        recipient_id=event.recipient_id,
        type=str(event.type),
        payload=event.stored_payload(),
    )
    session.add(notification)
    session.flush()
    for channel in CHANNELS:
        suppressed = aggregate_id is not None
        session.add(
            NotificationDelivery(
                tenant_id=ctx.tenant_id,
                notification_id=notification.id,
                channel=channel,
                # In-app has nothing to hand to anyone: the row *is* the
                # delivery. A channel with a wire behind it will leave this
                # PENDING for a worker to pick up.
                state=DeliveryState.SUPPRESSED if suppressed else DeliveryState.SENT,
                aggregated_into=aggregate_id,
                scheduled_at=now,
                sent_at=None if suppressed else now,
            )
        )
    session.flush()
    return notification.id


def emit_many(
    session, events: list[NotificationEvent], *, now: dt.datetime | None = None
) -> list[uuid.UUID]:
    """Emit in order, dropping the ones that were suppressed outright.

    Order matters: the first event for a (recipient, type, target) becomes the
    aggregate that the rest of the batch folds into.
    """
    emitted = []
    for event in events:
        notification_id = emit(session, event, now=now)
        if notification_id is not None:
            emitted.append(notification_id)
    return emitted


def _open_aggregate(
    session, event: NotificationEvent, *, now: dt.datetime
) -> uuid.UUID | None:
    """The live aggregate for this event's key, if the window is still open.

    Keyed on ``scheduled_at`` rather than ``created_at``: ``created_at`` is a
    server default, so a caller passing an explicit ``now`` — every scheduled
    job, and every test — would be comparing its clock against the database's.
    """
    return session.scalar(
        select(Notification.id)
        .join(NotificationDelivery, NotificationDelivery.notification_id == Notification.id)
        .where(
            Notification.recipient_id == event.recipient_id,
            Notification.type == str(event.type),
            Notification.payload["target_id"].astext == str(event.target_id),
            NotificationDelivery.channel == NotificationChannel.INAPP,
            NotificationDelivery.state != DeliveryState.SUPPRESSED,
            NotificationDelivery.scheduled_at > now - AGGREGATION_WINDOW,
        )
        .order_by(NotificationDelivery.scheduled_at.asc())
        .limit(1)
    )


def unread_count(user_id: uuid.UUID) -> int:
    """S1's whole reach surface, so it counts aggregates rather than events.

    A recipient with one aggregate holding six folded status changes has **one**
    unread item. Counting the folded rows would put the flooding back.
    """
    with tenant_session() as session:
        return int(
            session.scalar(
                select(func.count())
                .select_from(Notification)
                .join(
                    NotificationDelivery,
                    NotificationDelivery.notification_id == Notification.id,
                )
                .where(
                    Notification.recipient_id == user_id,
                    Notification.read_at.is_(None),
                    NotificationDelivery.channel == NotificationChannel.INAPP,
                    NotificationDelivery.state != DeliveryState.SUPPRESSED,
                )
            )
            or 0
        )


def inbox(user_id: uuid.UUID, limit: int = 50, *, unread_only: bool = False) -> list[InboxItem]:
    """Newest first, aggregates only, with the fold count attached."""
    folded = (
        select(
            NotificationDelivery.aggregated_into.label("aggregate_id"),
            func.count().label("folded"),
        )
        .where(NotificationDelivery.aggregated_into.is_not(None))
        .group_by(NotificationDelivery.aggregated_into)
        .subquery()
    )

    with tenant_session() as session:
        query = (
            select(Notification, func.coalesce(folded.c.folded, 0))
            .join(
                NotificationDelivery,
                NotificationDelivery.notification_id == Notification.id,
            )
            .outerjoin(folded, folded.c.aggregate_id == Notification.id)
            .where(
                Notification.recipient_id == user_id,
                NotificationDelivery.channel == NotificationChannel.INAPP,
                NotificationDelivery.state != DeliveryState.SUPPRESSED,
            )
            .order_by(NotificationDelivery.scheduled_at.desc())
            .limit(limit)
        )
        if unread_only:
            query = query.where(Notification.read_at.is_(None))

        return [
            InboxItem(
                notification_id=row.id,
                type=NotificationType(row.type),
                target_type=row.payload.get("target_type", ""),
                target_id=uuid.UUID(row.payload["target_id"]),
                payload=row.payload,
                created_at=row.created_at,
                read_at=row.read_at,
                folded_count=1 + int(count),
            )
            for row, count in session.execute(query).all()
        ]


def mark_read(notification_id: uuid.UUID, *, now: dt.datetime | None = None) -> bool:
    """Mark one aggregate read, and the notifications folded into it with it.

    Reading "3 changes to RL-331" is reading all three. Leaving the folded rows
    unread would make the count disagree with what the person just saw.
    """
    now = now or dt.datetime.now(dt.UTC)
    ctx = current_context()
    with tenant_session() as session:
        notification = session.get(Notification, notification_id)
        # RLS makes another tenant's row absent, and a notification addressed to
        # somebody else must not be markable by whoever holds this session.
        if notification is None or notification.recipient_id != ctx.actor_id:
            return False
        if notification.read_at is None:
            notification.read_at = now
        for folded in session.scalars(
            select(Notification)
            .join(
                NotificationDelivery,
                NotificationDelivery.notification_id == Notification.id,
            )
            .where(
                NotificationDelivery.aggregated_into == notification_id,
                Notification.read_at.is_(None),
            )
        ):
            folded.read_at = now
        session.commit()
        return True


def mark_all_read(user_id: uuid.UUID, *, now: dt.datetime | None = None) -> int:
    now = now or dt.datetime.now(dt.UTC)
    with tenant_session() as session:
        unread = session.scalars(
            select(Notification).where(
                Notification.recipient_id == user_id, Notification.read_at.is_(None)
            )
        ).all()
        for notification in unread:
            notification.read_at = now
        session.commit()
        return len(unread)
