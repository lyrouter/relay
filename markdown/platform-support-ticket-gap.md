# Relay × 平台支持工单：缺口与建议

> 对照文档：[ai_gateway_webui `support-ticket-design.md`](../../aigateway/ai_gateway_webui/docs/support-ticket-design.md)（状态：设计稿，待确认）
> Relay 侧既有口径：[relay-s1-design.md](relay-s1-design.md) §8.8 / API-6 / S-22 / **S-26**
> 日期：2026-08-26
> 范围：列清「平台设计要什么、Relay 现在给什么、差什么」。**§0 / §2.1–2.10 已拍板**：真源在网关；Relay 只做同步写入与坐席调查；租户列表/详情/会话/附件/API/配额/通知/服务面/安全都在网关表与网关契约上。**§2.2** Relay 状态机已改为 `new → assign → working → resolved → reopen → closed`（代码已落地）。

---

## 0. 先定一件事：这不是同一套工单

平台设计的「工单」是**租户提交给我们的客服支持单**（Account 级会话）。
Relay 现有的「工单」是**团队内部的调查/工程单**（Tenant 级看板：指派、迭代、AI 上下文）。状态机按澄清 2.2 为 `new → assign → working → resolved → reopen → closed`。

两边都叫 ticket，模型、状态机、可见性、附件、通知全部不是一套。
Relay S1 原先对平台的假设更窄：网关 WebUI 当**第一个 API 消费方**，把「问题反馈」落到 Relay，截图不走 API，进度由网关轮询。平台新设计已经扩成完整的客服会话系统。

**不先拍板「真源在哪」，后面每一项改动都会返工。**

建议默认：**支持工单的真源在网关控制面**（按平台设计落地），Relay 当坐席工作台 / 内部调查面。
理由见 §4。

**已拍板（2026-08-26）**：同意这条默认。Relay 侧同步时：

1. 网关有单、Relay 没有 → **建单**（沿用 `external_ref` 去重）。
2. 标记落库，不只写在描述里：`source`、`ticket_external_ref`（`tkt_…`）、`category`（平台 6 类）、标签名（如 `from-gateway-webui`）。
3. 图片 / 文件一并同步进 Relay 对象存储（`POST /api/v1/tickets/{key}/attachments`）。网关 AttachmentStore 仍是租户面真源；Relay 存坐席工作台用的副本。
4. **租户列表 / 详情 / 可见性只查网关自己的表**（澄清 2.1）。Relay `/api/v1` 是同步写入 + 坐席面，不是租户控制台的读后端，也不是网关租户 API 的代理。
5. **Relay 状态机改为** `new → assign → working → resolved → reopen → closed`（澄清 2.2）。仍是同一张 `ticket` 表、同一套枚举，不另开客服状态列。不改 `RL-` 编号。**已落地**（`TRANSITIONS` / 冻结契约 / 看板 / 数据迁移）。
6. **`category` 与内部字段边界**（澄清 2.3）：平台 6 类走可空 `category`，不改冻结的 `type`（`bug/feature/task`）。优先级、指派、迭代、标签、PR、`ai_context`、`rev` 只给坐席调查面；租户 API / 控制台不得下发这些字段。
7. **租户消息流只在网关表**（澄清 2.4）。Relay 评论是坐席调查笔记。硬过滤在写入网关租户时间线那一跳。默认 Relay → 网关不同步评论；坐席优先在网关回（4b），仅留在 Relay UI 才做评论 `internal`（4c）。
8. **附件两套职责**（澄清 2.5）：网关是租户面上传/下载/清理真源；Relay 只存最终挂单后的坐席副本。副本失败不回滚建单。
9. **租户 API 不是 Relay 换皮**（澄清 2.6）：`/support/*` 的路径、返回、错误码由网关定义；`tkt_` / `tmsg_` / `tatt_` 网关发号。
10. **配额 / 限流 / 校验在网关**（澄清 2.7）：按 Account 判；Relay 只做同步入口保护。
11. **租户通知在网关**（澄清 2.8）：站内信 / 邮件以网关消息落库为触发；Relay 不对外触达（S-22）。
12. **坐席闭环走网关服务面**（澄清 2.9 / Q10）：优先 4b；Relay UI 只兜底调查。
13. **租户面安全与留存在网关**（澄清 2.10）：下载头、解码、清理、最小审计都在网关；Relay 只管内部副本短链。

---

## 1. 已经能对上的（旧「问题反馈」入口）

这些满足的是 Relay §8.8，**不是**平台新设计：

