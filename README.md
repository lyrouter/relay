# Relay

**Collaboration infrastructure for teams shipping AI in production.**

> Relay pulls alerts, chat, tickets, code, and telemetry onto a single context
> chain — so both people and agents can reason about what actually happened.

---

## Why another collaboration platform

Relay is not a general-purpose work tracker. It targets one specific failure:
**context breaking as it is handed off.** An alert fires in a monitoring tool,
gets discussed in a group chat, becomes a ticket somewhere else, and the on-call
handoff loses the rest. Reconstructing a single AI incident means assembling a
trace, a model version, a prompt revision, retrieval context, and the provider's
status at that moment — five systems, and the investigation stalls if any one is
missing.

That pain is structurally worse for AI teams (§0.1):

| | |
|---|---|
| **Failures don't reduce to a stack trace** | Traditional services fail deterministically and hand you a line number. AI systems fail probabilistically. |
| **"It worked yesterday" is a real failure mode** | Providers silently update models, prompts drift, retrieval corpora go stale. Code didn't change but behavior did — so **change attribution has to reach beyond your own commits**. |
| **The critical path isn't yours** | Provider outages, rate limits, and model deprecations are absorbed, not fixed. That makes incident history compound in value: last quarter's mitigation is often this quarter's answer. |
| **Cost is an operational signal, not a finance report** | Token spend moves daily with traffic, routing, and model choice. It belongs next to the latency curve, not in a monthly rollup. |
| **Knowledge decays weekly** | Model capabilities, API surfaces, and best practices turn over fast enough that a static wiki is stale before anyone notices. |

## Scope boundary

> **Relay assumes you run AI systems in production and have telemetry worth
> correlating. That assumption is the boundary.** (§0.2)

- ✅ **Fits:** teams running an AI gateway, RAG application, agent product, model
  serving, or any product with AI features in it.
- ❌ **Doesn't fit:** teams with no traces, evals, or provider dependencies to
  reason over — for them Relay is just a worse Linear.

Generalizing to *AI teams* is not generalizing to *all teams*. Relay does not do
general-purpose document collaboration and does not compete with Notion. Blur
that line and the product degrades into "another Jira + Confluence" and loses
every differentiator.

---

## Module map

Items marked **[MVP]** ship inside the MVP boundary; **⏸** items are Phase 2 work
that starts early, in weeks 7–12, without gating MVP acceptance; everything else is
Phase 2–4 (§1).

### M0 · Foundation
- **[MVP]** Self-hosted accounts (invite-only, email + password, TOTP), 3 roles, single-level space
- **[MVP]** Triple identity binding: platform account ↔ IM userid ↔ GitHub handle
- **[MVP]** **Multi-tenant data model** — `tenant_id` through every entity, enforced at the query layer
- **[MVP]** **Telemetry adapter interface** — abstracted ingest; one implementation in MVP
- **[MVP]** **Configurable AI context schema** — structured field sets per team type
- On-call rotation and escalation, SLA clocks, change-management loop, full audit trail, unified event bus

### M1 · Log & Knowledge
- **[MVP]** Native Markdown / plain-text dual mode, version control, diff and rollback
- **[MVP]** Mermaid and inline ticket cards; sharing levels L0–L3
- Trace-ID card drill-down (read-only snapshot → full span expansion), AI-assisted writing, **bidirectional AI-DLP** (inbound redaction before vectorization, outbound on shares and bot answers), document health scoring

### M2 · Ticket & GitHub Sync
- **[MVP]** Tickets, six-state machine, list and board views; the PR link is a plain link field in the MVP
- **⏸** **Bidirectional GitHub Issue sync** — field-ownership matrix, triple loop prevention, webhook + reconciliation dual channel
- Live PR integration, Auto-Ticket from alerts, dedup and similar-history recall
- **Change attribution** — ranked suspect changes with owner and rollback entry, covering commits, deploys, config changes, and *provider-side behavior shifts*
- **Environment snapshots** — config slice at failure time, diffed against last-stable

### M3 · IM & ChatOps
- **[MVP]** WeCom bot: in-chat ticket creation with an AI-drafted confirmation card, ticket status lookup, identity binding (**⏸** `@Relay` Q&A and 👎 correction arrive with the RAG engine)
- **[MVP]** WeCom app notifications with a 5-minute aggregation window
- **Alert semantic convergence** — topological causal merge into a single `Incident`
- **AI incident brief** — a fixed six-section brief within 60s of the alert, with a stated confidence level and multiple surviving hypotheses
- **ChatOps** — Tier 1 read-only (Phase 2); Tier 2 write (Phase 4) against a configurable allowlist

