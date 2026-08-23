# Relay · Phase 1 Task Breakdown

Derived from [relay-prd.md](markdown/relay-prd.md) §4 (MVP spec), §4.10
(schedule), §4.11 (acceptance), §5 (roadmap).

> **Execution note (2026-08-23).** Delivery starts with a smaller slice — **S1**:
> MT · AC (self-service signup) · LOG · TKT + a public ticket API. BOT, TA-2…TA-4,
> AC-6/AC-7 and MT-5 are deferred out of it, with their interface seams kept.
> **This file stays the Phase 1 baseline** — it keeps the full breakdown and the
> reasoning behind the GH-vs-RAG ordering, none of which S1 changes. The execution
> list for S1 lives in [TODO-S1.md](TODO-S1.md); its design is
> [markdown/relay-s1-design.md](markdown/relay-s1-design.md). Task IDs are shared
> across all three. **When the two files disagree about S1, TODO-S1.md wins;
> for anything beyond S1, this file wins.**

> **Three epics sit outside the MVP boundary** — [GH](#gh--github-bidirectional-sync-v1)
> (GitHub sync), [RAG](#rag--rag-qa-engine) + [SEED](#seed--knowledge-seed-import)
> (Q&A engine and knowledge seeding), and routing platform AI calls through the
> in-house gateway. They are in this file, marked ⏸, because the 12-week window
> holds and they start in weeks 7–12 — but **under Phase 2 identity, and they do
> not gate MVP acceptance.**

| | |
|---|---|
| **MVP scope** | Milestone A (weeks 1–6) + BOT in Milestone B. **68.5 pd ≈ 13.5 person-weeks** |
| **Deferred (⏸)** | GH, RAG, SEED, gateway routing. **42.5 pd ≈ 8.5 person-weeks**, started weeks 7–12 |
| **Phase 1 total** | 12 weeks, two milestones, **111 pd ≈ 22 person-weeks** |
| **Team** | 2 backend · 1 frontend · 1 AI · 0.5 QA |
| **Goal** | Get the team off Jira, and let them feel in their group chat that the platform knows what they're working on (§4.0) — ⚠️ the second half rests on **one** feature, see [the AI-value warning](#the-ai-value-warning) |

---

## How to read this file

- **ID** — stable task identifier. Reference these in commits, branches, and tickets (`RL-` prefixed once the tracker exists).
- **Effort** — person-days. Role: BE backend · FE frontend · AI AI/ML · QA quality.
- **⏸** — Phase 2 work, started early in weeks 7–12. Spec takes no discount, but it is excluded from MVP acceptance.
- **🔒** — on the "cannot be cut" list (§4.10③). These survive any compression. Note that ⏸ and 🔒 co-occur: a deferred item can still be un-cuttable *within Phase 2*.
- **⛔** — cannot start until an [open decision](#blocked-on-decision) lands.

---

## The AI-value warning

§4.0 argues the MVP must carry **two** values — replacement (logs, tickets, board)
and AI — and that missing the second degrades Relay into "just another ticket
system," which no later feature recovers because the internal-tool adoption window
is the first week.

RAG Q&A is nonetheless outside the MVP. That risk was **accepted, not solved**
(§0.3 判断五). Two consequences this file enforces:

1. **[BOT-3](#bot--im-bot)'s AI-drafted ticket is the only AI touchpoint in the MVP.** Its
   acceptance metric — draft confirmation rate > 60% — is promoted from an
   experience metric to a **hard gate**. It takes no discount.
2. **The week-6 dual-track feedback is a real test.** If the team's reaction is
   "this is just Jira," RAG moves ahead of GH in the weeks 7–12 ordering. That
   call is deliberately left open until there is real feedback.

---

## Sequencing rules (non-negotiable)

1. **MT and TA come first, weeks 1–2, ahead of everything else — not in parallel
   with feature work.** One table missing `tenant_id` amplifies rework across
   every module built after it (§4.10②). This is the only ordering constraint the
   PRD states in imperative form. MT-2's schema lint is what makes it stick.
2. **TA ships even though nothing in the MVP consumes it.** All four downstream
   consumers are Phase 2+, and with RAG and the gateway in Phase 2 as well, TA's
   only MVP consumer is TKT-2's field reservation — which is empty until Phase 2
   alert ingest exists (§4.2). **This is prepaid technical debt, not a feature.** Say so
   at the week-2 review, or it gets cut as "nothing to show."
3. **Identity binding is a hard prerequisite, and its coverage is a gate.** No SSO
   means the platform cannot tell who is speaking in a group chat, so the WeCom
   userid binding ([AC-6](#ac--accounts--triple-identity-binding)) caps how usable the bot can be. Binding
   coverage > 90% is an acceptance gate ([INT-9](#int--integration-testing-rollout)) — drive it during weeks
   5–6 alongside dual-track, **not** after the bot ships.
4. **The WeCom spike stays in week 1, but it is no longer a feasibility question.**
   The carrier is settled: AI-bot API mode is **already enabled for this enterprise**
   and its callback carries `quote`, so BOT-3's in-channel confirmation card holds and
   BOT-4 is implementable. What is left for [BOT-1](#bot--im-bot)'s week-1 spike is
   parameters — and one of them, whether `from.userid` is plaintext or
   subject-encrypted, **decides how `identity_binding` is created**, with AC-6 and MT-1
   both in weeks 1–2. Cheap now, a full re-bind later.
5. **GitHub sync pilots on one repo for two weeks before expanding.** Sitting in
   Phase 2 does not relax it. Sync remains the one module where getting it wrong
   destroys trust permanently (§4.7).
6. **SEED precedes opening Q&A.** The ≥100-unit gate holds. Missing it is not an
   MVP failure, though — the bot is already live on ticket creation, so Q&A can
   open separately once knowledge is ready (§4.9①).

---

## Effort summary

### MVP scope — acceptance depends only on these

| Epic | Title | Effort | Milestone |
|---|---|---:|---|
| [MT](#mt--multi-tenant-data-model) | Multi-tenant data model 🔒 | 8 pd | A (wk 1–2) |
| [TA](#ta--telemetry-adapter-interface) | Telemetry adapter interface 🔒 | 5 pd | A (wk 1–2) |
| [AC](#ac--accounts--triple-identity-binding) | Accounts + triple identity binding | 10 pd | A |
| [LOG](#log--logs--knowledge-authoring) | Logs / knowledge authoring | 15 pd | A |
| [TKT](#tkt--tickets--board) | Tickets + board | 13 pd | A |
| [BOT](#bot--im-bot) | IM bot (create / status / bind / notify — **no Q&A**) | 10 pd | B (wk 7–8) |
| [INT](#int--integration-testing-rollout) | Integration, testing, rollout | 7.5 pd | A + B |
| | **MVP total** | **68.5 pd** | ≈ 13.5 pw |

### ⏸ Deferred to Phase 2 — started in weeks 7–12, not gating MVP

| Epic | Title | Effort | Window |
|---|---|---:|---|
| [GH](#gh--github-bidirectional-sync-v1) | GitHub bidirectional sync v1 🔒 | 15 pd | wk 7–12, priority 2 |
| [RAG](#rag--rag-qa-engine) | RAG Q&A engine | 17.5 pd | wk 7–12, priority 3 |
| [SEED](#seed--knowledge-seed-import) | Knowledge seed import 🔒 | 5 pd | wk 9+, precedes RAG opening |
| [BOT-2](#bot--im-bot) | Bot-side Q&A card wiring | 1.5 pd | with RAG |
| [INT-2, INT-3, INT-4](#int--integration-testing-rollout) | Gateway routing, sync pilot, model A/B | 3.5 pd | wk 7–12 |
| | **Deferred total** | **42.5 pd** | ≈ 8.5 pw |

### Reconciliation against the PRD

**Phase 1 totals 111 pd ≈ 22 pw against §4.10①'s 20.5 pw — a documented +1.5 pw.**
The delta is six tasks the PRD's own text requires but §4.10① folds into existing
lines rather than costing, minus one rescope:

| Δ | Task | Why |
|---:|---|---|
| +1.5 | [RAG-10](#rag--rag-qa-engine) minimum inbound redaction | §4.9⑤ makes it a hard ordering constraint before the first ingest |
| +1.0 | [RAG-11](#rag--rag-qa-engine) wrong-answer measurement | §4.11② concedes the <5% gate currently has no measurement mechanism |
| +1.0 | [INT-9](#int--integration-testing-rollout) binding drive | §4.11① makes binding coverage an acceptance gate |
| +1.0 | [INT-10](#int--integration-testing-rollout) hard budget alarm | §4.8② — no gateway means no per-feature cost view |
| +0.5 | [BOT-8](#bot--im-bot) guidance reply + question logging | §4.8② requires it, and §4.9's cold start depends on the samples |
| +0.5 | [BOT-1](#bot--im-bot) WeCom spike | Question 15 settled (AI-bot API mode enabled); the spike remains for userid semantics, which gates `identity_binding` |
| +0.5 | [BOT-3](#bot--im-bot) at 3.0 pd rather than 2.5 | Draft quality is a hard gate, and the estimate includes the adjustable-context-range UI |
| −1.0 | [INT-5](#int--integration-testing-rollout) at 1.0 pd rather than 2.0 | E2E covers the MVP flow only — the GitHub and RAG legs are Phase 2 |

**All of the +1.5 pw lands either on ⏸ work or on Milestone A, which carries
~4 weeks of slack**, so it does not threaten the MVP boundary. Still worth raising
with the PRD owner so §4.10① and this file converge on one number.

> **Why the smaller MVP does not shorten the schedule (§4.10①):** the 7.5 pw
> outside the MVP does not buy an earlier delivery, it buys implementation and
> observation time for the Phase 2 work. MVP net scope is ~13.5 pw against a
> 6-week Milestone A, leaving roughly 4 weeks of slack — and GH gets a ~6-week
> observation window. **The real win is that "does the MVP land" does not depend
> on "is sync solid."**

### Indicative week map

| Week | Focus |
|---|---|
| 1–2 | MT, TA (exclusive) · BOT-1 WeCom feasibility spike (parallel, 0.5 pd) |
| 3–4 | AC · LOG start · TKT start |
| 5–6 | LOG finish · TKT finish · INT-1 / INT-6 · **INT-9 binding drive** · **★ Milestone A gate** |
| 7–8 | **BOT build and rollout → MVP complete** · GH-1…GH-3 begin (⏸ Phase 2 identity) |
| 9–10 | GH-4…GH-9 · single-repo pilot begins · SEED begins |
| 11–12 | GH pilot observation → all-repo expansion · RAG core · Jira cutover |

> **RAG will probably spill past week 12. That is expected, not a slip.** Weeks
> 7–12 hold ~10 pw of capacity against 9 pw of planned work with zero slack, and
> RAG is last in priority. It completes in Phase 2 proper. What must *not* slip
> past week 12 is BOT — it is the last MVP item.

---

# Milestone A — week 6 · ★ MVP hard acceptance boundary · internally usable

Exit state: the team runs Relay and Jira in parallel (dual-track).

> **This milestone contains no high-risk items, so its delivery is
> deterministic.** Every MVP acceptance metric in §4.11① is judged here or at
> BOT rollout. GH sits outside the MVP precisely to achieve this.

## MT · Multi-tenant data model
**8 pd · 🔒 · weeks 1–2 · §4.1**

MVP builds the *data model* layer only. Per-tenant billing, self-service tenant
admin, cross-tenant sharing policy, per-tenant config isolation, and per-tenant
model routing are Phase 2+ product features.

> **The distinction that gets misread:** multi-tenant **product features are out,
> the data model is mandatory** (§4.1). Reading it the other way costs a multi-week
> refactor.

- [ ] **MT-1** Define the tenant entity and audit every business entity for tenancy — users, spaces, logs, tickets, knowledge units, comments, attachments, audit records. Produce the definitive entity list; nothing gets added later without a `tenant_id`. · 1 pd · BE
- [ ] **MT-2** Add `tenant_id` to every table in the MT-1 list, with an Alembic baseline and a **CI gate that fails on any new table lacking it**. Implement the gate as an *assertion against a real database* — spin up an empty PG, run every migration, then query `information_schema` / `pg_class` and require `tenant_id`, `relrowsecurity`, and `relforcerowsecurity` on every table outside the documented exemption allowlist. An assertion sees hand-written SQL migrations and `op.execute()` table creation that a model-code linter cannot, and it catches a forgotten `FORCE ROW LEVEL SECURITY` — the one human-error surface the RLS approach has. Mechanical enforcement, not code review. · 2 pd · BE
- [ ] **MT-3** Inject tenant filtering at the ORM / repository layer so it is structurally impossible to bypass. **Never delegated to business code, and never to a prompt.** · 2 pd · BE
- [ ] **MT-4** Composite indexes with `tenant_id` as the leading column, across all tenant-scoped query paths. · 0.5 pd · BE
- [ ] **MT-5** Vector-store tenant isolation — the filter goes *inside* the query predicate, not applied to results after retrieval. Ships now even though [RAG](#rag--rag-qa-engine) is deferred: retrofitting isolation into a populated vector store is exactly the rework MT exists to prevent. · 1 pd · AI
- [ ] **MT-6** Negative test suite asserting cross-tenant read *and* write both fail, wired as a CI gate. · 1.5 pd · QA

**Done when:** a deliberately malicious query written against the repository
layer cannot retrieve another tenant's row, and CI blocks any commit that
regresses that property.

## TA · Telemetry adapter interface
**5 pd · 🔒 · weeks 1–2 · §4.2**

Four downstream features depend on this layer (alert-to-ticket, change
attribution, ChatOps read-only, environment snapshots). Hard-coding the in-house
gateway means reworking all four in Phase 2 — which is why the interface ships
now even though only one implementation exists.

> ⚠️ **Read sequencing rule 2 before the week-2 review.** All four consumers are
> Phase 2+, and with RAG and gateway routing there too, **nothing in the MVP
> visibly exercises this epic.** It is un-cuttable, and it has nothing to demo.
> Both are true; say both out loud.

- [ ] **TA-1** Define the `TelemetryAdapter` interface and its data contracts: `queryMetrics`, `getTrace`, `sampleRequests`, `listRecentChanges`, `getProviderHealth`, `getCostBreakdown`. · 1 pd · BE
- [ ] **TA-2** Implement the in-house AI Gateway adapter against that interface. The first target team is the **AI Gateway team** ([question 2](#blocks-phase-1-startup) settled), so the first adapter targets **their own gateway** — the source, its field schema, and its on-call all sit inside the target team. Default dimensions: `tenant`, `provider`, `model`, `route`/`routing_policy`, `gateway_version`, `env`, `error_class`; default metrics: request volume, error rate, latency p50/p95/p99, token usage, cost, provider health and failover events. Gateway-specific dimensions live **inside `dimensions{}` only** — promoting one into the TA-1 contract makes the Phase 2 LiteLLM/OTel/Langfuse adapters unimplementable. · 2 pd · BE
- [ ] **TA-3** Redaction on `sampleRequests` — samples carry no payload by default. · 1 pd · BE
- [ ] **TA-4** Adapter conformance test suite, so Phase 2 adapters (LiteLLM, Portkey, OpenTelemetry, Langfuse, cloud gateways) have a contract to pass rather than a codebase to read. · 1 pd · QA

**Done when:** no application code calls the gateway API directly, and the
conformance suite passes against the one implementation.

## AC · Accounts + triple identity binding
**10 pd · §4.4, §4.5**

No SSO in MVP. That is a decided trade-off, and identity binding becomes a
*prerequisite for the bot working at all* — the platform otherwise cannot tell
who is speaking in a group chat.

- [ ] **AC-1** Invite-only signup, plus optional corporate email-domain allowlist for self-service. · 1.5 pd · BE
- [ ] **AC-2** Email + password auth: password policy (length, complexity, 90-day reminder), failed-login lockout, session timeout, unfamiliar-location alert. · 1.5 pd · BE
- [ ] **AC-3** Optional TOTP second factor; recommend enforcing for Admin. · 1 pd · BE
- [ ] **AC-4** Three roles — Admin / Member / Guest — with permission checks at the service layer. No fine-grained RBAC in MVP. · 1.5 pd · BE
- [ ] **AC-5** Team space, single level. No nesting. · 1 pd · BE
- [ ] **AC-6** 🔒 **WeCom userid binding** — user DMs `绑定` to the bot → bot returns a code → user enters it on the settings page. **Hard prerequisite: ticket creation, notification delivery, and message attribution all depend on it. Cut this and the bot is entirely unusable.** · 1.5 pd · BE
- [ ] **AC-7** GitHub handle binding via GitHub OAuth. **Cut candidate #5 in the MVP** — all three uses (@mention translation, PR linking, assignee mapping) serve [GH](#gh--github-bidirectional-sync-v1), which is Phase 2, and MVP can fall back to pasting PR links by hand. **But it must land before GH starts** (§4.5). · 1 pd · BE
- [ ] **AC-8** Unbound-user degradation matrix, implemented explicitly: chat Q&A allowed in principle (Q&A needs no identity) — **but there is no Q&A in the MVP, so the bot returns the guidance message from [BOT-8](#bot--im-bot); the allow path activates in Phase 2** · chat ticket creation **refused** with a DM guiding binding (a ticket must have a real reporter) · notifications degrade to in-app + email · ⏸ *GitHub-sync unmapped-user handling (never mis-@ an unrelated account) ships with GH.* · 1 pd · BE

**Done when:** every active path in AC-8 is exercised by a test.

> **Ops risk to record now:** without SSO, departures and role changes do **not**
> auto-deactivate accounts. A manual admin process must exist before rollout
> (recommend hooking the HR offboarding checklist) and must be written into the
> ops handbook — otherwise it becomes a security-audit finding. Resolved when
> WeCom SSO lands in Phase 2. Owner unassigned → [open question 6](#blocked-on-decision).

## LOG · Logs / knowledge authoring
**15 pd · §4.6**

- [ ] **LOG-1** Dual-mode editor — Markdown and plain text — with live split preview. · 3 pd · FE
- [ ] **LOG-2** Full GFM + syntax-highlighted code + Mermaid rendering. · 2 pd · FE
- [ ] **LOG-3** Inline ticket cards via `#331` syntax. · 1 pd · FE
- [ ] **LOG-4** Autosave, version history retained 90 days, diff view, rollback to any version. · 3 pd · BE + FE
- [ ] **LOG-5** Attachment and image upload. · 1 pd · BE
- [ ] **LOG-6** Share levels L0 private / L1 named people / L2 within space / L3 whole org. · 1.5 pd · BE
- [ ] **LOG-7** Templates: daily report, investigation record, incident retrospective, design doc. *Cut candidate #2.* · 1 pd · FE
- [ ] **LOG-8** Full-text search. · 2 pd · BE
- [ ] **LOG-9** 🔒 **"Add to knowledge base" marker — field and checkbox only.** ⏸ *Vectorization and indexing ship with [RAG](#rag--rag-qa-engine).* · 0.5 pd · BE
  > **Why half-build this — the one place the plan deliberately splits a feature.** Keeping the marker means every log written in weeks 6–12 already carries a human judgment about whether it belongs in the knowledge base, so Phase 2 can **backfill the entire history** when the index opens. Drop the field too and Phase 2 cold start (§4.9①, that module's largest risk) additionally owes a full re-annotation pass. ~0.5 pd buys the removal of a Phase 2 startup blocker — hence 🔒.

**Not in MVP:** L4 external links + DLP scanning (external links are the largest
leak surface — deliberately not opened), real-time collaborative editing (CRDT
cost; MVP uses an edit lock + conflict prompt), `!trace:` / `!metric:` inline
syntax (needs gateway integration), AI-assisted writing.

## TKT · Tickets + board
**13 pd · §4.7, §4.3**

- [ ] **TKT-1** Ticket entity and MVP fields: type (Bug/Feature/Task), title, description, status, priority P0–P3, assignee, reporter, labels, iteration, linked PR, comments. · 1.5 pd · BE
- [ ] **TKT-2** Configurable AI context schema (§4.3): reserve `trace_id[]`, `provider[]`, `model[]`, `prompt_version`, `deployment`, `error_class`, `eval_run`, `token_cost`, `blast_radius`, `tenant[]` as generic fields, and `gateway_version` / `routing_policy` as config-enabled domain fields. Fields are **reserved in the data model and show/hide-configurable in the UI**, but **have no data source in the MVP** — alert ingest arrives in Phase 2, so these are empty fields plus a visibility config. Justified purely by avoiding later migrations and index rebuilds. The UI show/hide layer is cut candidate #6. Default field set is settled with [question 2](#blocks-phase-1-startup): generic fields default-on for every tenant; `gateway_version` / `routing_policy` default-on **only for the first tenant** (the AI Gateway team) via `domain_scope`. ⚠️ The first team is also the team that builds the gateway, so every request they make looks generic — a gateway-specific field must never be promoted into the generic set before a second team onboards. Test: **could a team with no gateway of its own fill this field in?** · 2 pd · BE
- [ ] **TKT-3** State machine: `Todo → In Progress → In Review → Done`, plus `Blocked` and `Won't Fix`. The full machine (Triage / Verifying / Reopened) is deferred. See [GH-11](#gh--github-bidirectional-sync-v1) — this shape is lossy against GitHub's open/closed and the mapping must be settled before sync starts. · 1.5 pd · BE
- [ ] **TKT-4** Comments and @mentions. · 1 pd · BE
- [ ] **TKT-5** List view with filters. · 1.5 pd · FE
- [ ] **TKT-6** Board view grouped by status, with drag-and-drop. *Cut candidate #3.* · 2.5 pd · FE
- [ ] **TKT-7** "My tickets" view. · 0.5 pd · FE
- [ ] **TKT-8** Labels, iterations, and the PR link field — **a plain link field in the MVP**: no status write-back, no CI/review state, because those arrive with [GH](#gh--github-bidirectional-sync-v1). · 1 pd · BE
- [ ] **TKT-9** Ticket detail page, `RL-` numbering, stable permalinks (`https://relay.internal/t/331`). · 1.5 pd · FE

**Not in MVP:** Gantt and calendar views. Domain-specific field auto-population
(no data source until Phase 2).

### ★ Milestone A exit criteria — the MVP hard boundary

- [ ] `tenant_id` present on every entity; cross-tenant CI gate green; **schema lint blocking**
- [ ] No direct gateway API calls outside the adapter; conformance suite green
- [ ] Accounts, roles, and WeCom userid binding working end to end
- [ ] **Binding coverage > 90%** ([INT-9](#int--integration-testing-rollout)) — gates whether the bot can work at all
- [ ] Logs complete: dual mode, versioning, L0–L3 sharing, search, knowledge-base marker
- [ ] Tickets + board usable for real work
- [ ] Team begins dual-track use (Relay alongside Jira)
- [ ] **Retrospective held, and the weeks 7–12 ordering decided** — GH first, or RAG first? See [the AI-value warning](#the-ai-value-warning)

---

# Milestone B — weeks 7–12 · MVP complete, then Phase 2 begins early

Exit state: Jira decommissioned. **The cutover depends on BOT and Milestone A
only — not on the completion of any ⏸ epic below.**

## Priority 1 · BOT — completes the MVP

### BOT · IM bot
**10 pd · weeks 7–8 · §4.8**

Trigger is `@Relay` only. **No passive whole-channel monitoring** — false-positive
cost is too high at MVP (the bot interrupting a discussion gets it muted within a
week, and muted is unrecoverable), reading all messages needs psychological
buy-in even internally, per-message model cost does not match the benefit, and
there is no eval baseline yet for "is this a problem."

- [ ] **BOT-1** WeCom app and bot registration; `@` mention parsing and message routing. **Two channels**: an *AI bot* (API mode) for inbound callbacks and in-conversation replies, plus a *self-built app* (`message/send`) for platform-initiated outbound messages — `response_url` is scoped to one conversation, so notifications and DM nudges cannot use it. Callback carries `msgid` (dedupe), `chatid`, `chattype`, `from.userid`, `response_url`, and `quote`. AI-bot API mode is **already enabled for this enterprise**, so the carrier is settled and there is no A/B fork. **Week-1 spike (0.5 pd, run early per sequencing rule 4), now parameter confirmation:** (1) is `from.userid` plaintext or subject-encrypted, and is it stable across bot-owner / app changes — **this one decides how `identity_binding` is created** (see D-13 in the design doc); (2) do both channels yield the same userid; (3) is the outbound self-built app ready (agentid, secret, visible scope, callback IP allowlist) — inbound being enabled does not mean outbound is; (4) app-message rate limits. · 2 pd · BE
- [ ] **BOT-3** 🔒 `@Relay 建单 <description>` → ticket draft card → **Create / Edit / Cancel**, auto-expiring after 5 minutes. Flow: identity check (unbound → DM guidance, flow ends) → context capture → **AI-drafted title / description / type / priority** → confirmation card → create → card updates in place with the ticket link. ⏸ *the GitHub sync step arrives with GH.* **Never auto-create; confirmation is always required** — a ticket has an owner and an SLA, and a wrong ticket costs far more than a missing one. · 3 pd · BE
  > **This is the MVP's only AI value proof point** ([warning](#the-ai-value-warning)), so draft quality is a hard gate, not polish. Draft quality is dominated by the context-capture step, not the prompt: make **the captured range visible and adjustable on the card** ("including the previous 5 messages" → expandable to 10). That moves the confirmation rate faster than prompt tuning.
- [ ] **BOT-4** Quote a message + `@Relay 建单` → capture the quoted message plus surrounding channel context as the description. The AI-bot callback exposes `quote` (present only when the user actually quoted something), so BOT-3 and BOT-4 are two branches of one capture pipeline, not two implementations. · 1 pd · BE
- [ ] **BOT-5** `@Relay #331` → ticket status card (status, assignee, last update). *Cut candidate #4.* · 1 pd · BE
- [ ] **BOT-6** `@Relay 绑定` → DM the identity-binding flow (pairs with AC-6). · 0.5 pd · BE
- [ ] **BOT-7** WeCom app-message notifications for assignment, @mention, and status change over the self-built-app channel (`message/send` — a notification has no "current conversation" to reply into), with a 5-minute aggregation window to prevent flooding. Tiered routing, quiet hours, and subscription rules are Phase 2. · 2 pd · BE
- [ ] **BOT-8** **Explicit handling for unavailable commands — never silent.** On a probable question (`@Relay` without `建单` / `#id` / `绑定`), reply with fixed guidance: *"Q&A isn't open yet (expected in Phase 2). You can use `@Relay 建单 <description>` to file a ticket, or `@Relay #id` for status."* **And log every such question.** These are real demand samples for [SEED](#seed--knowledge-seed-import) — the best available evidence for what to import first, and a partial substitute for the shadow mode the team has no labor for (§4.9⑤). Near-zero cost, materially lower Phase 2 cold-start risk. · 0.5 pd · BE
- [ ] ⏸ **BOT-2** `@Relay <question>` → RAG answer card with cited sources; refuse below the confidence threshold. **Ships with [RAG](#rag--rag-qa-engine); gated on the ≥100-unit seed gate.** · 1.5 pd · BE
- [ ] ⏸ **BOT-9** 👎 + one sentence on the answer card → correction draft. **Ships with [RAG-9](#rag--rag-qa-engine)** — it attaches to an answer card that does not exist in the MVP.

> **MVP LLM calls go straight to the provider**, because gateway routing ([INT-2](#int--integration-testing-rollout))
> is Phase 2. So there is **no cost-broken-down-by-feature view during the MVP** —
> which makes [INT-10](#int--integration-testing-rollout)'s hard budget alarm a requirement, not a nicety. See
> [open question 5](#blocked-on-decision).

### MVP complete — exit criteria

- [ ] All Milestone A exit criteria still green
- [ ] Bot live: ticket creation with AI draft, status lookup, binding, notifications
- [ ] **Draft confirmation rate > 60%** — hard gate, the sole AI value proof point
- [ ] Chat-created tickets > 20% of new tickets
- [ ] Jira formally decommissioned

---

## Priority 2 · ⏸ GH — Phase 2, started early

### GH · GitHub bidirectional sync v1
**15 pd · 🔒 · ⏸ Phase 2 · §4.7 — spec takes no discount**

> **Why it is outside the MVP:** this is Phase 1's highest-risk item — 3 pw, and
> the PRD's own assessment is *"the one module where getting it wrong destroys
> user trust permanently."* Keeping it outside the MVP boundary means **week 6's
> delivery does not depend on whether sync is solid.** That decoupling is the
> primary benefit of the arrangement.
>
> **Outside the MVP ≠ discounted.** GH-3/5/6 stay 🔒 *within Phase 2*, the
> single-repo two-week pilot stays mandatory, and the weeks 7–12 window yields
> ~6 weeks of observation.

- [ ] **GH-1** GitHub App authorization (**not** a PAT) with a dedicated `relay-sync[bot]` identity. · 1.5 pd · BE
- [ ] **GH-2** Field mapping: title, body, status, assignee, labels, milestone, comments — implemented against the field-ownership matrix so each field has exactly one authoritative side. · 2 pd · BE
- [ ] **GH-3** 🔒 **Triple loop prevention: actor filtering + revision number + content fingerprint. All three — none is optional.** · 2.5 pd · BE
- [ ] **GH-4** Webhook channel for real-time sync. · 2 pd · BE
- [ ] **GH-5** 🔒 5-minute incremental reconciliation. · 1.5 pd · BE
- [ ] **GH-6** 🔒 Daily full reconciliation. · 1 pd · BE
- [ ] **GH-7** Conflict handling: conflicts are **surfaced for human choice, never silently dropped**. · 1.5 pd · BE + FE
- [ ] **GH-8** Rate limiting and degradation: token bucket, exponential backoff, replayable dead-letter queue. · 1.5 pd · BE
- [ ] **GH-9** Sync observability: P95 latency, success rate, conflict count, and a per-ticket sync timeline. · 1.5 pd · BE + FE
- [ ] **GH-11** **State-mapping matrix — settle before GH-2.** [TKT-3](#tkt--tickets--board) has six states; a GitHub issue has open/closed plus labels and `state_reason`. `In Review`, `Blocked`, and `Won't Fix` must land on labels or `state_reason`, which makes them **the highest-frequency source of conflicts and loops.** The field-ownership matrix must state the **bidirectional mapping rules and which side wins on conflict, explicitly** — not left to implementation judgment. ⛔ [open question 17](#blocked-on-decision). · folded into GH-2
- [ ] **GH-12** **Stop-the-bleeding switch.** One control to drop sync to **one-way (GitHub → Relay)** or halt it entirely. When reconciliation finds inconsistency, stop the bleeding first and repair data second — otherwise the corruption keeps amplifying on both sides. · folded into GH-8
- [ ] **GH-10** Expand from the pilot repo to all repos, only after two clean pilot weeks. Tracked under [INT-3](#int--integration-testing-rollout).

**May discount:** attachment re-hosting, Projects v2 field sync, the `relay:meta`
collapsed block (domain-specific fields have no data source yet).

**Prerequisite:** [AC-7](#ac--accounts--triple-identity-binding) GitHub handle binding must land before GH-1.

> **Naming lock.** `relay-sync[bot]` and `<!-- relay:meta:start -->` get written
> into issue bodies on the day sync goes live, and renaming later means rewriting
> historical issue metadata and updating `SyncMapping` (§8.2). Since no issue is
> touched during the MVP, **the deadline is "before GH-1 starts," not "before
> Phase 1 starts."** Still worth locking inside Phase 1: the
> product name, `@Relay` call name, `relay.internal`, and the `RL-` prefix are all
> visible to the team from week 6, so renaming carries its own communication cost.
> §8.3's trademark search belongs in the same window.

---

## Priority 3 · ⏸ RAG + SEED — Phase 2, started early

> **Ordering is deliberately open.** Default is GH before RAG, because sync carries
> more risk and needs the longer observation window. **But if week 6's dual-track
> feedback is "this is just Jira," RAG goes first** — that reaction means the
> missing AI value is already damaging adoption, which §4.0 argues at length is a
> worse failure than a sync delay. Decide at the Milestone A retrospective, with
> real feedback in hand.

### SEED · Knowledge seed import
**5 pd · 🔒 · ⏸ Phase 2 · §4.9①**

> A RAG bot with no seed knowledge answers "I don't know" for a week, and the team
> abandons it before it gets good. **Seeding precedes opening Q&A, not follows it.**

⛔ All of SEED depends on [open question 4](#blocked-on-decision) — where the
documents live and who owns the import. **It is not a Phase 1 startup blocker; it
blocks RAG.**

- [ ] **SEED-1** Importer for existing doc exports (Confluence / Notion / Feishu). Large volume, **P0**. · 1.5 pd · AI
- [ ] **SEED-2** Import from GitHub `docs/`, `README`, `CHANGELOG`. Medium volume, **P0**. · 1 pd · AI
- [ ] **SEED-3** Error-code tables and OpenAPI spec — **structured** ingestion, not slice-based retrieval, so parameters and error codes answer precisely. Small volume, highest answer-hit value, **P0**. · 1.5 pd · AI
- [ ] **SEED-4** Batch FAQ extraction from closed tickets → draft knowledge units. Large volume, **P1**. · 1 pd · AI
- [ ] **SEED-5** **Backfill from the MVP's accumulated signals — cheap and high-value:** ingest logs already flagged by [LOG-9](#log--logs--knowledge-authoring)'s marker, and prioritize the import queue using the questions [BOT-8](#bot--im-bot) logged. Real demand beats a guessed import list. · folded into SEED-1/2

**Hard gate:** **≥ 100 knowledge units before Q&A opens to the team.** If the count
is short, keep importing — do not ship early.

> **What this gate risks.** It gates the **Q&A command**, not the bot: the bot is
> already live on ticket creation and the team's trust in it is already
> established. **Cold start is not a one-shot bet on launch day** — Q&A opens
> independently once knowledge is ready. That is a benefit of separating the two.

### RAG · RAG Q&A engine
**15 pd · ⏸ Phase 2 · §4.9**

> **Why it is Phase 2:** 4 pw (engine 3 + seeding 1), the largest single block
> outside the MVP. The cost is stated plainly in §0.3 判断五 and §4.0 — **the
> MVP's AI value shrinks to the ticket draft.** Accepted, not eliminated.
>
> Three items stay 🔒 *within Phase 2*: the ≥100-unit gate, the correction entry,
> and mandatory-citations + refusal-first.

- [ ] **RAG-1** Knowledge unit model, and Markdown-marker → knowledge-unit extraction (the "write a doc, train the bot" path). Consumes [LOG-9](#log--logs--knowledge-authoring)'s markers, including the full backlog. · 2 pd · AI
- [ ] **RAG-2** Chunking by Markdown heading hierarchy with fixed overlap, preserving the heading path as context. · 2 pd · AI
- [ ] **RAG-3** Embedding pipeline and vector store, with tenant filtering inside the query (depends on MT-5). ⛔ model tier depends on [open question 5](#blocked-on-decision). · 2 pd · AI
- [ ] **RAG-4** BM25 keyword index. · 1 pd · AI
- [ ] **RAG-5** Hybrid recall combining BM25 and vector, merged by weighted recall score. **No rerank model** — score weighting is sufficient. · 2 pd · AI
- [ ] **RAG-6** Generation with **mandatory citations**: every assertion carries a source link; insufficient evidence produces an explicit refusal. · 2 pd · AI
- [ ] **RAG-7** Refusal path: log the gap as a knowledge-gap ticket and always give a next step — *"no coverage yet, recorded as gap #412; you can ask @zhangsan or file a ticket."* Refusals will be frequent at low knowledge density, and **that is correct behavior**. · 1.5 pd · AI
- [ ] **RAG-8** Metadata, four fields only: `module`, `scope`, `owner`, `updated_at`. Authority tiers, version ranges, and validity windows are deferred to Phase 3. · 1 pd · AI
- [ ] **RAG-9** 🔒 **Correction entry point:** 👎 + one sentence on the answer card → knowledge revision draft → pushed to the owner → confirmed → live. This is where the knowledge flywheel starts; conflict detection, regression validation, and staged activation are deferred to Phase 3, but *"see a wrong answer, fix it on the spot"* must exist on the first day Q&A is open. · 1.5 pd · AI
- [ ] **RAG-10** 🔒 **Minimum inbound redaction — must land before or with the first knowledge ingest.** Regex + entropy detection for API keys, tokens, internal IPs, and phone numbers; on match, **block the ingest and alert**. §2.6: *ingesting without redaction permanently fixes the leak risk into the retrieval layer.* Full AI-DLP can take its time; the irreversible part cannot wait. ⛔ [open question 18](#blocked-on-decision). · 1.5 pd · AI
  > **Note:** with RAG in Phase 2 **the MVP has no such gap at all** — no vector store means no risk fixed in place. What it leaves is a **hard ordering constraint inside Phase 2**: redaction before first ingest.

- [ ] **RAG-11** **Minimum wrong-answer measurement.** Wrong-answer rate < 5% is a veto-level acceptance metric, but the AI eval system is Phase 3 — so today **there is no way to measure it.** Minimum viable instrumentation: retain Q&A logs, human-review N sampled answers weekly, and count 👎 into the denominator. Without this the metric is unfillable and effectively nonexistent. · 1 pd · QA

> **Three-stage rollout, adjusted (§4.9⑤):** on first launch the bot may answer
> directly in **internal** channels — internal channels are fault-tolerant,
> mandatory citations plus refusal-first make errors immediately visible, shadow
> mode needs annotation labor the team does not have, live Q&A traffic is itself
> the eval set being accumulated, and **[BOT-8](#bot--im-bot)'s logged questions already
> provide a real pre-launch self-test set**, partly covering for the absent shadow
> mode. **This relaxation does not extend to external customer channels**, which
> still require the full shadow → copilot → autonomous progression in Phase 4.

---

## INT · Integration, testing, rollout
**7.5 pd MVP + 3.5 pd ⏸ · spans both milestones**

- [ ] **INT-1** CI pipeline, with the MT-6 cross-tenant gate wired in as blocking, plus MT-2's schema lint. · 1 pd · QA · *A*
- [ ] **INT-5** End-to-end suites over the MVP critical flow: **chat → draft → confirm → ticket → notification**. ⏸ *the ticket → GitHub → back leg and the doc → knowledge → answer → correction leg arrive with GH and RAG.* · 1 pd · QA · *B*
- [ ] **INT-6** Milestone A dogfood rollout and dual-track operating guidance for the team. · 1 pd · QA · *A*
- [ ] **INT-7** Jira decommission plan and cutover: data migration, freeze date, fallback path. **Prerequisite:** automated backups plus one *real restore drill* on the self-hosted PostgreSQL, completed before the freeze date — decommissioning Jira makes Relay the only copy of the tickets. · 1 pd · QA · *B*
- [ ] **INT-8** Instrument the §4.11① acceptance metrics as live dashboards, so acceptance is measured rather than asserted. **Includes pinning the denominators** — weekly-active-creator share and chat-created-ticket share are both gameable by a few people, so define the base (headcount vs bound users) and the window (calendar week) in the dashboard, not during the acceptance review. · 1.5 pd · BE + QA · *B*
- [ ] **INT-9** **Identity-binding drive — binding coverage > 90%.** A Milestone A gate and the ceiling on bot usability (§0.3 判断三). Run it in weeks 5–6 alongside dual-track, **not** after the bot ships. Covers the tracking dashboard, reminder flow, and the admin backfill path. · 1 pd · BE + QA · *A*
- [ ] **INT-10** **Hard AI budget alarm.** With gateway routing in Phase 2, MVP LLM calls go straight to the provider and there is **no per-feature cost view**. A hard monthly ceiling with alerting is the only backstop. ⛔ [open question 5](#blocked-on-decision). · 1 pd · BE · *B*
- [ ] ⏸ **INT-2** Route all platform AI calls through the in-house gateway, with **cost broken down by feature** — the dogfooding argument: cost becomes visible, no new vendor is introduced, and your own tooling is the canary. **Phase 2**, which also means the MVP forgoes the dogfooding argument. · 1.5 pd · AI
- [ ] ⏸ **INT-3** Operate the single-repo sync pilot: two weeks of observation, conflict triage, reconciliation review, then all-repo expansion (GH-10). · 1 pd · BE + QA
- [ ] ⏸ **INT-4** Model A/B and own-tooling canary setup. Depends on INT-2. · 1 pd · AI

---

## Optional / stretch

- [ ] **OPT-1** Passive monitoring with a **DM** nudge (§4.8① compromise): the bot listens but **never speaks in the channel** — on a high-confidence problem statement it DMs the speaker, *"about that issue you raised in XX — want a ticket for it?"*, with one-click creation. Zero interruption, and it accumulates labeled data (user clicked create = positive sample) for Phase 3 automatic detection. · ~2 pd · Not counted in the baseline. **First item on the cut list — dropping it does not affect MVP validity.** ⛔ [open question 3](#blocked-on-decision).

---

## Acceptance criteria

Instrumented by [INT-8](#int--integration-testing-rollout).

### ① MVP acceptance (§4.11①) — three hard gates

Hard gates are **cross-tenant leakage 0**, **binding coverage > 90%**, and **Jira
decommissioned 100%**. All three are independent of every ⏸ epic — matching the
design intent that Milestone A is a deterministic delivery.

| Area | Metric | Target | Judged at | ✓ |
|---|---|---|---|---|
| **Security 🔒** | Cross-tenant read/write | **0** — CI gate green, plus one pre-launch penetration spot-check | A | [ ] |
| **Foundation 🔒** | WeCom userid binding coverage | **> 90%** — ceiling on bot usability | end of A | [ ] |
| **Adoption 🔒** | Jira decommissioned | **100%**, all tickets in Relay for 2 consecutive weeks | B | [ ] |
| Adoption | Weekly active creators | > 70% | B | [ ] |
| Bot | Tickets created from chat | > 20% of new tickets | B | [ ] |
| **Bot 🔒** | Drafts confirmed without major edits | **> 60%** — sole AI value proof point, no discount | B | [ ] |
| Foundation | Logs flagged "add to knowledge base" | ≥ 30 meaningful positives (preheats Phase 2 cold start) | B | [ ] |
| Foundation | Questions logged by BOT-8 | ≥ 50 (demand samples for SEED) | B | [ ] |

> ≥100 knowledge units, sync conflict rate <1%, and wrong-answer rate <5% are
> Phase 2 gates (②), judged with their epics. The three MVP gates above share a
> property those do not: **none depends on a high-risk item.**

### ② ⏸ Phase 2 (§4.11②) — targets take no discount

| Area | Metric | Target | ✓ |
|---|---|---|---|
| Sync | P95 sync latency | < 30s (long-term 10s) | [ ] |
| **Sync 🔒** | Conflict rate / data inconsistency | **< 1%** / < 0.1% | [ ] |
| Sync | Misses caught by reconciliation | < 5/week, all auto-repairable | [ ] |
| Q&A | First-answer hit rate | > 40% | [ ] |
| **Q&A 🔒** | Wrong-answer rate | **< 5%** | [ ] |
| Q&A | Refusals offering a useful next step | 100% | [ ] |
| **Knowledge 🔒** | Units when Q&A opens | **≥ 100** | [ ] |
| Knowledge | Corrections in the first month | > 20 | [ ] |

> The 40% hit-rate target is deliberately low. Chasing a high hit rate on sparse
> knowledge pushes you to lower the refusal threshold, which raises the
> wrong-answer rate — **a strictly worse outcome**. Watch the wrong-answer rate and
> the correction count, not the hit rate. Do not evaluate against §6's long-term
> targets.
>
> ⚠️ **Wrong-answer rate has no measurement mechanism today** — see [RAG-11](#rag--rag-qa-engine). It
> is veto-level, and the eval system is Phase 3. Instrument it before opening Q&A
> or the metric is unfillable.
>
> ⚠️ **Metrics dependent on telemetry data are unmeasurable throughout Phase 1**
> (alert convergence, briefing latency, attribution hit rate, all ChatOps metrics
> in §6). Their data sources arrive in Phase 2. Only the eight rows in ① are
> actually observable during Phase 1.

---

## If Milestone A needs further compression

> Milestone A is 11 pw over 6 weeks with roughly **4 weeks of slack, so this list
> will probably go unused.** It exists so that an unexpected slip has a pre-agreed
> order instead of an argument.

| Order | Cut | Consequence |
|---|---|---|
| 1 | OPT-1 passive DM nudge | None — it was always a bonus |
| 2 | LOG-7 templates (keep free-form) | Mildly worse experience |
| 3 | TKT-6 board drag-and-drop (keep list + status dropdown) | Worse experience; PMs will object |
| 4 | BOT-5 `#331` status lookup | One fewer convenience path |
| 5 | AC-7 GitHub handle OAuth binding | Limited MVP value with GH in Phase 2 — **but must land before GH-1** |
| 6 | TKT-2 UI show/hide layer (keep the model fields) | None — the fields are empty in the MVP anyway |

**Never cut (MVP):**

| 🔒 | Why |
|---|---|
| MT — multi-tenant data model | Retrofitting later is a multi-week refactor |
| TA — telemetry adapter interface | Without the abstraction, Phase 2 reworks four features. Un-cuttable *even though it has nothing to demo* |
| AC-6 — WeCom userid binding | Cut it and the bot is entirely unusable: creation, notification, and attribution all depend on it |
| BOT-3 — AI ticket draft | The MVP's only AI value proof point (§0.3 判断五) |
| LOG-9 — knowledge-base marker field | ~0.5 pd; cutting it saddles Phase 2 with a full re-annotation pass |

**Never cut (inside Phase 2 — later scheduling does not relax these):**

| 🔒 | Why |
|---|---|
| GH-3, GH-5, GH-6 — loop prevention and reconciliation | Cut it and sync breaks; user trust does not come back |
| SEED P0 items and the ≥100-unit gate | Cut it and Q&A refuses everything in week one; the team gives up |
| RAG-9 correction entry | Cut it and knowledge never updates; the flywheel never turns |
| RAG-10 minimum inbound redaction | Ingest without redaction permanently fixes the leak risk into the retrieval layer |

---

## Blocked on decision

**Nothing on this list blocks week 1 any more.** #2 is settled (first target team =
the **AI Gateway team**, unblocking TA-2 and TKT-2) and #15 is settled (AI-bot API
mode is enabled; the carrier is decided and BOT-3/BOT-4 are unblocked). What remains
here is #16 by week 5 and #5 by week 7. The knowledge-seed question
([#4](#blocks-later-work)) blocks RAG, not week 1.

**D-0 (tech-stack selection) is now settled too**, so nothing gates week 1 at all.
The stack: self-hosted **PostgreSQL** carrying four jobs — relational store, Chinese
full-text (pgroonga/zhparser), vectors (pgvector), and the queue (`SKIP LOCKED`) —
with **row-level security as the tenant-filter enforcement point** rather than the ORM,
because an ORM cannot close the raw-SQL escape that MT-3 requires closing. Backend
**Python / FastAPI / SQLAlchemy 2.x + Alembic / Pydantic v2**, frontend **Vue 3 +
TypeScript**, with FastAPI's OpenAPI schema generating the frontend's types. Operated
by the AI Gateway team. One consequence to staff: self-hosting means backups are ours
— see INT-7, which now requires a real restore drill before the Jira freeze date.

### Blocks Phase 1 startup

| # | Question | Blocks | Needed by |
|---|---|---|---|
| ~~1~~ | ✅ **Settled**: 12 weeks, with the MVP boundary at Milestone A. What remains open is the weeks 7–12 ordering — GH first or RAG first — decided at the Milestone A retrospective with real feedback | weeks 7–12 plan | end of week 6 |
| ~~2~~ | ✅ **Settled: the first target team is the AI Gateway team (one team).** TA-2's first adapter is that team's own gateway; TKT-2 keeps generic fields default-on for all tenants and enables `gateway_version` / `routing_policy` for the first tenant only. **Residual risk**: the first team also builds the gateway, so the generic-vs-domain field boundary has no control group — do not move it before a second team onboards | TA-2, TKT-2 | ✅ done |
| ~~15~~ | ✅ **Settled: AI-bot API mode is enabled for this enterprise.** Group webhooks cannot receive `@`, so the carrier is the AI bot for inbound and in-conversation replies plus a self-built app (`message/send`) for platform-initiated outbound — `response_url` is scoped to one conversation. The callback carries `quote`, `msgid`, and a 6-minute `response_url` window, and template-card clicks fire `template_card_event`, so BOT-3's in-channel card and BOT-4 both hold. Residual items moved into [BOT-1](#bot--im-bot)'s week-1 spike — userid semantics is the sharp one, it gates `identity_binding` | BOT-1, BOT-3, BOT-4 | ✅ done |
| **16** | **Who drives binding coverage > 90%, and when?** An acceptance gate and the ceiling on bot usability. Recommend weeks 5–6, alongside dual-track | INT-9 | before week 5 |
| 5 | **Monthly AI cost ceiling?** Sets default model tier and cache aggressiveness. Gateway routing is Phase 2, so there is no per-feature cost view and a hard alarm is the only backstop | INT-10, RAG-3, RAG-5 | before week 7 |
| 3 | Include passive channel monitoring? Needs the team's comfort with a bot reading messages | OPT-1 | before week 9 |

### Blocks later work

| # | Question | Blocks | Needed by |
|---|---|---|---|
| 6 | Who executes account deactivation for departures while there is no SSO? (Security-audit finding if unowned) | AC-8 ops handbook | before rollout |
| 4 | **Where does the knowledge seed come from, and who imports it?** *(blocks SEED, not the Phase 1 start)* | all of SEED | before RAG starts |
| **17** | **The GitHub state-mapping matrix** — six Relay states → GitHub open/closed + label/`state_reason`. Which side wins on conflict? | GH-2, GH-11 | before GH-1 |
| **18** | **Minimum inbound-redaction scope and the false-positive allowlist process** — must precede the first knowledge ingest | RAG-10 | before SEED-1 |

---

## Out of scope

### ⏸ Phase 2 (spec retained in full — scheduled later, not cancelled)

GitHub Issue bidirectional sync v1 · RAG Q&A engine and knowledge seeding ·
routing platform AI calls through the in-house gateway (cost breakdown by feature,
model A/B) · vectorization and indexing behind the log "add to knowledge base"
marker · the bot's Q&A and 👎 correction commands.

### Not in Phase 1 at all (§4.0)

Alert ingest and Auto-Ticket · ChatOps (Tier 1 and Tier 2) · external customer
channels · sentiment analysis and escalation · change attribution · environment
snapshots · SLA clocks · on-call rotation · change-management loop · AI-DLP ·
fine-grained RBAC · L4 external link sharing · real-time collaborative editing ·
trace drill-down · full state machine · AI-assisted writing · MCP server · AI eval
system and release gates · GitHub docs co-sync · multi-tenant **product** features
(billing, self-service admin, cross-tenant sharing).

> ⚠️ **The last line is the one that gets misread.** Multi-tenant *product features*
> are out; the **multi-tenant data model ([MT](#mt--multi-tenant-data-model)) is mandatory** (§4.1).
> Getting this wrong costs a multi-week refactor.

Phase 1 defends two goals: **replace Jira**, and **let the team feel AI value for
the first time**. The second rests on a single feature ([BOT-3](#bot--im-bot)) — which is
why that feature carries a hard gate and cannot be cut.
