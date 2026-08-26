# CLAUDE.md

Guidance for Claude Code (and other agents) working in this repository.

Relay is collaboration infrastructure for teams that ship AI in production.
It keeps context on one chain (alert → chat → ticket → code → telemetry).
It is **not** a general-purpose work tracker. Do not design or ship Linear/Jira clones.

## Commands

```bash
make install          # Python 3.12 venv + editable install (uv)
make db-bootstrap     # three PG roles, relay_dev + relay_test (local Postgres is :5433)
make migrate          # alembic upgrade head
make gates            # everything CI runs — ruff, import-linter, registry, openapi snapshot, pytest
make serve            # uvicorn factory on :8000, reload on
make web-install      # npm ci in web/
make web-types        # regenerate TS types from the app's OpenAPI (required after /web shape changes)
make web-dev          # Vite on :5173; proxies /web /api /blobs to :8000
make openapi          # regenerate committed /api/v1 snapshot; commit openapi.json
```

Single test: `uv run pytest tests/test_tickets.py -q`. The suite **cannot** run twice against the same database at once (`clean_tables` truncates). Point a second process at `RELAY_PG_DATABASE=relay_test2`.

Local web login needs `RELAY_SESSION_COOKIE_SECURE=false` and `RELAY_WEB_ORIGINS` including `http://localhost:5173` (see `.env.example`). A `Secure` cookie on `http://` is silently dropped.

## What to read first

| File | When |
|---|---|
| `markdown/relay-s1-dev.md` | Changing backend behaviour. This is the developer contract. |
| `markdown/relay-s1-status.md` | Where the build stands. |
| `markdown/relay-s1-design.md` | S1 design + decision register (S-1…S-25). |
| `markdown/relay-prd.md` | Product spec (Chinese). Authoritative for scope and safety posture. |
| `TODO-S1.md` | Task-level acceptance. A box is ticked only when a mechanical gate exists. |
| `.cursor/skills/relay-ui/SKILL.md` | Any UI / chrome / 此刻 / chain-detail work. Mockups in `mockups/`. |

S1 is the workbench (accounts, logs, tickets, `/web` + frozen `/api/v1`).
**Do not implement** WeCom bot, GitHub Issue sync, RAG, ChatOps, DLP, fine-grained RBAC, or L4 external sharing — leave the ports and table seams.

## Architecture

```
src/relay/api/      HTTP contract: parse, transport auth, one use case, serialize
src/relay/app/      Use cases, authz, idempotency, audit  — shared by both HTTP surfaces
src/relay/domain/   Pure rules (state machine, capabilities, share levels). No I/O
src/relay/ports/    Protocols. Depend on nothing below them
src/relay/infra/    Adapters (db, blob, mail, search, telemetry, gateway)
web/                Vue 3 + TS + Pinia. Types generated from OpenAPI
tests/              pytest against live PostgreSQL (marker `db`)
migrations/         Alembic; do not reformat versions/
scripts/            Ops: bootstrap, purge, webhooks, blob smoke, backup
openapi.json        Frozen /api/v1 snapshot. CI diffs it (API-5)
```

Layering is a CI gate (`.importlinter`): `api > app > domain > ports`.
`relay.api` must not import `relay.infra.db.models` or `SystemRepository`.
Gateway clients may be imported **only** by `relay.infra.telemetry`.

The app is a factory: `uvicorn relay.api.app:create_app --factory`.

## Two HTTP surfaces

`/web/*` is the SPA's API: versionless, session-cookie auth, field names may change in the same commit as `web/`.
`/api/v1/*` is the frozen public ticket API: bearer `rly_…`, tenant from the token, **additive-only** inside v1. A removed field, a changed type, or a changed enum meaning is a v2 with 90 days of overlap.

They share the use cases, RFC 9457 `problem+json`, `If-Match` / `rev`, and the opaque pagination cursor. Drift here is "the UI notifies, the API doesn't".

- After `/web` shape changes: `make web-types`.
- After additive `/api/v1` changes: `make openapi` and commit `openapi.json`.
- A `tenant_id` in a v1 request body is 400 (`TenantInRequest`).
- Retryable v1 `POST`s take `Idempotency-Key`. Call `idempotency.abandon(key)` on failure.
- v1 routes name a scope (`TicketsWrite`, …). There is no permissive `AnyToken` default.

Session dependency is **async** (FastAPI copies `ContextVar` for sync generator deps). Endpoints that hit the DB are sync `def`. Only TOTP verification uses `HalfOpenSession`.

Raise `relay.app.errors.ApplicationError` subclasses. `relay.api.problems` maps them. Do not raise `HTTPException` for cases the application layer already named.

