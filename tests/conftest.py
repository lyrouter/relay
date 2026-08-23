"""Test fixtures.

Everything runs against a real PostgreSQL. That is not incidental: the property
under test in MT-3 and MT-6 is enforced *by the database*, so an in-memory or
SQLite substitute would test nothing that matters. A suite that cannot reach a
cluster skips rather than passes — a green run with the RLS tests silently
skipped is the worst possible outcome, so ``test_ci_gates.py`` asserts they ran.
"""

from __future__ import annotations

import os
import uuid

# Point the whole process at the test database before relay.config is imported.
os.environ.setdefault("RELAY_PG_DATABASE", "relay_test")

import pytest  # noqa: E402
from sqlalchemy import text  # noqa: E402

from relay.context import ActorType, Origin, TenantContext  # noqa: E402
from relay.domain.enums import Role, UserStatus  # noqa: E402
from relay.infra.db.engine import app_engine, owner_engine, system_engine  # noqa: E402
from relay.infra.db.models import Base, User  # noqa: E402
from relay.infra.db.session import tenant_session  # noqa: E402


def _cluster_reachable() -> bool:
    try:
        with owner_engine().connect() as conn:
            conn.execute(text("select 1"))
        return True
    except Exception:
        return False


CLUSTER_AVAILABLE = _cluster_reachable()

requires_db = pytest.mark.skipif(
    not CLUSTER_AVAILABLE,
    reason="no PostgreSQL cluster reachable; see scripts/bootstrap_db.sql",
)


@pytest.fixture(scope="session", autouse=True)
def migrated_database():
    """Bring the test database to head once per session, as the owner role."""
    if not CLUSTER_AVAILABLE:
        yield
        return

    from alembic import command
    from alembic.config import Config

    cfg = Config("alembic.ini")
    command.upgrade(cfg, "head")
    yield
    app_engine().dispose()
    system_engine().dispose()
    owner_engine().dispose()


@pytest.fixture(autouse=True)
def clean_tables(migrated_database):
    """Truncate between tests, as the owner (RLS would otherwise hide rows).

    Ordering by dependency is unnecessary: one TRUNCATE ... CASCADE over every
    table is both faster and immune to a future FK we forget about here.
    """
    if not CLUSTER_AVAILABLE:
        yield
        return
    yield
    names = ", ".join(f'"{t}"' for t in Base.metadata.tables)
    with owner_engine().begin() as conn:
        conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))


def _make_tenant(slug: str) -> uuid.UUID:
    """Created through the BYPASSRLS role, and that is the real bootstrap path.

    FORCE ROW LEVEL SECURITY binds the *owner* as well, so ``relay_owner`` cannot
    insert a tenant either — which is correct, and worth knowing before writing a
    data migration: creating the first tenant is an AC-9 deploy-time operation
    running as ``relay_system``, not something a migration does on the side.
    """
    tenant_id = uuid.uuid4()
    with system_engine().begin() as conn:
        conn.execute(
            text(
                "INSERT INTO tenant (id, name, slug, status, timezone, created_at, updated_at) "
                "VALUES (:id, :name, :slug, 'ACTIVE', 'Asia/Shanghai', now(), now())"
            ),
            {"id": tenant_id, "name": slug.title(), "slug": slug},
        )
    return tenant_id


@pytest.fixture
def tenant_a() -> uuid.UUID:
    return _make_tenant("alpha")


@pytest.fixture
def tenant_b() -> uuid.UUID:
    return _make_tenant("bravo")


def context_for(tenant_id: uuid.UUID, actor_id: uuid.UUID | None = None) -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id,
        actor_id=actor_id,
        actor_type=ActorType.USER,
        origin=Origin.WEB,
    )


@pytest.fixture
def user_factory():
    """Create a user inside a tenant, through the app role and its policies.

    ``status`` defaults to the model default (PENDING) so that the MT-era tests
    that only need *a row* keep their meaning. Anything exercising a use case
    wants ACTIVE, because ``actor_principal`` refuses a non-active actor.
    """

    def make(
        tenant_id: uuid.UUID,
        email: str = "someone@example.com",
        *,
        role: Role = Role.MEMBER,
        status: UserStatus = UserStatus.PENDING,
    ) -> uuid.UUID:
        user = User(
            tenant_id=tenant_id,
            email=email,
            password_hash="x",
            display_name=email.split("@")[0],
            role=role,
            status=status,
        )
        with tenant_session(context_for(tenant_id)) as session:
            session.add(user)
            session.commit()
            return user.id

    return make
