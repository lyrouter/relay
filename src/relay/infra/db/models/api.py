"""Public API surface tables (MT-1 · §4.3 · API-1/API-3/API-4).

§4.3 is the reason these get their own file: an API token and a webhook endpoint
are the two places the tenant boundary reaches past what RLS can see. A token is
a long-lived credential, and a webhook actively pushes tenant data to a URL
outside the tenant.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import (
    DateTime,
    Enum,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from relay.domain.enums import PrincipalType, WebhookDeliveryState, WebhookState
from relay.infra.db.base import Base, TenantScoped, TimestampMixin, UUIDPrimaryKey, tenant_fk


class ApiToken(UUIDPrimaryKey, TenantScoped, TimestampMixin, Base):
    """API-1. Hash-only storage; the plaintext is shown exactly once.

    A token belongs to **one** tenant and the ``TenantContext`` derived from it
    cannot be overridden by the request — a ``tenant_id`` in a body or query is
    a 400, not a hint.
    """

    __tablename__ = "api_token"
    __table_args__ = (
        Index("ix_api_token_tenant_id_revoked_at", "tenant_id", "revoked_at"),
        tenant_fk("principal_user_id", "user"),
        tenant_fk("created_by", "user", ondelete="SET NULL"),
    )

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    principal_type: Mapped[PrincipalType] = mapped_column(
        Enum(PrincipalType, native_enum=False, length=32), nullable=False
    )
    #: NULL for a service principal.
    principal_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    #: Prefix kept in the clear so a leaked token can be identified without it:
    #: ``rly_u_`` personal, ``rly_s_`` service.
    token_prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    scopes: Mapped[list[str]] = mapped_column(ARRAY(String(32)), nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    #: API-1: default 365 days, with a 14-day reminder to the creator.
    expires_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class ApiIdempotencyRecord(UUIDPrimaryKey, TenantScoped, TimestampMixin, Base):
    """API-3. Dedupe by ``(tenant_id, principal_id, key)`` for 24h; a replay
    returns the first result.

    This and ``ticket_external_ref`` are **both** needed: this one defends
    against network retries, that one against upstream replays.
    """

    __tablename__ = "api_idempotency_record"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "principal_id",
            "idempotency_key",
            name="uq_api_idempotency_record_tenant_principal_key",
        ),
        Index("ix_api_idempotency_record_expires_at", "expires_at"),
    )

    principal_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    #: Hash of method + path + body. A same-key-different-body replay is a 422,
    #: not a silent return of an unrelated response.
    request_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    response_status: Mapped[int | None] = mapped_column()
    response_snapshot: Mapped[dict | None] = mapped_column(JSONB)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WebhookEndpoint(UUIDPrimaryKey, TenantScoped, TimestampMixin, Base):
    """API-4. Admin-only, audited. Destinations must reject private, loopback and
    cloud-metadata targets — validating the **resolved IP**, not just the
    hostname, because DNS rebinding otherwise walks straight through (S-13)."""

    __tablename__ = "webhook_endpoint"
    __table_args__ = (tenant_fk("created_by", "user", ondelete="SET NULL"),)

    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    #: Per-endpoint, rotatable. Signature is sha256 HMAC over timestamp + "." + body.
    secret_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    event_types: Mapped[list[str]] = mapped_column(ARRAY(String(64)), nullable=False)
    state: Mapped[WebhookState] = mapped_column(
        Enum(WebhookState, native_enum=False, length=32),
        nullable=False,
        default=WebhookState.ACTIVE,
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)


class WebhookDelivery(UUIDPrimaryKey, TenantScoped, TimestampMixin, Base):
    """API-4. The queue itself: a PG table drained with ``FOR UPDATE SKIP
    LOCKED``. No Redis, no MQ — this is S1's first real queue consumer.

    Delivery is at-least-once and unordered, which is why the payload carries
    ``event_id`` (consumer dedupe) and ``rev`` (drop stale events).
    """

    __tablename__ = "webhook_delivery"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "endpoint_id", "event_id", name="uq_webhook_delivery_endpoint_event"
        ),
        # The claim query: pending rows whose retry time has come, oldest first.
        Index("ix_webhook_delivery_state_next_retry_at", "state", "next_retry_at"),
        tenant_fk("endpoint_id", "webhook_endpoint"),
    )

    endpoint_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    event_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    attempt: Mapped[int] = mapped_column(nullable=False, default=0)
    state: Mapped[WebhookDeliveryState] = mapped_column(
        Enum(WebhookDeliveryState, native_enum=False, length=32),
        nullable=False,
        default=WebhookDeliveryState.PENDING,
    )
    next_retry_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
