---
name: relay-ui
description: >-
  Design and implement Relay frontend UI so the product reads as AI-ops context
  relay (incident handoff + telemetry chain), not a generic ticket tracker.
  Use when building, redesigning, reviewing, or restyling Relay web views,
  navigation, ticket/log detail, inbox, board, or any UI that risks looking like
  Jira/Linear.
---

# Relay UI

Relay's differentiator is **context that survives handoff** (alert → chat → ticket → code → telemetry). If the UI navigates and looks like a work tracker, the product becomes "a worse Linear" and the PRD's adoption window collapses.

## Diagnosis of the current UI (S1 shell)

What is wrong today:

| Symptom | Why it hurts |
|---|---|
| Top nav peers: 日志 / 工单 / 看板 / 我的 | IA by **entity type**, not by **job** (investigate → resolve → capture) |
| Ticket detail = form + comments + status history | The **context chain** is invisible; AI fields are absent or secondary |
| Log list is a blog index | Knowledge capture is disconnected from the incident that produced it |
| Board as a primary peer | Kanban is a **view of work**, not the product thesis |
| Generic cards + system-ui + blue accent | Visual language of any SaaS tracker; no ops identity |

S1 can ship thin chrome. It must not ship the wrong mental model.

## North star

> One screen should answer: **what happened, what we know, who holds the baton next.**

Primary object: **Context chain** (接力链), not Ticket or Log.
Tickets and logs are **nodes on the chain**. The board is a lens, not a home.

## Information architecture

Default signed-in shell:

```
此刻        ← home: attention + open chains (inbox + P0 + waiting-on-you)
上下文      ← search / browse chains (and orphan tickets still linked here)
工作        ← list + board of tickets (secondary)
知识        ← logs / knowledge candidates
成员        ← admin only
```

Rules:

1. **Default route = 此刻**, never 日志 or 工单.
2. Ticket permalinks stay frozen (`/{tenant}/t/{n}`) — change chrome, not URLs.
3. Do not add more top-level entity tabs (PR, Trace, Alert as peers). Link them **into** the chain.
4. "我的" is a filter on 此刻 / 工作, not a nav item.

## Screen patterns

### 1 · 此刻 (home)

- Sections by **attention**, not by status column: P0 · 进行中的调查 · 等你接力 · 未读.
- Each row shows chain density: linked alert / WeCom / trace / log chips — empty chips are honest (MVP often empty).
- Unread badge opens here, not a dead number in the header.

### 2 · Chain detail (ticket URL can render this)

Three columns:

| Left | Center | Right |
|---|---|---|
| Vertical **context chain** timeline (alert → IM → ticket → trace → log → PR) | Title + description + **接力时间线** (people + system events interleaved) | AI context schema fields + assignee + legal transitions |

- Transitions stay edge-buttons (state machine), never a free status dropdown.
- Compose placeholder: "写下这一棒你知道的…" — handoff language, not "评论".
- History is merged into the center timeline; do not keep a separate "历史" dump below comments unless debugging.

### 3 · 工作 (list / board)

- Same ticket cards, but each card surfaces **context chips** (trace / model / source) before type/iteration chrome.
- Board remains optional; never the first thing a new user learns.

### 4 · 知识 (logs)

- Prefer "from chain" entry (write a postmortem from RL-n) over orphan "写一篇".
- Share level + 知识库 marker stay visible (already correct).

## Visual language

Tokens (keep CSS variables; evolve values):

```
--relay-bg:           cool gray / near-ink (ops, not cream, not purple)
--relay-accent:       teal or steel-blue for "live context" (not consumer indigo-purple)
--relay-danger:       P0 / blocked
--relay-mono:         keys, trace_id, model ids, timestamps in dense rows
```

Density:

- Ops-readable: 14–15px body, tight rows, monospace IDs.
- Severity is color + label; do not rely on color alone.
- Cards only when they bound an interactive unit (chain node, compose). Avoid card-in-card dashboards.
- No marketing hero, stat strips, or floating badges on media.

Motion (when adding any):

- Chain node focus / expand: one clear motion.
- Optimistic board drop: existing snap-back behavior stays.

## Copy

| Avoid | Prefer |
|---|---|
| 评论 | 接力笔记 / 这一棒 |
| 工单（as product pitch） | 上下文 / 调查 / 接力 |
| 看板 as home | 此刻 |
| Empty "暂无数据" | Empty that names the next action ("从企微 @Relay 建单" / "粘贴 trace_id") |

UI language stays **Chinese** for product surfaces (per design docs).

## Implementation checklist

When changing Relay web UI, verify:

- [ ] Would removing the word "Relay" make this screen indistinguishable from Linear/Jira? If yes, redesign.
- [ ] Can a new on-call see the **chain** within 5 seconds of opening a ticket URL?
- [ ] Are AI context fields (`trace_id`, `provider`, `model`, …) visible when the schema says they should be — even if empty?
- [ ] Is navigation by **workflow**, not by entity inventory?
- [ ] Did you keep tenant-qualified ticket URLs and If-Match / transition rules intact?

## Mockups

Reference frames in [mockups/](mockups/):

- `relay-vs-current.png` — wrong vs right mental model
- `relay-home-context.png` — 此刻 home
- `relay-context-chain.png` — chain detail

Treat them as direction, not pixel-perfect specs.

## Out of scope for this skill

- Marketing landing pages
- Replacing backend state machine, RLS, or OpenAPI contracts
- Building a full design-system library in S1 (tokens + patterns only)
