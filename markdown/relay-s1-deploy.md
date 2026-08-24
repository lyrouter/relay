# Relay · S1 部署须知

这份文件只写**部署时必须做、代码里做不了**的事：三个密钥/凭据、一个超级用户步骤、
一次租户初始化，以及一份「上线前逐项打勾」的清单。它对应业主行动清单里的
**O-1…O-5**（[relay-s1-owner-actions.md](relay-s1-owner-actions.md)）。

开发环境怎么跑起来见 [relay-s1-dev.md](relay-s1-dev.md)；设计依据见
[relay-s1-design.md](relay-s1-design.md)。

---

## 0. 上线前清单

| # | 事项 | 谁 | 不做的后果 |
|---|---|---|---|
| 1 | [`RELAY_BLOB_SIGNING_KEY`](#o-1-relay_blob_signing_key) 换成真值 | 运维 | 附件链接用众所知的默认密钥签名，等于任何人都能自己签一个 |
| 2 | [SMTP 接上](#o-2-smtp) | 运维 | **没有人能完成注册**（邮箱验证发不出去） |
| 3 | [pgroonga + PostgreSQL ≥ 15](#o-3-pgroonga--postgresql-15) | 运维（需 superuser） | 迁移直接报错；搜索不可用 |
| 4 | [第一个租户 bootstrap](#o-4-第一个租户) | 运维 | 没有任何账号，也没有 Admin 可以邀请别人 |
| 5 | [`RELAY_SESSION_COOKIE_SECURE=true` + `RELAY_WEB_ORIGINS`](#会话与-csrf) | 运维 | 会话 Cookie 走明文；或者前端所有写操作被 CSRF 检查挡住 |
| 6 | [90 天清理的 cron](#定时任务) | 运维 | 版本表持续增长；第一次跑时一次删掉很多行 |
| 7 | [备份 + 一次真实恢复演练（PG **与** MinIO）](#备份与恢复演练) | WANGLI（R-1） | 日志从第一天起就没有兜底；只恢复 PG = 正文都在、图片全裂 |
| 8 | [MinIO 连接配置 + 一次往返冒烟](#o-5-minio) | 运维 | **附件全裂，而应用日志里什么都没有**——浏览器直连对象存储，请求不回到应用 |
| 9 | [`RELAY_WEBHOOK_SIGNING_KEY` 换成真值](#webhook-签名密钥) | 运维 | 出站 webhook 用仓库里公开的默认密钥签名，任何人都能伪造一条投递 |
| 10 | [webhook 投递的 cron](#定时任务) | 运维 | 事件排进队列但永不发出——消费方看到的是「Relay 不发 webhook」 |

---

## O-1 `RELAY_BLOB_SIGNING_KEY`

附件下载是**先鉴权、再签发 5 分钟短时链接**（S-11）。签名用的密钥就是这一项。
默认值 `dev-only-unsafe-signing-key` 是故意写成一眼可见的样子——**没设置时要在配置
评审里被看见**，而不是藏在一个能用的链接后面。

**怎么生成**（32 字节十六进制，每个环境一份）：

```bash
openssl rand -hex 32
# 没有 openssl 时：
python3 -c "import secrets; print(secrets.token_hex(32))"
```

三条使用纪律：

1. **每个环境一份，不复用。** staging 的密钥如果在生产也能用，那么每一条 staging
   链接在生产同样有效。
2. **当密码对待**：不进版本库，不进 CI 日志，放在与数据库口令同一处（环境变量 /
   secret 管理）。
3. **泄露就轮换，代价很小**：轮换只会让**已经发出去的链接**失效，即 5 分钟的破损，
   不丢任何数据。所以不要因为怕影响而拖着不换。

同时确认 `RELAY_BLOB_ROOT`（或 MinIO bucket）在备份范围内 —— 见
[备份与恢复演练](#备份与恢复演练)。

> MinIO 载体下这把密钥只用于**文件系统载体的** `/blobs/{key}`：预签名由 MinIO 自己签
> （S3 签名），不再走这里。两者都要求同一件事——**先鉴权，再签一条短命链接**。

## O-2 SMTP

`RELAY_SMTP_HOST` 为空 = `NullMailPort`，**信只记录不发出**。启动时会打一条
`WARNING` 说明这件事，但没人看日志的时候它就是静默的。

影响三条路，**第一条决定系统能不能用**：

| 路径 | 任务 | 没有它会怎样 |
|---|---|---|
| 邮箱验证 | AC-1 | **没有人能登录**——注册后必须点验证链接 |
| 异地登录告警 | AC-2 | 新网络登录不通知本人 |
| 邀请链接 | AC-1 次要路径 | Admin 邀请的人收不到链接 |

⚠️ 这三条都是**事务性邮件**，和 F-1「S1 通知只做站内信」不冲突：F-1 管的是通知，
不是这些。

```bash
RELAY_SMTP_HOST=smtp.internal
RELAY_SMTP_PORT=587
RELAY_SMTP_USERNAME=relay
RELAY_SMTP_PASSWORD=…
RELAY_SMTP_USE_STARTTLS=true
RELAY_MAIL_SENDER=relay@your-domain
```

**最晚**：第一个人注册之前。上线后自查一次：注册一个测试账号，确认真的收到验证信。

## O-3 pgroonga + PostgreSQL ≥ 15

**pgroonga 不是 trusted extension，要 superuser 装**，所以它不在迁移链里——迁移以
`relay_owner` 身份跑，而那个角色故意不是超级用户。

```bash
sudo -u postgres psql -d relay_prod -f scripts/bootstrap_extensions.sql
```

LOG-8 的全文检索与 `migrations/…_log_8_pgroonga_full_text_indexes.py` 都依赖它；
**缺失时迁移直接报错并给出装法**，不会静默跳过。

**PostgreSQL ≥ 15**：11 个外键用了 `ON DELETE SET NULL (column)`（S-18），14 上迁移
直接失败。`tests/test_ci_gates.py` 里有一条测试会在版本不够时明确报出原因，而不是让人
对着一个括号旁边的语法错误发呆。

角色也在这一步建（`scripts/bootstrap_db.sql`，同样需要 superuser——`BYPASSRLS`
非超级用户授不了）：`relay_owner`（迁移）· `relay_app`（运行时，非表 owner 且
`NOBYPASSRLS`）· `relay_system`（`SystemRepository` 专用，每次调用落审计）。
**三个角色不能合并**：合并等于把 RLS 关掉，而且什么都不会报错。

## O-4 第一个租户

AC-9 的决定是**带凭据的一次性初始化**，不是「第一个注册的人变 Admin」——在内网里后者
是真实的接管风险：平台在还没人被告知它存在的时候就已经可达，谁先找到谁就拥有它。

```bash
RELAY_BOOTSTRAP_PASSWORD=… uv run python scripts/bootstrap_tenant.py \
    --tenant-name "AI 网关团队" --tenant-slug gateway \
    --admin-email <admin@your-domain> \
    --domain-scope gateway
```

三件必须写进手册的事：

1. **`--tenant-slug` 发布即冻结**（S-12）。它出现在每一个永久链接里
   （`https://relay.internal/{tenant_slug}/t/331`），事后改等于让所有存量链接失效。
2. **`--domain-scope gateway` 只给网关团队这一个租户。** 它开启 TKT-2 的
   `gateway_version` / `routing_policy` 两个字段。默认不开，是为了让第二个租户不会悄悄
   继承——§7.3 的判据是「没有自有网关的团队能不能给这个字段填出值」，答案要一直是
   「不能」。
3. **密码走环境变量或交互输入，不要放命令行参数**（会进 shell history 和进程表）。
   脚本按 slug 幂等，重跑不会造出第二个 Admin。

⚠️ **bootstrap 拒绝往已存在的租户里加第二个 Admin**，而 AC-4 又拒绝停用/降级租户内最后
一个 Admin。这两条合起来的意思是：**第二个 Admin 只能从产品里来**（邀请或提升），
所以上线后第一件事就是再造一个 Admin，否则唯一的管理员账号出问题时没有别的入口。

## 会话与 CSRF

| 变量 | 生产值 | 说明 |
|---|---|---|
| `RELAY_SESSION_COOKIE_SECURE` | `true`（默认） | 会话 Cookie 是 HttpOnly + SameSite=Lax + Secure。**只有本地 http 开发才关掉** |
| `RELAY_WEB_ORIGINS` | 前端实际的来源，逗号分隔 | 状态变更请求必须带被认可的 `Origin`。留空 = 只认 `RELAY_PUBLIC_BASE_URL` 的来源 |
| `RELAY_TRUSTED_PROXIES` | 反向代理的地址 | **留空 = 谁的 `X-Forwarded-For` 都不信**，客户端地址取对端地址。注册与登录限流是按 IP 的，默认信任转发头会让一个调用方花掉所有人的次数 |
| `RELAY_PUBLIC_BASE_URL` | `https://relay.internal` | 验证邮件与邀请链接里的地址 |

**HTTPS 是前提**，不是可选项：会话 Cookie 与 `Bearer` token 都是明文里的凭据。

## 定时任务

两个，都以**系统身份**运行——审计行记成 `system` 而不是某个 Admin（S-20）：

```cron
# 90 天版本清理 + API-3 幂等记录清理（同一条，见脚本注释）
17 4 * * *  cd /srv/relay && uv run python scripts/purge_log_versions.py >> /var/log/relay/purge.log 2>&1

# webhook 出站投递（API-4）。队列在 PG 里，用 FOR UPDATE SKIP LOCKED 抽取
*   * * * *  cd /srv/relay && uv run python scripts/deliver_webhooks.py >> /var/log/relay/webhooks.log 2>&1
```

⚠️ **投递那一条不配 = 事件排进队列但永不发出。** 对消费方来说这和「Relay 不支持
webhook」没有区别，而 Relay 这边一切正常、没有任何报错。多台主机同时跑是安全的
（`SKIP LOCKED` 就是为此），但一台够用。

每分钟一次意味着重试最多迟到一分钟——退避阶梯是 1m/5m/30m/2h/6h，没有任何契约
承诺比这更准。

- `--dry-run` 用**同一条选择语句**只计数不删除，所以演练不会「报 0 而真跑删掉几千行」。
- 只在**一台**主机上挂：并发跑是安全的（删除幂等），但白白多一份负载。
- **最晚**：团队开始认真写日志之后两个月内。自动保存每次都写版本，版本表长得快；
  拖到很久之后第一次跑，会一次删掉很多行。

## 备份与恢复演练

**自建的对价（R-1，WANGLI 认领）。** 备份范围是**两处**：PostgreSQL（日志正文、工单）
与 MinIO（附件、图片）。

| 对象 | 备份 | 保留 |
|---|---|---|
| PostgreSQL | 每日全量 + WAL 归档（可 PITR） | 全量 30 天，WAL 7 天 |
| MinIO | 每日增量同步到另一位置（不同磁盘/主机） | 30 天 |
| 恢复演练 | **PG + MinIO 一起**恢复到临时环境，打开一篇**带图片**的日志，确认正文与附件都在 | 团队真实写日志之前一次，此后每季度一次 |

⚠️ **为什么演练一定要带图片的日志**：只恢复 PG 会得到一批**正文完好、图片全裂**的日志。
这种「半恢复」不在演练里暴露，就会在真出事时才发现——那时没有第二次机会。工单还有
Jira 兜底（S-9 未停用），**日志从第一天起就没有任何兜底**。

## O-5 MinIO

LOG-5 的应用层已完成（大小/类型限制、病毒扫描位、key 里带 `tenant_id`、先鉴权再签
5 分钟链接）。**适配器按 S-25 盲写**——不等真实实例，按标准 S3 语义写，实例上的偏差按
BUG 处理。默认载体仍是文件系统（`FilesystemBlobStore`），**key 布局与 MinIO 版本完全一致**，
所以换载体不搬任何对象、不改任何已存的 `blob_key`。

> **状态**：**代码已交付**——`MinioBlobStore` + 容器化契约测试（`tests/test_blob_contract.py`，
> 用真实 `minio/minio` 跑同一套 blob 契约）+ 冒烟脚本（`scripts/check_blob_store.py`）。
> 剩下的全部是这一节的配置与验证。
>
> ⚠️ **变量名与本文早先的占位名不同**：适配器落地时定成了 `RELAY_BLOB_CARRIER` +
> `RELAY_MINIO_*`（载体开关归 blob，连接参数归 MinIO），下表是**真实名字**。

**运维要准备的（原来的「四样东西」，现在是配置项而不是阻塞）**：

| 变量 | 说明 |
|---|---|
| `RELAY_BLOB_CARRIER` | `filesystem`（默认）/ `minio`。**默认不是 MinIO**，所以忘了配 = 附件写到本地磁盘，而不是静默失败——启动时会打一条 WARNING 说这件事 |
| `RELAY_MINIO_ENDPOINT` | 应用访问 MinIO 用的地址（通常是内网），如 `http://minio.internal:9000` |
| `RELAY_MINIO_PUBLIC_ENDPOINT` | **浏览器**访问对象存储用的地址。留空 = 与上面相同，并且启动时会警告 |
| `RELAY_MINIO_ACCESS_KEY` / `RELAY_MINIO_SECRET_KEY` | **给 Relay 专用的一对**，权限只到这一个 bucket。不要用 MinIO 的 root 凭据 |
| `RELAY_MINIO_BUCKET` | bucket 名（默认 `relay-attachments`）。**事先建好，且必须是私有的** |
| `RELAY_MINIO_REGION` | S3 客户端要一个值，MinIO 不校验；默认 `us-east-1` |
| `RELAY_MINIO_PATH_STYLE` | 默认 `true`，即 `endpoint/bucket/key`。只有指向真实 S3 时才需要改 |

**四条必须在部署时确认的事**，它们是「盲写」唯一压不掉的部分——代码写不出这四个答案，
只有真实实例能给：

1. ⚠️ **`RELAY_BLOB_PUBLIC_ENDPOINT` 必须是浏览器真的能访问到的地址。** 预签名链接是
   **对 host 签的**，签成内网地址，用户那边就是**图片全裂，而应用日志里什么都没有**——
   浏览器直连对象存储，那些请求根本不回到应用。这是这一项最常见、也最难自查的故障。
2. **path-style 寻址**：MinIO 基本只吃 `endpoint/bucket/key`，不吃把 bucket 当子域名的
   virtual-host 形式。适配器按 path-style 写，所以**不要在前面放一个只认 virtual-host 的
   反向代理**。
3. **两端都要 NTP。** 预签名有效期只有 5 分钟（S-11），机器差几分钟就等于「链接一发出
   就过期」——而错误信息只会说「链接无效或已过期」，看不出是时钟问题。
4. **bucket 必须私有。** 开了匿名读，S-11 那套「先鉴权、再签 5 分钟链接」就**整套白做**：
   任何拿到 key 的人都能直接取对象，而权限检查发生在应用里，对象存储不知道它存在。

**配完跑一次往返**：

```bash
uv run python scripts/check_blob_store.py       # put → 预签 → GET → delete
```

它把上面四条一次性验掉：签出来的 URL 是不是能访问的 host、寻址风格对不对、时钟差多少、
bucket 是不是私有。退出码非 0 就是有问题，所以可以直接放进发布脚本。
**在上线前跑，而且要从一台「浏览器视角」的机器上再跑一次**——公网端点是这四条里最可能
两台机器答案不同的一条。

> 换成 MinIO 之后，`GET /blobs/{key}` 这条路由**不再挂载**（不是"不再有人访问"）：
> 签名链接直接指向对象存储，而这条路由依赖的 `verify` / `open` 是文件系统载体独有的。
> 载体开关在装配处（`relay.api.wiring`），路由跟着一起消失——留着它只会在每张图片上
> 回一个 500。

---

## Webhook 签名密钥

`RELAY_WEBHOOK_SIGNING_KEY`。每个 webhook 端点的签名密钥是**从这个主密钥派生**的
（`HMAC(master, "<endpoint id>:<version>")`），所以数据库里**不存任何签名材料**——
一份库转储伪造不出我们的签名。生成方式和 O-1 一样：

```bash
openssl rand -hex 32
```

⚠️ **它和 O-1 的密钥有一处关键不同：换掉它会让所有消费方的验签同时失败。** 每个端点
的密钥都是从它派生的，主密钥一变，所有派生值都变，而消费方手上存的是旧值。所以：

- **一次设定，长期不动**；
- 要作废某一个端点的密钥，用**轮换该端点**（`POST /api/v1/webhooks/{id}/secret`），
  不要动主密钥；
- 真的换了主密钥，应用会在投递时打一条 ERROR 说「派生密钥与存储的指纹不一致」——
  那是提示你去逐个轮换端点并重新分发密钥，不是可以忽略的告警。

---

## 附：一次冒烟验证

上线后按顺序走一遍，每一步都对应上面的一项：

```bash
curl -sS https://relay.internal/healthz                    # 应用起来了
# 注册一个测试账号 → 收到验证邮件（O-2）→ 点开 → 登录
# 建一篇日志，上传一张图片 → 图片能显示（O-1 + O-5）
# 建一张工单，指派给自己 → 站内信里有未读（NT-1）
# 搜索刚写的日志里的一个词 → 搜到（O-3）
# 在账号设置里建一个服务 token → 用它 curl 一次 /api/v1/tickets（API-1/2）
uv run python scripts/purge_log_versions.py --dry-run       # 定时任务能跑（S-20）
uv run python scripts/deliver_webhooks.py                   # 投递能跑（API-4，队列空时也应正常退出）
uv run python scripts/check_blob_store.py                   # 附件往返（O-5）
```

⚠️ 这几步之外还有一条**不能靠冒烟验证的**：恢复演练。它必须真的做一次，且必须同时
恢复 PG 与 MinIO。