`NotFound` is also "exists in another tenant" (MT-6: **404, not 403**). `PermissionDenied` only for a resource the caller may know about.

## Multi-tenancy

Non-negotiable. Cheap now, a multi-week refactor later.

- Every business table: `UUIDPrimaryKey, TenantScoped, TimestampMixin, Base`.
- FKs: `tenant_fk(...)` — composite `(id, tenant_id)`. PostgreSQL checks FKs with RLS bypassed; a single-column FK is a cross-tenant write (plant a reference, or cascade-delete into another tenant).
- New table checklist: model → `alembic revision --autogenerate` → `apply_rls(op.get_bind(), ["table"])` in that migration → `make registry`. Skipping RLS on a `tenant_id` table is worse than omitting the column.
- Exemptions: `schema_lint.toml`, written reason, reviewed in the PR.
- Data access: `tenant_session(ctx)`. No context **raises**. Missing `app.tenant_id` GUC raises (`missing_ok` is forbidden).
- Three roles, do not collapse: `relay_owner` (migrations, FORCE RLS), `relay_app` (NOBYPASSRLS), `relay_system` (`SystemRepository` only, BYPASSRLS, audited). Using the wrong engine to dodge a permission error turns isolation off and nothing fails.
- Cross-tenant reads go through `SystemRepository` with a written reason. The work itself is still per-tenant under RLS.
- Jobs: system identity (`origin=SYSTEM`, no `actor_id`). See `relay.app.logs.retention`.

## Domain rules that are easy to break

- **Principal from the stored row, every call.** `UserSession` does not cache role. Token authority = scopes ∩ owner's current role.
- Capabilities: `relay.app.authz.require` + `relay.domain.permissions.Capability`. Three roles. No fine-grained RBAC.
- Tickets: `TicketService` only. Mutations take `expected_rev`. `update()` cannot change `status`; `transition()` always writes `ticket_status_history`. Graph in `relay.domain.tickets.TRANSITIONS`. Keys `RL-{n}`. Permalinks `/{tenantSlug}/t/{n}` are frozen (S-12).
- Share levels: `relay.app.logs.sharing.can_read` and SQL twin `visible_logs_predicate`. Never a third copy without a test. Admin reads L0; those reads are audited (`read_audit`).
- `audit.record` and `notifications.emit` join the caller's transaction. Actor/origin from `TenantContext`, not arguments.
- Blobs: `relay.ports.blob.tenant_prefix`. Check permission, then sign. `/blobs/{key}` is filesystem-carrier only; MinIO signed URLs never come back here.

## Frontend

When changing UI, follow `.cursor/skills/relay-ui/SKILL.md` and pixel-match `mockups/now.png` + `detail.png`.

- Shell is a **left icon rail** (此刻 / 上下文 / 工作 / 知识 / 成员) + top search. Home is 此刻. Frozen permalink: `/:tenantSlug/t/:number`.
- Chinese UI. Prefer 接力笔记 / 发送 / 调查 / 上下文. Do not make 工单 the product home or restore top entity tabs.
- One HTTP client: `web/src/api/client.ts`. Session cookie, `credentials: "same-origin"`. Ticket writes send `If-Match`. A 409 re-reads and banners; never retry with a fresh `rev`. Logs use edit lock + versions, not `rev`.
- Types from OpenAPI. Alias `/web` schemas in `web/src/api/types.ts` so components do not import the public API's `TicketResponse`.
- Permissions from `/web/session`, never `role === "admin"`.
- `markdown-it` stays `html: false`. Unresolved `#331` stays plain text (LOG-3: no tooltip, no "no access").

## Style

- Code, comments, tests, commit messages: English. User-visible strings: Chinese.
- Ruff line length 100. `from __future__ import annotations`. Match the surrounding docstring voice: state *why the constraint exists*.
- Prefer extending the existing service/store over a parallel path.
- A security invariant gets a failing test in the same commit. Cross-tenant tests are adversarial (`tests/test_cross_tenant.py`).
- Do not add README or extra docs unless asked. Decision-register changes: update `markdown/relay-s1-design.md` first.

## Safety posture (do not weaken)

- Cross-tenant leakage target is **0** and is a CI gate, not an aspiration.
- ChatOps writes (future) are triage, not change: TTL-bound, auto-reverting. Natural language only produces candidate commands.
- Never in ChatOps: version rollback, instance restart, key revocation, config deletion, billing.
- Zero write permissions from external customer channels.
- AI briefs must state confidence and keep competing hypotheses. Never present inference as conclusion.
