"""Row-level security DDL (MT-3).

Design §2.4 lists three details that decide whether RLS actually binds. All
three are implemented here or in ``session.py``:

1. **The table owner bypasses RLS by default.** So every table gets
   ``FORCE ROW LEVEL SECURITY`` *and* the app connects as a non-owner role.
   Migrations run as the owner; the runtime never does.
2. **Transaction-scoped ``SET LOCAL``, never session-scoped ``SET``** — see
   ``session.py``. A session-scoped value leaks across pooled connections and
   crosses tenants.
3. **``current_setting`` without ``missing_ok``.** With ``missing_ok`` a missing
   context yields NULL, the policy evaluates false, and the query silently
   returns zero rows — which sends every investigation in the wrong direction.
   Without it, the query raises, which is what we want and what the
   cross-cutting constraint already promises.

Note on the second failure shape: once any transaction on a connection has set
``app.tenant_id``, PostgreSQL keeps the placeholder defined and resets it to the
empty string at commit. ``''::uuid`` also raises, so both "never set" and "set
then reset" fail closed. ``tests/test_rls_enforcement.py`` covers both.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Connection

from relay.infra.db.models import Base

#: The GUC the policies read. One name, referenced nowhere else as a literal.
TENANT_GUC = "app.tenant_id"

#: Policy name, uniform across tables so the lint can look for exactly one thing.
POLICY_NAME = "tenant_isolation"

#: Tables whose tenancy predicate is not ``tenant_id``. Only one, and it is the
#: tenant table itself: its own primary key *is* the tenant. It still gets a
#: policy — without one, the runtime role could enumerate every tenant's name
#: and slug, which is exactly the kind of "looks fine, leaks anyway" gap MT-2
#: was written to catch.
PREDICATE_OVERRIDES: dict[str, str] = {
    "tenant": f"id = current_setting('{TENANT_GUC}')::uuid",
}

#: Roles that read and write through the policies.
APP_ROLE = "relay_app"
#: The one cross-tenant path, audited per call (``SystemRepository``).
SYSTEM_ROLE = "relay_system"


def predicate_for(table_name: str) -> str:
    return PREDICATE_OVERRIDES.get(
        table_name, f"tenant_id = current_setting('{TENANT_GUC}')::uuid"
    )


def policy_statements(table_name: str) -> list[str]:
    predicate = predicate_for(table_name)
    return [
        f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY',
        # Detail 1. Without FORCE, whoever owns the table reads everything, and
        # the day someone runs the app as the owner "just to debug" the whole
        # model is off with no error to notice.
        f'ALTER TABLE "{table_name}" FORCE ROW LEVEL SECURITY',
        f'DROP POLICY IF EXISTS {POLICY_NAME} ON "{table_name}"',
        # USING gates reads and the pre-image of writes; WITH CHECK gates the
        # post-image. Both are required: USING alone lets a tenant INSERT a row
        # stamped with another tenant's id.
        f"CREATE POLICY {POLICY_NAME} ON \"{table_name}\" "
        f"USING ({predicate}) WITH CHECK ({predicate})",
    ]


def grant_statements(table_name: str) -> list[str]:
    return [
        f'GRANT SELECT, INSERT, UPDATE, DELETE ON "{table_name}" TO {APP_ROLE}',
        f'GRANT SELECT, INSERT, UPDATE, DELETE ON "{table_name}" TO {SYSTEM_ROLE}',
    ]


def apply_rls(connection: Connection, table_names: list[str] | None = None) -> None:
    """Apply policies and grants. Idempotent; safe to re-run from a migration."""
    names = table_names if table_names is not None else sorted(Base.metadata.tables)
    for name in names:
        for stmt in policy_statements(name) + grant_statements(name):
            connection.execute(text(stmt))
