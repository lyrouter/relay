"""MT-4 · the composite indexes, asserted by shape rather than by name.

Every one of them leads with ``tenant_id``. That ordering is not stylistic: a
policy that ANDs ``tenant_id = ...`` into every query means a leading tenant
column is the difference between an index scan and a sequential one, on every
query in the product.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from relay.infra.db.engine import owner_engine

from .conftest import requires_db

pytestmark = [requires_db, pytest.mark.db]

#: Design §4.4, verbatim. (table, ordered columns, unique?)
REQUIRED: list[tuple[str, tuple[str, ...], bool]] = [
    ("ticket", ("tenant_id", "status", "updated_at"), False),
    ("ticket", ("tenant_id", "assignee_id", "status"), False),
    ("log", ("tenant_id", "space_id", "updated_at"), False),
    ("ticket", ("tenant_id", "number"), True),
    ("ticket_external_ref", ("tenant_id", "system", "external_id"), True),
    ("api_idempotency_record", ("tenant_id", "principal_id", "idempotency_key"), True),
]


def _indexes(table: str) -> list[tuple[tuple[str, ...], bool]]:
    with owner_engine().connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT i.relname, ix.indisunique,
                       array_agg(a.attname ORDER BY k.ord)
                FROM pg_class t
                JOIN pg_index ix ON ix.indrelid = t.oid
                JOIN pg_class i ON i.oid = ix.indexrelid
                JOIN LATERAL unnest(ix.indkey) WITH ORDINALITY AS k(attnum, ord) ON true
                JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = k.attnum
                WHERE t.relname = :table
                GROUP BY i.relname, ix.indisunique
                """
            ),
            {"table": table},
        ).all()
    return [(tuple(cols), unique) for _, unique, cols in rows]


@pytest.mark.parametrize(("table", "columns", "unique"), REQUIRED)
def test_required_composite_index_exists(table: str, columns: tuple[str, ...], unique: bool):
    found = _indexes(table)
    match = [(cols, uniq) for cols, uniq in found if cols == columns]
    assert match, f"{table}: no index on {columns}. Present: {sorted(found)}"
    assert match[0][1] == unique, (
        f"{table} {columns}: uniqueness is {match[0][1]}, expected {unique}"
    )


@pytest.mark.parametrize(("table", "columns", "_unique"), REQUIRED)
def test_required_index_leads_with_tenant_id(table: str, columns: tuple[str, ...], _unique: bool):
    assert columns[0] == "tenant_id"


def test_no_multi_column_index_buries_tenant_id():
    """A composite index with ``tenant_id`` in the middle is the quiet version of
    the same mistake: it exists, it looks deliberate, and the planner cannot use
    the prefix."""
    from relay.infra.db.models import Base

    offenders = []
    for table in Base.metadata.tables.values():
        if "tenant_id" not in table.c:
            continue
        for index in table.indexes:
            names = [c.name for c in index.expressions if hasattr(c, "name")]
            if "tenant_id" in names and names[0] != "tenant_id":
                offenders.append(f"{table.name}.{index.name}: {names}")
    assert not offenders, "tenant_id is not the leading column in:\n  " + "\n  ".join(offenders)