| 能力 | Relay 现状 |
|------|------------|
| 网关持服务 token 建单 / 列表 / 详情 | `POST/GET /api/v1/tickets`、`GET /api/v1/tickets/{key}`。**仅网关/坐席对账与调查**；不能当租户读模型（§2.1） |
| 真实提交者（非 Relay 账号） | `submitter = {name, email?, external_id?}`，无权限效果 |
| 来源标记 | `source`（如 `gateway-webui`） |
| 去重 | `external_ref`（业务）+ `Idempotency-Key`（网络） |
| 进度回显 | 轮询 `status` + `updated_at`；或订 webhook |
| 租户隔离 | 跨租户 404（隔离单位是 Relay Tenant，不是平台 Account） |
| 坐席在 Relay 里改单、评论、流转 | 同一套 `/api/v1` + Web UI |

结论：现在能收一张「问题反馈」工单，不能当客服会话系统用。租户看见的列表/详情更不能从这条 GET 拼出来。

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

**已拍板（澄清 2.1，2026-08-26）**：租户列表 / 详情 / 可见性必须由**网关自己的表**做。Relay 不能当租户读模型。

这不是「网关可以代理 Relay GET」的实现细节，是职责边界：

| 面 | 读什么 | 谁鉴权、谁过滤 |
|----|--------|----------------|
| 租户控制台：列表、详情、本账户谁能看见 | 网关 `support_tickets`（及消息、附件元数据） | 网关 `AccountContext`；Q1 默认本账户全体成员 |
| 坐席调查 / 同步写入 | Relay `ticket`（`RL-n`、看板、内部评论、坐席附件副本） | Relay 服务 token + 坐席会话 |

禁止：

- 租户 `GET /support/tickets`（及详情）转调、聚合或缓存 `GET /api/v1/tickets`
- 用 Relay Guest 规则（只见负责人/报告人）去近似「本账户全体成员可见」
- 把平台 Account 成员映射成 Relay 用户，只为了让他们登录看见自己的单（与 §4.5 同一条）

Relay `GET /api/v1/tickets` 可以继续给网关服务 token 用（对账、排障、坐席面）。它的返回形状、可见性、编号都是工程看板的，**不是**平台租户契约。对外 `tkt_` 由网关发号并存在网关表；`RL-n` 只给坐席。

因此本节表格里的三条硬缺口（Account 作用域、`tkt_` 标识、本账户可见）**都不在 Relay 补**。网关落自己的表和租户 API；Relay 只接收同步写入。

### 2.2 状态机 — 硬（Relay 枚举**已改**）

平台（租户真源，不变）：

```
open → pending → awaiting → resolved → closed
                用户 reopen（resolved 后 7 天内）
closed 终态，不可逆
租户只能 close / reopen；其余只给坐席
```

Relay **现状**（代码 / 冻结 `/api/v1`）：

```
new → assign → working → resolved → closed
                         └→ reopen → assign | working
closed 终态，不可逆
只有 POST /tickets/{key}/transitions + If-Match
```

**已拍板且已落地（澄清 2.2，2026-08-26）**：改 Relay 自己的状态机（同一张 `ticket`、同一套 `status`），目标为上面这一套。

API 取值仍走 Relay 惯例（snake_case 小写）：`new` / `assign` / `working` / `resolved` / `reopen` / `closed`。

读写约定：

- 主链：`new → assign → working → resolved → closed`。
- `reopen` 是**状态**，不是关单前的必经节点：从 `resolved` 可以去 `closed`，也可以去 `reopen`；从 `reopen` 回到 `assign` 或 `working` 继续跟。
- 流转仍走 `POST /tickets/{key}/transitions` + `If-Match`。租户 `POST .../close` / `.../reopen` 仍在网关（澄清 2.1）；网关改自己的单之后，再对 Relay 发对应 transition。
- 去掉 `todo` / `in_progress` / `in_review` / `done` / `blocked` / `wont_fix`。工程看板和网关同步单共用这六个值。
- **不另开**一套客服状态列，也不把平台的 `open/pending/awaiting` 原样塞进 Relay。

和平台仍对不齐的（不在 Relay 补，网关自己扛）：

- 平台有 `awaiting`（等用户补充），Relay 没有；同步时落到 `working`（见 4.3）。
- 7 天重开窗口、租户只能 close/reopen：网关鉴权。Relay 不实现租户角色，也不做 7 天钟。
- 这是对冻结 `/api/v1` `status` 枚举的破坏（S1 §8.6：改枚举本是 v2）。已接受在现网上换值：迁移旧行、改 `TRANSITIONS`、改 `openapi.json` 与看板文案。

### 2.3 类型与字段 — 硬（类型）/ 绕（部分字段）

平台 6 类：`presale / aftersale / billing / technical / feedback / other`。
Relay 工程类型仍是 `bug / feature / task`（冻结）。

**已拍板（澄清 2.3，2026-08-26）**：

