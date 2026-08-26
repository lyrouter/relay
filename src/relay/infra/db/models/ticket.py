"""Tickets and board (MT-1 · TKT-1…TKT-9 · §8.4).

Three of the columns here exist because of §8.4 — ``rev``, ``actor_type`` /
``origin`` on the status history, and ``ticket_external_ref``. They are cheapest
at create-table time and expensive forever after, and they are un-cuttable.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import (
    Boolean,
    Enum,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from relay.context import ActorType, Origin
from relay.domain.enums import (
    AiContextFieldType,
    Priority,
    SupportCategory,
    TicketStatus,
    TicketType,
)
from relay.infra.db.base import Base, TenantScoped, TimestampMixin, UUIDPrimaryKey, tenant_fk


class Iteration(UUIDPrimaryKey, TenantScoped, TimestampMixin, Base):
    __tablename__ = "iteration"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_iteration_tenant_id_name"),)

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    starts_on: Mapped[dt.date | None] = mapped_column()
    ends_on: Mapped[dt.date | None] = mapped_column()
    closed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class Label(UUIDPrimaryKey, TenantScoped, TimestampMixin, Base):
    __tablename__ = "label"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_label_tenant_id_name"),)

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    color: Mapped[str] = mapped_column(String(16), nullable=False, default="#6b7280")


class Ticket(UUIDPrimaryKey, TenantScoped, TimestampMixin, Base):
    __tablename__ = "ticket"
    __table_args__ = (
        # MT-4, all tenant_id-leading.
        UniqueConstraint("tenant_id", "number", name="uq_ticket_tenant_id_number"),
        Index("ix_ticket_tenant_id_status_updated_at", "tenant_id", "status", "updated_at"),
        Index("ix_ticket_tenant_id_assignee_id_status", "tenant_id", "assignee_id", "status"),
        Index("ix_ticket_tenant_id_updated_at", "tenant_id", "updated_at"),
        Index("ix_ticket_tenant_id_category", "tenant_id", "category"),
        tenant_fk("assignee_id", "user", ondelete="SET NULL"),
        tenant_fk("reporter_id", "user", ondelete="SET NULL"),
        tenant_fk("iteration_id", "iteration", ondelete="SET NULL"),
    )

    #: TKT-9: increments **per tenant**, rendered as ``RL-<number>``.
    number: Mapped[int] = mapped_column(nullable=False)
    type: Mapped[TicketType] = mapped_column(
        Enum(TicketType, native_enum=False, length=32), nullable=False
    )
    #: Gateway support-ticket category. Null on tickets that did not come from
    #: that surface. The engineering ``type`` stays bug/feature/task (S-22).
    category: Mapped[SupportCategory | None] = mapped_column(
        Enum(SupportCategory, native_enum=False, length=32)
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[TicketStatus] = mapped_column(
        Enum(TicketStatus, native_enum=False, length=32), nullable=False, default=TicketStatus.TODO
    )
    priority: Mapped[Priority] = mapped_column(
        Enum(Priority, native_enum=False, length=32), nullable=False, default=Priority.P2
    )
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    #: For a service-token create this is the machine principal, not a person —
    #: which is precisely why INT-8 excludes service principals from people-metrics.
    reporter_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    iteration_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    #: TKT-8: a plain link in S1. No status write-back, no CI/review state.
    pr_url: Mapped[str | None] = mapped_column(String(1024))

    #: TKT-2. Validated by Pydantic against ``ai_context_field_config`` on write —
    #: **not** arbitrary JSON, because the public API can now write it.
    ai_context: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    #: §8.4. Monotonic per ticket. ``PATCH`` carries ``If-Match: <rev>``; a
    #: mismatch is a 409 with the current value. Also rides in the webhook payload
    #: so consumers can drop out-of-order events.
    rev: Mapped[int] = mapped_column(nullable=False, default=1)

    #: §8.8 / API-6. **Not** ``reporter``: gateway users are not Relay accounts.
    #: Display and traceability only — no permission effect, excluded from every
    #: people-metric. Shape: {"name": str, "email": str?, "external_id": str?}.
    submitter: Mapped[dict | None] = mapped_column(JSONB)
    #: Fixed label naming the surface a ticket came in through, e.g. "gateway-webui".
    source: Mapped[str | None] = mapped_column(String(64))

    actor_type: Mapped[ActorType] = mapped_column(
        Enum(ActorType, native_enum=False, length=32), nullable=False, default=ActorType.USER
    )
    origin: Mapped[Origin] = mapped_column(
        Enum(Origin, native_enum=False, length=32), nullable=False, default=Origin.WEB
    )


class TicketExternalRef(UUIDPrimaryKey, TenantScoped, TimestampMixin, Base):
    """§8.4. Turns "has this external record already been filed?" from a
    heuristic into a database fact. Alert replays, CI re-runs and webhook
    redeliveries stop creating duplicates."""

    __tablename__ = "ticket_external_ref"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "system", "external_id", name="uq_ticket_external_ref_tenant_sys_ext"
        ),
        tenant_fk("ticket_id", "ticket"),
    )

    ticket_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    system: Mapped[str] = mapped_column(String(64), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    external_url: Mapped[str | None] = mapped_column(String(1024))


class TicketComment(UUIDPrimaryKey, TenantScoped, TimestampMixin, Base):
    """TKT-4. Comments made through the API raise notifications too — otherwise
    the API is a silent back door for editing tickets."""

    __tablename__ = "ticket_comment"
    __table_args__ = (
        Index(
            "ix_ticket_comment_tenant_id_ticket_id_created_at",
            "tenant_id",
            "ticket_id",
            "created_at",
        ),
        tenant_fk("ticket_id", "ticket"),
        tenant_fk("author_id", "user", ondelete="SET NULL"),
    )

    ticket_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    author_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    actor_type: Mapped[ActorType] = mapped_column(
        Enum(ActorType, native_enum=False, length=32), nullable=False, default=ActorType.USER
    )
    origin: Mapped[Origin] = mapped_column(
        Enum(Origin, native_enum=False, length=32), nullable=False, default=Origin.WEB
    )


class TicketLabel(UUIDPrimaryKey, TenantScoped, TimestampMixin, Base):
    __tablename__ = "ticket_label"
    __table_args__ = (
        UniqueConstraint("tenant_id", "ticket_id", "label_id", name="uq_ticket_label_ticket_label"),
        tenant_fk("ticket_id", "ticket"),
        tenant_fk("label_id", "label"),
    )

    ticket_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    label_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)


class TicketStatusHistory(UUIDPrimaryKey, TenantScoped, TimestampMixin, Base):
    """TKT-3 · §8.4. ``actor_type`` is where Phase 2's GH loop guard lands.

    Without it, existing rows cannot be told apart by origin after the fact and
    the first line of the three-way loop defence is guesswork.
    """

    __tablename__ = "ticket_status_history"
    __table_args__ = (
        Index(
            "ix_ticket_status_history_tenant_id_ticket_id_created_at",
            "tenant_id",
            "ticket_id",
            "created_at",
        ),
        tenant_fk("ticket_id", "ticket"),
    )

    ticket_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    from_status: Mapped[TicketStatus | None] = mapped_column(
        Enum(TicketStatus, native_enum=False, length=32)
    )
    to_status: Mapped[TicketStatus] = mapped_column(
        Enum(TicketStatus, native_enum=False, length=32), nullable=False
    )
    actor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    actor_type: Mapped[ActorType] = mapped_column(
        Enum(ActorType, native_enum=False, length=32), nullable=False
    )
    origin: Mapped[Origin] = mapped_column(
        Enum(Origin, native_enum=False, length=32), nullable=False
    )
    #: Required for Blocked / Won't Fix (TKT-3).
    reason: Mapped[str | None] = mapped_column(Text)


class AiContextFieldConfig(UUIDPrimaryKey, TenantScoped, TimestampMixin, Base):
    """TKT-2. The visibility config that ``ai_context`` writes are validated
    against.

    ``domain_scope`` is the gate for fields that only make sense to a team that
    runs its own gateway. The test before promoting a field to the generic set:
    **could a team with no gateway of its own fill it in?**
    """

    __tablename__ = "ai_context_field_config"
    __table_args__ = (
        UniqueConstraint("tenant_id", "field_key", name="uq_ai_context_field_config_tenant_key"),
    )

    field_key: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    type: Mapped[AiContextFieldType] = mapped_column(
        Enum(AiContextFieldType, native_enum=False, length=32), nullable=False
    )
    visible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: NULL = generic field, on by default for every tenant.
    domain_scope: Mapped[str | None] = mapped_column(String(64))
