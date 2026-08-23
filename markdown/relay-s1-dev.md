# Relay · S1 development guide

Covers what is built so far: **MT (multi-tenant data model) · TA-1 · INT-1**.
Task spec is [TODO-S1.md](../TODO-S1.md); design is
[relay-s1-design.md](relay-s1-design.md).

## Getting a working tree running

Requires PostgreSQL 15+ (16 is what CI uses; the composite-FK `ON DELETE SET
NULL (column)` form needs 15) and `uv`.

```bash
make install         # venv + editable install
make db-bootstrap    # three roles, relay_dev + relay_test
make migrate         # alembic upgrade head
make gates           # everything CI runs
```

Settings come from `RELAY_*` environment variables; see `.env.example`.

## The three database roles, and why you cannot collapse them

| Role | Used by | Notes |
|---|---|---|
| `relay_owner` | Alembic only | Owns the tables. **Also bound by FORCE RLS**, so it cannot insert tenant-scoped rows either — a data migration has to run as `relay_system`. |
| `relay_app` | The application | Non-owner, `NOBYPASSRLS`. Every query it makes is filtered by policy. |
| `relay_system` | `SystemRepository` | `BYPASSRLS`. The only cross-tenant path, audited per call. |

Reaching for the wrong engine to get past a permission error switches isolation
off and **nothing fails** — which is why they are three separate objects in
`relay.infra.db.engine` rather than one with a flag.

## Writing code that touches data

```python
from relay.context import TenantContext, ActorType, Origin
from relay.infra.db.session import tenant_session

ctx = TenantContext(tenant_id=..., actor_id=..., actor_type=ActorType.USER, origin=Origin.WEB)
with tenant_session(ctx) as session:
    ...
```

Opening a `TenantSession` with no context **raises**. That is the designed
behaviour, not an inconvenience: the alternative — quietly querying every tenant
— is the bug multi-tenancy exists to prevent. The same applies one layer down,
where `current_setting('app.tenant_id')` is called without `missing_ok`, so a
missing GUC raises instead of matching zero rows.

## Adding a table

1. Add the model under `relay/infra/db/models/`, inheriting
   `UUIDPrimaryKey, TenantScoped, TimestampMixin, Base`.
2. Reference other tables with `tenant_fk(...)`, never a bare `ForeignKey`.
   PostgreSQL runs FK checks with policies bypassed, so a single-column FK lets
   one tenant plant a reference into another's graph — and lets that tenant's
   delete cascade back into yours.
3. `uv run alembic revision --autogenerate -m "..."`, then add
   `apply_rls(op.get_bind(), ["your_table"])` to the migration.
4. `make registry` and commit the updated snapshot.

Skipping any of these fails CI. Step 3 is the one worth internalising: a table
with `tenant_id` and no policy is **more dangerous** than one without the
column, because it reads as correct in every review.

## The gates, and how to check they still bite

| Gate | Where | Proven to fire by |
|---|---|---|
| Schema lint (MT-2) | `tests/test_schema_lint.py` | `test_schema_lint_catches_a_table_with_no_policy` |
| Cross-tenant negatives (MT-6) | `tests/test_cross_tenant.py` | the suite is entirely adversarial |
| Architecture contracts (§8.1, §2 rule 2) | `.importlinter` | `tests/test_ci_gates.py` writes a violating module and asserts the linter breaks |
| Entity registry snapshot (MT-1) | `scripts/gen_entity_registry.py --check` | `test_entity_registry_snapshot_is_current` |

`test_ci_gates.py::test_the_db_suite_actually_ran` fails the build when no
cluster is reachable. Without it, an unreachable database would skip every
isolation test and CI would go green having verified nothing — which is the
specific way this class of gate usually dies.

## Deviation from the design doc

**Composite `(id, tenant_id)` foreign keys** are not in
[relay-s1-design.md §4](relay-s1-design.md) and were added during MT-2/MT-3. The
design covers cross-tenant *reads* thoroughly and RLS handles those completely,
but referential integrity runs outside policy evaluation, which left a
cross-tenant *write* effect open. Rationale and tests:
`relay.infra.db.base.tenant_fk`,
`tests/test_cross_tenant.py::test_cannot_reference_another_tenants_row`. Folding
this into §4 keeps doc and code in sync.
