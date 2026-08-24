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
| **Scope** | MT · TA-1 · AC · LOG · TKT · **API** · **WEB** · NT · INT subset. **62 pd ≈ 12.4 person-weeks** (57.5 + [WEB](#web--the-web-uis-http-layer) 4, a scope correction rather than a re-estimate, + 0.5 for S-25's containerised contract test) |
| **First API consumer** | **AI Gateway WebUI feedback form** — users submit feedback, it lands as a Relay ticket (design §8.8) |
| **Duration** | ≈ 7 calendar weeks at 1.7 pw/week (2 BE · 1 FE · 0.5 QA; **the AI role has no S1 work — see [Staffing](#staffing-note)**) |
| **Exit state** | Dual-track use (Relay alongside Jira) + at least one external system integrated over the API |
| **Where it stands** | **Everything code can deliver is delivered** — MT · TA-1 · AC · LOG · TKT · WEB · API-1…6 · NT · INT-5/6/8, plus the frontend (LOG-1/2/3/7 · TKT-5/6/7/9). What is left needs a **person**: INT-11's restore drill on the real instances, the penetration spot-check, the gateway team connecting their form, and the team choosing to use it. See [Exit criteria](#exit-criteria), where a box is ticked only when something mechanical holds it |
| **Not the exit state** | Jira decommission — decided **not** an S1 gate (§12.1 S-9); it waits for WeCom notifications, which ship with BOT |
| **Decisions** | All design questions are **settled**, including S-19…S-25 from the owner-action rounds — see [relay-s1-design.md §12.1](markdown/relay-s1-design.md#121-决策记录全部采纳建议). Decided values are inlined in the tasks below so nobody has to cross-reference while implementing |
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
| [LOG](#log--logs--knowledge-authoring) | Logs / knowledge authoring | 15.5 pd | 3–6 |
| [TKT](#tkt--tickets--board) | Tickets + board | 13 pd | 3–6 |
| [API](#api--public-ticket-api) | Public ticket API | 7.5 pd | 5–7 |
| [WEB](#web--the-web-uis-http-layer) | **The Web UI's HTTP layer** (S-24, net new) | 4 pd | 5–6 |
| [NT](#nt--notifications) | Notifications (**in-app only**) | 1.5 pd | 5–6 |
| [INT](#int--integration-testing-rollout) | Integration, testing, rollout | 4 pd | 1–7 |
| | **Total** | **62 pd** | ≈ 12.4 pw |

**Against the original MVP (68.5 pd), net −6.5:**

| | Detail | Subtotal |
|---|---|---:|
| Removed | BOT 10 · TA-2…TA-4 4 · INT items that ship with BOT / Jira cutover 4 · AC-6 + AC-7 2.5 · MT-5 1 | **−21.5** |
| Added | API 7.5 · **WEB 4** · NT 1.5 · AC-9 1 · INT-11 0.5 · **S-25 contract test 0.5** | **+15** |

**API is net-new work, not budget freed up by dropping BOT.**

⚠️ **WEB is a scope correction.** The Web UI's own HTTP layer was in no task, and
all 13 pd of frontend work depends on it. Found at the right time — the frontend
had not started — so it is a gap being filled rather than rework. S-24.

### Week map

| Week | Focus |
|---|---|
| 1–2 | ✅ **MT exclusively** (schema lint + RLS policy check wired into CI) · ✅ TA-1 · ✅ **API contract sign-off** — §8 signed off, wire values frozen from here (see [Frozen contract](#frozen-contract)) · ✅ pgroonga 4.0.8 provisioned (superuser step, `scripts/bootstrap_extensions.sql`; CI image switched to `groonga/pgroonga`) · pgvector deferred to the RAG migration — MT-5 has nothing to isolate in S1 |
| 3–4 | ✅ **AC complete** (signup → login → roles → space; AC-6/AC-7 stay deferred) · ✅ **TKT backend complete** (TKT-1/2/3/4/8 — TKT-5/6/7/9 are the FE views) · LOG begins |
| 5–6 | ✅ **LOG backend complete** (LOG-4/6/8/9) · ✅ **NT complete** (pulled forward — TKT-4 and the assignment/status events depend on it, and building it after would have meant reworking `TicketService`) · ✅ **WEB complete** (WEB-1…4 — the frontend's HTTP layer, S-24) · ✅ **API-1/2/3** · ⏳ **INT-11 restore drill** — scripts delivered, the drill itself needs the real instances (WANGLI) |
| 7 | ✅ Frontend (LOG-1/2/3/7 · TKT-5/6/7/9) · ✅ API-4/5/6 · ✅ **LOG-5's MinIO adapter (S-25 — written blind, verified against a containerised MinIO)** · ✅ INT-5 end-to-end · ✅ INT-6 rollout guide · ✅ INT-8 dashboard · ✅ MT-6's token half · **dual-track use begins** (the team's move, not a task) |

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

- [x] **MT-1** Definitive entity list, every business entity audited for tenancy — tenants, users, identity bindings, spaces, logs, log versions, attachments, tickets, comments, labels, iterations, API tokens, webhook endpoints, notifications, audit log. Nothing gets added later without `tenant_id`. · 1 pd · BE
- [x] **MT-2** `tenant_id` on every table in the MT-1 list, migration baseline, and a **schema lint as a pytest**: reflect `Base.metadata`, assert every table has `tenant_id` **and an RLS policy**, with exemptions only from an explicit config-file whitelist carrying written reasons (decided, S-2). A table with `tenant_id` but no policy is **more** dangerous than one without the column — it looks correct. · 2 pd · BE
- [x] **MT-3** Tenant enforcement in the database via **PostgreSQL RLS** (decided, S-1a); SQLAlchemy only injects convenience. Three details that make or break it: **`FORCE ROW LEVEL SECURITY`** + app connects as a **non-owner role** (migrations use owner, runtime uses a restricted role); **transaction-scoped `SET LOCAL app.tenant_id`** on the session-begin event, never session-scoped `SET`; `current_setting` **without `missing_ok`** so a missing context raises instead of silently returning zero rows. `SystemRepository` gets its own `BYPASSRLS` connection, audited per call. **No PgBouncer in S1** (decided). · 2 pd · BE
- [x] **MT-4** Composite indexes with `tenant_id` leading: `(tenant_id, status, updated_at)` · `(tenant_id, assignee_id, status)` · `(tenant_id, space_id, updated_at)` · unique `(tenant_id, number)` · unique `(tenant_id, system, external_id)` · unique `(tenant_id, principal_id, idempotency_key)`. · 0.5 pd · BE
- [x] **MT-6** Negative suite as a CI gate: cross-tenant read **and** write both fail at the database level; **a token scoped to tenant A gets 404 (not 403) for a tenant B resource** — never leak that the resource exists. · 1.5 pd · QA
  > **Database half done** (`tests/test_cross_tenant.py`, blocking in CI): cross-tenant read and write both fail, raw SQL included, and referential integrity is covered too — see the deviation note below. **The 404-not-403 half waits on API-1**, since it needs a token to scope. Do not close MT-6 until it lands.
- [x] ⏹ **MT-5** Vector-store isolation — nothing to isolate in S1 (no `knowledge_unit` table). pgvector lives in the same database, so the policy applies to it as an ordinary table. **The rule goes into the `SearchPort` contract now**: when RAG creates those tables they must be same-database, same-policy. No external vector service for convenience.
  > S1's whole obligation here — writing the rule down where RAG will read it — is done: `relay/ports/search.py`.

**Done when:** a deliberately malicious query — including raw SQL — cannot reach
another tenant's row, and CI blocks any commit that regresses that property.

> ✅ **Ratified as S-18 and folded back into the design** — §4.2 (why the keys must be
> composite), §2.4 + D-0 (**PostgreSQL ≥ 15**), §12.1. Writeup:
> [relay-s1-fk-deviation.md](markdown/relay-s1-fk-deviation.md).** RLS covers cross-tenant *reads* completely, but PostgreSQL
> runs foreign-key checks with policies bypassed. With single-column FKs, tenant A could
> insert a row referencing tenant B's user (nothing leaks on read — the join finds
> nothing — so a read-only negative suite calls it clean), and B deleting that user would
> cascade into A's rows: a cross-tenant **write**, by a tenant who never had permission
> and would see no sign of it. Every FK is therefore composite `(id, tenant_id)`
> (`relay.infra.db.base.tenant_fk`). Same economics as the §8.4 fields — trivial at
> create-table time, a migration of all 32 foreign keys afterwards.

---

## TA · Telemetry adapter seam
**1 pd · 🔒 · weeks 1–2**

- [x] **TA-1** Declare the `TelemetryAdapter` interface and data contracts (`queryMetrics`, `getTrace`, `sampleRequests`, `listRecentChanges`, `getProviderHealth`, `getCostBreakdown`) plus the `import-linter` contract that keeps gateway clients out of application code. **No implementation, no adapter.** · 1 pd · BE

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

- [x] **AC-9** Tenant residency and bootstrap: `tenant_email_domain` table (`domain`, `default_role`, `auto_join`), **domain ↔ tenant one-to-one**, and a **deploy-time one-shot initialization** creating the first tenant + first Admin + allowlist. **Not** "first user to register becomes Admin" — that is a real takeover risk on an internal network. The deployment handbook must carry a credentialed init step. · 1 pd · BE
- [x] **AC-1** Self-service signup: email + password → look up the email domain → **`auto_join=true` grants membership with `default_role` = Member** · `auto_join=false` creates a pending user for Admin approval · **no match refuses registration** with "contact your administrator for an invite" (no pending pool). **Email verification is mandatory** (token TTL 24h, single-use) — unverified self-signup would let anyone in with a fake same-domain address, and the domain is the only residency credential. Rate-limit signups per IP/domain and cool down verification resends. Invitations stay as the secondary path for exceptions. · 2 pd · BE
  > **The secondary path shipped too** (`relay/app/accounts/invitations.py`), which the first pass left as a table with nothing writing it. Inviting is `USER_MANAGE` — a Member who could invite is a Member who can add anyone to the tenant, which is the residency rule undone from the inside. Accepting needs no verification round trip: holding the token *is* the proof of address, so an invited account starts active and logs in immediately. An invitation may carry `ADMIN`, which is the in-product way to get a second Admin — bootstrap refuses to add one and AC-4 refuses to remove the last. See the note under **Done when** for how this squares with "no account from a non-allowlisted domain".
  > ✅ **F-5 settled: a transactional sending path exists**, so this ships exactly as written. The Admin-approval fallback (`auto_join` defaulting to false) is off the table, and self-service signup keeps its full semantics. Note that F-1's in-app-only decision is about *notifications* — a separate question from verification email, which does get sent.
- [x] **AC-2** Email + password auth: password policy (length, complexity, **90-day reminder that does not block login**), failed-login lockout, session timeout, unfamiliar-location alert. · 1.5 pd · BE
  > Two narrowings worth recording rather than leaving in a commit message. The **unfamiliar-location** alert ships as unfamiliar *network* (/24, /48): real geolocation needs a MaxMind-style database, a licence and a refresh job, none of which fit 1.5 pd — both failure modes of the weaker signal are documented in `relay/domain/networks.py`. It goes out by **mail, not in-app**, because an in-app security alert is visible to whoever is holding the session, including the person it warns about. And **lockout is time-boxed**, not permanent: a permanent lock hands anyone who knows a colleague's address the power to keep them out.
- [x] **AC-3** Optional TOTP; **recommend enforcing for Admin** — self-service signup makes the Admin account the only control point, so this matters more here than under invite-only. · 1 pd · BE
  > "Recommend enforcing for Admin" ships as `admin_mfa_gap()` — a checkable list of Admins without a second factor, not a hard gate. A gate would lock out the Admin standing there the moment it shipped, and there is no second Admin to let them back in (see AC-4's last-admin rule).
- [x] **AC-4** Three roles (Admin / Member / Guest) checked at the service layer, no fine-grained RBAC. Includes the API-token rules: **Admin creates service tokens and webhook endpoints; Member may self-create personal tokens; Guest may not create tokens**. **Guest sees only L1 explicit grants + L3 — joining a space does not grant L2.** · 1.5 pd · BE
  > Matrix in `relay/domain/permissions.py`, enforced by `relay.app.authz.require`. Three rules the design does not state, decided here and each pinned by a test: **a personal token is always the creator's own** — an Admin minting one bound to a colleague is impersonation, and every audit row it produced would name the wrong person (an Admin who needs machine access creates a *service* token, which is attributable); **a token's authority is the intersection of its scopes and its owner's current role**, re-read per request, so a demotion is not survived by a credential minted before it; and **a tenant cannot be left with no active Admin**, because bootstrap refuses to add a second one and there would be no way back in. ⚠️ **Correction made while implementing LOG-6**: an earlier pass read §5.4's "查看内容: 按分享级别" as meaning an Admin is *not* a whole-tenant reader. §6.3 is more specific and wins — it defines **L0 as 仅作者 + Admin**, so an Admin does read a colleague's private log, and since L0 is the most restrictive level that means every level. The capability table now says so outright rather than in a special case, and the tests were inverted to match.
- [x] **AC-5** Team space, single level, no nesting. Space membership defines the L2 sharing scope. · 1 pd · BE
  > Space **creation** is an Admin power (not in §5.4's matrix, decided here): creating a space and adding people to it *is* granting L2 read access, so it belongs with the other access-granting rows rather than with "edit your own log". Running one is not — a space owner manages that space's membership, which is a per-object check rather than a fourth role. S-6 is enforced in `SpaceService.grants_space_read`, which checks the **role before the membership** so that a Guest in the space still gets nothing; LOG-6 composes that call rather than reimplementing the rule.
- [x] **AC-8** Degradation matrix, the two rows active in S1: notifications are **in-app only** (in S1 this is the *only* channel, not a fallback); unverified-email login is refused **with a resend link** — always give the next step. · 0.5 pd · BE
  > ⚠️ **Wording corrected, not the decision**: this line used to read "in-app **+ email**", which contradicts F-1 as recorded in design §5.5 and §9 — and contradicted §NT and §Settled further down this same file. F-1 is in-app only; the email *channel* stays declared in the enum so NT-3 is a switch, not a rewrite. Register lives in `relay/domain/degradation.py`, with all four §5.5 rows: the two active ones carry the required next step (asserted, not reviewed), and the two deferred ones name the epic that implements them, so BOT and GH implement the decision instead of inventing one.
- [x] ⏹ **AC-6** WeCom userid binding — ships with BOT. `identity_binding` is **created but never written** in S1.
  > S1's whole obligation — the table, its uniqueness constraints and the FK shape — is done (`relay/infra/db/models/account.py`). Nothing writes it, which is the specification.
- [ ] ⏹ **AC-7** GitHub handle via OAuth — ships before GH starts.
  > Left open deliberately: AC-6's table serves this too, so there is no S1 artifact of its own, and ticking it would claim an OAuth flow that does not exist.

**Done when:** every active path in AC-8 is covered by a test, and a registration
from a non-allowlisted domain cannot create an account by any route.

> ✅ **Both hold** (`tests/test_degradation.py`, `tests/test_invitations.py`). One
> reading has to be stated, because the two halves of AC-1 collide if it is not:
> **"by any route" means no *self-service* route.** An invitation is deliberately
> **not** checked against the allowlist — the refusal AC-1 gives an unknown domain
> says "contact your administrator for an invite", and a route that then refused
> the invite for the same reason would be a dead end wearing a next step. What is
> closed is the self-service path, which is what makes residency a rule; an
> invitation is an Admin naming one person, which is the judgment the allowlist
> exists to avoid having to make at scale rather than to forbid.
> `test_the_allowlist_still_refuses_the_same_address_by_signup` pins both halves
> at once.

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

- [x] **LOG-1** Dual-mode editor (Markdown / plain text) with live split preview — CodeMirror 6. · 3 pd · FE
- [x] **LOG-2** Full GFM + syntax-highlighted code + Mermaid — markdown-it. · 2 pd · FE
- [x] **LOG-3** Inline ticket cards via `#331`, resolved within the current tenant. **No permission or no such ticket → degrade to plain text; never leak the title.** · 1 pd · FE
- [x] **LOG-4** Autosave snapshots, **90-day** version history, line diff, rollback. **Rollback creates a new version** — history is never rewritten. After 90 days: **scheduled cleanup, latest version kept permanently** (decided, S-8). Edit lock instead of real-time collaboration: **TTL 5 min + heartbeat renewal; on timeout another user may take over and unsaved content is saved as a version, never discarded** (decided, S-7). · 3 pd · BE + FE
  > **Autosave writes a version**, and that is what makes S-7's "unsaved content is saved as a version, never discarded" true by construction rather than by a rescue step at takeover time — by the time a lock lapses the previous editor's work already *is* version N, so `acquire()` just reports the number for the UI to show. Identical consecutive saves are skipped, or an idle editor mints a version a second. Rollback **appends** (§6.2): no code path deletes or edits a version row, and `rolled_back_from` distinguishes a rollback from somebody retyping an old draft. The 90-day purge keeps the latest version of every log permanently — computed from `MAX(version_no)` rather than trusting `log.current_version`, because if the two ever disagreed the counter would delete the row the log points at. **Scheduling it was blocked on S-20 and is now done**: the purge needs `USER_MANAGE`, a scheduler has no session, so it runs as a **system identity** (`ActorType.SYSTEM` + `Origin.SYSTEM` + a short capability list) via `scripts/purge_log_versions.py` — one tenant per transaction, under RLS, audited as `system` rather than as whichever Admin's account was borrowed. It is still not reachable from a request: `system_principal` refuses any origin but `SYSTEM`, because a cleanup triggered by a request eventually runs inside somebody's page load.
- [x] **LOG-5** Attachment and image upload via `BlobPort`: size/type limits, virus-scan hook (may be a no-op), **self-hosted MinIO**, **path contains `tenant_id`**, and access **always permission-checked then served by a 5-minute signed link** — never "the URL is unguessable" (decided, S-11). The blob store is the one thing RLS does not cover — **and since it is self-hosted, attachments are now inside the backup scope too (INT-11)**. · 1.5 pd · BE
  > **Application layer done, MinIO carrier outstanding.** Shipped: `AttachmentService` with the size and MIME limits (an allowlist, not a blocklist), the virus-scan hook called with a `skipped` result rather than a lie about `clean`, tenant-prefixed keys defined once in `relay.ports.blob`, and **permission-check-then-5-minute-signed-link** with the check strictly first — a signature stops a link outliving the check, it is not the check. The carrier behind it is `FilesystemBlobStore`, same key layout, used by dev and tests. **Not done: the MinIO/S3 adapter.**
  >
  > **S-25 unblocks it: write the adapter blind.** The owner's answer to O-5 is "build it on your own understanding and file bugs against the real instance", so it no longer waits on instance details. Three deliverables, not one — the last two are what make writing blind acceptable: ① `MinioBlobStore` against standard S3 semantics (path-style addressing, presigned GET, same key layout, carrier chosen by config with `filesystem` as the default); ② a **containerised contract test** — a real `minio/minio` running the same blob contract, which is the only available way to verify the adapter without the production instance; ③ `scripts/check_blob_store.py`, a put → presign → GET → delete round trip ops runs against the real instance on day one. That third one is the difference between a deviation showing up as a failed command and showing up as every image in the product broken. **+0.5 pd over the original estimate** (1 → 1.5), which is the price of the container test and is the cheaper end of the trade: without it the adapter first meets a real MinIO the night before launch.
  >
  > **What blind writing cannot get right is deployment shape**, so four items go in the deploy checklist rather than the code: the **presigned URL's host** (an internal endpoint signed into a link the browser cannot reach yields broken images *and an empty application log*, because the browser talks straight to object storage), **path-style vs virtual-host addressing**, **clock skew** against a 5-minute expiry, and **the bucket being private** — anonymous read makes S-11's whole check-then-sign sequence decorative. Two more are semantics and get pinned in the adapter: the size limit stays a **streaming** decision (`BlobTooLarge` must not degrade into "accept the whole file, then refuse it"), and **`/blobs/{key}` disappears with the carrier** — it depends on `verify`/`open`, which only the filesystem store has, so the switch has to be visible at wiring time instead of failing on the first download.
  >
  > INT-11's restore drill still needs the real instance and still has to restore PG and MinIO *together* — S-25 removes a code blocker, not that one.
- [x] **LOG-6** Share levels L0 private / L1 named / L2 space / L3 whole tenant. Evaluation order: **tenant filter (MT, unbypassable) → share level → role**. No L4 external links, no DLP — external links are the largest leak surface and S1 does not open it. · 1.5 pd · BE
  > `relay/app/logs/sharing.py` holds the rule as a pure function and `relay/infra/db/visibility.py` mirrors it in SQL for the list and for search — two consumers, one rule, and `test_logs.py` cross-checks both implementations for every (log, reader) pair, because the drift between them is a leak or an invisible document and neither shows up in a diff. Default share level on create is **L0**: a draft that starts visible is a draft somebody reads mid-thought. A log the reader may not see raises `NotFound`, not `PermissionDenied` — MT-6's 404-not-403 reasoning applied inside a tenant. ⚠️ **§6.3 vs §5.4 resolved in favour of §6.3** and confirmed as **S-19**: L0 is "仅作者 + Admin", so an Admin reads private logs and therefore every level — **and the read now leaves a trail**. `relay/app/logs/read_audit.py` writes one `log.read_by_admin` row for a read that *only the role* made possible, judged by re-running the same rule with the reader demoted to Member: L3, their own logs, and an L1 they were explicitly granted record nothing, because those are reads any colleague could make. A list or a search writes **one** row naming what it surfaced rather than one per row. Reads therefore commit — narrowly, only when there is something to write — which is the honest cost of auditing reads and the reason the check is that specific: a trail that logs ordinary browsing buries the twenty rows somebody needs.
- [x] **LOG-7** Templates: daily report, investigation record, incident retrospective, design doc. *Cut candidate #1.* · 1 pd · FE
- [x] **LOG-8** Full-text search over log titles + bodies + ticket titles via `SearchPort`, on **PG FTS + pgroonga** (confirmed installable — the zhparser fallback is moot). No separate search service. · 2 pd · BE
  > pgroonga over `log(title, body)` and `ticket(title)` — descriptions stay out of the index (§6.4 lists titles for tickets, and descriptions are largely stack traces, i.e. double the index for the worst signal). Uses `&@`, **not** `&@~`: the second operator accepts pgroonga query syntax, so a stray bracket from a user is either an error or a silently different search. **Search applies the share-level filter, not just the tenant filter** — RLS knows nothing about share levels, so without it pgroonga would happily match a colleague's private draft; that is the one test in `test_search.py` that would have been a leak. Ranked by **recency, not relevance**: `pgroonga_score` returns 0 for an index built without a scorer, configuring one is real work at this corpus size, and "what did we write about this lately" is what people want from a log search anyway. The score field is still populated so a future scorer is an ORDER BY change.
- [x] **LOG-9** 🔒 **"Add to knowledge base" marker — field + checkbox only** (`knowledge_candidate`, `marked_by`, `marked_at`). Counting rule for the acceptance metric: **checked + body ≥ 300 characters** counts automatically, spot-check 10 before acceptance (decided, S-16). · 0.5 pd · BE
  > Field, checkbox, and the counting rule in one place (`KNOWLEDGE_MIN_BODY`) so INT-8's dashboard and the code cannot disagree: **checked and body ≥ 300 characters** (S-16). Both halves matter — the checkbox alone counts a one-line note somebody ticked out of optimism, the length alone counts every long log nobody judged. Marking is author-or-Admin, matching the edit rule; opening it to any reader is a Phase-2 question, since `marked_by` is singular and a second marker would overwrite the first.
  > **The longer BOT and RAG slip, the more this field is worth.** Every log written from day one carries a human judgment about whether it belongs in the knowledge base, so RAG can backfill the entire history instead of running a re-annotation pass. Do not cut it because "it does nothing right now."

**Not in S1:** L4 external links + DLP · real-time collaborative editing ·
`!trace:` / `!metric:` inline syntax (needs gateway integration) · AI-assisted
writing.

---

## TKT · Tickets + board
**13 pd · weeks 3–6 · [design §7](markdown/relay-s1-design.md)**

- [x] **TKT-1** Ticket entity and fields: type (Bug/Feature/Task), title, description, status, priority P0–P3, assignee, reporter, labels, iteration, PR link, comments — **plus `rev` (monotonic version for optimistic concurrency) and the `ticket_external_ref` table (unique `(tenant_id, system, external_id)`)**. Both are decided and land at create-table time; adding them later is the expensive path (design §8.4). · 1.5 pd · BE
  > Tables already existed from MT (all three §8.4 fields included), so this was the application layer: `relay/app/tickets/`. Numbering is a transaction-scoped advisory lock per tenant plus `MAX + 1`, and the `MAX` runs **under RLS** — per-tenant numbering falls out of the policy rather than out of a WHERE clause somebody can omit, with `UNIQUE (tenant_id, number)` as the backstop. Numbers are **not** gap-free: a rolled-back transaction leaves a hole, and closing that would mean holding the lock across the whole request. `external_ref` dedupe lives in the create use case rather than in API-3, because an alert firing twice is a fact about what a ticket is, not an HTTP concern — a repeat returns the existing ticket with `deduped=True`.
- [x] **TKT-2** Configurable AI context schema: reserve `trace_id[]` · `provider[]` · `model[]` · `prompt_version` · `deployment` · `error_class` · `eval_run` · `token_cost` · `blast_radius` · `tenant[]` as generic fields (default-on for every tenant), and `gateway_version` / `routing_policy` as `domain_scope`-gated fields (default-on for the first tenant only). **No automatic data source in S1** — but writes are validated by Pydantic against `ai_context_field_config`, **not stored as arbitrary JSON**, because the API can now write these fields. The justification is avoiding later migrations and index rebuilds, nothing else. ⚠️ The first team also builds the gateway, so every request they make looks generic. Test before promoting any field to the generic set: **could a team with no gateway of its own fill it in?** · 2 pd · BE
  > Registry in `relay/domain/ai_context.py`, seeded per tenant at bootstrap (`--domain-scope gateway`, off by default so a second tenant cannot silently inherit the gated fields). **The gate is data, not a branch**: the Pydantic validator is built from the tenant's own `ai_context_field_config` rows, so a tenant with no row for `routing_policy` cannot write it and there is no code path to forget. `extra="forbid"` is the load-bearing part — without it `ai_context` degrades into the arbitrary JSON §7.3 refuses, and the migration this task exists to avoid becomes necessary anyway. `visible` deliberately does **not** gate writes: it is a UI preference, and letting it reject writes would turn a cosmetic setting into an integration outage.
- [x] **TKT-3** State machine `Todo → In Progress → In Review → Done` plus `Blocked` and `Won't Fix`; `Blocked` / `Won't Fix` require a reason. Every transition writes `ticket_status_history` **with `actor_type` and `origin`** — which only becomes useful once the API exists. **Status names and semantics are frozen from here**: they are lossy against GitHub's open/closed (GH's problem) and they appear in API responses (a v2-level change to rename). · 1.5 pd · BE
  > `relay/domain/tickets.py`, exactly the graph §7.2 draws. Blocked's resume target is read from the `ticket_status_history` row that entered Blocked — no `blocked_from` column, so there is no second copy to disagree with the history a reviewer is reading. `update()` has **no** status parameter (pinned by a test): every status change goes through `transition()`, so there is no path that writes `status` without writing history, which is the data Phase 2's GH loop guard needs and cannot reconstruct. **S-23 added the last two edges**: `Done → Todo` (reopen — same number, same `rev` history, one more history row) and `In Review → In Progress` (a review sending work back; expressing that as Blocked would have been a lie, since Blocked means waiting on something else). Neither needed a new status, so the frozen enum is untouched, and S1 now has **no terminal status** — worth saying out loud, because "can this come back?" is a question the board, the metrics and every webhook consumer ask. Neither requires a reason: a reason requirement on reopen reads as diligence and would make filing a duplicate the cheap path again. The two tests that pinned their absence were rewritten in the same change as design §7.2 — the mechanism working, not an obstacle.
- [x] **TKT-4** Comments and @mentions. **Changes made through the API raise notifications too** — otherwise the API is a silent back door for editing tickets. · 1 pd · BE
  > Mentions resolve by **email local part** (`@lisa` → `lisa@zerosone.test`), which needs no new handle column because AC-9 fixes domain ↔ tenant one-to-one, so the local part is already unique inside a tenant. The parser is judged by what it refuses: fenced and inline code are stripped first, and an `@` preceded by a local-part character is not a mention — `ping bob@zerosone.test` mentions nobody. A handle matching nobody stays plain text rather than failing the comment. Mentions aggregate **per ticket, not per comment**: four mentions in one thread is one thing to come and read. Cap of 20 distinct mentions per comment — beyond that it is a broadcast, and S1 has no broadcast feature.
- [x] **TKT-5** List view with filters (status, assignee, priority, label, iteration, keyword). · 1.5 pd · FE
- [x] **TKT-6** Board grouped by status with drag-and-drop (vuedraggable). *Cut candidate #2.* · 2.5 pd · FE
- [x] **TKT-7** "My tickets" view. · 0.5 pd · FE
- [x] **TKT-8** Labels, iterations, and the PR link field — **a plain link in S1**: no status write-back, no CI/review state. · 1 pd · BE
- [x] **TKT-9** Detail page, `RL-` numbering **incrementing per tenant**, and permalinks that **reserve a tenant segment**: `https://relay.internal/{tenant_slug}/t/331`. With one tenant the UI may hide the segment, but **the router must support it from day one** — shipping `/t/331` first makes the second tenant a breaking change. **Frozen on release** (decided, S-12). · 1.5 pd · FE

**Not in S1:** Gantt and calendar views · automatic population of domain fields
(no data source).

---

## WEB · The Web UI's HTTP layer
**4 pd · weeks 5–6 · [design §8.9](markdown/relay-s1-design.md#89-web-ui-自己的-http-层s-24--web-14--4-pd)**

**Net new (S-24).** API-1/2/3 cover the *public* ticket API; the Vue frontend
additionally needs session login, log read/write, notification reads, attachment
links, board metadata. None of that was in a task, and all 13 pd of frontend work
was blocked on it.

> **Two surfaces, one application, different contract discipline.** `/web/*` ships
> with the frontend that consumes it, so it is versionless and a field can be
> renamed in one commit. `/api/v1/*` is frozen (§8.6) and enters API-5's committed
> OpenAPI snapshot. What they **share** is not optional: the same error shape, the
> same `If-Match` concurrency rule, the same opaque cursor, and the same
> application-layer use cases (§8.1). Two error formats or two concurrency policies
> would be exactly the drift the import-linter guard exists to prevent.

- [x] **WEB-1** 🔒 The floor every route stands on: **RFC 9457 `problem+json` for all four failure paths** (pulled forward from API-5 — an error format retrofitted later means touching every route again), the **async session dependency** that turns a cookie into a `TenantContext`, CSRF, the client-IP rule, the opaque cursor codec, and the app factory. · 1 pd · BE
  > Four handlers, because there are four ways a request fails: an `ApplicationError` (status from its `code`), Pydantic (**stays 422**, fields into `errors[]`), **Starlette's** `HTTPException` — not FastAPI's subclass, or "route does not exist" is the one response still answering `{"detail": …}` — and a bare `Exception` (500, **no detail**: an exception message is the one place a SQL fragment or a path leaks into a body). `mfa_required` is deliberately *not* in that table: login answers **200 with `mfa_required: true`**, because a second factor is the next step in a flow, and a 401 there trips every SPA's "session expired → go to login" interceptor, which is where the user already is.
  > **The session dependency must be `async`.** FastAPI runs a *sync* generator dependency in a worker thread whose context is a copy, so a `ContextVar` set there is invisible to the endpoint and every request fails with `MissingTenantContext`. Blocking work goes through `run_in_threadpool` instead. Session state lives in an **HttpOnly cookie** rather than `localStorage` — the difference is whether one XSS bug is a stolen session — and the price of that is CSRF, paid with `SameSite=Lax` plus an `Origin` allowlist on state-changing methods (a request with **no** `Origin` is allowed: that is curl or a test, not something a third-party page can cause). `X-Forwarded-For` is believed only from a configured trusted proxy, because the signup and login throttles count per IP and a forged header lets one caller spend everybody else's attempts.
- [x] **WEB-2** Account and session routes: signup · verify · resend · login · TOTP (enroll, confirm, verify, disable) · logout · accept invitation · `GET /web/session` · `GET /web/users`. · 1 pd · BE
  > `GET /web/session` returns the **tenant slug** (every permalink carries one, S-12) and the caller's **capabilities**, so the UI hides what the service layer would refuse instead of re-deriving the §5.4 matrix in TypeScript where it would drift. Logout takes **no** session dependency — it has to work when the session is already unusable, or the only way out of a stuck state is clearing cookies by hand. Addresses are plain `str`, not `EmailStr`: the domain layer already owns the rule, and `EmailStr` rejects `someone@corp.internal`, which is an ordinary address on the network Relay runs on.
- [x] **WEB-3** Logs, attachments and search: log CRUD + versions/diff/rollback + share level + L1 grants + the four edit-lock routes + the knowledge marker, attachment upload/list/link/delete, the signed-blob route, and `GET /web/search`. · 1 pd · BE
  > **PATCH distinguishes absent from null** (`model_fields_set` → the service's `UNSET`), which is what autosave needs: the editor sends the body every few seconds and the title only when it changes, and without the distinction every autosave blanks the title. The edit lock is **four routes rather than a flag on save**, because S-7's TTL, heartbeat and takeover-with-a-version-number are things the UI has to show. Two routes for a download, not one: `/link` is authorized and mints a 5-minute signature, `/blobs/{key}` verifies the signature and streams — the second is unauthenticated **by design** (the decision was already made and recorded when the link was minted) and disappears when the carrier becomes MinIO.
- [x] **WEB-4** Tickets and the rest of the board: list (filters + cursor) · create · get by **id, number or `RL-331`** · PATCH and transitions (both requiring `If-Match`) · comments · history · labels · iterations · `ai_context` field config · notifications · spaces · the admin account operations. · 1 pd · BE
  > `If-Match` is **required**, not optional, and a missing header is a 422 rather than "whatever is current" — the point of the header is that the client says which version it saw, and defaulting it makes the check pass by construction. A stale one is a **409 carrying the current `rev`**, so the loser of a race re-reads exactly once. The cursor is opaque because a client that can read it makes the sort order part of the contract (§8.6); a malformed one is a 422 rather than a silent page one, which is how a paging bug becomes an infinite loop over the same rows.

**Done when:** the frontend can build every S1 screen against `/web` without a
single endpoint added mid-sprint, and every failure — including Pydantic's and
including a route that does not exist — comes back as `problem+json`.

> **Not in WEB:** anything `/api/v1`. Token auth, idempotency keys, webhooks and
> the OpenAPI snapshot gate stay in API-1…API-5, which now inherit an error shape
> and a pagination convention that already exist.

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

- [x] **API-1** Token auth and tenancy: opaque tokens with type prefixes (`rly_u_` personal / `rly_s_` service), **hash-only storage**, plaintext shown once. **Tenant is derived from the token and never read from the request** — a `tenant_id` in a request body or query is a 400. Four coarse scopes: `tickets:read` / `tickets:write` / `comments:write` / `meta:read`. **Default 365-day expiry** with a 14-day reminder to the creator; nameable, revocable, `last_used_at` tracked, create/revoke audited. · 1.5 pd · BE
- [x] **API-2** Resource endpoints under `/api/v1`: `POST|GET /tickets` (filters + **cursor** pagination, `updated_since`), `GET|PATCH /tickets/{key}`, `POST /tickets/{key}/transitions`, `GET|POST /tickets/{key}/comments`, `GET /tickets/{key}/history`, and `/meta/{labels,iterations,users,ticket-fields}` — **`/meta/users` returns id and display name only, never emails**. Reserve the `/logs/*` and `/search` namespaces without implementing them, so the pagination and error conventions don't diverge later. · 1.5 pd · BE
- [x] **API-6** Feedback-path adaptation for the first consumer (design §8.8): a **`submitter`** field (`name` / `email?` / `external_id?`) recorded on the ticket and shown as "submitted by X via the gateway WebUI", plus a fixed source label. **`submitter` is not `reporter`** — reporter stays the service principal (S-10), because gateway users are not Relay accounts and should not have to be. `submitter` is display and traceability only: **no permission effect, excluded from every people-metric**. Also: **screenshots do not go through the API in S1** (no attachment endpoint — adding one would expose MinIO signing and quota to external consumers, and `BlobPort` has no quota concept yet); the WebUI stores them and pastes URLs into the description. Write that into the integration doc or someone asks on day one. · 0.5 pd · BE
  > **Treat feedback bodies as untrusted input.** Free-form human text may carry secrets, customer data, or injection attempts, and S1 has no DLP: warn on the WebUI form, cap body length, and validate `ai_context` against `ai_context_field_config` (never arbitrary JSON).
- [x] **API-3** Loop-safety and concurrency: `Idempotency-Key` on POST (dedupe by `(tenant_id, principal_id, key)` for 24h, replay returns the first result), `If-Match: <rev>` required on PATCH with **409 + current `rev`** on mismatch, and `external_ref` business-level dedupe (a repeated create is refused and returns the existing ticket). Writes record `actor_type` / `origin`; **service tokens surface `reporter` as the machine principal name** (e.g. `alertmanager`), which means people-metrics must exclude them. · 1 pd · BE
- [x] **API-4** Outbound webhooks: `ticket.created` · `ticket.updated` · `ticket.status_changed` · `ticket.comment_created`. Payload carries `event_id` (consumer dedupe), `actor`, and **`rev` so consumers can drop out-of-order events** — delivery is at-least-once and unordered. `X-Relay-Signature: sha256=HMAC(secret, timestamp + "." + body)` + `X-Relay-Timestamp`, per-endpoint rotatable secrets. Retry with exponential backoff (1m/5m/30m/2h/6h) into a replayable dead letter, **queued on PG + `FOR UPDATE SKIP LOCKED`** — no Redis/MQ. **Destination addresses must reject private, loopback, and cloud-metadata targets, validating the resolved IP as well** (DNS rebinding), no domain allowlist (decided, S-13). · 2 pd · BE
- [x] **API-5** Contract discipline and tests. ⚠️ **The direction of truth is inverted by FastAPI**: the spec is generated from code, so "spec disagrees with implementation" is vacuously false and would be a门禁 that never fires. Instead: commit the generated `openapi.json`, have CI regenerate and **diff it — any difference fails until a human updates the snapshot in the PR**, so every contract change is visible in review; deleted fields / changed types / changed enum semantics go to v2, never into v1. Frontend TS types are generated from the same snapshot, so mismatches break the frontend build rather than production. Also **install a global exception handler emitting RFC 9457 `problem+json` for both `HTTPException` and Pydantic `RequestValidationError`** — FastAPI's default `{"detail": …}` would otherwise give the API two error formats, which is the first thing an integrator hits. Keep 422 for validation failures, normalize only the body. Rate limits start loose with full instrumentation: **read 600 req/min, write 120 req/min per token**, tightened after two weeks of real usage. · 1 pd · BE + QA

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

- [x] **NT-1** In-app notifications for assignment, @mention, and status change — **including changes made through the API**. · 1 pd · BE
  > `relay/app/notifications.py`. `emit()` takes the **caller's session** on purpose: a notification describing a status change that rolled back is worse than none, and a committed change nobody heard about is the silent-back-door failure §7.5 is about. Nobody is notified of their own action — an inbox that reports what you just did trains people to stop reading it, and in-app is the only reach surface there is.
- [x] **NT-2** **5-minute aggregation window** and the multi-channel `notification_delivery` state machine, built now so adding email or WeCom later is a new channel rather than a rewrite. `MailPort` and `IMPort` are declared with no-op implementations. Tiered routing, quiet hours, and subscription rules are out. · 0.5 pd · BE
  > Aggregation is **derived, not counted into a column**: the fold count is the number of `SUPPRESSED` deliveries pointing at the aggregate, so a folded notification is still a row in the recipient's history — the flooding leaves the reach surface without the events leaving the record. `unread_count()` counts aggregates, so one ticket moving four times costs one unread item. Windowing keys on `delivery.scheduled_at` rather than `created_at`, which is a server default a caller passing an explicit clock would be comparing against.

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

- [x] **INT-1** CI pipeline with the MT-6 cross-tenant suite and MT-2 schema lint **as blocking gates**, plus the `import-linter` architecture contracts. · 1 pd · QA
- [ ] **INT-11** 🔒 **Automated backup + one real restore drill — covering PostgreSQL *and* MinIO** — before the team starts writing real logs. Owner: **WANGLI** (R-1). Self-hosting bought architectural simplicity; backups are the price. Tickets still have Jira as a fallback (S1 does not decommission it) — **logs and attachments have none from day one**. Suggested values unless the owner decides otherwise: PG daily full + WAL archiving (30d / 7d), MinIO daily incremental to a separate host (30d), drill quarterly thereafter. **Restore both together and open a log that contains an image** — restoring only PG yields intact prose with every picture broken, and a half-restore that never shows up in a drill shows up during a real incident instead. · 0.5 pd · BE
- [x] **INT-5** End-to-end suite over the S1 critical flow: **signup → email verification → login → log → ticket → API write → notification**. · 1 pd · QA
- [x] **INT-6** Dual-track rollout and operating guidance for the team. · 1 pd · QA
- [x] **INT-8** Minimal acceptance dashboard, **with denominators pinned in the dashboard rather than agreed during the review**: weekly-active-creator share uses activated accounts over a calendar week, and **service-token principals are excluded from every people-metric** — otherwise one alerting script inflates the numbers. · 0.5 pd · BE + QA

---

## Exit criteria

> **How the boxes below are ticked.** A box is checked only when something
> *mechanical* holds it — a gate in CI, a test that fails if the property stops
> being true. Boxes that need a **human act** (a drill, a spot-check, people
> actually using the thing) stay open no matter how much code exists, because
> that is the honest reading of them. The named test is given so the claim can be
> re-checked rather than trusted.

### Hard gates

- [x] Schema lint **blocks** any new table lacking `tenant_id` **or** an RLS policy — proven able to fire: `tests/test_ci_gates.py::test_schema_lint_catches_a_table_with_no_policy`
- [x] Idempotent replay produces **zero** duplicate tickets — `tests/test_api_v1.py::test_the_same_idempotency_key_three_times_makes_one_ticket`, and the `external_ref` half beside it
- [x] Concurrent writes produce **zero** silent overwrites (`rev` mismatch must 409) — `tests/test_api_v1.py::test_a_stale_if_match_is_a_409_carrying_the_current_rev`; the header is refused when absent, on **both** surfaces
- [x] OpenAPI snapshot gate blocks a changed contract until the snapshot is updated in the PR — `scripts/gen_openapi.py --check`, wired into `make gates` and CI
- [ ] Cross-tenant read/write = **0**, including the API and webhook paths; CI gate green; **one penetration spot-check before rollout**
      → the gate is green (`tests/test_cross_tenant.py`, `tests/test_rls_enforcement.py`, and MT-6's token half in `tests/test_api_v1.py::test_a_token_cannot_reach_another_tenants_ticket`; webhooks in `tests/test_webhooks.py::test_a_webhook_never_leaves_the_tenant_that_queued_it`). **The spot-check is a person's job and has not happened.**
- [ ] **Backup restored at least once, PG and MinIO together** — otherwise real log writing does not start
      → `scripts/backup.sh` and `scripts/restore_drill.sh` exist and the drill script cross-checks every `attachment` row against the restored bucket (the half-restore case). **Running it is INT-11, owner WANGLI, and it needs the real instances.**

### Functional exit

- [x] Signup → verification → login → log → ticket → **in-app notification** passes end to end — `tests/test_end_to_end.py::test_the_s1_critical_flow`, which walks the seams rather than the layers
- [x] Logs: dual mode · 90-day versioning and rollback · L0–L3 sharing · full-text search · knowledge-base marker
- [x] Tickets: fields · 6-state machine · list/board/my-tickets · detail + permalink
- [x] API: the criteria above, and the gateway feedback round trip — `tests/test_end_to_end.py::test_the_gateway_feedback_round_trip`: a submission lands with `submitter` and a source label, and neither a replayed key nor a repeated `external_ref` creates a second ticket
- [ ] **The gateway WebUI feedback form is live end to end** — our side is done and tested against a real token; the other side is the gateway team's, and it has not been connected
- [ ] Team begins **dual-track use** (Relay alongside Jira) — guidance written ([relay-s1-rollout.md](markdown/relay-s1-rollout.md)); the using is the team's
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
| **Un-cuttable** | **WEB-1…4** | Every frontend task calls it. Cutting it does not save 4 pd, it stops 13 |
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

**Settled in the owner-action rounds** (S-19…S-25; the ⚠️ column is the part that
is easy to miss while implementing):

| # | Decision | ⚠️ The consequence that is not obvious |
|---|---|---|
| **S-19** | **Admin reads every share level, including a colleague's L0** — plus a `log.read_by_admin` audit row for reads only the role made possible | An Admin account **is** whole-tenant read access, so this decision is bound to "who may be an Admin". And **reads now write**: a read path commits when — and only when — it recorded something |
| **S-20** | Scheduled jobs run as a **system identity**, not as a borrowed Admin and not through `SystemRepository` | The capability list is the job's own declaration, so this buys *attribution* and a hard wall against a request running as system — not a second opinion on whether the job may run. `SYSTEM_CAPABILITIES` is short on purpose |
| **S-21** | **A Guest reads only their own tickets** (assignee or reporter) | The refusal is 404, not 403 — and the same rule has to hold on comments, attachments, search and mentions, or one of them becomes the way around it |
| **S-22** | Feedback-loop details: WebUI shows progress, WebUI notifies the submitter, defaults are Bug/P2 | "Never internal comments" is a **consumer-side** constraint: a service token can read `/comments`, so it belongs in the integration doc |
| **S-23** | State machine gains `Done → Todo` and `In Review → In Progress` | **S1 has no terminal status any more.** Board columns, metrics and webhook consumers all assume "done means done" until told otherwise |
| **S-24** | The Web UI's HTTP layer is its own task group (WEB-1…4, +4 pd) | 57.5 → 61.5 pd. `/web` is versionless and `/api/v1` is frozen, but they must **share** the error shape, the concurrency rule and the cursor — two of either is the drift §8.1 exists to prevent |
| **S-25** | **Write the MinIO/S3 adapter blind** against standard S3 semantics rather than waiting for the real instance; deviations are filed as bugs | 61.5 → 62 pd, and the extra 0.5 is the point: a containerised MinIO contract test plus a round-trip smoke script. **Blind writing gets semantics right and deployment shape wrong** — presign host, addressing style, clock skew and a private bucket are checklist items, not code. The real instance is still required before launch, and **INT-11's drill still needs it** |

## Frozen contract

**§8 signed off in week 2.** Sequencing rule 3 is satisfied — and retroactively
validated the tables MT already created: `ticket.rev`, `ticket.submitter`,
`ticket_external_ref`, and `actor_type` / `origin` on `ticket_status_history`
all landed at create-table time, so there is no §8.4 rework.

Two conflicts inside §8 surfaced during sign-off and are now settled. Both are
folded back into [relay-s1-design.md §8.3](markdown/relay-s1-design.md#83-资源与端点apiv1):

| # | Conflict | Settled |
|---|---|---|
| **C-1** | §8.3's example showed `"type": "Bug"` / `"priority": "P1"`, and `status` had **no wire form anywhere in §8** — TKT-3 only gave display names (`In Progress`, `Won't Fix`) | **Uniform snake_case for all three**: `bug`/`feature`/`task` · `p0`/`p1`/`p2`/`p3` · `todo`/`in_progress`/`in_review`/`done`/`blocked`/`wont_fix`. Display names belong to the frontend; as wire values they would carry a space and an apostrophe into URL params, log keys and consumers' constant names |
| **C-2** | §8.3 said the create response's `url` is `https://relay.internal/t/331`; TKT-9 / S-12 requires a reserved tenant segment | **Tenant segment ships from day one**: `https://relay.internal/{tenant_slug}/t/331`. The first consumer is the one that *stores* this URL, so adding the segment later is its breaking change |

Frozen from here means mechanically frozen: `tests/test_frozen_contract.py`
pins every enum value, the four scopes, both token prefixes and the permalink
template, **hand-copied rather than derived from the enums** — a test that read
its expectations out of the code under test would sail through any rename. It
also reads §8.3 back, so the two copies cannot drift apart quietly.

---

## Open items

**None.** All three closed in the owner-action round; the decisions are recorded
as S-19…S-24 in [design §12.1](markdown/relay-s1-design.md#121-决策记录全部采纳建议)
and the reasoning is inlined in the tasks above. What they turned into:

| Was | Decided | Where it lives now |
|---|---|---|
| **F-6** — three product details on the feedback loop | **S-22**, recommendations adopted as written: the WebUI **does** show progress (polling `GET /tickets/{key}`, status and last-update only, **never internal comments**) · the **WebUI** notifies the submitter, not Relay · feedback defaults to **type=Bug / priority=P2** with the submitter unable to pick priority | API-6's defaults and the integration doc. `TicketView` now carries `updated_at`, so the polling path is one request. ⚠️ **A service token can reach `/comments`** — "no internal comments" is a constraint on the consumer and has to be written into the integration doc, not assumed from the API surface |
| **T-1** — two missing transitions | **S-23**, both added | `relay/domain/tickets.py`, design §7.2, and the rewritten tests in `test_ticket_state_machine.py` + a database-level reopen test in `test_tickets.py` |
| **T-2** — can a Guest read the whole board? | **S-21: no.** A Guest reads only tickets they are the **assignee or reporter** of. Admin and Member keep the tenant-wide board; **no per-ticket ACL column was added** — the role already carried the distinction | `relay/app/tickets/sharing.py` (pure rule) + `visible_tickets_predicate` (SQL mirror, cross-checked), applied to the list, get, history, comments, attachments and search. Refusal is **404**: for a Guest, learning that RL-412 exists is already more than the decision allows. Mentioning a Guest who cannot read the ticket notifies nobody — the alternative tells them it exists *and* tells the author the ping landed |

> **What T-2 deliberately did not become**: per-ticket sharing. That is a column,
> an evaluation order, and a change to every §8 response — a v2-level move. If the
> contractor case ever needs finer granularity than "their own work", it should be
> designed with LOG-6's evaluation order in front of it, not bolted on.

## Optional — not in the baseline

- [ ] **NT-3** Turn on **email notifications** — ~0.5 pd, not counted in the 62. Everything it needs already exists (F-5's sending path, declared `MailPort`, aggregation window, multi-channel delivery state machine). **This is the designated escape hatch** if week-6 dual-track feedback says reach is too low: cheaper and four weeks sooner than waiting for BOT's WeCom channel.

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
