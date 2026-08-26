# Relay × 平台支持工单：缺口与建议

> 对照文档：[ai_gateway_webui `support-ticket-design.md`](../../aigateway/ai_gateway_webui/docs/support-ticket-design.md)（状态：设计稿，待确认）
> Relay 侧既有口径：[relay-s1-design.md](relay-s1-design.md) §8.8 / API-6 / S-22 / **S-26**
> 日期：2026-08-26
> 范围：列清「平台设计要什么、Relay 现在给什么、差什么」。**§0 已拍板**：真源在网关控制面；Relay 同步落单、保留标记、同步图片/文件。

---

## 0. 先定一件事：这不是同一套工单

平台设计的「工单」是**租户提交给我们的客服支持单**（Account 级会话）。
Relay 现有的「工单」是**团队内部的调查/工程单**（Tenant 级看板：Todo → Done、指派、迭代、AI 上下文）。

两边都叫 ticket，模型、状态机、可见性、附件、通知全部不是一套。
Relay S1 原先对平台的假设更窄：网关 WebUI 当**第一个 API 消费方**，把「问题反馈」落到 Relay，截图不走 API，进度由网关轮询。平台新设计已经扩成完整的客服会话系统。

**不先拍板「真源在哪」，后面每一项改动都会返工。**

建议默认：**支持工单的真源在网关控制面**（按平台设计落地），Relay 当坐席工作台 / 内部调查面。
理由见 §4。

**已拍板（2026-08-26）**：同意这条默认。Relay 侧同步时：

1. 网关有单、Relay 没有 → **建单**（沿用 `external_ref` 去重）。
2. 标记落库，不只写在描述里：`source`、`ticket_external_ref`（`tkt_…`）、`category`（平台 6 类）、标签名（如 `from-gateway-webui`）。
3. 图片 / 文件一并同步进 Relay 对象存储（`POST /api/v1/tickets/{key}/attachments`）。网关 AttachmentStore 仍是租户面真源；Relay 存坐席工作台用的副本。

不把客服状态机塞进同一张 `ticket` 表，也不改 `RL-` 编号。

---

## 1. 已经能对上的（旧「问题反馈」入口）

这些满足的是 Relay §8.8，**不是**平台新设计：

| 能力 | Relay 现状 |
|------|------------|
| 网关持服务 token 建单 / 列表 / 详情 | `POST/GET /api/v1/tickets`、`GET /api/v1/tickets/{key}` |
| 真实提交者（非 Relay 账号） | `submitter = {name, email?, external_id?}`，无权限效果 |
| 来源标记 | `source`（如 `gateway-webui`） |
| 去重 | `external_ref`（业务）+ `Idempotency-Key`（网络） |
| 进度回显 | 轮询 `status` + `updated_at`；或订 webhook |
| 租户隔离 | 跨租户 404 |
| 坐席在 Relay 里改单、评论、流转 | 同一套 `/api/v1` + Web UI |

结论：现在能收一张「问题反馈」工单，不能当客服会话系统用。

---

## 2. 缺口清单

按对接时会立刻撞上的顺序排列。标 **硬** 的项：不改就无法按平台契约对接。标 **绕** 的项：网关可以自己补一层，但语义会损、或把约束推给消费方。

### 2.1 作用域与标识 — 硬

| 平台设计 | Relay 现状 |
|----------|------------|
| Account 级资源；`workspace_id` 可空，只作上下文，不参与授权 | Tenant 级；无账户、无工作区字段 |
| 对外 `tkt_` / `tmsg_` / `tatt_` | `RL-n` + UUID |
| 本账户全体成员可见（Q1 默认） | 网关用户不是 Relay 账号，**看不见** Relay 里的单；Guest 只见自己是负责人/报告人的单 |
| 禁止裸词 `ticket` | 全域就是 `ticket` |

租户列表/详情/可见性必须由**网关自己的表**做。Relay 不能当租户读模型。

### 2.2 状态机 — 硬

平台：

```
open → pending → awaiting → resolved → closed
                用户 reopen（resolved 后 7 天内）
closed 终态，不可逆
租户只能 close / reopen；其余只给坐席
```

Relay：

```
todo → in_progress → in_review → done
blocked / wont_fix 可从活跃态进入
done / wont_fix 均可 reopen 到 todo（无时限、无终态）
只有 POST /tickets/{key}/transitions + If-Match
```

缺：`awaiting`（等用户补充）、`resolved` vs `closed`、7 天重开窗口、`POST .../close`、`POST .../reopen`。
`done`/`wont_fix` 映射到 `resolved`/`closed` 是有损的，且与「closed 不可逆」冲突。

### 2.3 类型与字段 — 硬（类型）/ 绕（部分字段）

