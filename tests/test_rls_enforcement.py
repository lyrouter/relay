"""MT-3 · the three implementation details, each tested for the failure it causes.

Design §2.4 is emphatic that RLS is easy to configure into something that looks
right and enforces nothing. These tests pin the three details individually, so a
regression names which one broke rather than just "isolation is off".
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from relay.context import MissingTenantContext, TenantContext
from relay.infra.db.engine import app_engine, owner_engine
from relay.infra.db.models import Tenant, User
from relay.infra.db.session import TenantContextSwitchError, tenant_session

from .conftest import context_for, requires_db

pytestmark = [requires_db, pytest.mark.db]


def test_app_role_is_not_the_table_owner():
    """Detail 1, half one. If the runtime role owned the tables, FORCE would be
    the only thing standing between us and a full-database read — and one
    ``ALTER TABLE ... NO FORCE`` would remove it silently."""
    with owner_engine().connect() as conn:
        owners = conn.execute(
            text(
                "SELECT DISTINCT tableowner FROM pg_tables "
                "WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
            )
        ).scalars().all()
    assert owners == ["relay_owner"]

    with app_engine().connect() as conn:
        assert conn.execute(text("SELECT current_user")).scalar() == "relay_app"


def test_app_role_cannot_bypass_rls():
    """Detail 1, half two. BYPASSRLS on the runtime role would make every policy
    decorative."""
    with owner_engine().connect() as conn:
        bypass = conn.execute(
            text("SELECT rolbypassrls FROM pg_roles WHERE rolname = 'relay_app'")
        ).scalar()
    assert bypass is False


def test_tenant_scoping_is_transaction_local_not_session_local(tenant_a):
    """Detail 2 — the one most likely to be got wrong.

    A session-level ``SET`` survives the connection returning to the pool, and
    the next request on that connection runs as the previous tenant. Here we
    check the value is gone the moment the transaction ends.
    """
    engine = app_engine()
    with engine.connect() as conn:
        conn.execute(
            text("SELECT set_config('app.tenant_id', :v, true)"), {"v": str(tenant_a)}
        )
        assert conn.execute(text("SELECT current_setting('app.tenant_id')")).scalar() == str(
            tenant_a
        )
        conn.commit()
        # Same physical connection, new transaction: the tenant must be gone.
        leaked = conn.execute(text("SELECT current_setting('app.tenant_id')")).scalar()
        assert leaked != str(
            tenant_a
        ), "tenant id survived the transaction — SET LOCAL is not local"


def test_missing_context_raises_instead_of_returning_zero_rows(tenant_a, user_factory):
    """Detail 3, at the session layer.

    The cross-cutting constraint promises an exception. Returning an empty list
    would be the same bug with a friendlier face: every investigation would start
    from "why is the list empty" instead of "no tenant was established".
    """
    user_factory(tenant_a, "a@alpha.test")
    from relay.infra.db.session import TenantSessionFactory

    with pytest.raises(MissingTenantContext):
        with TenantSessionFactory(bind=app_engine()) as session:
            session.query(User).all()


@pytest.mark.parametrize("guc_state", ["never_set", "set_then_released"])
def test_raw_sql_with_no_tenant_fails_closed(tenant_a, user_factory, guc_state):
    """Detail 3, at the database layer — and the reason raw SQL needs no lint.

    Two shapes, because they raise different errors and both must fail:
    ``never_set`` hits "unrecognized configuration parameter"; after any
    transaction has set the GUC, PostgreSQL keeps the placeholder and resets it
    to the empty string, which fails on the ``::uuid`` cast instead.
    """
    user_factory(tenant_a, "a@alpha.test")
    engine = app_engine()
    with engine.connect() as conn:
        if guc_state == "set_then_released":
            conn.execute(
                text("SELECT set_config('app.tenant_id', :v, true)"), {"v": str(tenant_a)}
            )
            conn.commit()
        with pytest.raises(Exception) as excinfo:
            conn.execute(text('SELECT * FROM "user"')).all()
        message = str(excinfo.value).lower()
        assert "app.tenant_id" in message or "uuid" in message


def test_session_refuses_to_straddle_two_tenants(tenant_a, tenant_b):
    """Not in the design doc, but the same class of bug as detail 2.

    Rebinding a live session to a second tenant would leave objects from tenant A
    in the identity map while the database answers as tenant B.
    """
    from relay.context import tenant_scope
    from relay.infra.db.session import TenantSessionFactory

    with TenantSessionFactory(bind=app_engine()) as session:
        with tenant_scope(context_for(tenant_a)):
            session.execute(text("SELECT 1"))
            session.commit()
        with tenant_scope(context_for(tenant_b)):
            with pytest.raises(TenantContextSwitchError):
                session.execute(text("SELECT 1"))


def test_tenant_table_itself_is_policed(tenant_a, tenant_b):
    """The exemption in schema_lint.toml is for the *column*, not the policy.

    Without a policy here the runtime role could list every tenant's name and
    slug — a leak that no amount of correct ``tenant_id`` handling elsewhere
    would prevent.
    """
    with tenant_session(context_for(tenant_a)) as session:
        visible = session.query(Tenant.id).all()
    assert [row[0] for row in visible] == [tenant_a]


def test_context_carries_actor_and_origin(tenant_a):
    """Every write records actor_id / actor_type / origin (cross-cutting
    constraint, and §8.4's first GH loop guard)."""
    ctx = context_for(tenant_a, actor_id=uuid.uuid4())
    assert isinstance(ctx, TenantContext)
    assert ctx.actor_type and ctx.origin