1. 赞同 **S-26**：另有可空 `category`，取值就是上面 6 类，**不混进 `type`**。网关同步时写入；列表可按 `?category=` 筛。工程看板继续用 `type`。
2. Relay 多出来的（优先级、指派、迭代、标签、PR、`ai_context`、`rev`）是内部调查面需要的，**不应**暴露给租户。租户控制台只读网关表（澄清 2.1）；网关租户响应里也不要回填这些 Relay 字段。`rev` / `If-Match` 仅服务 token ↔ Relay 同步用。

| 平台字段 | Relay | 澄清 2.3 后 |
|----------|-------|-------------|
| 6 类 category | **`category` 列 + API 字段**（可空；`?category=`） | **已接通 / 已拍板** |
| `type` = bug/feature/task | 冻结工程类型 | **保持**；不拿平台 6 类替换 |
| 优先级、指派、迭代、标签、PR、`ai_context`、`rev` | 有 | **坐席面保留**；租户面不下发 |
| `contact_email` 必填 | 仅可选 `submitter.email` | 仍绕：网关自己校验必填 |
| `message_count` / `last_reply_at` / `last_reply_by`（tenant\|agent） | 无 | 仍缺口：网关表自己算 |
| `unread_by_tenant` + `POST .../read` | 无 | 仍缺口：网关做 |
| `resolved_at` / `closed_at` | 无 | 仍缺口：网关做 |
| `workspace_ref` | 无 | 仍绕：网关存上下文即可 |

### 2.4 会话模型 — 硬（internal 泄漏）

平台要求统一消息流：

- `author_kind = tenant | agent | system`
- 坐席 `internal=true` **永不下发租户**（repo 硬过滤 + 单测锁死）
- 状态迁移插 `system` 消息，时间线渲染为系统事件
- 坐席只露昵称/工号，不下发真名邮箱

Relay **现状**：`ticket_comment` 只有 `body` + `author_id`。

- **没有 `internal` 标志**。服务 token 带 `tickets:read` 能拿到全部评论。S-22 写明：过滤是消费方约束，API 不拦。
- 状态历史在 `ticket_status_history`，不是会话里的 system 气泡。
- 没有 `author_display`。

若网关把 Relay 评论原样给租户，内部讨论会漏出去。这不是文档约定能兜住的。

**已拍板（澄清 2.4，2026-08-26）**：会话时间线跟列表/详情走同一条边界——**租户消息流只存在网关自己的表**。Relay 评论是坐席调查笔记，不是租户读模型，也不是网关 `GET .../messages` 的后端。

具体：

| 谁写 | 写到哪 | 要不要同步 |
|------|--------|------------|
| 租户发消息 | 网关 messages（`author_kind=tenant`） | 可选：抄一份到 Relay 评论，标来源，方便坐席在看板里看见 |
| 坐席对租户回复 | **先写网关** messages（`author_kind=agent`，`internal=false`） | 可选：再抄到 Relay 评论 |
| 坐席内部备注 | 若坐席在 OmniControl：只写网关 `internal=true`。若坐席在 Relay：只写 Relay 评论，**默认当内部** | **永不**写入网关租户时间线 |
| 状态变化 | 网关插 `system` 气泡；Relay 仍用 `ticket_status_history` | 不要在 Relay 评论里伪造 system 消息给租户 |

硬过滤放在**写入网关租户时间线**的那一跳（repo + 单测），不要指望 S-22「消费方自己滤」。`author_display`（只露昵称/工号）是网关租户 API 的事；Relay 坐席面可以继续显示真名。

坐席在哪回，决定 Relay 要不要改评论模型：

- **优先：坐席在网关平台服务 API / 最小页回（4b）**。本期可以不给 Relay 评论加 `internal`。租户安全不依赖 Relay GET。S-22 维持：服务 token 仍能读到全部 Relay 评论（给坐席）。
- **仅当坐席必须留在 Relay UI 才做 4c**：评论加 `internal`（**默认 `true`，失败关闭**）和可选 `author_kind`。只有显式 `internal=false` 才允许 webhook / 同步任务写进网关 messages。公开 API 继续把全量评论给服务 token 和坐席；**不要**让租户 API 去 GET Relay comments。

默认：**Relay → 网关不同步任何评论**，直到上面那条「仅 `internal=false`」落地。先抄租户消息进 Relay 可以，反向必须等硬过滤。

禁止：把 Relay `ticket_comment` 改造成平台会话（`tmsg_`、system 气泡、对服务 token 默认藏 internal）。那会和工程讨论缠在同一张评论表上，且和澄清 2.1 打架。

### 2.5 附件 — 坐席副本已接通；租户面仍在网关