平台 6 类：`presale / aftersale / billing / technical / feedback / other`。
Relay 工程类型仍是 `bug / feature / task`（冻结）。**S-26 起另有可空 `category`**，取值就是上面 6 类，不混进 `type`。

| 平台字段 | Relay |
|----------|-------|
| `contact_email` 必填 | 仅可选 `submitter.email` |
| `message_count` / `last_reply_at` / `last_reply_by`（tenant\|agent） | 无 |
| `unread_by_tenant` + `POST .../read` | 无 |
| `resolved_at` / `closed_at` | 无 |
| `workspace_ref` | 无 |
| 6 类 category | **`category` 列 + API 字段**（可空；列表可按 `?category=` 筛） |

Relay 多出来的（优先级、指派、迭代、标签、PR、`ai_context`、`rev`）是内部调查面需要的，**不应**暴露给租户。

### 2.4 会话模型 — 硬（internal 泄漏）

平台要求统一消息流：

- `author_kind = tenant | agent | system`
- 坐席 `internal=true` **永不下发租户**（repo 硬过滤 + 单测锁死）
- 状态迁移插 `system` 消息，时间线渲染为系统事件
- 坐席只露昵称/工号，不下发真名邮箱

Relay：`ticket_comment` 只有 `body` + `author_id`。

- **没有 `internal` 标志**。服务 token 带 `tickets:read` 能拿到全部评论。S-22 写明：过滤是消费方约束，API 不拦。
- 状态历史在 `ticket_status_history`，不是会话里的 system 气泡。
- 没有 `author_display`。

若网关把 Relay 评论原样给租户，内部讨论会漏出去。这不是文档约定能兜住的，必须在真源侧硬过滤。

### 2.5 附件 — 坐席副本已接通；租户面仍在网关

平台：两段式上传（先 `POST /support/attachments` 拿 `attachment_ref`，建单再挂）；≤5 张；PNG/JPEG；5MiB；真解码 + 解压炸弹 + 剥 EXIF；孤儿 24h 清理；关单 180 天清图留字；配置缺失**降级仍能提单**。

Relay（S-26）：

- **`POST/GET /api/v1/tickets/{key}/attachments`**，挂在这张工单上；`GET …/link` 仍是先鉴权再签 5 分钟链接（S-11）
- 服务 token 可上传，`uploaded_by` 为 null（S-10）
- 内部 `/web` 附件走同一套 `AttachmentService`（MinIO / 文件系统，25MiB，MIME 更宽）
- **没有**平台那套两段式 `attachment_ref`、孤儿 24h、关单清图、缺配置降级——那些留在网关真源
- 租户读图仍走网关鉴权读端点；Relay 存的是坐席要看的副本

### 2.6 租户侧 API 形状 — 硬

平台要的（Account 会话鉴权）：

```
POST/GET  /support/attachments[/:ref]
GET/POST  /support/tickets
GET       /support/tickets/:ticket_ref
POST      /support/tickets/:ticket_ref/messages
POST      /support/tickets/:ticket_ref/close
POST      /support/tickets/:ticket_ref/reopen
POST      /support/tickets/:ticket_ref/read
```

Relay 现有（Bearer 服务 token）：

```
POST/GET  /api/v1/tickets
PATCH     /api/v1/tickets/{key}          强制 If-Match
POST      /api/v1/tickets/{key}/transitions
GET/POST  /api/v1/tickets/{key}/comments
GET       /api/v1/tickets/{key}/history
POST/GET  /api/v1/tickets/{key}/attachments
GET       /api/v1/tickets/{key}/attachments/{id}/link
DELETE    /api/v1/tickets/{key}/attachments/{id}
```

缺：关单/重开、已读、消息作者类型（会话仍在网关）。
多：`rev` / `If-Match`、幂等键、`external_ref`、`category`、标签名——网关同步时带上。
错误码：平台 `1130–1136` 数字码；Relay 是 RFC 9457 `problem+json`。

### 2.7 配额、限流、校验 — 绕（网关可自己做）

| 平台 | Relay |
|------|-------|
| 未结工单 ≤20、当日建单 ≤10 | 无账户配额 |
| 上传 30/min、建单/回复 10/min | token 读 600 / 写 120 per min |
| 标题 1–200、描述必填 10–5000 | 标题 1–500、描述可空、上限 20000 |
| `contact_email` RFC 必填 | `submitter.email` 可选 |

超限码 `1135`、附件不可用 `1136` 都不存在。这些更适合放在**网关**（它才有 Account 上下文），不必下沉到 Relay。

### 2.8 通知闭环 — 绕（且应保持现状）

平台：坐席回复 → 站内信（提交人 + Owner）+ 可选邮件到 `contact_email`（正文只摘要 + 控制台链接）。

Relay（S-22）：**不触达网关用户**。只给 Relay 内部人发站内信（指派 / 提及 / 状态变更）。没有 `support_ticket_replied`，邮件通道也不走工单回复。