### M4 · Customer Service Agent
Three-stage rollout (shadow → copilot → autonomous, not skippable), intent
recognition with tool calls, confidence gating, **customer sentiment and
escalation**, health profiles for churn warning, cross-tenant hard isolation.

### M5 · RAG Engine & Bot Education
- **[MVP]** The "add to knowledge base" marker on logs — field and checkbox only, so Phase 2 can backfill the whole history
- **⏸** Write-a-doc-to-train-the-bot: Markdown → knowledge units → embeddings
- **⏸** Hybrid retrieval (BM25 + vector), **mandatory citations**, low-confidence refusal
- **⏸** Knowledge seed import (existing docs, GitHub docs, error-code tables) — **≥100 units is a hard launch gate**
- **⏸** Correction entry point: 👎 + one sentence → knowledge revision draft
- **GitHub docs co-sync** (`docs/`, `CHANGELOG`, OpenAPI ship with the code), freshness labels, FAQ extraction, staged knowledge activation, gap capture loop

### M6 · AI Platform & Dogfooding
Agent orchestration, **AI quality evaluation** (datasets, release gates, online
sampling, automatic degradation), AI governance, **⏸** running on the team's own AI
gateway (MVP AI calls go straight to the provider), and an MCP server for IDE /
Claude Code access.

---

## Three architectural decisions

Positioning Relay for AI Native teams rather than one internal team has three
structural consequences. Two of them are **work that cannot be deferred** — cheap
now, expensive later — and neither is on any cut list (§4.10③).

**① Multi-tenant data model in Phase 1** (§4.1) — ~1–1.5 person-weeks now,
multi-week refactor later. MVP does the *data model* only: `tenant_id` on every
business entity with no exceptions, tenant filtering injected at the ORM /
repository layer (never trusted to business code or to a prompt), composite
indexes prefixed by `tenant_id`, vector-store filtering *inside* the query rather
than post-hoc, and CI-gating tests asserting cross-tenant access fails. It does
*not* do per-tenant billing, self-service admin, or cross-tenant sharing policy —
those are product features that can wait for real demand.

**② Telemetry ingest as an adapter interface** (§4.2) — ~1 person-week. Four
features depend on this layer (alert-to-ticket, change attribution, ChatOps
read-only, environment snapshots). Hard-coding the in-house gateway means
reworking all four in Phase 2. All four consumers are themselves Phase 2, so
**this epic has nothing to demo inside the MVP** — it is prepaid technical debt,
not a feature, and saying so out loud is what keeps it from being cut at the
week-2 review.

```
interface TelemetryAdapter {
  queryMetrics(dimensions, timeWindow)      // latency, error rate, throughput
  getTrace(traceId)                          // single request trace
  sampleRequests(filter, n)                  // request samples (redacted)
  listRecentChanges(timeWindow)              // deploys / config / provider changes
  getProviderHealth()                        // dependency health
  getCostBreakdown(dimensions, timeWindow)   // token cost
}
```

MVP implements one adapter (the in-house gateway); Phase 2 targets LiteLLM,
Portkey, OpenTelemetry, Langfuse, and cloud gateways.

**③ Configurable AI context schema** (§4.3) — `trace_id`, `provider`, `model`,
`prompt_version`, `error_class`, `eval_run`, `token_cost`, `blast_radius`, and
`tenant` turn out to be *generic AI-operations fields*, not gateway-specific ones.
Only `gateway_version` and routing-policy fields are domain-specific, so they load
by configuration. The right generalization was making the schema configurable —
not deleting the domain detail.

---

## MVP (Phase 1)

**Goal in one line:** get the team off Jira, and let them feel — for the first
time, in their group chat — that the platform knows what they're working on.

The MVP carries two values, and needs both (§4.0):

| Value | Features | If missing |
|---|---|---|
| **Replacement** | Logs + tickets + board + WeCom notifications | No substitute exists, so the team won't migrate |
| **AI** | In-chat ticket creation with an AI-drafted card | It becomes "another ticket system" and never feels AI-native |

Adoption windows for internal tools last about a week. If the first experience is
just "a different Jira," no amount of later AI work recovers the enthusiasm. That
puts real weight on the second row: with RAG Q&A in Phase 2, the AI-drafted ticket
is the MVP's **only** AI touchpoint, which is why its >60% draft confirmation rate
is a hard gate rather than a polish metric (§0.3 判断五).