平台：两段式上传（先 `POST /support/attachments` 拿 `attachment_ref`，建单再挂）；≤5 张；PNG/JPEG；5MiB；真解码 + 解压炸弹 + 剥 EXIF；孤儿 24h 清理；关单 180 天清图留字；配置缺失**降级仍能提单**。

Relay（S-26）：

- **`POST/GET /api/v1/tickets/{key}/attachments`**，挂在这张工单上；`GET …/link` 仍是先鉴权再签 5 分钟链接（S-11）
- 服务 token 可上传，`uploaded_by` 为 null（S-10）
- 内部 `/web` 附件走同一套 `AttachmentService`（MinIO / 文件系统，25MiB，MIME 更宽）
- **没有**平台那套两段式 `attachment_ref`、孤儿 24h、关单清图、缺配置降级——那些留在网关真源
- 租户读图仍走网关鉴权读端点；Relay 存的是坐席要看的副本

**已拍板（澄清 2.5，2026-08-26）**：附件分成“两套职责、一个副本”：

- **网关**负责租户上传体验与安全约束：两段式 `attachment_ref`、5 张 / 5MiB、格式白名单、真解码、EXIF 剥离、孤儿 24h 清理、关单 180 天清图留字、配置缺失时降级仍能提单。
- **Relay**只负责坐席副本：工单创建成功后，网关把最终挂单的图片/文件再 `POST /api/v1/tickets/{key}/attachments` 一份给 Relay，供坐席在调查面查看。
- 租户读附件**永远**回网关；不要把 Relay `/attachments/{id}/link` 暴露给租户，也不要让网关把这个链接转签给租户。
- 同步粒度以“最终挂到工单上的附件”为准；预上传孤儿、被用户取消的附件、不合规被拒的附件都**不要**进入 Relay。
- 网关表里保留自己的 `tatt_` / `attachment_ref`；Relay 只保留副本自己的 attachment id。两边用 `external_ref` / 附件映射表关联，不要求同号。

默认失败策略：**租户面成功优先**。若 Relay 副本上传失败，不回滚网关建单；记审计 / 告警，允许后补重试。

### 2.6 租户侧 API 形状 — 硬（路径在网关；禁止代理 Relay）

平台要的（Account 会话鉴权，**打在网关自己的表上**，澄清 2.1）：

```
POST/GET  /support/attachments[/:ref]
GET/POST  /support/tickets
GET       /support/tickets/:ticket_ref
POST      /support/tickets/:ticket_ref/messages
POST      /support/tickets/:ticket_ref/close
POST      /support/tickets/:ticket_ref/reopen
POST      /support/tickets/:ticket_ref/read
```

这些路径不能做成 Relay `/api/v1/tickets` 的薄封装。Relay 现有（Bearer 服务 token，只给同步写入 / 坐席）：

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

**已拍板（澄清 2.6，2026-08-26）**：租户 API 完全按平台契约收口，Relay `/api/v1` 继续只做同步写入 / 坐席面。

- 路径、鉴权、返回体、错误码都以网关 `/support/*` 为准；不要透传 Relay 的字段名、状态值、附件 id、`problem+json`。
- 网关把自己的账户语义翻译完再决定是否写 Relay：建单时写 `/api/v1/tickets`，回复 / 关单 / 重开时写相应 transition 或评论同步。
- `ticket_ref` / `message_ref` / `attachment_ref` 都由网关生成并冻结；Relay 的 `RL-n` 和内部 UUID 不进入租户 API。
- `read`、`close`、`reopen` 这些租户动作即使最终会同步到 Relay，也必须先落网关表，再异步或事务后写 Relay。
- 数字错误码 `1130–1136` 由网关统一产出；不要把 Relay 的错误直接冒给租户。

一句话：**租户看见的平台 API 是网关产品面，不是 Relay 契约的换皮。**

### 2.7 配额、限流、校验 — 绕（网关可自己做）

| 平台 | Relay |
|------|-------|
| 未结工单 ≤20、当日建单 ≤10 | 无账户配额 |
| 上传 30/min、建单/回复 10/min | token 读 600 / 写 120 per min |
| 标题 1–200、描述必填 10–5000 | 标题 1–500、描述可空、上限 20000 |
| `contact_email` RFC 必填 | `submitter.email` 可选 |

超限码 `1135`、附件不可用 `1136` 都不存在。这些更适合放在**网关**（它才有 Account 上下文），不必下沉到 Relay。

**已拍板（澄清 2.7，2026-08-26）**：这些规则全部定在网关，Relay 不复制一遍。

- 配额按 **Account** 维度算：未结工单数、当日建单数、上传频率、建单 / 回复频率，都由网关自己的表与限流器判。
- 校验按平台口径执行：标题长度、描述必填、`contact_email` RFC、附件上限 / 类型 / 大小，统一在网关拦住。
- 命中限额或附件能力不可用时，由网关返回 `1135` / `1136`；不要尝试把 Relay 的限流语义翻译成租户产品语义。
- Relay 保留自己现有的服务 token 限流与字段校验，作为**同步入口保护**，但它不是租户规则来源。

