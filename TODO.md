# Relay · Phase 1 (MVP) Task Breakdown

Derived from [Relay-PRD-v0.4.md](docs/Relay-PRD-v0.4.md) §4 (MVP spec), §4.10
(schedule), §4.11 (acceptance), §5 (roadmap).

| | |
|---|---|
| **Scope** | Phase 1 / MVP only. Phase 2–4 items are out of scope — see [Out of scope](#out-of-scope). |
| **Duration** | 12 weeks, two milestones |
| **Budget** | ≈ 21 person-weeks ≈ 106 person-days |
| **Team** | 2 backend · 1 frontend · 1 AI · 0.5 QA |
| **Goal** | Get the team off Jira, and let them feel in their group chat that the platform knows what they're working on (§4.0) |

---

## How to read this file

- **ID** — stable task identifier. Reference these in commits, branches, and tickets (`RL-` prefixed once the tracker exists).
- **Effort** — person-days. Epic totals match the §4.10① estimate exactly; sub-task splits are this document's own decomposition.
- **Role** — BE backend · FE frontend · AI AI/ML · QA quality.
- **Blocks** — a task that cannot start until an [open decision](#blocked-on-decision) lands is marked ⛔.
- **🔒** — on the "cannot be cut" list (§4.10③). These survive any compression.

---

## Sequencing rules (non-negotiable)

1. **MT and TA come first, weeks 1–2, ahead of everything else — not in parallel with feature work.**
   One table missing `tenant_id` amplifies rework across every module built after
   it (§4.10②). This is the single ordering constraint the PRD states in
   imperative form.
2. **SEED is the hidden critical path.** Knowledge seeding is not "tidying up
   docs" — the bot cannot open to the team below 100 units, so SEED must run
   *concurrently with* RAG development, not after it. Start SEED-1 no later than
   week 7.
3. **GitHub sync pilots on one repo for two weeks before expanding.** Sync is the
   one module where getting it wrong destroys trust permanently (§4.7). Single-repo
   pilot lands in Milestone A specifically to buy a 6-week observation window.
4. **The bot does not open to the team until the ≥100-unit gate passes.** A RAG
   bot with no seed knowledge answers "I don't know" all week and gets abandoned
   before it improves (§4.9①).

---

## Effort summary

| Epic | Title | Effort | Milestone |
|---|---|---:|---|
| [MT](#mt--multi-tenant-data-model) | Multi-tenant data model 🔒 | 8 pd | A (wk 1–2) |
| [TA](#ta--telemetry-adapter-interface) | Telemetry adapter interface 🔒 | 5 pd | A (wk 1–2) |
| [AC](#ac--accounts--triple-identity-binding) | Accounts + triple identity binding | 10 pd | A |
| [LOG](#log--logs--knowledge-authoring) | Logs / knowledge authoring | 15 pd | A |
| [TKT](#tkt--tickets--board) | Tickets + board | 13 pd | A |
| [GH](#gh--github-bidirectional-sync-v1) | GitHub bidirectional sync v1 🔒 | 15 pd | A build, B rollout |
| [SEED](#seed--knowledge-seed-import) | Knowledge seed import 🔒 | 5 pd | B (start wk 7) |
| [RAG](#rag--rag-qa-engine) | RAG Q&A engine | 15 pd | B |
| [BOT](#bot--im-bot) | IM bot | 10 pd | B |
| [INT](#int--integration-testing-rollout) | Integration, testing, rollout | 10 pd | A + B |
| | **Total** | **106 pd** | |

> The 106 pd of engineering effort sits against ~54 person-weeks of nominal team
> capacity over 12 weeks. The PRD's "12 weeks holds but has no slack" (§4.10①)
> therefore assumes a large share of capacity goes to coordination, review, and
> integration loss rather than to these line items. Worth validating against the
> team's actual historical throughput before committing the date externally —
> if the real overhead multiplier is lower, there is more room than the PRD claims;
> if it's higher, the 8-week cut list (§4.10③) matters sooner.

### Indicative week map

| Week | Focus |
|---|---|
| 1–2 | MT, TA (exclusive) |
| 3–4 | AC, LOG start, TKT start |
| 5–6 | LOG finish, TKT finish, GH build, single-repo pilot begins |
| 7–8 | SEED start, RAG core, GH pilot observation |
| 9–10 | RAG finish, BOT build, GH all-repo expansion |
| 11–12 | Bot rollout behind the 100-unit gate, acceptance instrumentation, Jira cutover |

---

# Milestone A — week 6 · internally usable

Exit state: the team runs Relay and Jira in parallel (dual-track).

## MT · Multi-tenant data model
**8 pd · 🔒 · weeks 1–2 · §4.1**

MVP builds the *data model* layer only. Per-tenant billing, self-service tenant
admin, cross-tenant sharing policy, per-tenant config isolation, and per-tenant
model routing are Phase 2+ product features.

- [ ] **MT-1** Define the tenant entity and audit every business entity for tenancy — users, spaces, logs, tickets, knowledge units, comments, attachments, audit records. Produce the definitive entity list; nothing gets added later without a `tenant_id`. · 1 pd · BE
- [ ] **MT-2** Add `tenant_id` to every table in the MT-1 list, with a migration baseline and a schema-lint rule that fails CI on any new table lacking it. · 2 pd · BE
- [ ] **MT-3** Inject tenant filtering at the ORM / repository layer so it is structurally impossible to bypass. **Never delegated to business code, and never to a prompt.** · 2 pd · BE
- [ ] **MT-4** Composite indexes with `tenant_id` as the leading column, across all tenant-scoped query paths. · 0.5 pd · BE
- [ ] **MT-5** Vector-store tenant isolation — the filter goes *inside* the query predicate, not applied to results after retrieval. · 1 pd · AI
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

- [ ] **TA-1** Define the `TelemetryAdapter` interface and its data contracts: `queryMetrics`, `getTrace`, `sampleRequests`, `listRecentChanges`, `getProviderHealth`, `getCostBreakdown`. · 1 pd · BE
- [ ] **TA-2** Implement the in-house AI Gateway adapter against that interface. · 2 pd · BE
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
- [ ] **AC-6** WeCom userid binding: user DMs `绑定` to the bot → bot returns a code → user enters it on the settings page. · 1.5 pd · BE
- [ ] **AC-7** GitHub handle binding via GitHub OAuth (most reliable; no hand-typed handles). · 1 pd · BE
- [ ] **AC-8** Unbound-user degradation matrix, implemented explicitly: chat Q&A **allowed** (Q&A needs no identity) · chat ticket creation **refused** with a DM guiding binding (a ticket must have a real reporter) · GitHub sync leaves unmapped users as plain text and **never mis-@s an unrelated GitHub account**, flagging Admin to fill the mapping · notifications degrade to in-app + email. · 1 pd · BE

**Done when:** every path in AC-8 is exercised by a test, including the
never-mis-mention guarantee.

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
- [ ] **LOG-7** Templates: daily report, investigation record, incident retrospective, design doc. · 1 pd · FE
- [ ] **LOG-8** Full-text search. · 2 pd · BE
- [ ] **LOG-9** "Add to knowledge base" checkbox → enqueue into the RAG index. This is the MVP's **primary** knowledge source, so the hook ships in Milestone A even though RAG lands in B. · 0.5 pd · BE

**Not in MVP:** L4 external links + DLP scanning (external links are the largest
leak surface — deliberately not opened), real-time collaborative editing (CRDT
cost; MVP uses an edit lock + conflict prompt), `!trace:` / `!metric:` inline
syntax (needs gateway integration), AI-assisted writing.

## TKT · Tickets + board
**13 pd · §4.7, §4.3**

- [ ] **TKT-1** Ticket entity and MVP fields: type (Bug/Feature/Task), title, description, status, priority P0–P3, assignee, reporter, labels, iteration, linked PR, comments. · 1.5 pd · BE
- [ ] **TKT-2** Configurable AI context schema (§4.3): reserve `trace_id[]`, `provider[]`, `model[]`, `prompt_version`, `deployment`, `error_class`, `eval_run`, `token_cost`, `blast_radius`, `tenant[]` as generic fields, and `gateway_version` / `routing_policy` as config-enabled domain fields. Fields are **reserved in the data model, populated by adapters, and show/hide-configurable in the UI** — no custom-field editor in MVP, and no auto-population until Phase 2 alert ingest provides a data source. ⛔ default field set depends on [open question 2](#blocked-on-decision). · 2 pd · BE
- [ ] **TKT-3** State machine: `Todo → In Progress → In Review → Done`, plus `Blocked` and `Won't Fix`. The full machine (Triage / Verifying / Reopened) is deferred. · 1.5 pd · BE
- [ ] **TKT-4** Comments and @mentions. · 1 pd · BE
- [ ] **TKT-5** List view with filters. · 1.5 pd · FE
- [ ] **TKT-6** Board view grouped by status, with drag-and-drop. · 2.5 pd · FE
- [ ] **TKT-7** "My tickets" view. · 0.5 pd · FE
- [ ] **TKT-8** Labels, iterations, PR link field. · 1 pd · BE
- [ ] **TKT-9** Ticket detail page, `RL-` numbering, stable permalinks (`https://relay.internal/t/331`). · 1.5 pd · FE

**Not in MVP:** Gantt and calendar views.

## GH · GitHub bidirectional sync v1
**15 pd · 🔒 · §4.7 — highest-risk item in the MVP; the spec takes no discount**

- [ ] **GH-1** GitHub App authorization (**not** a PAT) with a dedicated `relay-sync[bot]` identity. · 1.5 pd · BE
- [ ] **GH-2** Field mapping: title, body, status, assignee, labels, milestone, comments — implemented against the field-ownership matrix so each field has exactly one authoritative side. · 2 pd · BE
- [ ] **GH-3** 🔒 **Triple loop prevention: actor filtering + revision number + content fingerprint. All three — none is optional.** · 2.5 pd · BE
- [ ] **GH-4** Webhook channel for real-time sync. · 2 pd · BE
- [ ] **GH-5** 🔒 5-minute incremental reconciliation. · 1.5 pd · BE
- [ ] **GH-6** 🔒 Daily full reconciliation. · 1 pd · BE
- [ ] **GH-7** Conflict handling: conflicts are **surfaced for human choice, never silently dropped**. · 1.5 pd · BE + FE
- [ ] **GH-8** Rate limiting and degradation: token bucket, exponential backoff, replayable dead-letter queue. · 1.5 pd · BE
- [ ] **GH-9** Sync observability: P95 latency, success rate, conflict count, and a per-ticket sync timeline. · 1.5 pd · BE + FE

**MVP may discount:** attachment re-hosting, Projects v2 field sync, the
`relay:meta` collapsed block (domain-specific fields have no data source yet).

> **Naming is frozen before development starts.** `relay-sync[bot]` and
> `<!-- relay:meta:start -->` get written into GitHub issue bodies on day one;
> renaming later means rewriting historical issue metadata and updating
> `SyncMapping` (§8.2).

### Milestone A exit criteria

- [ ] `tenant_id` present on every entity; cross-tenant CI gate green
- [ ] No direct gateway API calls outside the adapter
- [ ] Accounts, roles, and both identity bindings working end to end
- [ ] Logs complete: dual mode, versioning, L0–L3 sharing, search
- [ ] Tickets + board usable for real work
- [ ] GitHub sync running on **one** pilot repo
- [ ] Platform AI calls routed through the in-house gateway
- [ ] Team begins dual-track use (Relay alongside Jira)

---

# Milestone B — week 12 · MVP complete

Exit state: Jira decommissioned.

## SEED · Knowledge seed import
**5 pd · 🔒 · start week 7 · §4.9①**

> A RAG bot with no seed knowledge answers "I don't know" for a week, and the team
> abandons it before it gets good. **Seeding runs before the bot opens, not after.**

⛔ All of SEED depends on [open question 4](#blocked-on-decision) — where the
documents live and who owns the import.

- [ ] **SEED-1** Importer for existing doc exports (Confluence / Notion / Feishu). Large volume, **P0**. · 1.5 pd · AI
- [ ] **SEED-2** Import from GitHub `docs/`, `README`, `CHANGELOG`. Medium volume, **P0**. · 1 pd · AI
- [ ] **SEED-3** Error-code tables and OpenAPI spec — **structured** ingestion, not slice-based retrieval, so parameters and error codes answer precisely. Small volume, highest answer-hit value, **P0**. · 1.5 pd · AI
- [ ] **SEED-4** Batch FAQ extraction from closed tickets → draft knowledge units. Large volume, **P1** — and first on the compression cut list. · 1 pd · AI

**Hard gate:** **≥ 100 knowledge units before the bot opens to the team.** If the
count is short, keep importing — do not ship early.

## RAG · RAG Q&A engine
**15 pd · §4.9**

- [ ] **RAG-1** Knowledge unit model, and Markdown-marker → knowledge-unit extraction (the "write a doc, train the bot" path). · 2 pd · AI
- [ ] **RAG-2** Chunking by Markdown heading hierarchy with fixed overlap, preserving the heading path as context. · 2 pd · AI
- [ ] **RAG-3** Embedding pipeline and vector store, with tenant filtering inside the query (depends on MT-5). · 2 pd · AI
- [ ] **RAG-4** BM25 keyword index. · 1 pd · AI
- [ ] **RAG-5** Hybrid recall combining BM25 and vector, merged by weighted recall score. **No rerank model in MVP** — score weighting is sufficient. · 2 pd · AI
- [ ] **RAG-6** Generation with **mandatory citations**: every assertion carries a source link; insufficient evidence produces an explicit refusal. · 2 pd · AI
- [ ] **RAG-7** Refusal path: log the gap as a knowledge-gap ticket and always give a next step — *"no coverage yet, recorded as gap #412; you can ask @zhangsan or file a ticket."* Refusals will be frequent at MVP knowledge density, and **that is correct behavior**. · 1.5 pd · AI
- [ ] **RAG-8** Metadata, four fields only: `module`, `scope`, `owner`, `updated_at`. Authority tiers, version ranges, and validity windows are deferred. · 1 pd · AI
- [ ] **RAG-9** 🔒 **Correction entry point:** 👎 + one sentence on the answer card → knowledge revision draft → pushed to the owner → confirmed → live. This is where the knowledge flywheel starts; conflict detection, regression validation, and staged activation are deferred to Phase 2/3, but *"see a wrong answer, fix it on the spot"* must exist on day one. · 1.5 pd · AI

> **Three-stage rollout, adjusted for MVP (§4.9⑤):** the bot may answer directly
> in **internal** channels, because internal channels are fault-tolerant,
> mandatory citations plus refusal-first make errors immediately visible, shadow
> mode needs annotation labor the team does not have, and MVP Q&A traffic is
> itself the eval set being accumulated. **This relaxation does not extend to
> external customer channels**, which still require the full shadow → copilot →
> autonomous progression in Phase 4.

## BOT · IM bot
**10 pd · §4.8**

Trigger is `@Relay` only. **No passive whole-channel monitoring** — false-positive
cost is too high at MVP (the bot interrupting a discussion gets it muted within a
week, and muted is unrecoverable), reading all messages needs psychological
buy-in even internally, per-message model cost does not match the benefit, and
there is no eval baseline yet for "is this a problem."

- [ ] **BOT-1** WeCom app and bot registration; `@` mention parsing and message routing. · 1.5 pd · BE
- [ ] **BOT-2** `@Relay <question>` → RAG answer card with cited sources; refuse below the confidence threshold. Gated on the 100-unit seed gate. · 1.5 pd · BE
- [ ] **BOT-3** `@Relay 建单 <description>` → ticket draft card → **Create / Edit / Cancel**, auto-expiring after 5 minutes. Flow: identity check (unbound → DM guidance, flow ends) → context capture → AI-drafted title/description/type/priority → confirmation card → create → sync to GitHub → card updates in place with the ticket link. **Never auto-create; confirmation is always required** — a ticket is an entity with an owner and an SLA, and a wrong ticket costs far more than a missing one. · 2.5 pd · BE
- [ ] **BOT-4** Quote a message + `@Relay 建单` → capture the quoted message plus surrounding channel context as the description. · 1 pd · BE
- [ ] **BOT-5** `@Relay #331` → ticket status card (status, assignee, last update). *First on the cut list at #5.* · 1 pd · BE
- [ ] **BOT-6** `@Relay 绑定` → DM the identity-binding flow (pairs with AC-6). · 0.5 pd · BE
- [ ] **BOT-7** WeCom app-message notifications for assignment, @mention, and status change, with a 5-minute aggregation window to prevent flooding. Tiered routing, quiet hours, and subscription rules are Phase 2. · 2 pd · BE

## GH rollout (Milestone B)

- [ ] **GH-10** Expand sync from the pilot repo to all repos, only after two clean pilot weeks. Tracked under [INT-3](#int--integration-testing-rollout). · see INT

### Milestone B exit criteria

- [ ] GitHub sync live across all repos, with loop prevention and reconciliation proven
- [ ] WeCom bot live: Q&A, ticket creation, status lookup
- [ ] RAG Q&A with mandatory citations and a working correction path
- [ ] ≥ 100 knowledge units
- [ ] WeCom app notifications live
- [ ] Jira formally decommissioned

---

## INT · Integration, testing, rollout
**10 pd · spans both milestones**

- [ ] **INT-1** CI pipeline, with the MT-6 cross-tenant gate wired in as blocking. · 1 pd · QA · *A*
- [ ] **INT-2** Route all platform AI calls through the in-house gateway, with **cost broken down by feature** — the dogfooding argument that holds for any team: cost becomes visible, no new vendor is introduced, and your own tooling is the canary. · 1.5 pd · AI · *A*
- [ ] **INT-3** Operate the single-repo sync pilot: two weeks of observation, conflict triage, reconciliation review, then all-repo expansion (GH-10). · 1 pd · BE + QA · *A→B*
- [ ] **INT-4** Model A/B and own-tooling canary setup. · 1 pd · AI · *B*
- [ ] **INT-5** End-to-end test suites over the critical flows: chat → ticket → GitHub → back, and doc → knowledge unit → answer → correction → live. · 2 pd · QA · *B*
- [ ] **INT-6** Milestone A dogfood rollout and dual-track operating guidance for the team. · 1 pd · QA · *A*
- [ ] **INT-7** Jira decommission plan and cutover: data migration, freeze date, fallback path. · 1 pd · QA · *B*
- [ ] **INT-8** Instrument the §4.11 acceptance metrics as live dashboards, so acceptance is measured rather than asserted. · 1.5 pd · BE + QA · *B*

---

## Optional / stretch

- [ ] **OPT-1** Passive monitoring with a **DM** nudge (§4.8① compromise): the bot listens but **never speaks in the channel** — on a high-confidence problem statement it DMs the speaker, *"about that issue you raised in XX — want a ticket for it?"*, with one-click creation. Zero interruption, and it accumulates labeled data (user clicked create = positive sample) for Phase 3 automatic detection. · ~2 pd · Not counted in the 106 pd baseline. **First item on the cut list — dropping it does not affect MVP validity.** ⛔ [open question 3](#blocked-on-decision).

---

## Acceptance criteria (§4.11)

Instrumented by INT-8. Three are hard gates: ≥100 knowledge units, sync conflict
rate <1%, wrong-answer rate <5%.

| Area | Metric | Target | ✓ |
|---|---|---|---|
| Adoption | Jira decommissioned | 100%, all tickets in Relay for 2 consecutive weeks | [ ] |
| Adoption | Weekly active creators | > 70% | [ ] |
| Sync | P95 sync latency | < 30s | [ ] |
| Sync | Conflict rate / data inconsistency | **< 1%** / < 0.1% | [ ] |
| Sync | Misses caught by reconciliation | < 5/week, all auto-repairable | [ ] |
| Bot | Tickets created from chat | > 20% of new tickets | [ ] |
| Bot | Drafts confirmed without major edits | > 60% | [ ] |
| Q&A | First-answer hit rate | > 40% | [ ] |
| Q&A | Wrong-answer rate | **< 5%** | [ ] |
| Q&A | Refusals offering a useful next step | 100% | [ ] |
| Knowledge | Units at launch | **≥ 100** | [ ] |
| Knowledge | Corrections triggered during MVP | > 20 | [ ] |

> The 40% hit-rate target is deliberately low. Chasing a high hit rate on sparse
> knowledge pushes you to lower the refusal threshold, which raises the
> wrong-answer rate — **a strictly worse outcome**. At this stage, watch the
> wrong-answer rate and the correction count, not the hit rate. Do not evaluate
> MVP against the long-term targets in §6.

---

## If compressed to 8 weeks

Cut in this order (§4.10③), and no further:

| Order | Cut | Consequence |
|---|---|---|
| 1 | OPT-1 passive DM nudge | None — it was always a bonus |
| 2 | SEED-4 batch FAQ extraction | Smaller seed corpus; compensate with doc import |
| 3 | LOG-7 templates (keep free-form) | Mildly worse experience |
| 4 | TKT-6 board drag-and-drop (keep list + status dropdown) | Worse experience; PMs will object |
| 5 | BOT-5 `#331` status lookup | One fewer convenience path |

**Never cut:**

| 🔒 | Why |
|---|---|
| GH-3, GH-5, GH-6 — loop prevention and reconciliation | Cut it and sync breaks; user trust does not come back |
| SEED (all P0 items) | Cut it and the bot refuses everything in week one; the team gives up |
| RAG-9 correction entry | Cut it and knowledge never updates; the flywheel never turns |
| MT — multi-tenant data model | Retrofitting later is a multi-week refactor |
| TA — telemetry adapter interface | Without the abstraction, Phase 2 reworks four features |

---

## Blocked on decision

Five open questions gate Phase 1 startup (§7.1). Two are routinely underestimated:
**#2**, because "for AI teams" that never lands on 1–2 concrete teams degrades into
abstraction built for an imagined user; and **#4**, which looks like tidying up
docs but is the MVP's real critical path.

| # | Question | Blocks | Needed by |
|---|---|---|---|
| 1 | Is 12 weeks / 21 person-weeks accepted, or must it compress to 8? | whole plan | before week 1 |
| **2** | **Which 1–2 teams are the first targets?** Determines the default schema field set and the first adapter. | TKT-2, TA-2 | before week 1 |
| 3 | Include passive channel monitoring? Needs the team's comfort with a bot reading messages. | OPT-1 | before week 9 |
| **4** | **Where does the knowledge seed come from, and who imports it?** | all of SEED | before week 7 |
| 5 | Monthly AI cost ceiling? Sets default model tier and cache aggressiveness. | RAG-3, RAG-5, INT-2 | before week 8 |
| 6 | Who executes account deactivation for departures while there is no SSO? (Security-audit finding if unowned.) | AC-8 ops handbook | before rollout |

---

## Out of scope

Explicitly **not** in Phase 1 (§4.0), regardless of available capacity:

Alert ingest and Auto-Ticket · ChatOps (Tier 1 and Tier 2) · external customer
channels · sentiment analysis and escalation · change attribution · environment
snapshots · SLA clocks · on-call rotation · AI-DLP · fine-grained RBAC ·
multi-tenant *product* features (billing, self-service admin, cross-tenant
sharing) · L4 external link sharing · real-time collaborative editing · AI-assisted
writing · trace drill-down · MCP server · AI eval system and release gates ·
GitHub docs co-sync.

Phase 1 defends two goals only: **replace Jira**, and **let the team feel AI value
for the first time**. Everything else waits, no matter how much the feature tree grows.
