# Relay · S1 开发状态

**截至 2026-08-25 · 对应提交 `254121f`**

> 这份文件回答两个问题：**现在做到哪了**、**还差什么**。
> 需要你拍板的事在 [relay-s1-owner-actions.md](relay-s1-owner-actions.md)；
> 任务级的实现说明在 [TODO-S1.md](../TODO-S1.md)；上线步骤在
> [relay-s1-deploy.md](relay-s1-deploy.md)；团队怎么用在
> [relay-s1-rollout.md](relay-s1-rollout.md)。

---

## 0. 一句话

**代码能做的，S1 范围内已经全部做完了。** 剩下没完成的分三类：**必须有真实环境或真人**
的（4 项）、**等你决策或排期**的（5 项）、**当初就明确推迟出 S1**的（不是欠债）。

判断"做完"的标准写在 [TODO-S1 的验收出口](../TODO-S1.md#exit-criteria)：**只有当有
一个机械的东西（CI 门禁、会失败的测试）在守着它，才打勾**。需要人做的事，无论代码写
了多少，都不打勾。

---

## 1. 总览

| 模块 | 状态 | 说明 |
|---|---|---|
| **MT** 多租户数据模型 | ✅ 6/6 | RLS + 组合外键 + schema lint 进 CI；MT-6 的两半都完成（跨租户读写在数据库层失败 · **token 拿别租户资源是 404 不是 403**） |
| **TA** 遥测适配器接缝 | ✅ 1/1 | 只有接口，S1 无消费方；`import-linter` 守着"只有适配器包能 import 网关客户端" |
| **AC** 账号与自助注册 | ✅ 8/9 | 差 AC-7（GitHub OAuth）——**明确推迟**，GH 开工前才需要 |
| **LOG** 日志与知识撰写 | ✅ 9/9 | 含 LOG-5 的 MinIO 适配器（S-25 盲写 + 容器契约测试 + 冒烟脚本） |
| **TKT** 工单与看板 | ✅ 9/9 | 六状态机（含 S-23 两条新边）· 列表 / 看板 / 我的 / 详情 |
| **WEB** Web UI 的 HTTP 层 | ✅ 4/4 | 53 条路径 |
| **API** 对外工单 API | ✅ 6/6 | API-1…6 全部完成，16 条路径，契约快照进 CI |
| **NT** 通知 | ✅ 2/3 | NT-1/NT-2 完成；NT-3（邮件）是**可选的逃生口**，不在 62 pd 里 |
| **前端** LOG-1/2/3/7 · TKT-5/6/7/9 | ✅ 8/8 | Vue 3 + TS + Vite + Pinia；类型从 API schema 生成 |
| **INT** 集成与上线 | ⏳ 4/5 | INT-1/5/6/8 完成；**INT-11 恢复演练要真实实例**，脚本已交付、演练没做 |

**数字**（用来对照，不是用来炫耀）：

| | |
|---|---|
| 后端 | 119 个 Python 文件 · 16,025 行 |
| 测试 | 33 个文件 · 10,319 行 · **694 个测试** |
| 前端 | 27 个文件 · 4,265 行手写代码（生成的类型不算） |
| HTTP 面 | `/web` 53 条路径 · `/api/v1` 16 条 · 共 88 个操作 |
| 门禁 | `make gates` 全绿（ruff · import-linter · 实体注册表快照 · **OpenAPI 快照** · 全量测试） |

---

## 2. 这一轮（`254121f`）新做完的

上一轮结束时还剩：API-1…6、LOG-5 的 MinIO 适配器、MT-6 的另一半、INT-5/6/8、全部前端。
**这些现在都完成了。**

| 项 | 落点 |
|---|---|
| **LOG-5 · MinIO 适配器**（S-25） | `infra/blob/minio.py` · `tests/test_blob_contract.py`（两个载体跑**同一套**契约，其中一个是真跑起来的 `minio/minio` 容器）· `scripts/check_blob_store.py`（往返冒烟，退出码可进发布脚本） |
| **API-1** token 鉴权 | `app/api_tokens.py` · `api/v1/dependencies.py` · `api/web/tokens.py`（界面里管理 token） |
| **API-2** `/api/v1` 资源端点 | `api/v1/tickets.py` · `api/v1/meta.py` · `api/v1/reserved.py`（`/logs`、`/search` 占名返回 501） |
| **API-3** 幂等与并发 | `app/idempotency.py` · `api/revisions.py`（`If-Match` 解析两个面共用一份） |
| **API-4** 出站 webhook | `app/webhooks.py` · `domain/destinations.py`（SSRF 规则）· `ports/webhook.py` + `infra/http/` · `scripts/deliver_webhooks.py` |
| **API-5** 契约纪律 | `scripts/gen_openapi.py` + 仓库根的 `openapi.json` · `app/api_rate_limit.py` · 错误形状进了文档（`Problem` 模型） |
| **API-6** 反馈链路 | `submitter` / `source` 落在 `/api/v1/tickets`，端到端测试覆盖"同一反馈提三次只有一张单" |
| **MT-6 另一半** | `tests/test_api_v1.py::test_a_token_cannot_reach_another_tenants_ticket` |
| **前端 8 项** | `web/`，见 [web/README.md](../web/README.md) |
| **INT-5 / 6 / 8** | `tests/test_end_to_end.py` · [relay-s1-rollout.md](relay-s1-rollout.md) · `app/metrics.py` + `/web/admin/dashboard` |
| 备份 / 恢复脚本 | `scripts/backup.sh` · `scripts/restore_drill.sh`（**演练本身仍要人跑**） |

**顺手修掉的两个既有缺陷**（不是新功能，但都会在上线后咬人）：

1. **超大附件返回 500。** `BlobTooLarge` 会漏到兜底处理器，上传一张过大的图片对用户显示
   为"服务出现意外错误"。现在是 **413**，限制本身没变。
2. **全量测试跑到后半段连片失败。** 症状像测试顺序问题，实际是 `TRUNCATE` 重建表存储、
   把 pgroonga 的索引对象弄失效。清理改成按依赖顺序 `DELETE`，并走 BYPASSRLS 角色
   （owner 被 FORCE RLS 拦住，其实一行都没删掉）。**这条以前每次全量跑都在踩**。

---

## 3. 还没完成的

### 3.1 必须有真实环境或真人 —— 4 项 🔴

**这四项代码替不了，也不该由代码替。**

| # | 事项 | 谁 | 现在的状态 | 不做的后果 |
|---|---|---|---|---|
| 1 | **MinIO 实例本身**（O-5） | 运维 | 适配器、契约测试、冒烟脚本都在；**实例还没有** | 附件功能上线即不可用 |
| 2 | **INT-11 备份 + 一次真实恢复演练**（PG **与** MinIO 一起） | WANGLI（R-1） | `scripts/backup.sh` / `scripts/restore_drill.sh` 已交付，演练脚本会把每行 `attachment` 与恢复出来的 bucket 逐个对账 | 日志与附件从第一天起没有兜底；只恢复 PG = **正文都在、图片全裂**，而这种半恢复不在演练里暴露就会在真事故里暴露 |
| 3 | **上线前一次人工越权抽检** | 你安排 | CI 门禁全绿（跨租户读写 · token 404 · webhook 不出租户），但**抽检是人的活** | 门禁只证明"我们想到的越权路径都堵了" |
| 4 | **网关 WebUI 把反馈表单接上** | 网关团队 | **我们这一侧完全就绪**，并用真实 token 跑通了端到端（含重复提交只出一张单） | S1 的功能出口少一条：「至少一个外部系统通过 API 接入」 |

> ⚠️ 第 1 与第 2 是连着的：**没有实例就做不了演练**。这是目前唯一一条真正的关键路径。

### 3.2 等你决策或排期 —— 5 项 🟡

原样保留在 [relay-s1-owner-actions.md §0](relay-s1-owner-actions.md#0-还需要你做的)，
这里只列状态：

| # | 事项 | 类型 | 现在的状态 |
|---|---|---|---|
| **P-1** | R-2 每月账号复核 + 离职 checklist | 流程 | 产品里能做了（`POST /web/admin/users/{id}/deactivation` 会同时终止该用户所有会话）；**流程本身要建** |
| **P-2** | AI 角色在 S1 无事可做 | 排期 | **已经过期**。前端已完成，这个去处也没有了——要么给 RAG 预研，要么并进别的事 |
| **P-3** | 双轨试用观察项：通知看不见就开邮件 | 流程 | 已写进 [rollout 指南 §1](relay-s1-rollout.md)；NT-3 约 0.5 pd 待命 |
| **P-4** | LOG-9 验收前人工抽检 10 篇 | 验收 | 计数能直接读（`/web/logs/knowledge-count` 与验收看板同一个常数）；**抽检要在验收会之前做** |
| **P-5** | 要不要砍 LOG-7 / TKT-6 | 决策 | **两个都已经做完了**，所以这条决策的性质变了——见下方 ⚠️ |

> ⚠️ **P-5 现在是一个不同的问题。** 当初问的是"要不要砍掉这 3.5 pd 的前端工作"，
> 现在它们已经写完并入库了，砍掉不再省开发时间，只省**维护与认知成本**。两块都是刻意
> 自包含的（删掉 `views/BoardView.vue` 与它的路由、或 `markdown/templates.ts` 与引用它的
> 按钮，就干净地没了），所以**这条可以推迟到试用之后按真实使用率决定**，不必在开工前
> 拍板。我的建议：**留着，看第 6 周有没有人用**。

### 3.3 明确推迟出 S1 —— 不是欠债 ⚪

这些当初就不在 S1 范围内，接缝已经留好（[TODO-S1 · Deferred](../TODO-S1.md#deferred-out-of-s1)）：

| 项 | S1 里留下的接缝 |
|---|---|
| **BOT**（企微机器人） | `identity_binding` 建表不写入 · `IMPort` 声明 · 通知模型本来就是多渠道 |
| **AC-6 / AC-7** 身份绑定 | 表与唯一约束已设计；**AC-7 必须在 GH 开工前落地** |
| **TA-2…TA-4** 适配器实现 | TA-1 的接口 + 架构门禁 |
| **GH** GitHub 同步 | `rev` · `actor_type`/`origin` · `ticket_external_ref` · webhook 投递指标——回环三防的锚点都在 |
| **RAG / SEED** | `knowledge_candidate` 标记与计数口径已定 |
| **INT-2/3/4/7/9/10** | 网关路由 · 同步试点 · 模型 A/B · Jira 下线 · 绑定率推动 · AI 预算告警（S1 无 LLM 调用，暂无可告警的花费） |
| **NT-3** 邮件通知 | 约 0.5 pd，**F-1 的指定逃生口**：发信通道、聚合窗口、多渠道投递状态机都已建好 |

### 3.4 已知的口子（技术债，摆出来而不是藏着）🔍

| # | 是什么 | 我的判断 |
|---|---|---|
| 1 | **`PATCH /web/logs/{id}` 不要求 `If-Match`**，与设计 §8.9 字面上的"`/web` 上也必需"不一致 | 这是上一轮 WEB-3 就有的选择，我**没有改**。日志的并发答案是 LOG-4 的编辑锁 + 每次保存一个版本（第二个人不会抹掉第一个人的内容，只是新增版本），和工单的 `rev` 是两套等价的回答。**要对齐字面是小改动，但会动已发布的 `/web` 契约与我刚写的前端**——请你定 |
| 2 | **前端没有单元测试** | 门禁是 `npm run build`（类型 + 编译），它拦的正是这一层真正会产生的 bug：形状与 API 对不上。行为由后端 694 个测试和端到端测试覆盖。**等到组件里有值得测的逻辑时再加** |
| 3 | **前端没有在真实浏览器里点过** | 我验证的是**类型检查通过 + 生产构建通过 + 它调用的每个端点都有后端测试**。第一次人肉点击一定会发现样式与交互上的问题——这是正常的，但请知道它还没发生 |
| 4 | **未认证请求不计入限流** | 限流按 token 计（S-14 的口径），所以无效 token 的洪水打不到配额上。S1 是内网工具，反向代理挡这一层更合适；真要做要另设计 |
| 5 | **`identity_binding` 建表不写入** | 设计已记（§12.3）：D-13 的口径 spike 随 BOT 推后，**BOT 开工时这张表很可能要改一次结构**，约 0.5 pd 返工。已知、可接受 |
| 6 | **截图不走 API** | 不是债，是 §8.8 的决定：加附件端点会把 MinIO 的签发与配额暴露给外部消费方。**必须写进对接文档**，否则第一天就有人问 |

---

## 4. 验收出口的状态

打勾的标准：**有机械的东西守着它**。

### 硬门槛

| | 项 | 守着它的是什么 |
|---|---|---|
| ✅ | schema lint 拦住没有 `tenant_id` 或没有 RLS 策略的新表 | `test_ci_gates.py::test_schema_lint_catches_a_table_with_no_policy`——这个测试会**故意造一张坏表**确认门禁真的会响 |
| ✅ | 幂等重放产生 **0** 张重复工单 | `test_api_v1.py::test_the_same_idempotency_key_three_times_makes_one_ticket` + 旁边的 `external_ref` 一半 |
| ✅ | 并发写产生 **0** 次静默覆盖 | `test_api_v1.py::test_a_stale_if_match_is_a_409_carrying_the_current_rev`；缺 `If-Match` 直接拒绝，两个面都是 |
| ✅ | OpenAPI 快照门禁 | `scripts/gen_openapi.py --check`，进了 `make gates` 与 CI |
| ⏳ | 跨租户读写 = 0 **+ 上线前一次渗透抽检** | 门禁全绿；**抽检没做**（§3.1 第 3 条） |
| ⏳ | 备份至少恢复过一次（PG 与 MinIO 一起） | 脚本已交付；**演练没做**（§3.1 第 2 条） |

### 功能出口

| | 项 |
|---|---|
| ✅ | 注册 → 验证 → 登录 → 日志 → 工单 → **站内通知** 端到端跑通（`test_end_to_end.py::test_the_s1_critical_flow`，走的是层与层之间的接缝而不是单层） |
| ✅ | 日志：双模式 · 90 天版本与回滚 · L0–L3 分享 · 全文检索 · 知识库标记 |
| ✅ | 工单：字段 · 六状态机 · 列表/看板/我的 · 详情 + 永久链接 |
| ✅ | API：上述标准，加网关反馈往返（`test_the_gateway_feedback_round_trip`） |
| ⏳ | **网关 WebUI 的反馈表单真的接上**——我们这侧完成，对方没接 |
| ⏳ | 团队开始**双轨试用**——指南写好了，用不用是团队的事 |
| ⏳ | **BOT 排期在 S1 出口评审上定下来**（R-3，第 7 周）——这个会必须真的开 |

---

## 5. 怎么自己验证

不用信这份文件，跑一遍：

```bash
make gates          # ruff · import-linter · 实体注册表 · OpenAPI 快照 · 694 个测试
make web-install    # 首次
make web-types      # 从运行中的 app 重新生成前端类型
make web-build      # 类型检查 + 生产构建
```

有 docker 时，MinIO 契约测试会真的起一个 `minio/minio` 跑；没有就跳过并说明原因。
**CI 里设了 `RELAY_REQUIRE_MINIO_CONTRACT=1`，跳过即失败**——盲写的适配器最怕契约测试
悄悄不跑了。

配好真实 MinIO 之后：

```bash
uv run python scripts/check_blob_store.py    # put → 预签 → GET → delete，四处盲区一次验掉
```

---

## 6. 如果只看三件事

1. **搞定 MinIO 实例，然后让 WANGLI 跑一次恢复演练**（§3.1 第 1、2 条）。这是唯一的
   关键路径，而且演练必须**同时**恢复 PG 与 MinIO。
2. **约网关团队接反馈表单**（§3.1 第 4 条）。我们这侧的契约冻结了、文档在 `/docs`、
   端到端测过；缺的只是对方的排期。
3. **决定 §3.4 第 1 条**（日志保存要不要也上 `If-Match`）。这是唯一一个我留给你的技术
   决策，其余都是流程与排期。