禁止把这些规则下沉到 Relay：那会把 Account 维度硬塞进 Tenant 工程系统里，还会让两边阈值漂移。

### 2.8 通知闭环 — 绕（且应保持现状）

平台：坐席回复 → 站内信（提交人 + Owner）+ 可选邮件到 `contact_email`（正文只摘要 + 控制台链接）。

Relay（S-22）：**不触达网关用户**。只给 Relay 内部人发站内信（指派 / 提及 / 状态变更）。没有 `support_ticket_replied`，邮件通道也不走工单回复。

Webhook 已有：`ticket.created` / `updated` / `status_changed` / `comment_created`。
网关可以拿它当钩子，但站内信和邮件必须网关自己发。**不要让 Relay 变成对外系统**——这与 S-22 / Phase 4 范围一致。

**已拍板（澄清 2.8，2026-08-26）**：通知闭环完全留在网关，Relay 只当内部工作台。

- 触达租户的站内信 / 邮件由网关根据自己的消息表和状态表发，不依赖 Relay 直接出站。
- 若坐席在网关回，网关可同步写 Relay，但通知仍以“网关消息已落库”为触发点，而不是以 Relay webhook 为真。
- 若坐席在 Relay 回且 4c 已落地，网关才可消费明确 `internal=false` 的评论同步事件；通知仍由网关生成。
- Relay 内部通知继续只给内部用户，不新增 `support_ticket_replied` 之类面对租户的通道。

默认顺序：**先有网关消息，再有租户通知**。不要把通知建立在“稍后也许会同步成功”的 Relay 事件上。

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

若坐席就在 Relay Web UI 里回，这条可以不做成「平台服务 API」，但必须先做 4c（评论 `internal` 默认 true），否则不能把任何 Relay 评论写回网关租户时间线（澄清 2.4）。优先仍是 4b。

**已拍板（澄清 2.9，2026-08-26）**：本期优先做**网关自己的平台服务 API（4b）**，保证“能收也能回”。

- 最小闭环是：列表、详情、公开回复、内部备注、改状态、取附件。先给 OmniControl / 最小后台页用，不要求一步到位做完整客服台。
- 坐席回复的真源仍是网关消息表，这样 `author_kind`、`internal`、`author_display`、通知、审计都在一处成立。
- Relay Web UI 只作为兜底调查面，不承担租户会话权威写入口；若必须在 Relay 回，才补 4c。
- 平台服务面筛选维度按账户、分类、状态、未读、最近回复时间来设计；不要反过来按 Relay 看板字段设计再向外解释。

一句话：**Q10 的答案不是“让坐席去 Relay 评论区回复”，而是尽快给网关一个能回的最小服务面。**

### 2.10 安全与留存 — 硬（附件路径）/ 绕（其余）

- 跨账户中性 404：Relay 有租户隔离，隔离单位是 Tenant，不是平台 Account。
- 附件读端点的 CSP / nosniff / 不信客户端 MIME：公开 API 上不存在。
- SVG 排除、EXIF 剥离、像素炸弹：公开路径没有。
- 审计「只记状态/类型、不记正文和附件」：有审计，不是 `support_ticket` 这套 ResourceKind。
- 孤儿 / 过期附件清理：没有。

**已拍板（澄清 2.10，2026-08-26）**：安全与留存按“租户面在网关、内部副本在 Relay”分层处理。

- **网关**承担租户面安全约束：跨账户中性 404、附件下载头（CSP / `nosniff`）、类型白名单、真解码、EXIF 剥离、炸弹检测、孤儿清理、关单后清图留字。
- **Relay**只保留内部副本需要的最小安全：先鉴权再签短链、Tenant 隔离、内部审计；不要把它包装成租户文件服务。
- 审计以最小泄露为原则：租户面动作只记状态、类型、actor、对象 ref、是否含附件，不记消息正文和附件内容。
- 留存策略以网关真源为准；Relay 副本可更短，不应比网关更“权威”。若网关清图，Relay 也应有配套清理或至少标记失效，避免坐席继续依赖过期副本。

禁止把租户面附件安全责任外包给 Relay 链接鉴权；那会让真正暴露在外的下载路径失去自己的安全保证。

---

## 3. 一张对照表（给评审用）

