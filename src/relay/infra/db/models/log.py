"""Logs / knowledge authoring (MT-1 · LOG-4…LOG-9)."""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from relay.domain.enums import LogFormat, ShareLevel
from relay.infra.db.base import Base, TenantScoped, TimestampMixin, UUIDPrimaryKey, tenant_fk


class Log(UUIDPrimaryKey, TenantScoped, TimestampMixin, Base):
    __tablename__ = "log"
    __table_args__ = (
        # MT-4: tenant_id leading.
        Index("ix_log_tenant_id_space_id_updated_at", "tenant_id", "space_id", "updated_at"),
        Index("ix_log_tenant_id_author_id_updated_at", "tenant_id", "author_id", "updated_at"),
        tenant_fk("space_id", "space", ondelete="SET NULL"),
        # RESTRICT, not CASCADE: deleting an account must not silently delete the
        # logs it wrote. R-2 deactivates departing accounts; it does not erase
        # what they knew.
        tenant_fk("author_id", "user", ondelete="RESTRICT"),
        tenant_fk("marked_by", "user", ondelete="SET NULL"),
    )

    space_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    author_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    format: Mapped[LogFormat] = mapped_column(
        Enum(LogFormat, native_enum=False, length=32), nullable=False, default=LogFormat.MARKDOWN
    )
    share_level: Mapped[ShareLevel] = mapped_column(
        Enum(ShareLevel, native_enum=False, length=32), nullable=False, default=ShareLevel.PRIVATE
    )
    current_version: Mapped[int] = mapped_column(nullable=False, default=1)

    # LOG-9 🔒 — field + checkbox only in S1. Counting rule for the acceptance
    # metric: checked AND len(body) >= 300. Worth more the longer RAG slips,
    # because it lets RAG backfill history instead of re-annotating it.
    knowledge_candidate: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    marked_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    marked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class LogVersion(UUIDPrimaryKey, TenantScoped, TimestampMixin, Base):
    """LOG-4. 90-day history. Rollback appends a new version — history is never
    rewritten, and after 90 days scheduled cleanup keeps the latest permanently
    (decided, S-8)."""

    __tablename__ = "log_version"
    __table_args__ = (
        UniqueConstraint("tenant_id", "log_id", "version_no", name="uq_log_version_log_version_no"),
        tenant_fk("log_id", "log"),
        tenant_fk("author_id", "user", ondelete="RESTRICT"),
    )

    log_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    version_no: Mapped[int] = mapped_column(nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    author_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    #: Set when this version was produced by a rollback, naming the source version.
    rolled_back_from: Mapped[int | None] = mapped_column()


class LogShareGrant(UUIDPrimaryKey, TenantScoped, TimestampMixin, Base):
    """L1 named grants. Guests see only these plus L3 — joining a space does not
    grant L2 to a Guest (AC-4)."""

    __tablename__ = "log_share_grant"
    __table_args__ = (
        UniqueConstraint("tenant_id", "log_id", "user_id", name="uq_log_share_grant_log_user"),
        tenant_fk("log_id", "log"),
        tenant_fk("user_id", "user"),
        tenant_fk("granted_by", "user", ondelete="SET NULL"),
    )

    log_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    granted_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)


class LogEditLock(UUIDPrimaryKey, TenantScoped, TimestampMixin, Base):
    """LOG-4, decided S-7: TTL 5 min + heartbeat renewal. On timeout another user
    may take over, and the previous holder's unsaved content is **saved as a
    version, never discarded**."""

    __tablename__ = "log_edit_lock"
    __table_args__ = (
        UniqueConstraint("tenant_id", "log_id", name="uq_log_edit_lock_log"),
        tenant_fk("log_id", "log"),
        tenant_fk("holder_id", "user"),
    )

    log_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    holder_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    acquired_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LogTemplate(UUIDPrimaryKey, TenantScoped, TimestampMixin, Base):
    """LOG-7 — cut candidate #1. The table is cheap; the four seeded templates
    are the cuttable part."""

    __tablename__ = "log_template"
    __table_args__ = (UniqueConstraint("tenant_id", "key", name="uq_log_template_tenant_id_key"),)

    key: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
