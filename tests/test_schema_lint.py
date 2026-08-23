"""MT-2 · the schema lint. A CI gate, not a report.

Two checks that have to be separate, because they fail for different reasons and
one of them is worse:

1. every table in ``Base.metadata`` has ``tenant_id``;
2. every table in the live database has RLS **enabled, FORCEd, and exactly one
   tenant_isolation policy**.

Check 2 is the one that earns the task. A table with ``tenant_id`` and no policy
looks correct in every code review and in every ORM query, and leaks everything.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from sqlalchemy import text

from relay.infra.db.engine import owner_engine
from relay.infra.db.models import Base
from relay.infra.db.rls import POLICY_NAME

from .conftest import requires_db

CONFIG_PATH = Path(__file__).resolve().parents[1] / "schema_lint.toml"


def _exemptions() -> dict[str, dict]:
    with CONFIG_PATH.open("rb") as fh:
        raw = tomllib.load(fh)
    return {e["table"]: e for e in raw.get("exemption", [])}


EXEMPTIONS = _exemptions()


def test_every_exemption_carries_a_written_reason():
    """S-2: exemptions come from a config file with written reasons.

    An exemption with an empty reason is the same as a verbal one, which is what
    the decision ruled out.
    """
    assert EXEMPTIONS, "the whitelist should never be empty; alembic_version needs an entry"
    for table, entry in EXEMPTIONS.items():
        reason = entry.get("reason", "").strip()
        assert len(reason) >= 40, f"exemption for {table!r} needs a real written reason"
        assert "requires_tenant_id" in entry, f"{table!r} must state requires_tenant_id explicitly"
        assert "requires_policy" in entry, f"{table!r} must state requires_policy explicitly"


@pytest.mark.parametrize("table_name", sorted(Base.metadata.tables))
def test_every_table_has_tenant_id(table_name: str):
    entry = EXEMPTIONS.get(table_name)
    if entry is not None and not entry["requires_tenant_id"]:
        pytest.skip(f"whitelisted: {entry['reason'].strip().splitlines()[0]}")
    columns = Base.metadata.tables[table_name].columns
    assert "tenant_id" in columns, (
        f"table {table_name!r} has no tenant_id. Add it, or add an exemption to "
        f"schema_lint.toml with a written reason."
    )
    assert not columns["tenant_id"].nullable, (
        f"{table_name}.tenant_id is nullable; a NULL tenant matches no policy and the row "
        f"becomes invisible to every role except BYPASSRLS."
    )


@requires_db
@pytest.mark.db
def test_every_live_table_has_a_forced_rls_policy():
    """The check that makes 'has tenant_id' mean something."""
    with owner_engine().connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT c.relname,
                       c.relrowsecurity,
                       c.relforcerowsecurity,
                       COALESCE(array_agg(p.polname) FILTER (WHERE p.polname IS NOT NULL), '{}')
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = 'public'
                LEFT JOIN pg_policy p ON p.polrelid = c.oid
                WHERE c.relkind = 'r'
                GROUP BY c.relname, c.relrowsecurity, c.relforcerowsecurity
                """
            )
        ).all()

    assert rows, "no tables found — did the migration run?"
    failures: list[str] = []
    for name, enabled, forced, policies in rows:
        entry = EXEMPTIONS.get(name)
        if entry is not None and not entry["requires_policy"]:
            continue
        if not enabled:
            failures.append(f"{name}: RLS not enabled")
        if not forced:
            # Without FORCE the owner reads everything, and the day someone runs
            # the app as the owner the whole model is off with no error.
            failures.append(f"{name}: RLS enabled but not FORCEd")
        if POLICY_NAME not in policies:
            failures.append(f"{name}: no {POLICY_NAME} policy (policies={list(policies)})")

    assert not failures, "RLS gaps:\n  " + "\n  ".join(failures)


@requires_db
@pytest.mark.db
def test_no_stale_exemptions():
    """A whitelist nobody prunes stops being a whitelist.

    If an exempted table no longer exists, the entry is dead weight that makes
    the next reader think the exemption is still load-bearing.
    """
    with owner_engine().connect() as conn:
        live = {
            r[0]
            for r in conn.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            )
        }
    stale = set(EXEMPTIONS) - live
    assert not stale, f"schema_lint.toml exempts tables that do not exist: {sorted(stale)}"


def test_policy_predicate_never_uses_missing_ok():
    """MT-3, detail 3 — the trap that costs a day of debugging.

    ``current_setting('app.tenant_id', true)`` returns NULL when unset, the
    policy evaluates false, and the query **silently returns zero rows**. The
    one-argument form raises instead, which is what the cross-cutting constraint
    already promises callers.
    """
    from relay.infra.db import rls

    for table in ["ticket", *rls.PREDICATE_OVERRIDES]:
        predicate = rls.predicate_for(table)
        assert "missing_ok" not in predicate
        assert ", true)" not in predicate, f"{table}: missing_ok passed positionally"