| 平台能力 | Relay | 判定 |
|----------|-------|------|
| Account 级作用域 + 成员可见 | 网关自己的表；Relay 不当读模型 | **已拍板**（澄清 2.1） |
| `tkt_` 对外标识 | `RL-n` | 硬缺口（不要改 Relay 编号，网关自己发 `tkt_`） |
| 6 类 category | **`category` 列**（不混进 `type`） | **已拍板**（澄清 2.3 / S-26） |
| 内部字段（优先级/指派/迭代/标签/PR/`ai_context`/`rev`） | 坐席面有 | **已拍板**：不暴露给租户 |
| 客服状态机 + close/reopen | Relay 已改为 `new/assign/working/resolved/reopen/closed`；租户 close/reopen 仍在网关 | **已落地**（澄清 2.2）。`awaiting` 仍有损，见 4.3 |
| 会话 + internal 硬过滤 | 租户时间线在网关；Relay 评论不当读模型 | **已拍板**（澄清 2.4）。默认不同步评论回网关 |
| 两段式图片附件（租户面） | 公开 API 可把文件挂到工单 | **已拍板**（澄清 2.5）：网关真源 + Relay 副本；租户读图只走网关 |
| 未读 / 已读 | 无 | 硬缺口（租户 UX，网关做） |
| 配额 / 描述长度 / 邮箱 | 规则不同 | **已拍板**（澄清 2.7）：全部定在网关；Relay 只保留同步入口保护 |
| 租户站内信 + 邮件 | 明确不做 | **已拍板**（澄清 2.8）：完全留网关；Relay 不对外发 |
| 坐席工作台 | Relay UI 可用 | **已拍板**（澄清 2.9）：优先补网关最小服务面，Relay 只兜底 |
| 安全与留存 | Relay 只有内部附件短链与通用审计 | **已拍板**（澄清 2.10）：租户面安全 / 留存在网关；Relay 管内部副本 |
| SLA / 评分 / 富文本 / 分派 | 双方都本期不做 | 对齐 |

---

## 4. 建议

### 4.1 架构：真源放网关，Relay 当坐席面（推荐）

```
租户 ── 网关 WebUI / backend（support_tickets 真源；列表/详情/可见性只查这张表）
              │
              │ 可选：建单/状态/公开回复同步到 Relay（只写，不读回租户面）
              ▼
         Relay（坐席调查、指派、内部评论、AI 上下文）
              │
              │ webhook：status_changed（评论默认不同步；见 2.4）
              ▼
         网关：站内信 + 邮件 + 租户时间线
```

为什么选这个，而不是「Relay 当真源、网关只做 UI」：

1. 平台设计已经按 Account、附件抽象、错误码 1130、OmniControl 契约写完；硬塞进 Relay 当真源等于推翻 `RL-` 编号与租户契约。状态机按澄清 2.2 **会**改（坐席看板与同步对齐），编号仍不改。
2. 租户可见性、配额、联系邮箱、未读、关单 7 天，都是 Account 语义，网关已有 `AccountContext`。列表/详情/谁能看见因此必须读网关表，不能读 Relay（澄清 2.1）。
3. Relay 的价值在坐席侧：指派、优先级、AI 上下文、内部讨论、与日志/trace 接力。这些平台明确本期不做。
4. S-22 已经定过：Relay 不直接触达网关用户。通知留在网关，两边都不用改这条。

不推荐的替代：把 Relay `/api/v1/tickets` 改造成平台契约，或让网关租户 API 代理 Relay GET。前者破坏冻结 API；后者把工程看板的可见性、编号、字段泄漏进租户面，且 Guest 规则对不上「本账户全体成员可见」。

### 4.2 同步契约（若采纳 4.1）

网关是租户真源。同步是**网关写 Relay**（坐席调查面），不是租户 UI 读 Relay。同步时建议：

| 方向 | 内容 | 注意 |
|------|------|------|
| 网关 → Relay | 建单：`external_ref = {system: "gateway-webui", id: tkt_xxx}`，`submitter`，`source`，`category`，`labels: ["from-gateway-webui"]`，`Idempotency-Key` | 缺单则建，初始 `new`。`external_ref` 命中返回既有单。标记全部落库 |
| 网关 → Relay | 图片/文件 → `POST /api/v1/tickets/{key}/attachments` | 只同步**最终挂单成功**的附件。租户面真源仍在网关 AttachmentStore；这是坐席副本。副本失败不回滚租户建单 |
| 网关 → Relay | 租户补充说明 → Relay 评论 | 标来源，避免和坐席评论混读 |
| 网关 → Relay | 租户关单 / 重开 → Relay `transitions` | `resolved → closed`；`resolved → reopen` 再回 `assign`/`working`。见 4.3 |
| Relay → 网关 | webhook `status_changed` | 只映射「坐席可触发」的状态；不要让租户在网关关单后再被 Relay 看板拖回 |
| Relay → 网关 | webhook `comment_created` | **默认不同步。** 仅当 4c 落地且 `internal=false` 才写入网关 messages（澄清 2.4） |
| 网关 → 租户 | `/support/*` 响应、通知、附件下载、错误码 | 一律以网关表和网关规则为准；不要透传 Relay 契约 |
| **禁止** | 租户列表 / 详情 / 可见性 ← `GET /api/v1/tickets` | 租户读模型是网关表（澄清 2.1）。不要用 Relay GET 回填控制台 |

