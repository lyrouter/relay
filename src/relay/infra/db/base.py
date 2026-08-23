"""Declarative base and the mixins that make MT-2's lint enforceable.

Every business table inherits ``TenantScoped``. That is not a convenience — the
schema lint (MT-2) reflects ``Base.metadata`` and fails CI for any table lacking
``tenant_id`` or lacking an RLS policy, so a table that skips the mixin has to be
justified in ``schema_lint.toml`` in writing.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import DateTime, ForeignKey, ForeignKeyConstraint, MetaData, Uuid, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampMixin:
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class UUIDPrimaryKey:
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


class TenantScoped:
    """The column the whole model hangs on.

    ``ondelete="CASCADE"`` so removing a tenant cannot leave orphaned rows that
    no policy matches — such rows would be invisible to the app role and only
    ever findable through the BYPASSRLS connection.
    """

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False, index=True
    )


def tenant_fk(
    local_column: str,
    parent_table: str,
    *,
    ondelete: str = "CASCADE",
    name: str | None = None,
) -> ForeignKeyConstraint:
    """A foreign key that cannot cross a tenant boundary.

    RLS does **not** cover referential integrity: PostgreSQL runs foreign-key
    checks with policies bypassed, so a plain ``FOREIGN KEY (user_id)`` lets
    tenant A insert a row referencing tenant B's user. Nothing leaks on read —
    the join finds nothing, because the parent stays invisible — but two real
    things follow: A can plant references into B's graph, and B deleting that
    user cascades into A's rows. A cross-tenant write effect, in other words,
    from a design that only defends reads.

    The fix is to make ``tenant_id`` part of the key: ``(id, tenant_id)`` on the
    parent, ``(child_id, tenant_id)`` on the child. A mismatched pair then has no
    parent row to match and the database refuses the write. Like the §8.4
    columns, this is cheap at create-table time and a migration of every foreign
    key afterwards.

    ``ondelete="SET NULL"`` is rewritten to PostgreSQL 15+'s column-list form.
    The plain form would try to NULL ``tenant_id`` too, which is NOT NULL, and
    the delete would fail at runtime rather than at review time.
    """
    if ondelete.upper() == "SET NULL":
        ondelete = f"SET NULL ({local_column})"
    return ForeignKeyConstraint(
        [local_column, "tenant_id"],
        [f"{parent_table}.id", f"{parent_table}.tenant_id"],
        ondelete=ondelete,
        name=name or f"fk_{local_column}_{parent_table}",
    )