Webhook 已有：`ticket.created` / `updated` / `status_changed` / `comment_created`。
网关可以拿它当钩子，但站内信和邮件必须网关自己发。**不要让 Relay 变成对外系统**——这与 S-22 / Phase 4 范围一致。

### 2.9 坐席 / 平台服务面 — 硬（若本期要闭环）

平台 §8 给 OmniControl 预留：

```
GET  /platform/service/support-tickets
GET  /platform/service/support-tickets/:ref
POST .../messages          可 internal=true
POST .../status            pending / resolved
GET  .../attachments/:ref
```

本期至少要「读 + 回复」，否则就是能收不能回（平台 Q10）。

Relay 服务 token 能建单/改单/评论/流转，但：

- 不能按账户 / 类型筛（没有这些字段）
- 不能写 internal 备注
- 没有坐席展示名 / `AuthorOperatorID`
- 没有平台 JWT

若坐席就在 Relay Web UI 里回，这条可以不做成「平台服务 API」，但 2.4 的 internal 过滤仍然要有——否则租户面不能安全地同步会话。

### 2.10 安全与留存 — 硬（附件路径）/ 绕（其余）

- 跨账户中性 404：Relay 有租户隔离，隔离单位是 Tenant，不是平台 Account。
- 附件读端点的 CSP / nosniff / 不信客户端 MIME：公开 API 上不存在。
- SVG 排除、EXIF 剥离、像素炸弹：公开路径没有。
- 审计「只记状态/类型、不记正文和附件」：有审计，不是 `support_ticket` 这套 ResourceKind。
- 孤儿 / 过期附件清理：没有。

---

## 3. 一张对照表（给评审用）

| 平台能力 | Relay | 判定 |
|----------|-------|------|
| Account 级作用域 + 成员可见 | 无 | 硬缺口 |
| `tkt_` 对外标识 | `RL-n` | 硬缺口（不要改 Relay 编号，网关自己发 `tkt_`） |
| 6 类 category | **`category` 列** | 已接通（S-26） |
| 客服状态机 + close/reopen | 工程状态机 + transitions | 硬缺口（有损映射，见 4.3） |
| 会话 + internal 硬过滤 | 评论全量可读 | 硬缺口 |
| 两段式图片附件（租户面） | 公开 API 可把文件挂到工单 | 租户面仍在网关；**坐席副本已接通** |
| 未读 / 已读 | 无 | 硬缺口（租户 UX，网关做） |
| 配额 / 描述长度 / 邮箱 | 规则不同 | 网关可补 |
| 租户站内信 + 邮件 | 明确不做 | 网关做，保持 |
| 坐席工作台 | Relay UI 可用 | 可当本期兜底 |
| SLA / 评分 / 富文本 / 分派 | 双方都本期不做 | 对齐 |

---

## 4. 建议

### 4.1 架构：真源放网关，Relay 当坐席面（推荐）

```
租户 ── 网关 WebUI / backend（support_tickets 真源，按平台设计落地）
              │
              │ 可选：建单/状态/公开回复同步到 Relay
              ▼
         Relay（坐席调查、指派、内部评论、AI 上下文）
              │
              │ webhook：status_changed / comment_created（仅非 internal）
              ▼
         网关：站内信 + 邮件 + 租户时间线
```

为什么选这个，而不是「Relay 当真源、网关只做 UI」：

1. 平台设计已经按 Account、附件抽象、错误码 1130、OmniControl 契约写完；硬塞进 Relay 等于推翻 Relay 冻结的状态机与 `RL-` 编号（§8.6：改枚举是 v2）。
2. 租户可见性、配额、联系邮箱、未读、关单 7 天，都是 Account 语义，网关已有 `AccountContext`。
3. Relay 的价值在坐席侧：指派、优先级、AI 上下文、内部讨论、与日志/trace 接力。这些平台明确本期不做。
4. S-22 已经定过：Relay 不直接触达网关用户。通知留在网关，两边都不用改这条。

不推荐的替代：把 Relay `/api/v1/tickets` 改造成平台契约。代价是破坏冻结 API，且客服会话与工程看板会缠在同一张 `ticket` 表上。

### 4.2 同步契约（若采纳 4.1）

网关是租户真源。同步到 Relay 时建议：

| 方向 | 内容 | 注意 |
|------|------|------|
| 网关 → Relay | 建单：`external_ref = {system: "gateway-webui", id: tkt_xxx}`，`submitter`，`source`，`category`，`labels: ["from-gateway-webui"]`，`Idempotency-Key` | 缺单则建；`external_ref` 命中返回既有单。标记全部落库 |
| 网关 → Relay | 图片/文件 → `POST /api/v1/tickets/{key}/attachments` | 租户面真源仍在网关 AttachmentStore；这是坐席副本 |
| 网关 → Relay | 租户补充说明 → Relay 评论 | 标来源，避免和坐席评论混读 |
| 网关 → Relay | 租户关单 / 重开 → Relay `transitions` | 映射表必须写死，见 4.3 |
| Relay → 网关 | webhook `status_changed` | 只映射「坐席可触发」的状态；不要让租户在网关关单后再被 Relay 看板拖回 |
| Relay → 网关 | webhook `comment_created` | **只同步明确标记为对租户可见的回复**；在 2.4 落地前，不要同步任何评论 |

