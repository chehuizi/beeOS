# CLAUDE.md

Project guidance for Claude Code (claude.ai/code) when working in this repo.

## Project Overview

**beeOS** — 私有化部署的 AI 数字员工平台。私自照搬 [domain-box](../domain-box) 的部署模型，但内部架构不是单进程 Next.js，而是 **4 个 native systemd unit**：

```
beeos
  ├─ postgres (pgvector) + redis       ── 数据层（系统包）
  ├─ beeos-queen (FastAPI)             ── 调度层 + ReAct 执行 + Boxes
  └─ portal (静态 HTML + Alpine.js)    ── 交互层（nginx 服务）
```

**部署在阿里云 ECS**（与 domain-box 同台 `101.37.146.194`），用 `scripts/deploy-to-ecs.sh` 一键发版。

## Local dev

```bash
make install         # uv sync（无 npm）
make up-services     # 启动本机 PG + Redis（systemctl）
make dev-queen       # 裸跑 Queen
```

**前置**：Linux（systemctl）；macOS 开发者用 `brew services start postgresql@15 redis` 自行起 PG/Redis。

URL（本机）：

- Portal: http://localhost/ (nginx 80 服务静态 HTML)
- Queen:  http://localhost:8080/health
- Postgres: `localhost:5432` (beeos/beeos-dev-password)
- Redis:  `localhost:6379`

## Deploy

```bash
bash scripts/deploy-to-ecs.sh    # 完整部署
```

**4 个 native systemd unit** 在 ECS 上各管一摊（nginx / postgresql / redis / beeos-queen），零 Docker。详见 [`docs/DEPLOY.md`](docs/DEPLOY.md) 生产 runbook。

## Architecture decisions（不要轻易改）

详见 [`docs/architecture/`](docs/architecture/)：

- [技术架构 §5](docs/architecture/tech-architecture.md#5-关键技术选型与理由) — 选型表
- [技术架构 §3](docs/architecture/tech-architecture.md#3-部署架构) — 部署架构
- [全景图 §6](docs/architecture/overview.md#6-从全景图到代码) — 模块优先级

**铁律**：

1. 私有化优先（数据不出门）
2. 1 人天可部署（4 native systemd unit + 源码 tarball）
3. 模型中立 AB（DeepSeek + 通义）

## Key files

```
apps/
  queen/                           # 调度服务 FastAPI
    src/queen/api/app.py           # /health + 后续任务 API
    src/queen/core/state_machine.py # 5 状态机
  bee/                             # ReAct 引擎（M1 随 Queen 跑）
    src/bee/runtime.py             # MVP 占位
  boxes/month-close/
    box.yaml                       # 7 模块清单
    src/month_close/workflow.py    # 月结工作流
  portal/                          # 纯静态 HTML（nginx 直服务，无 Node.js）
    index.html / jobs.html / jobs/new.html / audit.html

packages/beeos-core/               # 跨服务共享
  config.py                        # pydantic-settings (BEEOOS_ 前缀)
  db.py                            # SQLAlchemy 2.0 async
  guardian.py                      # AES-256-GCM + JWT + 注入检测
  models.py                        # 6 张 ORM 表

deploy/
  systemd/beeos-queen.service     # Queen 唯一 systemd unit
  nginx/beeos.conf                 # 80 → 8080 + 静态 Portal
  scripts/check-server.sh          # 部署前自检

pyproject.toml                     # uv workspace 4 包
```

## Conventions

- **不要改 `BEEOOS_` 环境变量前缀** — 部署脚本依赖它
- **不要把 `.env` 加入 git** — 已写进 `.gitignore`
- **命名一致性**：Bee / BeeBox / Queen / Hive / Guardian / Granary / Bridge，见 [术语表](docs/architecture/glossary.md)
- **DB schema 变更**：M1 阶段直接改 `models.py` + 手动 `Base.metadata.create_all()`，V1 接入 Alembic
- **新增 Box** 必须包含 `box.yaml` 清单 + `src/<box>/workflow.py` 骨架
- **新增 API** 必须先在 [技术架构 §4](docs/architecture/tech-architecture.md#4-核心模块设计) 里写接口契约
- **无 Docker / 无 Node.js**：本地开发 = `systemctl start postgresql redis` + `uv run queen`；生产部署 = 源码 tarball + `uv pip install` + `systemctl restart beeos-queen`

## 部署相关

- ECS：`101.37.146.194`（与 domain-box 同台）
- 部署目录：`/opt/beeos`
- 4 个 systemd unit：`nginx.service` / `postgresql.service` / `redis.service` / `beeos-queen.service`
- 反向代理：nginx 已存在，新加 `conf.d/beeos.conf`（80 端口反代 + 静态 Portal）
- 域名：**demo 阶段 IP-only**（V1+ 接入域名 + HTTPS）

## 共享记忆

复用 domain-box 的几条记忆：

- `project_prod_better_sqlite3_node20` — 我们用 PostgreSQL，不用管这条
- `feedback_zodtype_variance_gotcha` — Portal 端用 zod，Python 端用 pydantic，保持严格
- `ecs_deploy_tarball_pattern` — tarball 部署源模式

beeOS 独有：

- `beeos_env_prefix_must_be_BEEOOS_` — 部署脚本靠前前缀识别
- `beeos_native_systemd_no_docker` — 全部用系统包 + systemd，零 Docker、零 docker-compose
- `beeos_secrets_live_on_host_only` — `.env` 永远不进 tarball
- `beeos_portal_is_static_html` — Portal 是 nginx 服务的静态 HTML + Alpine.js，零 Node.js
