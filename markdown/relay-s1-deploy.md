# Relay · S1 部署手册

从一台空机器走到可上线：装什么、配什么、按什么顺序启动、上线后怎么运。
开发环境怎么跑见 [relay-s1-dev.md](relay-s1-dev.md)；设计依据见
[relay-s1-design.md](relay-s1-design.md)；业主行动对应 **O-1…O-5**
（[relay-s1-owner-actions.md](relay-s1-owner-actions.md)）。双轨试用怎么带团队见
[relay-s1-rollout.md](relay-s1-rollout.md)。

S1 现在交付的是：**HTTP 工作台**（`/web/*`）+ **公开工单 API**（`/api/v1`）+
**Vue 前端**（`web/`）+ **MinIO 附件适配器**。`/healthz` 只报活。附件默认仍是
本地文件系统；生产应切到 MinIO（[O-5](#o-5-minio)），忘了切会在启动日志里看到
WARNING，而不是静默失败。

---

## 0. 上线前清单

按顺序打勾。1–4 卡住则系统不可用；5 卡住则会话不安全或写操作全被挡；
6–10 不立刻死人，但第一次出事时没有第二次机会——或消费方会认定「Relay 不发 webhook」。

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

应用启动时会把「还在用默认签名密钥」「webhook 主密钥还是默认值」「SMTP 没接」
「Cookie 没开 Secure」「附件还在本地盘」「MinIO 公网端点没配」打成 `WARNING`。
配置评审看启动日志就能发现，不必靠记性。这些都是**系统能起来、但静默错**的配置——
所以它们是 warning 而不是启动失败。

---

## 1. 部署形态

S1 的推荐形态是**单机自建**：一台应用进程 + 一台 PostgreSQL（可以同机）+ 一台
MinIO（可以同机，数据盘要分开），前面一台 HTTPS 反向代理同时托管 Vue 静态文件。
不要上 PgBouncer（已决策）：租户上下文靠事务级 `SET LOCAL app.tenant_id`，会话级
连接池复用会串租户；将来若引入，只能用 transaction 模式。

```
浏览器 ──HTTPS──► nginx (relay.internal)
                      │
          ┌───────────┼───────────┐
          ▼           ▼           ▼
     静态文件     127.0.0.1:8000   预签名直连
     web/dist     uvicorn          MinIO 公网端点
                      │
          ┌───────────┼───────────┐
          ▼           ▼           ▼
     PostgreSQL     MinIO        SMTP
     15+ / pgroonga  私有 bucket
     三个角色
```

| 组件 | S1 要求 | 不要做 |
|---|---|---|
| 应用 | Python 3.12 + `uv`，`uvicorn … --factory`，**不要 `--reload`** | 不要用 `relay_owner` 跑 Web |
| 前端 | Node 20+，`make web-build`，nginx 托管 `web/dist` | 不要把 Vite 开发服暴露到生产 |
| 数据库 | PostgreSQL **≥ 15**（16 是 CI 用的），**三个角色不能合并** | 不要 PgBouncer；不要用超级用户跑应用 |
| 附件 | `RELAY_BLOB_CARRIER=minio`；默认 `filesystem` 是开发值 | 不要把 blob 目录放进 git 工作树 |
| 邮件 | 内网 SMTP，STARTTLS | 不要指望 `NullMailPort` 能完成注册 |
| 入口 | HTTPS 是前提，不是可选项 | 不要在生产关掉 `RELAY_SESSION_COOKIE_SECURE` |

单机、单进程即可覆盖一个团队的 S1 量级。多进程可以（每个进程自己的连接池）。
**切到 MinIO 之前不要多机**：文件系统载体的附件在本地盘上，没有共享存储就会裂。

---

## 2. 前置

| 项 | 最低 | 说明 |
|---|---|---|
| OS | Linux（下文以 Debian/Ubuntu 为例） | 需要 `sudo` 给 postgres 超户跑引导 SQL |
| Python | **3.12** | `requires-python = ">=3.12"` |
| `uv` | 当前稳定版 | [安装](https://docs.astral.sh/uv/getting-started/installation/)：`curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Node | **20+** | 只为构建前端；运行时不需要 Node |
| PostgreSQL | **≥ 15** | 11 个外键用了 `ON DELETE SET NULL (column)`（S-18），14 上迁移直接失败 |
| pgroonga | 与 PG 主版本匹配；CI 钉的是 `groonga/pgroonga:4.0.8` | 不是 trusted extension，必须超户装，所以**不在迁移链里** |
| MinIO | 标准 S3 语义；bucket 事先建好且私有 | 还要 `mc`（`scripts/backup.sh` 用它做增量镜像） |
| TLS 证书 | `relay.internal`（或实际域名） | 会话 Cookie 与 Bearer 都是明文里的凭据 |
| SMTP | 能发事务性邮件 | 邮箱验证 / 异地登录告警 / 邀请，三条都走它 |
| 磁盘 | 数据盘与对象存储盘分开 | 附件上限 25 MiB/个；版本表会随自动保存涨 |

NTP 两端都要开。附件签名链接只有 5 分钟有效期；机器差几分钟 = 「链接一发出就过期」，
错误信息只会说「链接无效或已过期」，看不出是时钟。

---

## 3. 逐步部署

以下默认：代码在 `/srv/relay`，配置在 `/etc/relay/env`，附件在 MinIO，
进程以 `relay` 系统用户跑，库名 `relay_prod`，监听 `127.0.0.1:8000`，由 nginx
做 TLS 并托管前端。路径可以换，角色名不要换。

### 3.1 系统用户与目录

```bash
sudo useradd --system --home /srv/relay --shell /usr/sbin/nologin relay
sudo mkdir -p /srv/relay /var/log/relay /etc/relay /var/backups/relay
sudo chown -R relay:relay /srv/relay /var/log/relay /var/backups/relay
sudo chmod 750 /etc/relay
```

若暂用文件系统载体，再加 `/var/lib/relay/blobs` 并属主 `relay`。
`/etc/relay/env` 含数据库口令与签名密钥，权限 `640`、属主 `root:relay`。

### 3.2 安装 PostgreSQL 与 pgroonga

发行版软件源即可。pgroonga 通常来自 Groonga 的包，而不是发行版自带的
`postgresql-contrib`。装完确认：

```bash
sudo -u postgres psql -c "SELECT version();"          # 必须 ≥ 15
# 超户身份能 CREATE EXTENSION pgroonga 即可；下一步会真正装进业务库
```

Ubuntu 示例（以 PG 16 为例，按机器上的主版本替换）：

```bash
sudo apt install -y postgresql-16
# pgroonga：按 https://pgroonga.github.io/install/ 给该发行版的说明装
# 包名形如 postgresql-16-pgdg-pgroonga
```

### 3.3 三个角色、业务库、扩展

`scripts/bootstrap_db.sql` 会建 `relay_owner` / `relay_app` / `relay_system`，
**口令写死成角色名本身**——那是开发默认值。生产必须立刻改掉。

```bash
cd /srv/relay
sudo -u postgres psql -v ON_ERROR_STOP=1 -f scripts/bootstrap_db.sql

sudo -u postgres psql <<'SQL'
ALTER ROLE relay_owner  PASSWORD '<owner 的强口令>';
ALTER ROLE relay_app    PASSWORD '<app 的强口令>';
ALTER ROLE relay_system PASSWORD '<system 的强口令>';
SQL

sudo -u postgres createdb -O relay_owner relay_prod
sudo -u postgres psql -d relay_prod -c "GRANT USAGE ON SCHEMA public TO relay_app, relay_system;"
sudo -u postgres psql -d relay_prod -c "GRANT CREATE ON SCHEMA public TO relay_owner;"
sudo -u postgres psql -d relay_prod -f scripts/bootstrap_extensions.sql
```

三个角色**不能合并**。合并等于把 RLS 关掉，而且什么都不会报错：

| 角色 | 谁用 | 约束 |
|---|---|---|
| `relay_owner` | **只**给 Alembic 迁移 | 表 owner；也被 `FORCE RLS` 绑住，所以数据迁移要改行时得走 `relay_system` |
| `relay_app` | Web 进程 | 非表 owner，`NOBYPASSRLS`。每一条查询都被策略过滤 |
| `relay_system` | `SystemRepository` + 定时任务的跨租户读 | `BYPASSRLS`，每次调用落审计。**绝不**拿来服务 Web 请求 |

`BYPASSRLS` 非超级用户授不了，所以这一步必须超户跑——这也是它不进迁移链的原因。

### 3.4 代码与依赖

生产**不要**装 `[dev]`（pytest / ruff / httpx）。`boto3` 是硬依赖：载体按配置切换，
忘装会在第一次上传时变成 `ImportError`，而部署看起来是健康的。

```bash
sudo -u relay git clone <repo-url> /srv/relay   # 或把已审过的 tag 同步过来
cd /srv/relay
sudo -u relay uv venv --python 3.12
sudo -u relay uv pip install -e .
sudo -u relay bash -lc 'cd /srv/relay && make web-install && make web-build'
```

构建产物在 `web/dist/`。nginx 读它，uvicorn 不托管静态文件。

### 3.5 配置

把下面写成 `/etc/relay/env`（**不要**提交到版本库）。应用通过
`pydantic-settings` 读 `RELAY_*`；systemd 用 `EnvironmentFile=` 注入即可，
不必在工作目录放 `.env`。

```bash
# /etc/relay/env · 生产。chmod 640 · root:relay

RELAY_PG_HOST=127.0.0.1
RELAY_PG_PORT=5432
RELAY_PG_DATABASE=relay_prod

RELAY_OWNER_USER=relay_owner
RELAY_OWNER_PASSWORD=<owner 的强口令>
RELAY_APP_USER=relay_app
RELAY_APP_PASSWORD=<app 的强口令>
RELAY_SYSTEM_USER=relay_system
RELAY_SYSTEM_PASSWORD=<system 的强口令>

RELAY_PUBLIC_BASE_URL=https://relay.internal

# HTTPS 是前提。只有本地 http 开发才关掉。
RELAY_SESSION_COOKIE_SECURE=true
# 前端与 API 同域（nginx 托管 dist 并反代 /web /api）时留空即可
# （= 只认 PUBLIC_BASE_URL 的来源）。
RELAY_WEB_ORIGINS=
# nginx 与应用同机：信 127.0.0.1 的 X-Forwarded-For。
# 留空 = 谁的转发头都不信，限流按对端地址计——经反代时所有人会算成同一个 IP。
RELAY_TRUSTED_PROXIES=127.0.0.1

RELAY_SMTP_HOST=smtp.internal
RELAY_SMTP_PORT=587
RELAY_SMTP_USERNAME=relay
RELAY_SMTP_PASSWORD=<smtp 口令>
RELAY_SMTP_USE_STARTTLS=true
RELAY_MAIL_SENDER=relay@your-domain

RELAY_BLOB_CARRIER=minio
RELAY_BLOB_SIGNING_KEY=<见 §O-1，openssl rand -hex 32>
RELAY_MINIO_ENDPOINT=http://127.0.0.1:9000
RELAY_MINIO_PUBLIC_ENDPOINT=https://files.relay.internal
RELAY_MINIO_ACCESS_KEY=<Relay 专用，不要用 root>
RELAY_MINIO_SECRET_KEY=…
RELAY_MINIO_BUCKET=relay-attachments
RELAY_MINIO_REGION=us-east-1
RELAY_MINIO_PATH_STYLE=true

RELAY_WEBHOOK_SIGNING_KEY=<见 webhook 节，openssl rand -hex 32>
```

签名密钥、三个数据库口令、SMTP 口令与仓库里的开发默认值**不是同一类东西**：
开发默认值是故意写得一眼能看出来的，生产必须是新生成的。

### 3.6 迁移

迁移以 `relay_owner` 跑（`migrations/env.py` 读 `owner_dsn`）。**先扩展、后迁移**——
LOG-8 的 pgroonga 索引在迁移链里，扩展不在；缺扩展时迁移直接报错并给出装法，
不会静默跳过。

```bash
sudo -u relay bash -c 'set -a; source /etc/relay/env; set +a; cd /srv/relay && uv run alembic upgrade head'
```

成功后 `alembic current` 应指向 `head`。不要用 `relay_app` 跑这一步：它不是
owner，建不了表。

### 3.7 第一个租户

见 [O-4](#o-4-第一个租户)。这一步不完成，库是空的，也没有人能邀请别人。

### 3.8 进程

部署形式是工厂，不是模块级 `app`（设置按实例读）：

```
uv run uvicorn relay.api.app:create_app --factory --host 127.0.0.1 --port 8000
```

`--reload` 是开发专用。生产用 systemd：

```ini
# /etc/systemd/system/relay.service
[Unit]
Description=Relay S1
After=network.target postgresql.service
Requires=postgresql.service

[Service]
Type=simple
User=relay
Group=relay
WorkingDirectory=/srv/relay
EnvironmentFile=/etc/relay/env
ExecStart=/srv/relay/.venv/bin/uvicorn relay.api.app:create_app --factory --host 127.0.0.1 --port 8000
Restart=on-failure
RestartSec=5
# 附件上限 25 MiB；给请求体留余量
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now relay
sudo journalctl -u relay -e
```

启动日志里**不应再出现**那些配置 WARNING。若出现，停下来改 `/etc/relay/env`，
不要带着错误默认值对外。

`GET /healthz` 只报活，**不查数据库**。探针去查 PG，会把一次慢库变成滚动重启，
把降级做成中断。就绪（这台还能不能接请求）是另一个问题，S1 没有单独的就绪接口。

### 3.9 反向代理

HTTPS 终止在 nginx，应用只听回环，前端静态文件由 nginx 直接托管。需要同时做对的
几件事：

1. SPA：`try_files` 回 `index.html`，否则刷新 `/gateway/t/331` 会 404；
2. `/web/`、`/api/`、`/healthz` 反代到 uvicorn（`Host` / `X-Forwarded-Proto` /
   `X-Forwarded-For`）；
3. `RELAY_TRUSTED_PROXIES` 填反代地址（同机即 `127.0.0.1`）——注册与登录限流按 IP，
   **默认信任转发头会让一个调用方花掉所有人的次数**；
4. `client_max_body_size` ≥ 26m，否则 25 MiB 的附件会被反代先拒，应用看不到原因；
5. MinIO 的公网端点是**另一条 server**（或另一个 `server_name`），且必须是
   path-style。不要在它前面放一个只认 virtual-host 的反代。

```nginx
# /etc/nginx/sites-available/relay
server {
    listen 443 ssl http2;
    server_name relay.internal;

    ssl_certificate     /etc/ssl/relay.internal.crt;
    ssl_certificate_key /etc/ssl/relay.internal.key;

    client_max_body_size 32m;
    root /srv/relay/web/dist;

    location /web/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host              $host;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 60s;
    }
    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host              $host;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 60s;
    }
    location = /healthz {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
    }
    # 文件系统载体才需要；切到 MinIO 后签名链接不再回到这里，可删。
    location /blobs/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
    }

    location / {
        try_files $uri $uri/ /index.html;
    }
}

server {
    listen 80;
    server_name relay.internal;
    return 301 https://$host$request_uri;
}
```

`/docs` 与 `/openapi.json` 默认没有反代出去。内网联调需要时再加两条 location；
入口会暴露到更大网段时不要挂它们。

### 3.10 定时任务、第二个 Admin、备份

- cron：[定时任务](#定时任务)
- 再造一个 Admin：[O-4 末尾的警告](#o-4-第一个租户)
- 备份：[备份与恢复演练](#备份与恢复演练)

这三件不要放到「以后有空」。第二个 Admin 是唯一管理员锁死时的入口；
备份演练是日志从第一天起唯一的兜底；webhook cron 不挂等于功能不存在。

---

## 4. 配置参考

全部带 `RELAY_` 前缀，未列出的键会被忽略（`extra="ignore"`）。

### 数据库

| 变量 | 生产 | 说明 |
|---|---|---|
| `RELAY_PG_HOST` | PG 地址；Unix socket 时填路径（以 `/` 开头） | 代码对 socket 与 TCP 都支持 |
| `RELAY_PG_PORT` | 通常 `5432`（仓库开发默认是 `5433`） | |
| `RELAY_PG_DATABASE` | `relay_prod` | 不要和生产测试库共用 |
| `RELAY_OWNER_USER` / `RELAY_OWNER_PASSWORD` | `relay_owner` + 强口令 | 只给迁移 |
| `RELAY_APP_USER` / `RELAY_APP_PASSWORD` | `relay_app` + 强口令 | Web 进程 |
| `RELAY_SYSTEM_USER` / `RELAY_SYSTEM_PASSWORD` | `relay_system` + 强口令 | 跨租户只读 + 审计 |
| `RELAY_SQL_ECHO` | 不要开 | 会把 SQL 打进日志，含业务内容 |

### 会话与 CSRF

| 变量 | 生产值 | 说明 |
|---|---|---|
| `RELAY_SESSION_COOKIE_SECURE` | `true`（默认） | 会话 Cookie 是 HttpOnly + SameSite=Lax + Secure。**只有本地 http 开发才关掉** |
| `RELAY_WEB_ORIGINS` | 前端实际的来源，逗号分隔 | 状态变更请求必须带被认可的 `Origin`。留空 = 只认 `RELAY_PUBLIC_BASE_URL` 的来源 |
| `RELAY_TRUSTED_PROXIES` | 反向代理的地址 | **留空 = 谁的 `X-Forwarded-For` 都不信**，客户端地址取对端地址。注册与登录限流是按 IP 的，默认信任转发头会让一个调用方花掉所有人的次数 |
| `RELAY_PUBLIC_BASE_URL` | `https://relay.internal` | 验证邮件与邀请链接里的地址。永久链接带租户段（S-12）：`{base}/{tenant_slug}/t/331` |

**HTTPS 是前提**，不是可选项：会话 Cookie 与 `Bearer` token 都是明文里的凭据。
没有 `Origin` 头的请求会放行——那是 curl / 服务端调用 / 测试，第三方页面制造不出这种请求。

### 邮件

| 变量 | 生产 | 说明 |
|---|---|---|
| `RELAY_SMTP_HOST` | 必填 | 空 = `NullMailPort`，信只记录不发出 |
| `RELAY_SMTP_PORT` | `587` | |
| `RELAY_SMTP_USERNAME` / `RELAY_SMTP_PASSWORD` | 按中继要求 | 用户名为空则不 LOGIN |
| `RELAY_SMTP_USE_STARTTLS` | `true` | |
| `RELAY_MAIL_SENDER` | 真实可投递的 From | |

### 附件

| 变量 | 生产 | 说明 |
|---|---|---|
| `RELAY_BLOB_CARRIER` | `minio` | 默认 `filesystem`。忘了配 = 附件写到本地磁盘，启动时 WARNING |
| `RELAY_BLOB_ROOT` | 仅 filesystem | `/var/lib/relay/blobs`；切 MinIO 后不再写入 |
| `RELAY_BLOB_SIGNING_KEY` | 每环境一份 32 字节 hex | 见 [O-1](#o-1-relay_blob_signing_key)。MinIO 载体下只给 `/blobs/{key}` 用，而那条路由此时已下线 |
| `RELAY_BLOB_LINK_TTL_SECONDS` | `300` | 5 分钟；改长等于把「先鉴权再签」的窗口拉大 |
| `RELAY_BLOB_MAX_BYTES` | `26214400`（25 MiB） | 流式判定；超限不会先把文件收完再拒绝 |
| `RELAY_MINIO_*` | 见 [O-5](#o-5-minio) | 载体不是 `minio` 时被忽略 |

### Webhook

| 变量 | 生产 | 说明 |
|---|---|---|
| `RELAY_WEBHOOK_SIGNING_KEY` | 每环境一份 32 字节 hex | 见 [Webhook 签名密钥](#webhook-签名密钥)。**不要随手轮换** |

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

发信失败时看应用日志（SMTP 超时默认 10 秒）。From 地址必须是中继允许代发的，
否则信在 Relay 侧显示已交出去、收件箱里永远没有。

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
set -a; source /etc/relay/env; set +a
RELAY_BOOTSTRAP_PASSWORD=… uv run python scripts/bootstrap_tenant.py \
    --tenant-name "AI 网关团队" --tenant-slug gateway \
    --admin-email <admin@your-domain> \
    --domain-scope gateway
```

Admin 密码至少 8 位，且大写 / 小写 / 数字 / 符号四类里至少三类，不能包含邮箱本地部分。
脚本按 slug 幂等，重跑不会造出第二个 Admin。

可选参数：

| 参数 | 默认 | 说明 |
|---|---|---|
| `--allowed-domain` | Admin 邮箱的域名（可重复） | 自助注册只对这些域名开放 |
| `--no-auto-join` | 关闭（即允许自动加入） | 打开后，白名单域名的注册要等 Admin 批准 |
| `--default-role` | `member` | 自动加入时的角色 |
| `--timezone` | `Asia/Shanghai` | 租户时区 |
| `--domain-scope` | 空 | 见下第 2 条 |

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

自助注册的规则（AC-1），bootstrap 之后立刻生效：

- 邮箱域名在白名单且 `auto_join=true` → 以 `default_role` 加入（默认 Member）
- 白名单但 `auto_join=false` → 挂起，等 Admin 批准
- 域名不匹配 → 拒绝，文案是「联系管理员要邀请」——**没有**一个给陌生人蹲着的待审池

不在白名单的人要进来，走邀请。邀请**不**再查白名单：拒绝文案已经把邀请说成下一步，
同一条规则再拒一次就是死胡同。

## 定时任务

两个，都以**系统身份**运行——审计行记成 `system` 而不是某个 Admin（S-20）。cron
要带上同一份环境文件，否则脚本连的是开发默认库：

```cron
# 90 天版本清理 + API-3 幂等记录清理（同一条，见脚本注释）
17 4 * * *  cd /srv/relay && set -a && . /etc/relay/env && set +a && uv run python scripts/purge_log_versions.py >> /var/log/relay/purge.log 2>&1

# webhook 出站投递（API-4）。队列在 PG 里，用 FOR UPDATE SKIP LOCKED 抽取
*   * * * *  cd /srv/relay && set -a && . /etc/relay/env && set +a && uv run python scripts/deliver_webhooks.py >> /var/log/relay/webhooks.log 2>&1
```

⚠️ **投递那一条不配 = 事件排进队列但永不发出。** 对消费方来说这和「Relay 不支持
webhook」没有区别，而 Relay 这边一切正常、没有任何报错。多台主机同时跑是安全的
（`SKIP LOCKED` 就是为此），但一台够用。

每分钟一次意味着重试最多迟到一分钟——退避阶梯是 1m/5m/30m/2h/6h，没有任何契约
承诺比这更准。

清理那一条：

- `--dry-run` 用**同一条选择语句**只计数不删除，所以演练不会「报 0 而真跑删掉几千行」。
- 只在**一台**主机上挂：并发跑是安全的（删除幂等），但白白多一份负载。
- **最晚**：团队开始认真写日志之后两个月内。自动保存每次都写版本，版本表长得快；
  拖到很久之后第一次跑，会一次删掉很多行。
- 不要从某个 HTTP 请求里触发：自动保存让版本涨得很快，挂在页面加载里终究会变成一次
  超时。`system_principal` 也拒绝任何非 `SYSTEM` 的来源，HTTP 层即使写错了也到不了。

上线当天先跑一次 dry-run，确认它能连上库、能列出租户：

```bash
uv run python scripts/purge_log_versions.py --dry-run
uv run python scripts/deliver_webhooks.py     # 队列空时也应正常退出
```

## 备份与恢复演练

**自建的对价（R-1，WANGLI 认领）。** 备份范围是**两处**：PostgreSQL（日志正文、工单）
与 MinIO（附件、图片）。仓库里已经有入口，不要另写一套口径更窄的 cron：

```bash
# 每日全量 PG + MinIO 增量镜像。RELAY_BACKUP_MINIO_MIRROR 必须指向另一台主机。
0 2 * * *  cd /srv/relay && set -a && . /etc/relay/env && set +a && \
  RELAY_BACKUP_DIR=/var/backups/relay \
  RELAY_BACKUP_MINIO_MIRROR=backup-host/relay-attachments-backup \
  scripts/backup.sh >> /var/log/relay/backup.log 2>&1
```

| 对象 | 备份 | 保留 |
|---|---|---|
| PostgreSQL | 每日全量（`scripts/backup.sh` 的 `pg_dump -Fc`）+ WAL 归档（可 PITR） | 全量 30 天，WAL 7 天 |
| MinIO | 每日增量同步到另一位置（不同磁盘/主机，`mc mirror`） | 30 天 |
| 恢复演练 | `scripts/restore_drill.sh <备份目录>` **PG + MinIO 一起**，再打开一篇**带图片**的日志 | 团队真实写日志之前一次，此后每季度一次 |

脚本会把角色（`pg_dumpall --globals-only`）一起带走：没有角色，RLS 策略点名的名字
不存在，应用连不上。`--pg-only` 不是完整备份——恢复它就是「正文完好、图片全裂」。

WAL 归档在 `postgresql.conf` 里开 `archive_mode` / `archive_command`。备份脚本
**故意不配这一项**：把它塞进 cron 等于归档悄悄依赖这条 cron 有没有跑。

⚠️ **为什么演练一定要带图片的日志**：只恢复 PG 会得到一批**正文完好、图片全裂**的日志。
这种「半恢复」不在演练里暴露，就会在真出事时才发现——那时没有第二次机会。工单还有
Jira 兜底（S-9 未停用），**日志从第一天起就没有任何兜底**。`restore_drill.sh` 的第 5
步就是「每个附件行在恢复后的 bucket 里都有对象」，最后打印一篇带图日志的 URL 给人打开。

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

1. ⚠️ **`RELAY_MINIO_PUBLIC_ENDPOINT` 必须是浏览器真的能访问到的地址。** 预签名链接是
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
> 回一个 500。nginx 里的 `/blobs/` location 也可以一起删。

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

## 5. 一次冒烟验证

上线后按顺序走一遍，每一步都对应上面的一项：

```bash
curl -sS https://relay.internal/healthz                    # 应用起来了
# 启动日志里没有那些配置 WARNING
# 打开前端 → 注册一个测试账号 → 收到验证邮件（O-2）→ 点开 → 登录
# 建一篇日志，上传一张图片 → 图片能显示（O-1 + O-5）
# 建一张工单，指派给自己 → 站内信里有未读（NT-1）
# 搜索刚写的日志里的一个词 → 搜到（O-3）
# 从产品里再邀请 / 提升一个 Admin（O-4）
# 在账号设置里建一个服务 token → 用它 curl 一次 /api/v1/tickets（API-1/2）
uv run python scripts/purge_log_versions.py --dry-run       # 定时任务能跑（S-20）
uv run python scripts/deliver_webhooks.py                   # 投递能跑（API-4，队列空时也应正常退出）
uv run python scripts/check_blob_store.py                   # 附件往返（O-5）
```

⚠️ 这几步之外还有一条**不能靠冒烟验证的**：恢复演练。它必须真的做一次，且必须同时
恢复 PG 与 MinIO。

---

## 6. 发布与回滚

S1 没有独立的制品仓库，发布 = 检出一个已审 tag + 迁移 + 构建前端 + 重启。

```bash
sudo systemctl stop relay
cd /srv/relay
sudo -u relay git fetch --tags
sudo -u relay git checkout <tag>
sudo -u relay uv pip install -e .
sudo -u relay bash -c 'set -a; source /etc/relay/env; set +a; uv run alembic upgrade head'
sudo -u relay bash -lc 'cd /srv/relay && make web-build'
sudo systemctl start relay
curl -sS https://relay.internal/healthz
```

- **先迁移、再起新进程。** 迁移以 `relay_owner` 跑，与 Web 用的 `relay_app` 不是同一个
  角色，互不影响；但新代码若依赖新列，旧进程会在迁移完成后、重启前的窗口里出错——
  所以停机窗口里做完再起。
- Alembic 迁移默认视为**向前兼容、不可随意 downgrade**。回滚代码可以 `git checkout`
  上一个 tag；回滚 schema 要单独评估，不要习惯性 `alembic downgrade`。
- 配置变更：改 `/etc/relay/env` 后 `systemctl restart relay`。附件签名密钥轮换见 O-1
  （5 分钟链接失效）；webhook 主密钥**不要**当常规轮换，见上一节。
- OpenAPI 快照（`openapi.json`）是 `/api/v1` 的门禁：合同变了必须在同一 PR 里更新
  快照，删字段 / 改枚举语义走 v2，不进 v1。

---

## 7. 上线后运维

### 7.1 账号复核（R-2，WANGLI）

没有 SSO，**离职 / 转岗不会自动停用账号**；自助注册让这件事更重——账号不是 Admin
一个个发出去的，Admin 未必知道有谁在。

每月一次：核对租户成员名单，停用不该留下的账号。离职 checklist 加一项
「在 Relay 停用账号」。停用走 `POST /web/admin/users/{id}/deactivation`，会**同时
终止该用户所有会话**——不是只改一个状态位，被停用的人下一个请求就是 401。

租户内最后一个 Admin 不能被停用也不能被降级。

Admin 建议开 TOTP。产品提供 `admin_mfa_gap()`（未开第二因素的 Admin 名单），
不是硬门禁——硬门禁会把当时站在那里的那个 Admin 锁在门外，而 bootstrap 又不许
直接造第二个。

### 7.2 日志与审计

| 看什么 | 在哪 |
|---|---|
| 进程、启动 WARNING、SMTP 失败 | `journalctl -u relay` |
| 版本清理 | `/var/log/relay/purge.log` |
| webhook 投递 | `/var/log/relay/webhooks.log` |
| 备份 | `/var/log/relay/backup.log` |
| 业务审计（含 Admin 读别人的 L0） | 库里的审计表；Admin 读私密日志会写 `log.read_by_admin` |

不要把 `RELAY_SQL_ECHO` 开在生产：SQL 日志带业务内容。

### 7.3 容量

自动保存每几秒写一个版本（连续相同的会跳过）。90 天清理是唯一的收缩阀，所以及时
挂上 cron。附件单文件 25 MiB 上限是有意的：恢复演练必须能在演练窗口内跑完。

---

## 8. 故障排查

| 现象 | 先查 |
|---|---|
| 登录返回 200，浏览器里仍是未登录 | `RELAY_SESSION_COOKIE_SECURE=true` 但站点是 http。浏览器会**静默丢掉** Secure Cookie |
| 所有 POST/PATCH/DELETE 被拒 | `Origin` 不在 `RELAY_WEB_ORIGINS`（也不是 `PUBLIC_BASE_URL` 的来源） |
| 注册成功，验证信永远不来 | `RELAY_SMTP_HOST` 为空（启动 WARNING）；或 From 未被中继允许；或收件人把信当垃圾 |
| 迁移报 pgroonga / 语法错误 | 扩展没装，或 PG < 15。不要对着括号旁的语法错误猜 |
| 搜索什么都没有，别的都正常 | 同一原因：pgroonga 索引没建起来 |
| 图片全裂，应用日志是空的 | 文件系统：目录权限 / `BLOB_ROOT`。MinIO：`RELAY_MINIO_PUBLIC_ENDPOINT` 浏览器到不了（请求根本不回应用） |
| 刚生成的附件链接就过期 | NTP；或签名密钥刚轮换（预期内，最多 5 分钟） |
| 刷新前端路由 404 | nginx 没 `try_files … /index.html` |
| 注册 / 登录莫名限流 | `TRUSTED_PROXIES` 留空，所有人经反代算同一个 IP；或填得太宽，伪造 `X-Forwarded-For` 在刷别人的次数 |
| 「没有人能进系统」 | 没跑 bootstrap；或 Admin 密码忘了且没有第二个 Admin——这就是为什么上线后第一件事是再造一个 |
| 跨租户数据「看起来隔离了」但心里不踏实 | 不要把三个角色合成一个来「图省事」。合成之后 RLS 形同关闭，测试也会全部假绿 |
| 消费方说收不到 webhook | 没挂 `deliver_webhooks.py` 那条 cron。队列在涨、应用无报错 |
| 所有 webhook 同时验签失败 | 动了 `RELAY_WEBHOOK_SIGNING_KEY`。去逐个轮换端点并重新分发，不要再改回去撞指纹 |

`/healthz` 返回 `{"status":"ok","version":"0.1.0"}` 只说明进程活着。它不能证明
数据库、SMTP 或附件可用——那是上面冒烟清单的工作。

---

## 9. 这份手册不覆盖的

| 项 | 状态 |
|---|---|
| Docker / Compose / K8s | S1 不提供；单机 systemd 是推荐形态 |
| PgBouncer | S1 明确不做 |
| SSO、企微、GitHub 同步、RAG | 不在 S1 |
| 邮件通知（站内信之外） | NT-3，约 0.5 pd；双轨试用如果「看不见通知」再开，见 [relay-s1-rollout.md](relay-s1-rollout.md) |

本地开发不要照着这份手册把 Cookie Secure 打开、也不要在开发库上跑生产口令。
开发走 [relay-s1-dev.md](relay-s1-dev.md) 的 `make install && make db-bootstrap && make serve`，
前端走 `make web-dev`（Vite 把 `/web` `/api` 代理到 :8000）。