### 4.3 状态映射（澄清 2.2 之后；`awaiting` 仍有损）

只用于「网关真源 → Relay 看板展示」，**不要反向当权威**。Relay 枚举以澄清 2.2 为准。

| 平台 status | Relay status | 说明 |
|-------------|--------------|------|
| `open` | `new` | 已落单、尚未指派 |
| `pending` | `assign` | 已指派坐席，尚未开干 |
| `awaiting` | `working` | Relay 没有 awaiting；坐席仍在跟 |
| `resolved` | `resolved` | 可关可重开 |
| `closed` | `closed` | 终态；网关拒绝之后来自 Relay 的 reopen 写回 |

租户 `reopen`（7 天内）：网关改自己的单为 `open`/`pending`，再调 Relay `resolved → reopen`，随后 `reopen → assign` 或 `working`。
超过 7 天：只允许新建单（平台口径），Relay 侧不要重开旧 `RL-n`。
租户 `close`：网关改自己的单为 `closed`，再调 Relay `resolved → closed`。`closed` 不可再转出。

### 4.4 落地顺序

与平台文档 §11 对齐，但按「真源在网关」切责任：

| 步 | 谁做 | 验收 | Relay 要不要改 |
|----|------|------|----------------|
| 0 | Relay | 状态机换成 `new/assign/working/resolved/reopen/closed`；迁旧数据；改冻结契约与看板 | **已落地**（澄清 2.2） |
| 1 | 网关 | 3 张表 + 租户 API（无附件）：建单/列表/详情/回复/关单。**列表/详情只查网关表** | 建单时**写** Relay `/api/v1/tickets`，带上 `external_ref` / `source` / `category` / 标签名。不要 GET Relay 回填租户列表。状态按 4.3 映射 |
| 2 | 网关 | 附件两段式 + 降级 + 安全校验 + 清理策略 | 建单成功后把最终挂单附件 `POST` 到 Relay `/tickets/{key}/attachments`。副本失败不回滚建单 |
| 3 | 网关 | 租户 UI（列表 + 抽屉 + 上传 + i18n） | 不必改 |
| 4a | 网关 | 站内信 + 邮件 | 以网关消息 / 状态落库为触发点，不依赖 Relay 对外发信 |
| 4b | 网关 | 平台服务 API 读 + 回复（Q10） | **已拍板优先做**（澄清 2.9）。若坐席用 OmniControl / 最小后台页：不必改 Relay。若坐席用 Relay UI：才需要 4c |
| 4c | Relay（仅当坐席留在 Relay） | 评论加 `internal`（默认 true）+ 可选 `author_kind`；仅 `internal=false` 可写网关 messages。服务 token 仍返回全量评论 | **不是**「公开 API 默认藏 internal」。澄清 2.4 已拍板；坐席走 4b 则可跳过 |
| 5 | 网关 | 配额 / 限流 / 校验 / 错误码 `1130–1136` | **已拍板**（澄清 2.7）：全部在网关收口；不要把 Account 规则下沉到 Relay |
| 6 | 网关 + Relay | 副本清理与审计对齐 | **已拍板**（澄清 2.10）：网关真源决定留存；Relay 跟随清理或标记失效 |

步 1 即可让租户「能提单」。步 4b/4c 之前是能收不能回，体验比没有工单更差——平台 Q10 说得对，这一步不能省。

### 4.5 Relay 明确不要做的

- 不要为客服单另开一套与工程单并行的 `status` 列。澄清 2.2 是**换掉**同一套枚举，不是两套并存。
- 不要把平台的 `open/pending/awaiting` 原样拷进 Relay；Relay 就用 `new/assign/working/resolved/reopen/closed`。
- 不要把平台 6 类写进冻结的 `type`；6 类只走可空 `category`（澄清 2.3）。
- 不要把优先级、指派、迭代、标签、PR、`ai_context`、`rev` 下发给租户控制台或租户 API（澄清 2.3）。
- 不要把 Relay `ticket_comment` 当租户时间线，也不要把 `GET /comments` 代理给租户（澄清 2.4）。
- 不要在 4c 落地前把 Relay 评论同步进网关 messages。4c 之后也只有显式 `internal=false` 才能写。
- 不要把 `ticket_comment` 改造成 `tmsg_` / system 气泡 / 对服务 token 默认藏 internal。
- 不要把租户 Account 成员映射成 Relay 用户只为了「他们能登录看自己的单」。
- 不要让 Relay 给 `contact_email` 发信。
- 不要把网关的两段式 `attachment_ref` / 配额 / 清图策略下沉到 Relay——租户面真源仍在网关（澄清 2.5 / 2.7 / 2.10）。
- 不要把租户附件下载指到 Relay `/attachments/{id}/link`，也不要把它转签给租户（澄清 2.5）。
- 不要让 Relay webhook 成为租户通知或租户 API 成功返回的前置条件（澄清 2.6 / 2.8）。
- **不要把 `GET /api/v1/tickets` 当租户列表 / 详情 / 可见性的读模型**（澄清 2.1）。网关租户 API 只打自己的表。

