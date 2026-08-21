# Relay

**Collaboration infrastructure for teams shipping AI in production.**

> Relay pulls alerts, chat, tickets, code, and telemetry onto a single context
> chain — so both people and agents can reason about what actually happened.

Status: **pre-implementation.** This repository currently holds the product
specification only. See [Relay-PRD-v0.4.md](docs/Relay-PRD-v0.4.md) (Chinese) for
the authoritative spec; section references below point into it. Phase 1 is broken
down into executable tasks in [TODO.md](TODO.md).

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

Items marked **[MVP]** ship in Phase 1; everything else is Phase 2–4 (§1).

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
- **[MVP]** **Bidirectional GitHub Issue sync** — field-ownership matrix, triple loop prevention, webhook + reconciliation dual channel
- Live PR integration, Auto-Ticket from alerts, dedup and similar-history recall
- **Change attribution** — ranked suspect changes with owner and rollback entry, covering commits, deploys, config changes, and *provider-side behavior shifts*
- **Environment snapshots** — config slice at failure time, diffed against last-stable

### M3 · IM & ChatOps
- **[MVP]** WeCom bot: `@Relay` Q&A, in-chat ticket creation with a confirmation card, ticket status lookup
- **[MVP]** WeCom app notifications with a 5-minute aggregation window
- **Alert semantic convergence** — topological causal merge into a single `Incident`
- **AI incident brief** — a fixed six-section brief within 60s of the alert, with a stated confidence level and multiple surviving hypotheses
- **ChatOps** — Tier 1 read-only (Phase 2); Tier 2 write (Phase 4) against a configurable allowlist

### M4 · Customer Service Agent
Three-stage rollout (shadow → copilot → autonomous, not skippable), intent
recognition with tool calls, confidence gating, **customer sentiment and
escalation**, health profiles for churn warning, cross-tenant hard isolation.

### M5 · RAG Engine & Bot Education
- **[MVP]** Write-a-doc-to-train-the-bot: Markdown → knowledge units → embeddings
- **[MVP]** Hybrid retrieval (BM25 + vector), **mandatory citations**, low-confidence refusal
- **[MVP]** Knowledge seed import (existing docs, GitHub docs, error-code tables) — **≥100 units is a hard launch gate**
- **[MVP]** Correction entry point: 👎 + one sentence → knowledge revision draft
- **GitHub docs co-sync** (`docs/`, `CHANGELOG`, OpenAPI ship with the code), freshness labels, FAQ extraction, staged knowledge activation, gap capture loop

### M6 · AI Platform & Dogfooding
Agent orchestration, **AI quality evaluation** (datasets, release gates, online
sampling, automatic degradation), AI governance, **[MVP]** running on the team's
own AI gateway, and an MCP server for IDE / Claude Code access.

---

## The three architectural decisions of v0.4

v0.4 widened the positioning from "internal tool for the AI Gateway team" to
"platform for AI Native teams." Two of the resulting changes are **structural
work that cannot be deferred** — cheap now, expensive later — and neither is on
any cut list (§4.10③).

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
reworking all four in Phase 2.

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
| **Replacement** | Logs + tickets + board + GitHub sync | No substitute exists, so the team won't migrate |
| **AI** | In-chat ticket creation + RAG Q&A | It becomes "another ticket system" and never feels AI-native |

Adoption windows for internal tools last about a week. If the first experience is
just "a different Jira," no amount of later AI work recovers the enthusiasm.

**Explicitly out of MVP scope:** alert ingest and Auto-Ticket, ChatOps, external
customer channels, sentiment analysis, change attribution, SLA clocks, on-call
rotation, DLP, fine-grained RBAC, and multi-tenant *product* features.

### Schedule — 12 weeks, two milestones (§4.10)

**Milestone A (week 6) · internally usable**
Multi-tenant data model + telemetry adapter interface (weeks 1–2, **first, not in
parallel** — one table missing `tenant_id` amplifies rework across every module
built after it) │ accounts + identity binding │ logs complete │ tickets + board │
GitHub sync piloted on a single repo
→ *team runs Relay and Jira in parallel*

**Milestone B (week 12) · MVP complete**
GitHub sync across all repos │ WeCom bot │ RAG Q&A │ ≥100 knowledge units
→ *Jira decommissioned*

Estimated at ~21 person-weeks for a 4–5 person team (2 backend / 1 frontend /
1 AI / 0.5 QA). 12 weeks holds, with no slack left. Two milestones exist so real
feedback arrives in week 6 rather than as a single week-12 verification — and the
GitHub sync gets a 6-week pilot.

