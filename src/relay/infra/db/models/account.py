"""Accounts, identity, spaces (MT-1 · AC-1…AC-7)."""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import DateTime, Enum, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from relay.domain.enums import IdentityProvider, Role, SpaceRole, UserStatus
from relay.infra.db.base import Base, TenantScoped, TimestampMixin, UUIDPrimaryKey, tenant_fk


class User(UUIDPrimaryKey, TenantScoped, TimestampMixin, Base):
    __tablename__ = "user"
    __table_args__ = (
        # Email is unique per tenant, not globally: the same address must be able
        # to belong to two tenants once there is more than one.
        UniqueConstraint("tenant_id", "email", name="uq_user_tenant_id_email"),
    )

    email: Mapped[str] = mapped_column(String(320), nullable=False)
    email_verified_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[UserStatus] = mapped_column(
        Enum(UserStatus, native_enum=False, length=32), nullable=False, default=UserStatus.PENDING
    )
    role: Mapped[Role] = mapped_column(
        Enum(Role, native_enum=False, length=32), nullable=False, default=Role.MEMBER
    )
    display_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    totp_secret: Mapped[str | None] = mapped_column(String(255))
    last_login_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    failed_login_count: Mapped[int] = mapped_column(nullable=False, default=0)
    locked_until: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    password_changed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class EmailVerification(UUIDPrimaryKey, TenantScoped, TimestampMixin, Base):
    """AC-1. Mandatory: the email domain is the only residency credential, so an
    unverified self-signup would let anyone in with a fake same-domain address.

    Only the hash is stored, and ``consumed_at`` makes the token single-use.
    """

    __tablename__ = "email_verification"
    __table_args__ = (tenant_fk("user_id", "user"),)

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class Invitation(UUIDPrimaryKey, TenantScoped, TimestampMixin, Base):
    """The secondary path once signup is self-service: exceptions only."""

    __tablename__ = "invitation"
    __table_args__ = (tenant_fk("invited_by", "user", ondelete="SET NULL"),)

    email: Mapped[str] = mapped_column(String(320), nullable=False)
    role: Mapped[Role] = mapped_column(
        Enum(Role, native_enum=False, length=32), nullable=False, default=Role.MEMBER
    )
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    invited_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class IdentityBinding(UUIDPrimaryKey, TenantScoped, TimestampMixin, Base):
    """AC-6 / AC-7 · **created but never written in S1.**

    Known and accepted rework (TODO-S1.md): with BOT deferred, the WeCom userid
    spike is deferred too, so this table is designed blind. Expect one ALTER when
    BOT starts. It exists now so the uniqueness constraint and the FK shape are
    settled, not to be used.
    """

    __tablename__ = "identity_binding"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "provider", "external_id", name="uq_identity_binding_tenant_provider_ext"
        ),
        UniqueConstraint("tenant_id", "user_id", "provider", name="uq_identity_binding_user_prov"),
        tenant_fk("user_id", "user"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    provider: Mapped[IdentityProvider] = mapped_column(
        Enum(IdentityProvider, native_enum=False, length=32), nullable=False
    )
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    external_id_kind: Mapped[str | None] = mapped_column(String(64))
    external_name: Mapped[str | None] = mapped_column(String(255))
    bound_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class Space(UUIDPrimaryKey, TenantScoped, TimestampMixin, Base):
    """AC-5. Single level, no nesting. Membership defines the L2 sharing scope."""

    __tablename__ = "space"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_space_tenant_id_name"),)

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(String(1000), nullable=False, default="")


class SpaceMember(UUIDPrimaryKey, TenantScoped, TimestampMixin, Base):
    __tablename__ = "space_member"
    __table_args__ = (
        UniqueConstraint("tenant_id", "space_id", "user_id", name="uq_space_member_space_user"),
        tenant_fk("space_id", "space"),
        tenant_fk("user_id", "user"),
    )

    space_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    space_role: Mapped[SpaceRole] = mapped_column(
        Enum(SpaceRole, native_enum=False, length=32), nullable=False, default=SpaceRole.MEMBER
    )