### 4.6 需要两边一起拍板的（平台 §10 里和 Relay 有关的）

| # | 问题 | 建议 |
|---|------|------|
| 真源 | 支持工单存在哪 | **网关**。Relay 同步内部调查视图（**已拍板**） |
| 租户读路径 | 列表 / 详情 / 可见性读哪 | **网关自己的表**。Relay 只接收同步写入，不当读模型（**已拍板**，澄清 2.1） |
| Relay 状态机 | 坐席看板用哪套 status | **`new → assign → working → resolved → reopen → closed`**（**已落地**，澄清 2.2） |
| `category` vs `type` | 平台 6 类放哪 | **可空 `category`**，不混进冻结 `type`（**已拍板**，澄清 2.3 / S-26） |
| 内部字段对外 | 优先级/指派/迭代等给不给租户 | **不给**。只留坐席调查面（**已拍板**，澄清 2.3） |
| 附件职责 | 两段式 / 清理 / 租户下载放哪 | 都在**网关**；Relay 只存坐席副本（**已拍板**，澄清 2.5） |
| 租户 API 形状 | `/support/*` 是不是 Relay 换皮 | **不是**。路径 / 返回 / 错误码都由网关定义（**已拍板**，澄清 2.6） |
| 配额 / 校验 | 谁来判 | **网关**按 Account 判；Relay 只做同步入口保护（**已拍板**，澄清 2.7） |
| 租户通知 | 谁发站内信 / 邮件 | **网关**发；Relay 不对外触达（**已拍板**，澄清 2.8） |
| Q10 | §8 落地前怎么回 | 优先做网关平台服务 API 的读 + 回复；坐席短期用 Postman / 最小页。若坐席必须在 Relay 回，先做 4c 再同步评论（**已拍板**，澄清 2.9） |
| 评论同步 | 哪些评论回写租户 | **默认不同步。** 租户时间线在网关。仅当坐席在 Relay 回且标记 `internal=false` 才写入网关 messages（**已拍板**，澄清 2.4） |
| `awaiting` 映射 | Relay 仍没有该状态 | 同步落到 `working`；或只改网关、Relay 保持 `working` |
| 附件 | 要不要进 Relay | **要，作为坐席副本**（S-26）。租户面真源仍在网关 AttachmentStore |
| 安全 / 留存 | 下载安全、审计、清理谁负责 | 租户面都在**网关**；Relay 只管内部副本与短链（**已拍板**，澄清 2.10） |

平台 Q1–Q9、Q11–Q12（可见性、string 枚举、GIF/WebP、纯文本、EXIF、通知分类、SLA、OSS vs 本地盘、类型清单、配额数值）都是网关自己的题，Relay 无意见，按平台建议默认即可。

---

## 5. 一句话

平台设计要的是**网关里的客服会话系统**；Relay 给的是**内部调查看板 + 同步过来的坐席副本**（单、分类标记、图片/文件）。
租户看见的列表 / 详情 / 谁能看见，一律来自网关自己的表；Relay 不是租户读模型（澄清 2.1）。
Relay 状态机已改为 `new → assign → working → resolved → reopen → closed`（澄清 2.2）；租户 close/reopen 仍在网关。
`category` 走平台 6 类且不混进 `type`；优先级/指派等内部字段不暴露给租户（澄清 2.3）。
租户消息流只在网关表；Relay 评论不当时间线，默认不同步回网关（澄清 2.4）。坐席优先在网关回。
附件、租户 API、配额校验、通知、坐席服务面、安全留存均已拍板（澄清 2.5–2.10）：**租户面都在网关，Relay 只保留坐席副本与调查能力**。
租户面剩余实现缺口主要在网关：已读、平台 `awaiting`、平台服务面落地、附件安全与清理、部分会话元字段。
真源在网关；Relay 缺单则建、标记落库、最终挂单附件再进对象存储副本（S-26）。坐席若继续在 Relay 回单，才需要 4c 评论 `internal`。
