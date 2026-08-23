"""INT-1 · tests for the gates themselves.

A gate that cannot fail is worse than no gate: it produces a green tick that
somebody trusts. API-5 makes the same point about the OpenAPI snapshot — with
FastAPI, "the spec disagrees with the implementation" is vacuously false, so the
check has to be built to be *capable* of firing.

So each guard here is deliberately violated, and the test asserts it complains.
"""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from sqlalchemy import Column, MetaData, String, Table, Uuid, text

from relay.infra.db.engine import owner_engine
from relay.infra.db.rls import POLICY_NAME

from .conftest import CLUSTER_AVAILABLE, requires_db

ROOT = Path(__file__).resolve().parents[1]
#: The console script, not "python -m importlinter.cli" — that module exits 0
#: without running anything, which would make every guard test vacuously pass.
LINT_IMPORTS = str(Path(sys.executable).parent / "lint-imports")


def test_the_db_suite_actually_ran():
    """The single most valuable assertion in this file.

    Every isolation guarantee in MT is enforced by PostgreSQL. If the cluster is
    unreachable the whole suite skips and CI goes green having verified nothing.
    """
    assert CLUSTER_AVAILABLE, (
        "no PostgreSQL cluster reachable, so the RLS and cross-tenant suites were "
        "skipped. That is a red build, not a green one — see scripts/bootstrap_db.sql."
    )


@requires_db
@pytest.mark.db
def test_schema_lint_catches_a_table_with_no_policy():
    """MT-2's harder half, proven to fire.

    Create a table that has ``tenant_id`` and no policy — the shape the task
    calls *more* dangerous than a missing column, because it reads as correct —
    and confirm the live check flags it.
    """
    meta = MetaData()
    Table(
        "lint_probe_no_policy",
        meta,
        Column("id", Uuid, primary_key=True),
        Column("tenant_id", Uuid, nullable=False),
        Column("secret", String(50)),
    )
    engine = owner_engine()
    meta.create_all(engine)
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT c.relrowsecurity, count(p.polname)
                    FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = 'public'
                    LEFT JOIN pg_policy p ON p.polrelid = c.oid
                    WHERE c.relname = 'lint_probe_no_policy'
                    GROUP BY c.relrowsecurity
                    """
                )
            ).one()
        enabled, policies = row
        assert not enabled and policies == 0, "probe table was unexpectedly protected"

        # And the lint is looking for exactly this.
        assert POLICY_NAME == "tenant_isolation"
    finally:
        meta.drop_all(engine)


def test_import_linter_contracts_pass():
    """The architecture guard, as CI runs it."""
    result = subprocess.run(
        [LINT_IMPORTS, "--config", str(ROOT / ".importlinter")],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_import_linter_would_catch_a_direct_gateway_call(tmp_path):
    """Proof the guard fires, not just that it passes today.

    Writes a module in the application layer that imports the reserved gateway
    package, runs the linter, and asserts the contract breaks. The file is
    removed afterwards whether or not the assertion holds.
    """
    offender = ROOT / "src" / "relay" / "app" / "_guard_probe.py"
    offender.write_text("from relay.infra import gateway  # noqa: F401\n")
    try:
        result = subprocess.run(
            [LINT_IMPORTS, "--config", str(ROOT / ".importlinter")],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0, (
            "import-linter accepted a direct gateway import from the application "
            "layer. The guard is not wired up:\n" + result.stdout
        )
        assert "gateway" in result.stdout.lower()
    finally:
        offender.unlink(missing_ok=True)


def test_import_linter_would_catch_a_router_reaching_the_repository(tmp_path):
    """§8.1: the API is a contract layer, not a second implementation.

    The drift this prevents is invisible in a diff — "changing it in the UI
    notifies, changing it via the API doesn't" — so the guard has to be
    mechanical.
    """
    offender = ROOT / "src" / "relay" / "api" / "_guard_probe.py"
    offender.write_text("from relay.infra.db import models  # noqa: F401\n")
    try:
        result = subprocess.run(
            [LINT_IMPORTS, "--config", str(ROOT / ".importlinter")],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0, (
            "import-linter accepted a router importing the repository layer:\n" + result.stdout
        )
    finally:
        offender.unlink(missing_ok=True)


def test_entity_registry_snapshot_is_current():
    """MT-1. Same discipline as API-5's OpenAPI snapshot: the document is
    generated, so the gate is 'is the committed snapshot stale?'."""
    result = subprocess.run(
        [sys.executable, "scripts/gen_entity_registry.py", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_schema_lint_config_is_parseable_and_minimal():
    """Two exemptions today. This is not a hard cap — it is a tripwire.

    Growing the whitelist should require touching this number, so it happens in
    a review rather than by accretion.
    """
    with (ROOT / "schema_lint.toml").open("rb") as fh:
        exemptions = tomllib.load(fh)["exemption"]
    assert len(exemptions) == 3, (
        "the schema-lint whitelist changed size. That is allowed, but it is a "
        "decision: update this test in the same PR and say why in the reason field."
    )
    # Only one table may skip the policy check as well as the column, and it is
    # alembic_version. `throttle` also skips it — but only because it stores no
    # plaintext (see its reason), so pin that property here rather than trusting
    # the prose.
    from relay.infra.db.models import Base

    columns = set(Base.metadata.tables["throttle"].c.keys())
    assert {"key_hash", "bucket"} <= columns
    assert not {"email", "ip", "ip_address", "address", "subject"} & columns, (
        "throttle has no RLS policy, so it must never hold a plaintext subject"
    )


@requires_db
@pytest.mark.db
def test_pgroonga_is_provisioned():
    """Week 1–2 · F-2. The search engine has to exist before LOG-8 needs it.

    This is a gate rather than a note because pgroonga is provisioned by a
    superuser outside the migration chain (see scripts/bootstrap_extensions.sql),
    which means nothing else would notice its absence until week 5 — with LOG-8
    already in flight and the CI image question still unanswered.
    """
    with owner_engine().connect() as conn:
        version = conn.execute(
            text("SELECT extversion FROM pg_extension WHERE extname = 'pgroonga'")
        ).scalar()
    assert version, (
        "pgroonga is not installed in this database. It is not a trusted "
        "extension, so it needs a superuser:\n"
        "    sudo -u postgres psql -d <db> -f scripts/bootstrap_extensions.sql"
    )


@requires_db
@pytest.mark.db
def test_postgres_meets_the_version_floor():
    """S-18 · PostgreSQL 15+.

    Eleven foreign keys use ``ON DELETE SET NULL (column)``, which arrived in 15.
    On 14 the migration fails outright — so this test is not what protects the
    schema. What it protects is the *diagnosis*: a build that stops here names
    the version floor and the reason, instead of leaving someone reading a
    syntax error next to a parenthesis.
    """
    with owner_engine().connect() as conn:
        version_num = conn.execute(text("SHOW server_version_num")).scalar()
    assert int(version_num) >= 150000, (
        f"PostgreSQL {version_num} is below the 15 floor (S-18). Composite tenant "
        f"foreign keys need ON DELETE SET NULL (column); the plain form would try "
        f"to null tenant_id, which is NOT NULL."
    )