### 4.3 状态映射（有损，必须写进对接文档）

只用于「网关真源 → Relay 看板展示」，**不要反向当权威**。

| 平台 status | Relay status（建议） | 说明 |
|-------------|----------------------|------|
| `open` | `todo` | 待受理 |
| `pending` | `in_progress` | 坐席已接手 |
| `awaiting` | `blocked` + reason「等待用户补充」 | Relay 没有 awaiting；用 blocked 是权宜，产品文案不要对坐席说「阻塞」 |
| `resolved` | `done` | 可重开 |
| `closed` | `wont_fix` 或保持 `done` 不再流转 | Relay 无终态；网关必须拒绝之后来自 Relay 的 reopen 写回 |

租户 `reopen`（7 天内）：网关改自己的单为 `open`/`pending`，再调 Relay `done → todo`。
超过 7 天：只允许新建单（平台口径），Relay 侧不要重开旧 `RL-n`。

### 4.4 落地顺序

与平台文档 §11 对齐，但按「真源在网关」切责任：

| 步 | 谁做 | 验收 | Relay 要不要改 |
|----|------|------|----------------|
| 1 | 网关 | 3 张表 + 租户 API（无附件）：建单/列表/详情/回复/关单 | 建单时调 Relay `/api/v1/tickets`，带上 `external_ref` / `source` / `category` / 标签名 |
| 2 | 网关 | 附件两段式 + 降级 | 建单成功后把图 `POST` 到 Relay `/tickets/{key}/attachments` |
| 3 | 网关 | 租户 UI（列表 + 抽屉 + 上传 + i18n） | 不必改 |
| 4a | 网关 | 站内信 + 邮件 | 不必改（S-22） |
| 4b | 网关 | 平台服务 API 读 + 回复（Q10） | 若坐席用 OmniControl：不必改 Relay。若坐席用 Relay UI：才需要 4c |
| 4c | Relay（仅当坐席留在 Relay） | 评论加 `internal` + `author_kind`（或等价可见性），公开 API 默认不返回 internal；webhook 可过滤 | 会话同步前仍要做。**S-26 已做**：分类标记落库、工单附件端点 |

步 1 即可让租户「能提单」。步 4b/4c 之前是能收不能回，体验比没有工单更差——平台 Q10 说得对，这一步不能省。

### 4.5 Relay 明确不要做的

- 不要为客服单新增一套与 `todo/done` 并行的状态枚举进同一张 `ticket` 表。
- 不要把租户 Account 成员映射成 Relay 用户只为了「他们能登录看自己的单」。
- 不要让 Relay 给 `contact_email` 发信。
- 不要把网关的两段式 `attachment_ref` / 配额 / 清图策略下沉到 Relay——租户面真源仍在网关。

### 4.6 需要两边一起拍板的（平台 §10 里和 Relay 有关的）

| # | 问题 | 建议 |
|---|------|------|
| 真源 | 支持工单存在哪 | **网关**。Relay 同步内部调查视图（**已拍板**） |
| Q10 | §8 落地前怎么回 | 优先做网关平台服务 API 的读 + 回复；坐席短期用 Postman / 最小页。若坐席必须在 Relay 回，先做 4c 再同步评论 |
| 评论同步 | 哪些评论回写租户 | 默认不同步。仅 `author_kind=agent AND internal=false` |
| `awaiting` 映射 | Relay 没有该状态 | 用 `blocked` + 固定 reason，或坐席只在网关改、Relay 保持 `in_progress` |
| 附件 | 要不要进 Relay | **要，作为坐席副本**（S-26）。租户面真源仍在网关 AttachmentStore |

平台 Q1–Q9、Q11–Q12（可见性、string 枚举、GIF/WebP、纯文本、EXIF、通知分类、SLA、OSS vs 本地盘、类型清单、配额数值）都是网关自己的题，Relay 无意见，按平台建议默认即可。

---

## 5. 一句话

平台设计要的是**网关里的客服会话系统**；Relay 给的是**内部调查看板 + 同步过来的坐席副本**（单、分类标记、图片/文件）。
租户面缺口仍在：Account 作用域、客服状态机、internal 会话、关单/重开/已读。
真源在网关；Relay 缺单则建、标记落库、附件进对象存储（S-26）。坐席若继续在 Relay 回单，还要补评论可见性。