**⏸ Phase 2, started early in weeks 7–12 — full specs, no discount, but no bearing
on MVP acceptance:** bidirectional GitHub Issue sync, the RAG Q&A engine and
knowledge seeding, and routing platform AI calls through the in-house gateway.
Keeping the highest-risk module (sync) outside the boundary is the point: week 6's
delivery does not depend on whether sync is solid.

**Explicitly out of Phase 1 entirely:** alert ingest and Auto-Ticket, ChatOps,
external customer channels, sentiment analysis, change attribution, SLA clocks,
on-call rotation, DLP, fine-grained RBAC, and multi-tenant *product* features —
note that the multi-tenant *data model* is mandatory (§4.1).

### Schedule — 12 weeks, two milestones (§4.10)

**Milestone A (week 6) · ★ the MVP hard acceptance boundary · internally usable**
Multi-tenant data model + telemetry adapter interface (weeks 1–2, **first, not in
parallel** — one table missing `tenant_id` amplifies rework across every module
built after it) │ accounts + triple identity binding │ logs complete │ tickets +
board │ an all-hands identity-binding drive
→ *team runs Relay and Jira in parallel*

**Milestone B (weeks 7–12) · MVP complete, then Phase 2 starts early**
Priority 1: WeCom bot (in-chat creation with AI draft, status lookup, binding) plus
app notifications — **this is what completes the MVP** │ ⏸ priority 2: GitHub sync
piloted on a single repo │ ⏸ priority 3: RAG engine + ≥100 knowledge units
→ *Jira decommissioned — a call that rests on priority 1 and Milestone A only*

The order of priorities 2 and 3 is deliberately left open until the Milestone A
retrospective. Sync goes first by default, since it carries more risk and needs the
longer pilot. But if the week-6 dual-track feedback is "this is just Jira," RAG goes
first — that reaction means the missing AI value is already costing adoption, which
is a worse failure than a sync delay.

MVP net scope is ~13.5 person-weeks; Phase 1 as a whole ~20.5, for a 4–5 person team
(2 backend / 1 frontend / 1 AI / 0.5 QA). The smaller MVP does not buy an earlier
delivery — it buys ~4 weeks of slack on Milestone A and a ~6-week pilot window for
sync.

### What cannot be cut

**Inside the MVP:**
- **The multi-tenant data model** — retrofitting it later is a multi-week refactor.
- **The telemetry adapter interface** — un-cuttable *even though it has nothing to
  demo*; without it, Phase 2 reworks four features.
- **WeCom userid binding** — cut it and the bot is entirely unusable: creation,
  notification, and attribution all depend on it.
- **The AI ticket draft** — the MVP's only AI value proof point.
- **The log "add to knowledge base" marker** — ~0.5 person-days; cutting it saddles
  Phase 2 with a full re-annotation pass.

**Inside Phase 2 — later scheduling does not relax these:**
- **Loop prevention and reconciliation in GitHub sync** — sync is the one module
  where getting it wrong destroys trust in the platform permanently. Actor
  filtering + revision numbers + content fingerprints, all three. Webhook for
  real-time, 5-minute incremental reconciliation, daily full reconciliation.
- **Knowledge seed import** — a RAG bot with no seed knowledge answers "I don't
  know" all week and the team abandons it before it gets good. ≥100 units before
  Q&A opens to the team, no exceptions.
- **The correction entry point** — 👎 + one sentence → revision draft → owner
  confirms. Without it, knowledge never updates and the flywheel never turns.
- **Minimum inbound redaction, before the first ingest** — ingesting without
  redaction fixes the leak risk permanently into the retrieval layer.

### Acceptance criteria (§4.11)

**MVP — three hard gates, none of which depends on a high-risk item:**

| Area | Metric | Target |
|---|---|---|
| **Security** | Cross-tenant read/write | **0** — CI gate green, plus one pre-launch penetration spot-check |
| **Foundation** | WeCom userid binding coverage | **> 90%** — the ceiling on bot usability |
| **Adoption** | Jira decommissioned | **100%**, all tickets in Relay for 2 consecutive weeks |
| Adoption | Weekly active creators | > 70% |
| Bot | Tickets created from chat | > 20% of new tickets |
| **Bot** | Draft confirmed without major edits | **> 60%** — the sole AI value proof point |
| Foundation | Logs flagged "add to knowledge base" | ≥ 30 meaningful positives |
| Foundation | Questions logged by the bot's guidance reply | ≥ 50 — demand samples for seeding |

**⏸ Phase 2 — judged with their epics, at full targets:**

