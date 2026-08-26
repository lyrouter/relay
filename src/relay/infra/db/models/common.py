"""Cross-domain tables: attachments and the audit log (MT-1)."""

from __future__ import annotations

import uuid

from sqlalchemy import BigInteger, Enum, Index, String, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from relay.context import ActorType, Origin
from relay.infra.db.base import Base, TenantScoped, TimestampMixin, UUIDPrimaryKey, tenant_fk


class Attachment(UUIDPrimaryKey, TenantScoped, TimestampMixin, Base):
    """LOG-5. ``blob_key`` **contains ``tenant_id``** by construction.

    The blob store is the one thing RLS does not cover, so isolation here is the
    path prefix plus a permission check followed by a 5-minute signed link
    (decided, S-11). Never "the URL is unguessable".
    """

    __tablename__ = "attachment"
    __table_args__ = (tenant_fk("uploaded_by", "user", ondelete="RESTRICT"),)

    owner_type: Mapped[str] = mapped_column(String(32), nullable=False)  # log | ticket | comment
    owner_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    blob_key: Mapped[str] = mapped_column(String(1024), nullable=False, unique=True)
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    mime: Mapped[str] = mapped_column(String(255), nullable=False)
    #: Null when a service token uploaded: there is no user row to attribute
    #: (S-10). The FK stays so a person-upload cannot point at another tenant.
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    #: LOG-5 virus-scan hook; may be a no-op implementation in S1.
    scan_state: Mapped[str] = mapped_column(String(32), nullable=False, default="skipped")


class AuditLog(UUIDPrimaryKey, TenantScoped, TimestampMixin, Base):
    """Every account, permission, share-level, ticket-status, API-token and
    webhook-config change lands here (design §2 cross-cutting constraints).

    ``SystemRepository`` also writes one row per BYPASSRLS call — that is what
    makes the cross-tenant escape hatch auditable rather than merely documented.
    """

    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_tenant_id_created_at", "tenant_id", "created_at"),
        Index(
            "ix_audit_log_tenant_id_target_type_target_id",
            "tenant_id",
            "target_type",
            "target_id",
        ),
    )

    actor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    actor_type: Mapped[ActorType] = mapped_column(
        Enum(ActorType, native_enum=False, length=32), nullable=False
    )
    origin: Mapped[Origin] = mapped_column(
        Enum(Origin, native_enum=False, length=32), nullable=False
    )
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    target_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_id: Mapped[str | None] = mapped_column(String(128))
    before: Mapped[dict | None] = mapped_column(JSONB)
    after: Mapped[dict | None] = mapped_column(JSONB)
