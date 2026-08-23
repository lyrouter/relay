# Relay · S1 Task Breakdown

Execution list for the **S1 slice** — the first delivery: multi-tenant data
model, accounts with self-service signup, logs, tickets + board, and a public
ticket API. Spec: [relay-s1-design.md](markdown/relay-s1-design.md).

> **This file does not replace [TODO.md](TODO.md).** TODO.md remains the Phase 1
> baseline and keeps the full breakdown for BOT / GH / RAG / SEED — including the
> reasoning behind their ordering, which is worth preserving. This file is the
> **execution view for S1 only**. Task IDs are shared between the two files: an
> ID here means the same work as the ID there, minus what §Deferred lists.

| | |
|---|---|
| **Scope** | MT · TA-1 · AC · LOG · TKT · **API** · NT · INT subset. **57.5 pd ≈ 11.5 person-weeks** |
| **First API consumer** | **AI Gateway WebUI feedback form** — users submit feedback, it lands as a Relay ticket (design §8.8) |
| **Duration** | ≈ 7 calendar weeks at 1.7 pw/week (2 BE · 1 FE · 0.5 QA; **the AI role has no S1 work — see [Staffing](#staffing-note)**) |
| **Exit state** | Dual-track use (Relay alongside Jira) + at least one external system integrated over the API |
| **Not the exit state** | Jira decommission — decided **not** an S1 gate (§12.1 S-9); it waits for WeCom notifications, which ship with BOT |
| **Decisions** | All design questions are **settled** — see [relay-s1-design.md §12.1](markdown/relay-s1-design.md#121-决策记录全部采纳建议). Decided values are inlined in the tasks below so nobody has to cross-reference while implementing |
| **Stack** | Python 3.12+ / FastAPI / SQLAlchemy 2.x + Alembic / Pydantic v2 · Vue 3 + TS + Vite + Pinia · self-hosted PostgreSQL (RLS · PG FTS · pgvector · `SKIP LOCKED`) · **self-hosted MinIO** |

---

## How to read this file

- **ID** — stable task identifier, shared with [TODO.md](TODO.md). Reference these in commits and branches.
- **Effort** — person-days. Role: BE backend · FE frontend · QA quality.
- **🔒** — cannot be cut. Survives any compression (see [If S1 needs compression](#if-s1-needs-compression)).
- **⏹** — deferred out of S1; the interface seam stays. Full spec lives in TODO.md.
- Values in **bold** inside a task are **decided**, not suggestions. Don't re-litigate them mid-implementation; if one is wrong, change it in the design doc first so the two stay in sync.

---

## Sequencing rules (non-negotiable)

1. **MT runs exclusively in weeks 1–2**, not in parallel with feature work. One
   table missing `tenant_id` amplifies rework across every module built after it.
   MT-2's schema lint is what makes this stick.
2. **No code outside the TA adapter package may touch a gateway API** — enforced
   by `import-linter`, not by review. TA-1 ships even though nothing in S1
   consumes it: four Phase 2 consumers depend on the seam, and the team that
   operates Relay also builds the gateway, so a direct call is otherwise near
   certain.
3. **The API contract (design §8) is signed off before TKT-1 creates tables.**
   Once the first consumer exists, field names, the numbering scheme, and status
   values are all frozen — and `rev` / `actor_type` / `external_ref` are cheapest
   at create-table time (design §8.4).

---

## Effort summary

| Epic | Title | Effort | Weeks |
|---|---|---:|---|
| [MT](#mt--multi-tenant-data-model) | Multi-tenant data model 🔒 | 7 pd | 1–2 |
| [TA](#ta--telemetry-adapter-seam) | Telemetry adapter seam (interface only) 🔒 | 1 pd | 1–2 |
| [AC](#ac--accounts--self-service-signup) | Accounts + self-service signup | 8.5 pd | 3–4 |
| [LOG](#log--logs--knowledge-authoring) | Logs / knowledge authoring | 15 pd | 3–6 |
| [TKT](#tkt--tickets--board) | Tickets + board | 13 pd | 3–6 |
| [API](#api--public-ticket-api) | Public ticket API | 7.5 pd | 5–7 |
| [NT](#nt--notifications) | Notifications (**in-app only**) | 1.5 pd | 5–6 |
| [INT](#int--integration-testing-rollout) | Integration, testing, rollout | 4 pd | 1–7 |
| | **Total** | **57.5 pd** | ≈ 11.5 pw |

**Against the original MVP (68.5 pd), net −11:**

| | Detail | Subtotal |
|---|---|---:|
| Removed | BOT 10 · TA-2…TA-4 4 · INT items that ship with BOT / Jira cutover 4 · AC-6 + AC-7 2.5 · MT-5 1 | **−21.5** |
| Added | API 7.5 · NT 1.5 · AC-9 1 · INT-11 0.5 | **+10.5** |

**API is net-new work, not budget freed up by dropping BOT.**

### Week map

| Week | Focus |
|---|---|
| 1–2 | **MT exclusively** (schema lint + RLS policy check wired into CI) · TA-1 · **API contract sign-off** (no code — just the fields and semantics of design §8) · install pgroonga + pgvector (confirmed available) |
| 3–4 | AC (signup → login → roles → space) · LOG begins · TKT begins |
| 5–6 | LOG completes · TKT completes · NT · API-1/2/3 · **INT-11 restore drill before the team starts writing real logs** |
| 7 | API-4/5 · INT-5 end-to-end · dual-track use begins |

### Staffing note

**The AI role has nothing to do in S1** — no BOT, no RAG, no gateway routing.
That is a real gap in the plan, not an oversight to discover in week 3. Either
give it the RAG chunking/retrieval spike early, or fold it into API/frontend
work. Leaving it idle until BOT starts makes BOT's 10 pd look like it appeared
out of nowhere.

---

## MT · Multi-tenant data model
**7 pd · 🔒 · weeks 1–2 · [design §4](markdown/relay-s1-design.md)**

Data-model-layer multi-tenancy only. Per-tenant billing, tenant self-service
admin, cross-tenant sharing policy, per-tenant config isolation, and per-tenant
model routing are **product features** and out of scope. This pair gets misread
in the other direction, and that costs a multi-week refactor.

- [ ] **MT-1** Definitive entity list, every business entity audited for tenancy — tenants, users, identity bindings, spaces, logs, log versions, attachments, tickets, comments, labels, iterations, API tokens, webhook endpoints, notifications, audit log. Nothing gets added later without `tenant_id`. · 1 pd · BE
- [ ] **MT-2** `tenant_id` on every table in the MT-1 list, migration baseline, and a **schema lint as a pytest**: reflect `Base.metadata`, assert every table has `tenant_id` **and an RLS policy**, with exemptions only from an explicit config-file whitelist carrying written reasons (decided, S-2). A table with `tenant_id` but no policy is **more** dangerous than one without the column — it looks correct. · 2 pd · BE
- [ ] **MT-3** Tenant enforcement in the database via **PostgreSQL RLS** (decided, S-1a); SQLAlchemy only injects convenience. Three details that make or break it: **`FORCE ROW LEVEL SECURITY`** + app connects as a **non-owner role** (migrations use owner, runtime uses a restricted role); **transaction-scoped `SET LOCAL app.tenant_id`** on the session-begin event, never session-scoped `SET`; `current_setting` **without `missing_ok`** so a missing context raises instead of silently returning zero rows. `SystemRepository` gets its own `BYPASSRLS` connection, audited per call. **No PgBouncer in S1** (decided). · 2 pd · BE
- [ ] **MT-4** Composite indexes with `tenant_id` leading: `(tenant_id, status, updated_at)` · `(tenant_id, assignee_id, status)` · `(tenant_id, space_id, updated_at)` · unique `(tenant_id, number)` · unique `(tenant_id, system, external_id)` · unique `(tenant_id, principal_id, idempotency_key)`. · 0.5 pd · BE
- [ ] **MT-6** Negative suite as a CI gate: cross-tenant read **and** write both fail at the database level; **a token scoped to tenant A gets 404 (not 403) for a tenant B resource** — never leak that the resource exists. · 1.5 pd · QA
- [ ] ⏹ **MT-5** Vector-store isolation — nothing to isolate in S1 (no `knowledge_unit` table). pgvector lives in the same database, so the policy applies to it as an ordinary table. **The rule goes into the `SearchPort` contract now**: when RAG creates those tables they must be same-database, same-policy. No external vector service for convenience.

**Done when:** a deliberately malicious query — including raw SQL — cannot reach
another tenant's row, and CI blocks any commit that regresses that property.

---

## TA · Telemetry adapter seam
**1 pd · 🔒 · weeks 1–2**

- [ ] **TA-1** Declare the `TelemetryAdapter` interface and data contracts (`queryMetrics`, `getTrace`, `sampleRequests`, `listRecentChanges`, `getProviderHealth`, `getCostBreakdown`) plus the `import-linter` contract that keeps gateway clients out of application code. **No implementation, no adapter.** · 1 pd · BE

> ⚠️ **Say this out loud at the week-2 review**: TA has **no demoable output in
> S1**, and it is still un-cuttable. Both are true. 1 pd buys one CI-enforced
> architectural constraint; without it, Phase 2 owes rework in four places.
> TA-2…TA-4 stay in [TODO.md](TODO.md).

---

## AC · Accounts + self-service signup
**8.5 pd · weeks 3–4 · [design §5](markdown/relay-s1-design.md)**

No SSO. Compared to the MVP plan, the primary path flips from invite-only to
**self-service signup** — which turns "who gets into the platform" from a human
decision into a rule. AC-9 *is* that rule, which is why it lands first.

- [ ] **AC-9** Tenant residency and bootstrap: `tenant_email_domain` table (`domain`, `default_role`, `auto_join`), **domain ↔ tenant one-to-one**, and a **deploy-time one-shot initialization** creating the first tenant + first Admin + allowlist. **Not** "first user to register becomes Admin" — that is a real takeover risk on an internal network. The deployment handbook must carry a credentialed init step. · 1 pd · BE
- [ ] **AC-1** Self-service signup: email + password → look up the email domain → **`auto_join=true` grants membership with `default_role` = Member** · `auto_join=false` creates a pending user for Admin approval · **no match refuses registration** with "contact your administrator for an invite" (no pending pool). **Email verification is mandatory** (token TTL 24h, single-use) — unverified self-signup would let anyone in with a fake same-domain address, and the domain is the only residency credential. Rate-limit signups per IP/domain and cool down verification resends. Invitations stay as the secondary path for exceptions. · 2 pd · BE
  > ✅ **F-5 settled: a transactional sending path exists**, so this ships exactly as written. The Admin-approval fallback (`auto_join` defaulting to false) is off the table, and self-service signup keeps its full semantics. Note that F-1's in-app-only decision is about *notifications* — a separate question from verification email, which does get sent.
- [ ] **AC-2** Email + password auth: password policy (length, complexity, **90-day reminder that does not block login**), failed-login lockout, session timeout, unfamiliar-location alert. · 1.5 pd · BE
- [ ] **AC-3** Optional TOTP; **recommend enforcing for Admin** — self-service signup makes the Admin account the only control point, so this matters more here than under invite-only. · 1 pd · BE
- [ ] **AC-4** Three roles (Admin / Member / Guest) checked at the service layer, no fine-grained RBAC. Includes the API-token rules: **Admin creates service tokens and webhook endpoints; Member may self-create personal tokens; Guest may not create tokens**. **Guest sees only L1 explicit grants + L3 — joining a space does not grant L2.** · 1.5 pd · BE
- [ ] **AC-5** Team space, single level, no nesting. Space membership defines the L2 sharing scope. · 1 pd · BE
- [ ] **AC-8** Degradation matrix, the two rows active in S1: notifications degrade to **in-app + email** (in S1 this is the *only* channel, not a fallback); unverified-email login is refused **with a resend link** — always give the next step. · 0.5 pd · BE
- [ ] ⏹ **AC-6** WeCom userid binding — ships with BOT. `identity_binding` is **created but never written** in S1.
- [ ] ⏹ **AC-7** GitHub handle via OAuth — ships before GH starts.

**Done when:** every active path in AC-8 is covered by a test, and a registration
from a non-allowlisted domain cannot create an account by any route.

> ⚠️ **Record the ops risk now**: without SSO, departures and role changes do not
> deactivate accounts — and self-service signup makes this worse, because Admin
> did not hand out the accounts and may not know who is in. A monthly account review
> is owned by **WANGLI** (R-2), with "deactivate in Relay" added to the offboarding
> checklist.
>
> ⚠️ **Known rework, accepted**: with BOT deferred, the WeCom userid spike is
> deferred too, so `identity_binding` is created blind. Expect to alter that
> table once when BOT starts (~0.5 pd). This is the only real rework the reduced
> scope introduces.

---

## LOG · Logs / knowledge authoring
**15 pd · weeks 3–6 · [design §6](markdown/relay-s1-design.md)**

- [ ] **LOG-1** Dual-mode editor (Markdown / plain text) with live split preview — CodeMirror 6. · 3 pd · FE
- [ ] **LOG-2** Full GFM + syntax-highlighted code + Mermaid — markdown-it. · 2 pd · FE
- [ ] **LOG-3** Inline ticket cards via `#331`, resolved within the current tenant. **No permission or no such ticket → degrade to plain text; never leak the title.** · 1 pd · FE
- [ ] **LOG-4** Autosave snapshots, **90-day** version history, line diff, rollback. **Rollback creates a new version** — history is never rewritten. After 90 days: **scheduled cleanup, latest version kept permanently** (decided, S-8). Edit lock instead of real-time collaboration: **TTL 5 min + heartbeat renewal; on timeout another user may take over and unsaved content is saved as a version, never discarded** (decided, S-7). · 3 pd · BE + FE
- [ ] **LOG-5** Attachment and image upload via `BlobPort`: size/type limits, virus-scan hook (may be a no-op), **self-hosted MinIO**, **path contains `tenant_id`**, and access **always permission-checked then served by a 5-minute signed link** — never "the URL is unguessable" (decided, S-11). The blob store is the one thing RLS does not cover — **and since it is self-hosted, attachments are now inside the backup scope too (INT-11)**. · 1 pd · BE
- [ ] **LOG-6** Share levels L0 private / L1 named / L2 space / L3 whole tenant. Evaluation order: **tenant filter (MT, unbypassable) → share level → role**. No L4 external links, no DLP — external links are the largest leak surface and S1 does not open it. · 1.5 pd · BE
- [ ] **LOG-7** Templates: daily report, investigation record, incident retrospective, design doc. *Cut candidate #1.* · 1 pd · FE
- [ ] **LOG-8** Full-text search over log titles + bodies + ticket titles via `SearchPort`, on **PG FTS + pgroonga** (confirmed installable — the zhparser fallback is moot). No separate search service. · 2 pd · BE
- [ ] **LOG-9** 🔒 **"Add to knowledge base" marker — field + checkbox only** (`knowledge_candidate`, `marked_by`, `marked_at`). Counting rule for the acceptance metric: **checked + body ≥ 300 characters** counts automatically, spot-check 10 before acceptance (decided, S-16). · 0.5 pd · BE
  > **The longer BOT and RAG slip, the more this field is worth.** Every log written from day one carries a human judgment about whether it belongs in the knowledge base, so RAG can backfill the entire history instead of running a re-annotation pass. Do not cut it because "it does nothing right now."

**Not in S1:** L4 external links + DLP · real-time collaborative editing ·
`!trace:` / `!metric:` inline syntax (needs gateway integration) · AI-assisted
writing.

---

## TKT · Tickets + board
**13 pd · weeks 3–6 · [design §7](markdown/relay-s1-design.md)**

- [ ] **TKT-1** Ticket entity and fields: type (Bug/Feature/Task), title, description, status, priority P0–P3, assignee, reporter, labels, iteration, PR link, comments — **plus `rev` (monotonic version for optimistic concurrency) and the `ticket_external_ref` table (unique `(tenant_id, system, external_id)`)**. Both are decided and land at create-table time; adding them later is the expensive path (design §8.4). · 1.5 pd · BE
- [ ] **TKT-2** Configurable AI context schema: reserve `trace_id[]` · `provider[]` · `model[]` · `prompt_version` · `deployment` · `error_class` · `eval_run` · `token_cost` · `blast_radius` · `tenant[]` as generic fields (default-on for every tenant), and `gateway_version` / `routing_policy` as `domain_scope`-gated fields (default-on for the first tenant only). **No automatic data source in S1** — but writes are validated by Pydantic against `ai_context_field_config`, **not stored as arbitrary JSON**, because the API can now write these fields. The justification is avoiding later migrations and index rebuilds, nothing else. ⚠️ The first team also builds the gateway, so every request they make looks generic. Test before promoting any field to the generic set: **could a team with no gateway of its own fill it in?** · 2 pd · BE
- [ ] **TKT-3** State machine `Todo → In Progress → In Review → Done` plus `Blocked` and `Won't Fix`; `Blocked` / `Won't Fix` require a reason. Every transition writes `ticket_status_history` **with `actor_type` and `origin`** — which only becomes useful once the API exists. **Status names and semantics are frozen from here**: they are lossy against GitHub's open/closed (GH's problem) and they appear in API responses (a v2-level change to rename). · 1.5 pd · BE
- [ ] **TKT-4** Comments and @mentions. **Changes made through the API raise notifications too** — otherwise the API is a silent back door for editing tickets. · 1 pd · BE
- [ ] **TKT-5** List view with filters (status, assignee, priority, label, iteration, keyword). · 1.5 pd · FE
- [ ] **TKT-6** Board grouped by status with drag-and-drop (vuedraggable). *Cut candidate #2.* · 2.5 pd · FE
- [ ] **TKT-7** "My tickets" view. · 0.5 pd · FE
- [ ] **TKT-8** Labels, iterations, and the PR link field — **a plain link in S1**: no status write-back, no CI/review state. · 1 pd · BE
- [ ] **TKT-9** Detail page, `RL-` numbering **incrementing per tenant**, and permalinks that **reserve a tenant segment**: `https://relay.internal/{tenant_slug}/t/331`. With one tenant the UI may hide the segment, but **the router must support it from day one** — shipping `/t/331` first makes the second tenant a breaking change. **Frozen on release** (decided, S-12). · 1.5 pd · FE

**Not in S1:** Gantt and calendar views · automatic population of domain fields
(no data source).

---

## API · Public ticket API
**7.5 pd · weeks 5–7 · [design §8](markdown/relay-s1-design.md)**

Lets other systems read and write Relay tickets. **First consumer: the AI Gateway
WebUI feedback form** (design §8.8) — and later GH sync. **Contract signed off in
week 1–2, built in weeks 5–7.**

> **The API is not a second implementation.** Web UI and public API go through
> the same application-layer use cases; the API is a contract layer (auth,
> serialization, idempotency, error shape) on top. Otherwise state-machine
> validation, permission checks, and notification triggers drift — and the
> symptom is "changing it in the UI notifies, changing it via API doesn't",
> which nobody spots by reading code. Enforced by `import-linter`: routers must
> not import the repository layer.

- [ ] **API-1** Token auth and tenancy: opaque tokens with type prefixes (`rly_u_` personal / `rly_s_` service), **hash-only storage**, plaintext shown once. **Tenant is derived from the token and never read from the request** — a `tenant_id` in a request body or query is a 400. Four coarse scopes: `tickets:read` / `tickets:write` / `comments:write` / `meta:read`. **Default 365-day expiry** with a 14-day reminder to the creator; nameable, revocable, `last_used_at` tracked, create/revoke audited. · 1.5 pd · BE
- [ ] **API-2** Resource endpoints under `/api/v1`: `POST|GET /tickets` (filters + **cursor** pagination, `updated_since`), `GET|PATCH /tickets/{key}`, `POST /tickets/{key}/transitions`, `GET|POST /tickets/{key}/comments`, `GET /tickets/{key}/history`, and `/meta/{labels,iterations,users,ticket-fields}` — **`/meta/users` returns id and display name only, never emails**. Reserve the `/logs/*` and `/search` namespaces without implementing them, so the pagination and error conventions don't diverge later. · 1.5 pd · BE
- [ ] **API-6** Feedback-path adaptation for the first consumer (design §8.8): a **`submitter`** field (`name` / `email?` / `external_id?`) recorded on the ticket and shown as "submitted by X via the gateway WebUI", plus a fixed source label. **`submitter` is not `reporter`** — reporter stays the service principal (S-10), because gateway users are not Relay accounts and should not have to be. `submitter` is display and traceability only: **no permission effect, excluded from every people-metric**. Also: **screenshots do not go through the API in S1** (no attachment endpoint — adding one would expose MinIO signing and quota to external consumers, and `BlobPort` has no quota concept yet); the WebUI stores them and pastes URLs into the description. Write that into the integration doc or someone asks on day one. · 0.5 pd · BE
  > **Treat feedback bodies as untrusted input.** Free-form human text may carry secrets, customer data, or injection attempts, and S1 has no DLP: warn on the WebUI form, cap body length, and validate `ai_context` against `ai_context_field_config` (never arbitrary JSON).
- [ ] **API-3** Loop-safety and concurrency: `Idempotency-Key` on POST (dedupe by `(tenant_id, principal_id, key)` for 24h, replay returns the first result), `If-Match: <rev>` required on PATCH with **409 + current `rev`** on mismatch, and `external_ref` business-level dedupe (a repeated create is refused and returns the existing ticket). Writes record `actor_type` / `origin`; **service tokens surface `reporter` as the machine principal name** (e.g. `alertmanager`), which means people-metrics must exclude them. · 1 pd · BE
- [ ] **API-4** Outbound webhooks: `ticket.created` · `ticket.updated` · `ticket.status_changed` · `ticket.comment_created`. Payload carries `event_id` (consumer dedupe), `actor`, and **`rev` so consumers can drop out-of-order events** — delivery is at-least-once and unordered. `X-Relay-Signature: sha256=HMAC(secret, timestamp + "." + body)` + `X-Relay-Timestamp`, per-endpoint rotatable secrets. Retry with exponential backoff (1m/5m/30m/2h/6h) into a replayable dead letter, **queued on PG + `FOR UPDATE SKIP LOCKED`** — no Redis/MQ. **Destination addresses must reject private, loopback, and cloud-metadata targets, validating the resolved IP as well** (DNS rebinding), no domain allowlist (decided, S-13). · 2 pd · BE
- [ ] **API-5** Contract discipline and tests. ⚠️ **The direction of truth is inverted by FastAPI**: the spec is generated from code, so "spec disagrees with implementation" is vacuously false and would be a门禁 that never fires. Instead: commit the generated `openapi.json`, have CI regenerate and **diff it — any difference fails until a human updates the snapshot in the PR**, so every contract change is visible in review; deleted fields / changed types / changed enum semantics go to v2, never into v1. Frontend TS types are generated from the same snapshot, so mismatches break the frontend build rather than production. Also **install a global exception handler emitting RFC 9457 `problem+json` for both `HTTPException` and Pydantic `RequestValidationError`** — FastAPI's default `{"detail": …}` would otherwise give the API two error formats, which is the first thing an integrator hits. Keep 422 for validation failures, normalize only the body. Rate limits start loose with full instrumentation: **read 600 req/min, write 120 req/min per token**, tightened after two weeks of real usage. · 1 pd · BE + QA

**Done when:** the gateway WebUI submits a real piece of feedback and it lands as
a ticket carrying `submitter` and the source label; an external system does
create → list → update → transition → comment with nothing but a token; the same `Idempotency-Key` replayed 3× yields
**one** ticket; concurrent PATCHes produce exactly one winner and one 409; a
webhook survives a consumer returning 500 and lands in a replayable dead letter;
a cross-tenant token gets 404; every error response is `problem+json`.

---

## NT · Notifications
**1.5 pd · weeks 5–6**

**Decided (F-1): in-app only.** No email notifications in S1 — **a scope choice, not a capability limit**: the sending path exists (F-5).

- [ ] **NT-1** In-app notifications for assignment, @mention, and status change — **including changes made through the API**. · 1 pd · BE
- [ ] **NT-2** **5-minute aggregation window** and the multi-channel `notification_delivery` state machine, built now so adding email or WeCom later is a new channel rather than a rewrite. `MailPort` and `IMPort` are declared with no-op implementations. Tiered routing, quiet hours, and subscription rules are out. · 0.5 pd · BE

> **In-app-only has one consequence worth stating plainly**: in-app notifications
> require people to *come to the platform* to see them. So S1's adoption shape can
> only be "the team opens Relay daily", not "Relay finds people". Two things follow:
> Jira decommission stays out of S1's gates (a ticket system with no push should not
> be the only entry point), and **the "My tickets" view plus an unread count carry
> more weight than they otherwise would** — in S1 they are the entire reach surface.
>
> ✅ **But that consequence is reversible, and cheaply.** F-5 confirmed a sending
> path exists, `MailPort` is declared, and the aggregation + delivery state machine
> is already built — **turning on email notifications is ~0.5 pd** ([NT-3](#optional--not-in-the-baseline)).
> So if the week-6 dual-track feedback is "I never see notifications", the fix is
> to switch email on, **not to wait for BOT**. Put this on the dual-track watch
> list, or the team will endure poor reach while waiting four more weeks for a bot.

---

## INT · Integration, testing, rollout
**4 pd · weeks 1–7**

- [ ] **INT-1** CI pipeline with the MT-6 cross-tenant suite and MT-2 schema lint **as blocking gates**, plus the `import-linter` architecture contracts. · 1 pd · QA
- [ ] **INT-11** 🔒 **Automated backup + one real restore drill — covering PostgreSQL *and* MinIO** — before the team starts writing real logs. Owner: **WANGLI** (R-1). Self-hosting bought architectural simplicity; backups are the price. Tickets still have Jira as a fallback (S1 does not decommission it) — **logs and attachments have none from day one**. Suggested values unless the owner decides otherwise: PG daily full + WAL archiving (30d / 7d), MinIO daily incremental to a separate host (30d), drill quarterly thereafter. **Restore both together and open a log that contains an image** — restoring only PG yields intact prose with every picture broken, and a half-restore that never shows up in a drill shows up during a real incident instead. · 0.5 pd · BE
- [ ] **INT-5** End-to-end suite over the S1 critical flow: **signup → email verification → login → log → ticket → API write → notification**. · 1 pd · QA
- [ ] **INT-6** Dual-track rollout and operating guidance for the team. · 1 pd · QA
- [ ] **INT-8** Minimal acceptance dashboard, **with denominators pinned in the dashboard rather than agreed during the review**: weekly-active-creator share uses activated accounts over a calendar week, and **service-token principals are excluded from every people-metric** — otherwise one alerting script inflates the numbers. · 0.5 pd · BE + QA

---

## Exit criteria

### Hard gates

- [ ] Cross-tenant read/write = **0**, including the API and webhook paths; CI gate green; one penetration spot-check before rollout
- [ ] Schema lint **blocks** any new table lacking `tenant_id` **or** an RLS policy
- [ ] Idempotent replay produces **zero** duplicate tickets
- [ ] Concurrent writes produce **zero** silent overwrites (`rev` mismatch must 409)
- [ ] OpenAPI snapshot gate blocks a changed contract until the snapshot is updated in the PR
- [ ] **Backup restored at least once, PG and MinIO together** — otherwise real log writing does not start

### Functional exit

- [ ] Signup → verification → login → log → ticket → **in-app notification** passes end to end
- [ ] Logs: dual mode · 90-day versioning and rollback · L0–L3 sharing · full-text search · knowledge-base marker
- [ ] Tickets: fields · 6-state machine · list/board/my-tickets · detail + permalink, carrying real work
- [ ] API: the criteria above, **with the gateway WebUI feedback form live end to end** — a real submission lands as a ticket with `submitter` and source label, and a repeated submission does not create a second one
- [ ] Team begins **dual-track use** (Relay alongside Jira)
- [ ] **BOT's schedule is fixed at the S1 exit review** — committed for **week 7** (R-3). This review must actually happen; it is the only mechanism that pulls BOT out of "later"

### Explicitly not S1 gates

Jira decommission · WeCom binding coverage > 90% · draft confirmation rate > 60%
— all three depend on BOT and are judged with it.

> ⚠️ **S1 contains no AI touchpoint at all.** The MVP's entire AI value sat on
> BOT-3's AI-drafted ticket. Two consequences to keep saying out loud: present S1
> as "the workbench first", not as "Relay has launched" — a first impression spent
> on "it's a ticket system" is not recoverable; and fix BOT's schedule at the exit
> review, because the smoother S1 lands, the stronger the "this is enough"
> inertia gets.

---

## If S1 needs compression

S1 is already the reduced scope, so this list is short and should not be needed.
Written down so there is a pre-agreed order instead of an argument under pressure.

| Order | Cut | Impact |
|---:|---|---|
| 1 | **LOG-7** templates | Free-form authoring still works; minor experience loss |
| 2 | **TKT-6** board drag-and-drop (keep list + status dropdown) | Real experience loss; PM will object |
| 3 | **API-4** webhooks — viable now that the first consumer polls (F-6 ① recommends the WebUI poll `GET /tickets/{key}`) | Consumers must poll; **cut this and GH's Phase 2 reconciliation loses its rehearsal**, so cut it last among the three |
| 4 | **AC-3** TOTP | Acceptable only if there are 1–2 Admins and they accept the risk in writing |
| **Un-cuttable** | **MT (all)** | Retrofitting is a multi-week refactor |
| **Un-cuttable** | **TA-1** | 1 pd for a CI-enforced constraint; without it Phase 2 owes rework in four places |
| **Un-cuttable** | **AC-9** | It *is* the signup rule; without it accounts have no tenant |
| **Un-cuttable** | **The three §8.4 fields** | `rev` / `actor_type` / `external_ref` are cheap now and expensive forever after |
| **Un-cuttable** | **LOG-9 marker** | 0.5 pd that removes a Phase 2 startup blocker |
| **Un-cuttable** | **INT-11 restore drill** | Logs have no fallback copy |

---

## Settled, and what's left

**Settled this round** — every one of these came with a consequence, listed so
nobody re-derives it during implementation:

| # | Decision | Consequence |
|---|---|---|
| **F-1** | Notifications are **in-app only** | NT 2 → 1.5 pd · `MailPort` declared only · "My tickets" + unread count are the whole reach surface. **A scope choice, not a capability limit** — email is ~0.5 pd away ([NT-3](#optional--not-in-the-baseline)) |
| **F-5** | **A transactional sending path exists** | AC-1 ships as written, mandatory email verification intact; the Admin-approval fallback is off the table |
| **F-2** | **pgroonga installs fine** | zhparser fallback moot; LOG-8 goes straight at pgroonga |
| **F-3** | First API consumer is the **gateway WebUI feedback form** | +0.5 pd (API-6): `submitter` field, source label, no attachment endpoint, untrusted-input handling → three product details left in [F-6](#open-items) |
| **F-4** | Blob store is **self-hosted MinIO** | Backup scope becomes **two** systems; INT-11's drill must restore both |
| **R-1** | **WANGLI** owns PG + MinIO ops and backups | Drill timing already fixed: before the team writes real logs, restoring both together |
| **R-2** | **WANGLI** owns account deactivation while there is no SSO | Monthly account review + "deactivate in Relay" added to the offboarding checklist. PRD §7.2 item 6 closes |
| **R-3** | **BOT schedule lands in week 7** | Matches the recommendation — and makes the S1 exit review load-bearing |

## Open items

One left, and it blocks nothing.

- **F-6 — three product details on the feedback loop.** Needed by **week 5**
  (before API-6). Recommendations: ① the WebUI **does** show progress, by polling
  `GET /tickets/{key}`, status and last-update only, **never internal comments**;
  ② the **WebUI** notifies the submitter on close, not Relay — Relay reaching
  gateway users directly would make it an external-facing system, which is Phase 4
  territory; ③ feedback defaults to **type=Bug, priority=P2**, triaged by the
  assignee — **do not let submitters pick priority**, or everything is P0 within a
  week.

> ⚠️ **The risk that decides whether this integration is worth anything** is not
> technical: **feedback submitted and never answered means nobody submits twice.**
> F-6 ① and ② are what close that loop.

## Optional — not in the baseline

- [ ] **NT-3** Turn on **email notifications** — ~0.5 pd, not counted in the 57.5. Everything it needs already exists (F-5's sending path, declared `MailPort`, aggregation window, multi-channel delivery state machine). **This is the designated escape hatch** if week-6 dual-track feedback says reach is too low: cheaper and four weeks sooner than waiting for BOT's WeCom channel.

---

## Deferred out of S1

Interface seams stay; full specs live in [TODO.md](TODO.md).

| Epic / task | Seam kept in S1 |
|---|---|
| **BOT** (WeCom bot) | `identity_binding` created-not-written · `IMPort` declared with no-op · notification channel model already multi-channel · `POST /tickets` use case reusable for bot-created tickets |
| **AC-6 / AC-7** identity binding | Tables and uniqueness constraints designed; AC-7 must land before GH starts |
| **TA-2…TA-4** adapter implementation | `TelemetryAdapter` interface + architecture guard (TA-1) |
| **MT-5** vector isolation | Rule written into the `SearchPort` contract: same database, same policy |
| ⏸ **GH** GitHub sync | `rev` · `actor_type`/`origin` · `ticket_external_ref` · webhook delivery metrics — the three loop-prevention anchors and the reconciliation rehearsal |
| **Email notifications** | `MailPort` declared with a no-op; sending path confirmed available (F-5); aggregation and multi-channel delivery already built — see [NT-3](#optional--not-in-the-baseline) |
| ⏸ **RAG / SEED** | `knowledge_candidate` marker with a counting rule already defined |
| **INT-2/3/4**, **INT-7**, **INT-9**, **INT-10** | Gateway routing, sync pilot, model A/B, Jira cutover, binding drive, AI budget alarm — all tied to BOT/GH/RAG. **S1 makes no LLM calls, so there is no AI spend to alarm on yet** |