| Area | Metric | Target |
|---|---|---|
| Sync | P95 sync latency | < 30s |
| Sync | Conflict rate / data inconsistency | **< 1% / < 0.1%** |
| Sync | Misses found by reconciliation | < 5/week, all auto-repairable |
| Q&A | First-answer hit rate | > 40% |
| Q&A | **Wrong-answer rate** | **< 5%** |
| Q&A | Refusals that offered a useful next step | 100% |
| Knowledge | Units when Q&A opens | ≥ 100 (hard gate) |
| Knowledge | Corrections in the first month | > 20 |

> The 40% hit-rate target is deliberately low. Chasing a high hit rate on sparse
> knowledge pushes you to lower the refusal threshold, which raises the
> wrong-answer rate — **a strictly worse outcome**. At this stage, watch the
> wrong-answer rate and the correction count, not the hit rate.
>
> Two of these are not yet measurable as written. The wrong-answer rate needs Q&A
> log retention plus weekly sampled human review before Q&A opens, because the eval
> system is Phase 3. And the weekly-active-creator and chat-created-ticket shares
> are gameable by a few people until their denominators — headcount vs bound users,
> calendar week — are pinned in the dashboard rather than argued at review time.

---

## Roadmap

| Phase | Focus |
|---|---|
| **1 · MVP** (12 wks) | Replace Jira + first taste of AI value. Everything else waits. |
| **2 · Foundation & ops** (8 wks, ~6 in practice since three epics start in weeks 7–12) | **First: GitHub Issue sync → all repos │ RAG Q&A + ≥100 knowledge units + correction entry │ running on the in-house gateway.** Then IM SSO + directory sync │ adapter expansion (LiteLLM / Portkey / OTel) │ ChatOps Tier 1 read-only │ alert convergence + AI briefs │ Auto-Ticket │ change attribution + environment snapshots │ L4 external sharing + bidirectional DLP │ collaborative editing. *In parallel: on-call, SLA clocks, change management.* |
| **3 · Knowledge flywheel & copilot** (8 wks) | GitHub docs sync │ FAQ extraction │ authority tiers, conflict detection, expiry governance │ knowledge regression + staged activation │ **AI eval system and release gates** │ trace drill-down │ MCP server │ automated retrospectives |
| **4 · External & autonomous** (8 wks, security review required) | Bot in external customer channels (full shadow → copilot → autonomous) │ sentiment and escalation │ customer health profiles │ cross-tenant hardening │ **ChatOps Tier 2 write** │ multi-channel │ auto-degradation |

**Three dependencies that don't bend:**

1. **ChatOps Tier 2 prerequisites** (§2.2⑦): change management and on-call must be
   live, Tier 1 read-only must have run ≥1 month at >95% parse accuracy, and a
   red-team exercise must confirm prompt injection cannot escape the allowlist.
2. **Three-stage rollout**: when Q&A first launches, the bot may answer directly in
   *internal* channels, but *external customer* channels require the full shadow →
   copilot → autonomous progression. That relaxation does not extend to them.
3. **Two ordering constraints inside Phase 2**: GitHub handle binding lands before
   sync starts, and minimum inbound redaction lands before or with the first
   knowledge ingest.

## Safety posture worth knowing up front

- **ChatOps writes are triage, not change** (§2.2) — every write action defaults
  to temporary, TTL-bound, and auto-reverting. Permanent config changes go through
  change management. This shrinks the risk surface from "production config
  management" to "temporary emergency intervention," which is what makes the whole
  security model work.
- **Natural language only generates candidate commands.** Once parsed into a
  structured command, everything downstream — confirmation, dry-run, execution —
  is decoupled from the original text. Injected input still cannot exceed the
  allowlist, because only whitelisted structured actions are executable.
- **Never in ChatOps, ever:** version rollback, instance restart, key revocation,
  config deletion, billing changes.
- **Two-person confirmation, with a documented escape hatch.** If the mechanism
  hard-blocks at 3am, engineers bypass it via a jump host — no audit trail, no
  TTL, no change record, which is strictly worse. So emergency single-person
  execution exists, at a cost: 10-minute TTL, a written reason, non-silenceable
  notification to all on-call plus management, and a mandatory 24h retrospective.
  **Above 15% usage, the mechanism is considered a design failure.** The goal is
  not zero bypass — it's that the bypass path is observed, constrained, and
  costly.
- **AI briefs must state confidence and preserve competing hypotheses.** An
  on-call engineer under pressure will take a brief at face value, so its wording
  carries real responsibility. Never present inference as conclusion.