### Three things that cannot be cut

- **Loop prevention and reconciliation in GitHub sync** — sync is the one module
  where getting it wrong destroys trust in the platform permanently. Actor
  filtering + revision numbers + content fingerprints, all three. Webhook for
  real-time, 5-minute incremental reconciliation, daily full reconciliation.
- **Knowledge seed import** — a RAG bot with no seed knowledge answers "I don't
  know" all week and the team abandons it before it gets good. ≥100 units before
  the bot opens to the team, no exceptions.
- **The correction entry point** — 👎 + one sentence → revision draft → owner
  confirms. Without it, knowledge never updates and the flywheel never turns.

### Acceptance criteria (§4.11)

| Area | Metric | Target |
|---|---|---|
| Adoption | Jira decommissioned | 100%, all tickets in Relay for 2 consecutive weeks |
| Adoption | Weekly active creators | > 70% |
| Sync | P95 sync latency | < 30s |
| Sync | Conflict rate / data inconsistency | < 1% / < 0.1% |
| Sync | Misses found by reconciliation | < 5/week, all auto-repairable |
| Bot | Tickets created from chat | > 20% of new tickets |
| Bot | Draft confirmed without major edits | > 60% |
| Q&A | First-answer hit rate | > 40% |
| Q&A | **Wrong-answer rate** | **< 5%** |
| Q&A | Refusals that offered a useful next step | 100% |
| Knowledge | Units at launch | ≥ 100 (hard gate) |
| Knowledge | Corrections triggered during MVP | > 20 |

> The 40% hit-rate target is deliberately low. Chasing a high hit rate on sparse
> knowledge pushes you to lower the refusal threshold, which raises the
> wrong-answer rate — **a strictly worse outcome**. At this stage, watch the
> wrong-answer rate and the correction count, not the hit rate.

---

## Roadmap

| Phase | Focus |
|---|---|
| **1 · MVP** (12 wks) | Replace Jira + first taste of AI value. Everything else waits. |
| **2 · Foundation & ops** (8 wks) | IM SSO + directory sync │ adapter expansion (LiteLLM / Portkey / OTel) │ ChatOps Tier 1 read-only │ alert convergence + AI briefs │ Auto-Ticket │ change attribution + environment snapshots │ L4 external sharing + bidirectional DLP │ collaborative editing. *In parallel: on-call, SLA clocks, change management.* |
| **3 · Knowledge flywheel & copilot** (8 wks) | GitHub docs sync │ FAQ extraction │ authority tiers, conflict detection, expiry governance │ knowledge regression + staged activation │ **AI eval system and release gates** │ trace drill-down │ MCP server │ automated retrospectives |
| **4 · External & autonomous** (8 wks, security review required) | Bot in external customer channels (full shadow → copilot → autonomous) │ sentiment and escalation │ customer health profiles │ cross-tenant hardening │ **ChatOps Tier 2 write** │ multi-channel │ auto-degradation |

**Two dependencies that don't bend:**

1. **ChatOps Tier 2 prerequisites** (§2.2⑦): change management and on-call must be
   live, Tier 1 read-only must have run ≥1 month at >95% parse accuracy, and a
   red-team exercise must confirm prompt injection cannot escape the allowlist.
2. **Three-stage rollout**: the bot may answer directly in *internal* channels
   during MVP, but *external customer* channels require the full shadow → copilot
   → autonomous progression. MVP's relaxation does not extend to it.

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

Locked before Phase 1 development starts — `relay-sync[bot]` and `relay:meta`
markers get written into GitHub issue bodies on day one, so renaming later means
migrating data.

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

1. Is the 12-week / 21-person-week plan accepted, or must it compress to 8 weeks?
   (If compressed, the cut list applies — but the two architectural items are not
   on it.)
2. **Which teams are the first targets?** This determines the default schema field
   set and the first telemetry adapter. Recommend 1–2 teams for MVP. Without
   concrete users, "for AI teams" degrades into abstraction built for an imagined
   one.
3. Include passive channel monitoring (DM the speaker to suggest a ticket)?
   Depends on the team's comfort with a bot reading group messages.
4. **Where does the knowledge seed come from, and who imports it?** Looks like
   "tidying up docs"; it is actually the MVP's hidden critical path.
5. What is the monthly AI cost ceiling? Sets default model tier and cache
   aggressiveness.

## Repository layout

```
docs/Relay-PRD-v0.4.md    Product requirements (Chinese) — the authoritative spec
TODO.md                   Phase 1 task breakdown — epics, effort, sequencing, gates
```
