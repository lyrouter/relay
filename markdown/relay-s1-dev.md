# Relay · S1 development guide

Covers what is built so far: **MT (multi-tenant data model) · TA-1 · INT-1 · AC
(accounts, authentication, roles, spaces) · TKT backend (tickets, state machine,
AI context, comments, board metadata) · NT (in-app notifications) · LOG backend
(versions, edit lock, sharing, search, attachments, knowledge marker) · WEB
(the `/web` HTTP layer the frontend talks to)**. The frontend itself
(LOG-1/2/3/7, TKT-5/6/7/9) and the public `/api/v1` (API-1/2/3) are not started.
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

```bash
make serve           # uvicorn on :8000, reload on
open http://127.0.0.1:8000/docs
```

**Two settings you have to change for local development, and the reason each one
defaults the unhelpful way:**

| Setting | Local value | Why the default is the other one |
|---|---|---|
| `RELAY_SESSION_COOKIE_SECURE` | `false` | The cookie is `Secure` by default, and a browser **silently** drops a `Secure` cookie on `http://` — "login returns 200 and nothing happens" is a bad first hour. Production must not turn this off |
| `RELAY_WEB_ORIGINS` | `http://localhost:5173` | State-changing requests must carry a recognised `Origin` (CSRF). A Vite dev server is a different origin from the API, so add it rather than switching the check off |