- **Zero write permissions from external channels.** Hard rule, no exceptions.
- **Cross-tenant leakage: 0.** Tracked as a release-gating eval metric, not an
  aspiration.

## Naming conventions (§8.2)

`relay-sync[bot]` and `relay:meta` markers get written into GitHub issue bodies the
day sync goes live, so renaming after that means migrating data — their lock
deadline is *before sync starts*, not before Phase 1. The rest (product name,
`@Relay`, `relay.internal`, the `RL-` prefix) is visible to the team from week 6, so
locking it inside Phase 1 is still worth doing.

| Context | Convention | Example |
|---|---|---|
| Product | Relay | |
| IM bot | `@Relay` | `@Relay what's azure latency` |
| GitHub sync bot | `relay-sync[bot]` | used for loop detection and audit |
| Issue metadata | `<!-- relay:meta:start -->` | |
| Internal domain | `relay.internal` | `https://relay.internal/t/331` |
| Ticket prefix | `RL-` | `RL-331` |
| MCP server | `relay-mcp` | |

The name states the failure the product addresses: *relay* is a handoff, and a
failed handoff is the worst part of on-call. An AI gateway is itself a relay, and
both fail the same way — **by dropping context in transit**.

## Open questions blocking Phase 1 (§7.1)

1. **Which 1–2 teams are the first targets?** This determines the default schema
   field set and the first telemetry adapter, so it is the design input for weeks
   1–2 and the most urgent item on this list. Without concrete users, "for AI teams"
   degrades into abstraction built for an imagined one.
2. **Is the WeCom API verified?** `@` triggering, quoted-message context capture, DM
   code delivery, and app-message aggregation each need different app types, scopes,
   and callbacks. Quoted-message capture feeds the draft quality that carries a hard
   gate — hence a week-1 spike, not a week-7 discovery.
3. **Who drives binding coverage >90%, and when?** It is an acceptance gate and the
   ceiling on bot usability. Recommended for weeks 5–6, alongside dual-track use.
4. What is the monthly AI cost ceiling? Sets default model tier and cache
   aggressiveness — and with gateway routing in Phase 2 there is no per-feature cost
   view, so a hard budget alarm is the only backstop.
5. Include passive channel monitoring (DM the speaker to suggest a ticket)? Depends
   on the team's comfort with a bot reading group messages.

Later, but worth naming now: **where the knowledge seed comes from and who imports
it** blocks the RAG work rather than week 1, and the GitHub state-mapping matrix has
to be settled before sync starts.

## Repository layout

```
markdown/relay-prd.md               Product requirements (Chinese) — the authoritative spec
markdown/relay-mvp-design.md        MVP module design (Chinese) — logical design per epic
TODO.md                             Phase 1 task breakdown — epics, effort, sequencing, gates

S1 — the slice being built now (workbench first, no BOT/GH/RAG):
markdown/relay-s1-design.md         S1 design and the decision register (S-1…S-25)
TODO-S1.md                          S1 execution view — task status and implementation notes
markdown/relay-s1-dev.md            Developer guide: how to run it, how to change it safely
markdown/relay-s1-deploy.md         Deployment: keys, superuser steps, tenant bootstrap, checklist
markdown/relay-s1-rollout.md        INT-6: how the team uses it during the dual-track trial
markdown/relay-s1-owner-actions.md  What needs a human decision, and what each one landed as
markdown/relay-s1-entities.md       MT-1 entity registry snapshot (generated)
markdown/relay-s1-fk-deviation.md   S-18: why every cross-table reference is a composite key

src/relay/{domain,app,api,infra,ports}   Layered, and the layering is CI-enforced
web/                                     Vue 3 + TS frontend; types generated from the API schema
openapi.json                             The frozen /api/v1 contract snapshot (API-5's gate)
scripts/                                 Ops entry points: bootstrap, purges, webhook delivery,
                                         blob smoke test, backup and the restore drill
```

### Two HTTP surfaces, on purpose

`/web/*` is the frontend's own API: versionless, session-cookie authenticated, and
free to change field names in the same commit as its consumer. `/api/v1/*` is the
**frozen** public ticket API: bearer tokens, tenant derived from the token, and
additive-only change inside v1 (a removed field or a changed enum meaning is a v2
with 90 days of overlap). They share the error shape, the concurrency rule and the
pagination cursor — sharing those is what stops the two from drifting into "it
notifies when you change it in the UI but not through the API".

`make serve` then `/docs` is the live reference; `openapi.json` is what CI diffs.
