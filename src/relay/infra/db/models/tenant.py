"""Tenant and tenant residency (MT-1 · AC-9)."""

from __future__ import annotations

from sqlalchemy import Boolean, Enum, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from relay.domain.enums import Role, TenantStatus
from relay.infra.db.base import Base, TenantScoped, TimestampMixin, UUIDPrimaryKey


class Tenant(UUIDPrimaryKey, TimestampMixin, Base):
    """The one table that legitimately has no ``tenant_id`` — it *is* the tenant.

    It still carries an RLS policy, keyed on ``id`` instead (see ``rls.py``), so
    the runtime role cannot enumerate other tenants. The exemption in
    ``schema_lint.toml`` is for the column only, not for the policy.
    """

    __tablename__ = "tenant"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    status: Mapped[TenantStatus] = mapped_column(
        Enum(TenantStatus, native_enum=False, length=32),
        nullable=False,
        default=TenantStatus.ACTIVE,
    )
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Asia/Shanghai")


class TenantEmailDomain(UUIDPrimaryKey, TenantScoped, TimestampMixin, Base):
    """AC-9. Email domain is the only residency credential a self-signup has.

    ``domain`` is globally unique, not unique-per-tenant: the design fixes a
    **one-to-one** domain ↔ tenant mapping, so two tenants claiming the same
    domain must fail at the database, not at a service-layer check somebody can
    forget to call.
    """

    __tablename__ = "tenant_email_domain"
    __table_args__ = (UniqueConstraint("domain", name="uq_tenant_email_domain_domain"),)

    domain: Mapped[str] = mapped_column(String(253), nullable=False)
    default_role: Mapped[Role] = mapped_column(
        Enum(Role, native_enum=False, length=32), nullable=False, default=Role.MEMBER
    )
    auto_join: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
