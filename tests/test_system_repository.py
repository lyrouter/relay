"""MT-3 · the audited cross-tenant path.

Design §4.2 allows exactly one way across the tenant boundary, and attaches two
conditions to it: it is explicitly declared, and every call is audited. Both are
testable, so both are tested — an escape hatch whose audit trail is optional is
just a back door with good documentation.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, text

from relay.infra.db.models import AuditLog, Ticket
from relay.infra.db.session import tenant_session
from relay.infra.db.system_repository import SystemRepository

from .conftest import context_for, requires_db

pytestmark = [requires_db, pytest.mark.db]


def test_system_role_can_see_across_tenants(tenant_a, tenant_b):
    tenants = SystemRepository().list_tenants(reason="test: verify the escape hatch works at all")
    slugs = {t.slug for t in tenants}
    assert {"alpha", "bravo"} <= slugs


def test_every_call_writes_an_audit_row(tenant_a, tenant_b):
    actor = uuid.uuid4()
    SystemRepository(actor_id=actor).list_tenants(reason="quarterly platform inventory")

    # Read it back through the tenant path, which is the point: the audit row is
    # visible to the tenant it was filed under, not only to the platform.
    with tenant_session(context_for(tenant_a)) as session:
        rows = session.scalars(select(AuditLog)).all()
    assert len(rows) == 1
    assert rows[0].action == "system_repository.list_tenants"
    assert rows[0].actor_id == actor
    assert rows[0].after == {"reason": "quarterly platform inventory"}


def test_a_call_with_no_reason_is_refused(tenant_a):
    """If a caller cannot say why it needs to cross the boundary, that is the
    review signal — so an empty reason fails before any query runs."""
    with pytest.raises(ValueError, match="written reason"):
        SystemRepository().list_tenants(reason="   ")


def test_the_audit_row_survives_a_failing_operation(tenant_a):
    """Evidence has to outlive the work.

    An unexplained cross-tenant read that then errors is *more* interesting than
    one that succeeds, so the audit row must not roll back with it.
    """

    def boom(session):
        raise RuntimeError("operation failed")

    with pytest.raises(RuntimeError):
        SystemRepository().run("probe", "investigating a report", boom, tenant_id=tenant_a)

    with tenant_session(context_for(tenant_a)) as session:
        actions = session.scalars(select(AuditLog.action)).all()
    assert actions == ["system_repository.probe"]


def test_ad_hoc_run_is_audited_too(tenant_a, tenant_b, user_factory):
    """The escape hatch's escape hatch.

    ``run()`` exists so that "the repository has no method for this" never
    becomes "I will open my own BYPASSRLS connection", which would be unaudited.
    """
    user_factory(tenant_b, "b@bravo.test")

    def count_all(session):
        return session.execute(text("SELECT count(*) FROM ticket")).scalar()

    result = SystemRepository().run(
        "count_tickets", "capacity planning across tenants", count_all, tenant_id=tenant_a
    )
    assert result == 0

    with tenant_session(context_for(tenant_a)) as session:
        rows = session.scalars(select(AuditLog)).all()
    assert [r.action for r in rows] == ["system_repository.count_tickets"]
    assert rows[0].target_id == str(tenant_a)


def test_tenant_sessions_never_see_the_system_role(tenant_a, user_factory):
    """Guard against the lazy fix: reaching for the BYPASSRLS engine to get past
    a permission error would switch isolation off with nothing failing."""
    a_user = user_factory(tenant_a, "a@alpha.test")
    with tenant_session(context_for(tenant_a)) as session:
        session.add(
            Ticket(tenant_id=tenant_a, number=1, type="bug", title="t", reporter_id=a_user)
        )
        session.commit()
        current = session.execute(text("SELECT current_user")).scalar()
    assert current == "relay_app"
