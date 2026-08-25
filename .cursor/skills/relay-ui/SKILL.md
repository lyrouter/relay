---
name: relay-ui
description: >-
  Design and implement Relay frontend UI so the product matches the Relay
  context-relay mockups (left rail + 此刻 home + chain detail), not a generic
  ticket tracker. Use when building, redesigning, reviewing, or restyling Relay
  web views, navigation, ticket/log detail, inbox, board, or chrome.
---

# Relay UI

Relay's differentiator is **context that survives handoff** (alert → chat → ticket → code → telemetry). The UI **must** match the attached mockups in [mockups/](mockups/) — especially `now.png` and `detail.png`. Direction framing is `direction.png`.

## Canonical mockups (source of truth)

| File | Screen |
|---|---|
| [mockups/now.png](mockups/now.png) | Shell + **此刻** home |
| [mockups/detail.png](mockups/detail.png) | Ticket / chain detail |
| [mockups/direction.png](mockups/direction.png) | Wrong vs right IA |

When implementing, **pixel-match the structure** of these frames (chrome, columns, section hierarchy, component density). Do not invent a different layout and call it "same intent".

## Shell (every signed-in page)

From `now.png`:

```
┌─ top bar ──────────────────────────────────────────────┐
│ Relay⚡   [ 搜索 trace / 工单 / 日志 … ⌘K ]   🔔  tenant  👤 │
├────┬───────────────────────────────────────────────────┤
│ ⚡ │                                                   │
│此刻│              main (route view)                    │
│ ⛓ │                                                   │
│上下文│                                                  │
│ ☐ │                                                   │
│工作│                                                   │
│ 📖 │                                                   │
│知识│                                                   │
│ 👥 │                                                   │
│成员│                                                   │
└────┴───────────────────────────────────────────────────┘
```

Rules:

1. **Left icon rail** (narrow, ~72px): 此刻 / 上下文 / 工作 / 知识 / 成员 — icon + label under, active = soft fill + left accent bar. **Not** a top text-tab bar.
2. **Top bar**: brand left; **centered search** (`搜索 trace / 工单 / 日志`, ⌘K hint); bell with unread count → `此刻#inbox`; tenant name; avatar (initials).
3. Default route = **此刻**. Permalinks `/{tenant}/t/{n}` stay frozen.
4. No top-level entity tabs (日志/工单/看板/我的 as peers).

## Screen · 此刻 (`now.png`)

Two columns inside main: **feed (flex)** + **AI 上下文 rail (~300px)**.

### Feed sections (in order)

1. **P0 最高优先级** — soft red strip; count badge; single dense mono line (`RL-n · title · model · provider=… · 负责人 · age`); 「查看全部」→ 工作/上下文.
2. **活跃调查中** — large white cards:
   - key + title
   - `trace_id` mono + copy + **model badge** (colored pill)
   - footer: 关联企微线程 (or source) · 最后接力: name · relative time + avatar
3. **等待你的接力** — compact rows: status dot · key · title · model badge · thread · last person · **接力** button → ticket.
4. Unread inbox may sit below or behind the bell; do not make "未读" a fourth peer of P0/活跃/等待 in the mockup hierarchy unless space allows a quiet block.

### Right rail · AI 上下文

- Always visible on 此刻.
- Fields as **labeled boxes**: filled solid; empty = dashed + 「未设置」/「未评估」.
- Keys at least: `trace_id`, `provider`, `model`, `prompt_version`, `token_cost`, `blast_radius`.
- Disclaimer: 「以上信息由系统与 AI 自动提取，可能不完整」.
- Button: 「刷新上下文」.
- Bound to the **selected** investigation (click card/row); default = first P0 else first 活跃.

## Screen · Chain detail (`detail.png`)

Full-bleed three columns under a detail header (no max-width card dump):

| Left ~220px | Center flex | Right ~280px |
|---|---|---|
| **CONTEXT CHAIN** vertical timeline | 问题描述 + **接力时间线** + compose | 结构化上下文 + 指派 + 状态流转 + 关联 PR |

Header: `此刻 / RL-n` · title · status pill · **P0** badge · primary **写接力笔记**.

Chain nodes: time · icon · title · detail; **active node** teal soft fill + border. Empty future nodes still listed.

Center:

- Description card; AI draft note if applicable.
- Timeline: system rows (robot) interleaved with people (avatar + name + 「当前接力人」 on assignee).
- Compose: placeholder `写下这一棒你知道的…` · **发送** (not 「发表评论」).

Right:

- Structured fields with copy; `error_class` as danger pill when set.
- Assignee control.
- Transition edge buttons only (state machine).
- PR empty state + 关联 PR.
- Secondary: 从这条调查写日志 / export later.

## Visual tokens

```
--relay-bg:           #f4f6f8
--relay-surface:      #ffffff
--relay-rail:         #f7f8fa
--relay-accent:       #0f766e   /* teal primary / active chain */
--relay-accent-soft:  #e6f4f2
--relay-danger:       #dc2626   /* P0 */
--relay-mono:         SF Mono / ui-monospace
```

- Model badges: soft green / blue / violet by model family — not one generic gray pill.
- Cards: white, 8–12px radius, light border; P0 strip soft red.
- No purple-on-white marketing theme; no cream+serif look.

## Copy

| Avoid | Prefer |
|---|---|
| 评论 / 发表 | 接力笔记 / 发送 / 写下这一棒你知道的… |
| 工单 as product home | 此刻 / 调查 / 上下文 |
| Top nav 日志\|工单\|看板\|我的 | Left rail 此刻\|上下文\|工作\|知识 |

Chinese UI surfaces.

## Implementation checklist

- [ ] Shell is **left rail + top search**, matching `now.png`
- [ ] 此刻 has P0 strip + investigation cards + waiting rows + **AI 上下文** right rail
- [ ] Ticket URL shows **CONTEXT CHAIN** within 5s (even with empty nodes)
- [ ] AI fields visible when empty (dashed)
- [ ] Transitions = legal edges only; `If-Match` / permalinks unchanged
- [ ] Removing "Relay" would still not look like Linear/Jira

## Out of scope

- Marketing landing pages
- Backend state machine / RLS / OpenAPI changes
- Full design-system package beyond tokens + these patterns
