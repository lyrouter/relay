"""Notifications (MT-1 · NT-1 / NT-2).

F-1 decided in-app only for S1 — a **scope choice, not a capability limit**. The
multi-channel delivery row and the aggregation window are built now so NT-3
(email) is a switch rather than a rewrite, and so BOT adds a channel rather than
changing domain logic.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import DateTime, Enum, Index, String, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from relay.domain.enums import DeliveryState, NotificationChannel
from relay.infra.db.base import Base, TenantScoped, TimestampMixin, UUIDPrimaryKey, tenant_fk


class Notification(UUIDPrimaryKey, TenantScoped, TimestampMixin, Base):
    __tablename__ = "notification"
    __table_args__ = (
        # "My tickets" + unread count are S1's entire reach surface, so the
        # unread query gets its own index rather than a filtered scan.
        Index(
            "ix_notification_tenant_id_recipient_id_read_at",
            "tenant_id",
            "recipient_id",
            "read_at",
        ),
        tenant_fk("recipient_id", "user"),
    )

    recipient_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    #: assignment | mention | status_change
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    read_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class NotificationDelivery(UUIDPrimaryKey, TenantScoped, TimestampMixin, Base):
    """NT-2. One row per (notification, channel)."""

    __tablename__ = "notification_delivery"
    __table_args__ = (
        Index("ix_notification_delivery_state_scheduled_at", "state", "scheduled_at"),
        tenant_fk("notification_id", "notification"),
        tenant_fk("aggregated_into", "notification", ondelete="SET NULL"),
    )

    notification_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    channel: Mapped[NotificationChannel] = mapped_column(
        Enum(NotificationChannel, native_enum=False, length=32), nullable=False
    )
    state: Mapped[DeliveryState] = mapped_column(
        Enum(DeliveryState, native_enum=False, length=32),
        nullable=False,
        default=DeliveryState.PENDING,
    )
    #: NT-2: the 5-minute aggregation window. A notification folded into an
    #: earlier one points at it here and goes to SUPPRESSED.
    aggregated_into: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    scheduled_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(1000))
