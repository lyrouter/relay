"""Alembic environment.

Migrations run as **relay_owner** (design §2.4, RLS detail 1). That is not a
convenience — the owner is the role that can create and alter tables, and it is
deliberately *not* the role the application uses, so that FORCE ROW LEVEL
SECURITY has something to bind.
"""

from __future__ import annotations

from alembic import context

from relay.config import settings
from relay.infra.db.engine import owner_engine
from relay.infra.db.models import Base

config = context.config
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=settings.owner_dsn,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = owner_engine()
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
