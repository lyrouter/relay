# Relay · S1 development guide

Covers what is built so far: **MT (multi-tenant data model) · TA-1 · INT-1 · AC
(accounts, authentication, roles, spaces)**. Task spec is
[TODO-S1.md](../TODO-S1.md); design is
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

## One thing that will waste your afternoon

**The suite cannot run twice against the same database at once.** `clean_tables`
truncates every table between tests, so two pytest processes sharing
`relay_test` — `make gates` in one terminal and `pytest` in another is the usual
way — will delete each other's fixtures and produce failures that vanish when
you rerun the file alone.

CI runs one job, so it never sees this. Locally, either wait, or point the second
run at its own database with `RELAY_PG_DATABASE=relay_test2` after creating it
the same way `make db-bootstrap` does.

## Checking a permission (AC-4)

The capability table is `relay/domain/permissions.py` and the enforcement point
is `relay.app.authz.require`. A service method looks like this:

```python
from relay.app.authz import Principal, require
from relay.domain.permissions import Capability

def _actor(session) -> Principal:
    ctx = current_context()
    user = session.get(User, ctx.actor_id)          # under RLS
    ...
    return Principal(tenant_id=ctx.tenant_id, user_id=user.id, role=user.role)

with tenant_session() as session:
    actor = _actor(session)
    require(actor, Capability.TICKET_WRITE)
```

Three things about that shape are load-bearing:

**Build the Principal from the stored row, every call.** `UserSession` does not
cache the role, so a demotion takes effect on the demoted user's next request
rather than whenever their session happens to expire. Anything longer-lived than
the current transaction throws that away — and for a token, the authority is the
*intersection* of its scopes and its owner's current role, so a personal token
minted while its holder was a Member grants nothing once they are a Guest.

**Look the target up inside the tenant session.** RLS makes another tenant's row
simply absent, so `NotFound` falls out for free — which is MT-6's 404-not-403
rule arriving as a consequence instead of a check somebody has to remember. Do
not raise `PermissionDenied` for a row the caller should not know exists.

**Admin is not a super-reader.** §5.4 gives Admin "查看内容: 按分享级别" — the
same rule as Member. Administering a tenant is not permission to read a
colleague's private log, and `share_levels_reachable_by` says so for every role.

For L2, call `SpaceService.grants_space_read` rather than joining
`space_member` yourself: it checks the role *before* the membership, which is
what makes S-6 true (a Guest in a space still does not reach L2).

## Writing an audit row

`relay.app.audit.record(session, action, target_type=..., target_id=...)` joins
the caller's transaction, so an audit row never describes a change that rolled
back. Actor and origin come from the `TenantContext`, not from parameters —
a write cannot be attributed to the wrong person by passing the wrong id.

That is the opposite trade from `SystemRepository`, which commits its audit row
*before* the work: a failed cross-tenant read is more interesting than a
successful one, while a record of an in-tenant change that did not happen is
noise that costs an investigator time.

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

## Composite tenant-scoped foreign keys (S-18)

**Ratified and in the design** — §4.2,
§2.4 (PostgreSQL ≥ 15) and §12.1. Full writeup, including the alternative that lost:
[relay-s1-fk-deviation.md](relay-s1-fk-deviation.md).

RLS covers cross-tenant reads completely, but referential integrity runs outside
policy evaluation — so a single-column FK leaves a cross-tenant *write* effect
open. Every reference is therefore `(id, tenant_id)`. Code:
`relay.infra.db.base.tenant_fk`. Tests:
`tests/test_cross_tenant.py::test_cannot_reference_another_tenants_row` and
`::test_another_tenants_delete_cannot_cascade_into_ours`.

**PostgreSQL 15+ is required** because of it: 11 keys use
`ON DELETE SET NULL (column)`, and the plain form would try to null `tenant_id`,
which is NOT NULL. That fails at delete time, not at review time.