`RELAY_BLOB_SIGNING_KEY` also needs a real value in any deployment — see
[the deployment notes](relay-s1-deploy.md#o-1-relay_blob_signing_key).

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

**An Admin reads every share level, including L0.** Design §6.3 defines the
level as "仅作者 + Admin", which is more specific than §5.4's coarse "按分享级别"
row. It is a real privacy decision rather than an oversight, and
`share_levels_reachable_by` states it in the table instead of hiding it in a
branch further down.

Do not evaluate share levels by hand — call `relay.app.logs.sharing.can_read`.
It applies the §6.3 order (tenant → level → role) and holds S-6: a Guest in a
space still does not reach L2, because the role is checked before the
membership.

## Writing an audit row

`relay.app.audit.record(session, action, target_type=..., target_id=...)` joins
the caller's transaction, so an audit row never describes a change that rolled
back. Actor and origin come from the `TenantContext`, not from parameters —
a write cannot be attributed to the wrong person by passing the wrong id.

That is the opposite trade from `SystemRepository`, which commits its audit row
*before* the work: a failed cross-tenant read is more interesting than a
successful one, while a record of an in-tenant change that did not happen is
noise that costs an investigator time.

## Changing a ticket

Everything goes through `relay.app.tickets.service.TicketService`, and two of its
rules are easy to trip over:

**Every mutation needs the current `rev`.** `update()` and `transition()` both
take `expected_rev` and raise `Conflict` carrying the current value if it has
moved. That is API-3's `If-Match` / 409, enforced one layer below the API so the
UI cannot have a second concurrency policy.

```python
view = TicketService().create(NewTicket(type=TicketType.BUG, title="网关 502"))
view = TicketService().transition(view.id, TicketStatus.IN_PROGRESS, expected_rev=view.rev)
```

**`update()` cannot change the status — that is deliberate.** There is no code
path that writes `status` without writing a `ticket_status_history` row, because
a transition with no history is exactly the data Phase 2's GitHub loop guard
needs and cannot reconstruct. A test asserts the parameter does not exist.

`ai_context` is validated against the tenant's own `ai_context_field_config`
rows, so an undeclared key is an error rather than a passenger. A tenant gets the
gateway-only fields (`gateway_version`, `routing_policy`) only if it was
bootstrapped with `--domain-scope gateway`.

## Notifying somebody

`relay.app.notifications.emit(session, event)` — note the session: it joins the
caller's transaction, so a notification never describes a change that rolled
back, and a committed change never goes unannounced. It returns None when the
recipient is the actor, so callers do not need to filter that themselves.

In-app is the **only** channel in S1 (F-1), and the unread count is therefore the
whole reach surface rather than a badge next to one. Which is why aggregation
matters: four events on one ticket inside five minutes are one unread item with
`folded_count == 4`, and the suppressed rows stay in history rather than being
dropped.

## Reading a log

**Never evaluate share levels by hand.** There are exactly two implementations
of the LOG-6 rule and both are deliberate:

| Where | What it is |
|---|---|
| `relay.app.logs.sharing.can_read` | the rule, as a pure function. The authority. |
| `relay.infra.db.visibility.visible_logs_predicate` | the same rule as SQL, for the log list and for search |

`test_logs.py::test_the_list_query_agrees_with_the_rule_for_every_log`
cross-checks them for every (log, reader) pair. If you add a third place, add it
to that test in the same commit — the drift between two copies of an access rule
is a leak or an invisible document, and neither shows up in a diff.

A log the reader may not see raises `NotFound`, not `PermissionDenied`: same
reasoning as MT-6's 404-not-403, applied inside a tenant.

An **Admin reads every level, L0 included** (§6.3: "仅作者 + Admin"). Search
agrees with the read path on this, because a rule enforced on read and forgotten
on search is not enforced.

**And that read writes** (S-19). `relay.app.logs.read_audit` records one
`log.read_by_admin` row when a read succeeded *only* because the reader is an
Admin — judged by re-running `can_read` with the reader demoted to Member. So an
Admin opening a colleague's private draft is audited; an Admin opening an L3 log,
their own log, or an L1 they were granted is not. A list or a search writes one
row naming what it surfaced, never one per row.

The practical consequence when you add a read path: `_require_readable` returns
**whether it recorded something**, and the caller commits if it did. That is the
only reason a read path in `LogService` ever commits, and it is why the check is
narrow — a trail that logged ordinary browsing would bury the rows somebody
actually needs.

## Running something on a schedule

There is one scheduled job (the 90-day version purge) and it runs as the **system
identity** (S-20): `relay.app.logs.retention.system_context(tenant_id)` plus
`system_principal`, entry point `scripts/purge_log_versions.py`.

```python
with tenant_scope(system_context(tenant.id)):
    purge_old_versions()
```

Three properties to preserve if you add a second job:

* **`origin` must be `SYSTEM`.** `system_principal` refuses anything else, so a
  request can never run as system even if a bug arranged the actor type.
* **No `actor_id`.** The point of the identity is to *not* be somebody; an audit
  row naming an Admin who was asleep is worse than no row.
* **The tenant list is the only cross-tenant read**, and it goes through
  `SystemRepository` with a written reason. The work itself is per tenant, under
  RLS, exactly as a request would do it.

## Attaching a file

`AttachmentService` needs a `BlobPort`. In dev and tests that is
`FilesystemBlobStore`; the S1 carrier is self-hosted MinIO and **its adapter is
not written yet** — S-25 says to write it blind against standard S3 semantics
instead of waiting for the real instance, verified by a contract test against a
containerised `minio/minio` (see LOG-5 in the task list). The key layout is the
same under both, so switching carriers moves no object and changes no stored key.

⚠️ **`/blobs/{key}` belongs to the filesystem carrier only.** It calls `verify`
and `open`, which are not on `BlobPort` — with MinIO the signed URL points at the
object store and the browser never comes back here. Keep that visible at wiring
time: the carrier switch has to take the route with it, not leave a path that
raises on the first download.

Two rules the object store cannot get from RLS, so they live in code:

1. the key contains `tenant_id` — `relay.ports.blob.tenant_prefix` is the only
   definition of that layout;
2. access is **permission-checked, then signed**, in that order. The signature
   stops a link outliving the check; it is not the check.

## Adding an HTTP route

`relay/api/` is a **contract layer**: parse, authorize the transport, call one use
case, serialize. `import-linter` refuses a router that imports the repository
layer, because the drift that prevents is invisible in a diff — "changing it in
the UI notifies, changing it through the API doesn't".

```python
from relay.api.dependencies import Session   # Annotated[..., Depends(require_session)]

@router.post("", response_model=LogResponse, status_code=201)
def create_log(payload: CreateLogPayload, session: Session) -> LogResponse:
    return _log(LogService().create(payload.title, payload.body))
```

Four things about that shape are load-bearing:

**The session dependency is `async`, and it must stay that way.** FastAPI runs a
*sync* generator dependency in a worker thread whose context is a **copy**, so the
`ContextVar` it sets is invisible to the endpoint — every request would fail with
`MissingTenantContext`. The endpoint itself is a plain `def` (it does blocking
database work and FastAPI puts it in the threadpool, inheriting a copy of the
request's context, tenant included). Blocking calls inside the dependency go
through `run_in_threadpool` rather than making the dependency sync.

**Do not raise `HTTPException` for anything the application layer has an opinion
about.** Raise the `ApplicationError` and let `relay.api.problems` map it: that
is what keeps one error format (§8.6) and what keeps a route from inventing a
status code that disagrees with the use case's meaning. `NotFound` → 404 is
MT-6's 404-not-403 rule arriving for free.

**Two surfaces, different contract discipline.** `/web/*` ships with the frontend
in this repository, so it is versionless and a field can be renamed in one
commit. `/api/v1/*` is frozen (§8.6). What they **share** is not negotiable: the
same use cases, the same `problem+json`, the same `If-Match` rule, the same
opaque cursor.

**A route that needs a session gets `Session`; the TOTP route gets
`HalfOpenSession`.** AC-3 opens a session before the second factor is verified,
and `require_session` refuses one — with `mfa_required`, not `session_expired`,
because the fix is a six-digit code rather than another password. `HalfOpenSession`
exists for exactly one route and should stay that way.

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
| HTTP layer end to end (WEB) | `tests/test_web_api.py` | it drives the real ASGI app with real cookies — the async-dependency wiring, the CSRF check and all four `problem+json` paths are in the request path, not stubbed |

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
