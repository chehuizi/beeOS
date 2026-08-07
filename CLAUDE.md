# CLAUDE.md

Project guidance for Claude Code (claude.ai/code) when working in this repo.

## Project Overview

**beeOS** — 私有化部署的 AI 数字员工平台。私自照搬 [domain-box](../domain-box) 的部署模型，但内部架构不是单进程 Next.js，而是 **多服务 Docker Compose**：

```
beeos
  ├─ postgres (pgvector) + redis          ── 数据层
  ├─ queen (FastAPI)                       ── 调度层
  ├─ bee (ReAct) + boxes/month-close       ── 执行层
  └─ portal (Next.js)                      ── 交互层
```

**部署在阿里云 ECS**（与 domain-box 同台 `101.37.146.194`），用 `scripts/deploy-to-ecs.sh` 一键发版。

## Local dev

```bash
make install          # uv sync + npm install
make up               # docker compose up -d
make logs             # 全部服务日志
make dev-queen        # 裸跑 Queen（不走 Docker）
```

URL（本机）：

- Portal: http://localhost:3000
- Queen:  http://localhost:8080/health
- Postgres: `localhost:5432` (beeos/beeos-dev-password)
- Redis:  `localhost:6379`

## Deploy

```bash
bash scripts/deploy-to-ecs.sh    # 完整部署
```

**单 systemd unit** `beeos` 在 ECS 上管整个 compose 栈。详见 [`docs/DEPLOY.md`](docs/DEPLOY.md) 生产 runbook。

## Architecture decisions（不要轻易改）

详见 [`docs/architecture/`](docs/architecture/)：

- [技术架构 §5](docs/architecture/tech-architecture.md#5-关键技术选型与理由) — 选型表
- [技术架构 §3](docs/architecture/tech-architecture.md#3-部署架构) — 部署架构
- [全景图 §6](docs/architecture/overview.md#6-从全景图到代码) — 模块优先级

**铁律**：

1. 私有化优先（数据不出门）
2. 1 人天可部署（单机 Compose + 单 systemd）
3. 模型中立 AB（DeepSeek + 通义）

## Key files

```
apps/
  queen/                           # 调度服务 FastAPI
    src/queen/api/app.py           # /health + 后续任务 API
    src/queen/core/state_machine.py # 5 状态机
  bee/                             # ReAct 引擎
    src/bee/runtime.py             # MVP 占位
  boxes/month-close/
    box.yaml                       # 7 模块清单
    src/month_close/workflow.py    # 月结工作流
  portal/                          # Next.js
    src/app/page.tsx               # 入口

packages/beeos-core/               # 跨服务共享
  config.py                        # pydantic-settings (BEEOOS_ 前缀)
  db.py                            # SQLAlchemy 2.0 async
  guardian.py                      # AES-256-GCM + JWT + 注入检测
  models.py                        # 6 张 ORM 表

deploy/
  systemd/beeos.service            # 管整个 compose
  nginx/beeos.conf                 # 80/443 → 3000/8080
  docker/{Dockerfile.backend,Dockerfile.frontend}
  scripts/check-server.sh          # 部署前自检

docker-compose.yml                 # 4 容器开发环境
pyproject.toml                     # uv workspace 4 包
```

## Conventions

- **不要改 `BEEOOS_` 环境变量前缀** — 部署脚本依赖它
- **不要把 `.env` 加入 git** — 已写进 `.gitignore`
- **命名一致性**：Bee / BeeBox / Queen / Hive / Guardian / Granary / Bridge，见 [术语表](docs/architecture/glossary.md)
- **DB schema 变更**：M1 阶段直接改 `models.py` + 手动 `Base.metadata.create_all()`，V1 接入 Alembic
- **新增 Box** 必须包含 `box.yaml` 清单 + `src/<box>/workflow.py` 骨架
- **新增 API** 必须先在 [技术架构 §4](docs/architecture/tech-architecture.md#4-核心模块设计) 里写接口契约

## 部署相关

- ECS：`101.37.146.194`（与 domain-box 同台）
- 部署目录：`/opt/beeos`
- systemd unit：`beeos.service`
- 反向代理：nginx 已存在，新加 `conf.d/beeos.conf`
- 域名：**待定**（建议 `beeos.agentbeeline.com` 子域名）

## 共享记忆

复用 domain-box 的几条记忆：

- `project_prod_better_sqlite3_node20` — 我们用 PostgreSQL，不用管这条
- `feedback_zodtype_variance_gotcha` — Portal 端用 zod，Python 端用 pydantic，保持严格
- `ecs_deploy_tarball_pattern` — tarball 部署源模式

beeOS 独有：

- `beeos_env_prefix_must_be_BEEOOS_` — 部署脚本靠前前缀识别
- `beeos_docker_compose_managed_by_systemd` — 不要手动 docker compose up
- `beeos_secrets_live_on_host_only` — `.env` 永远不进 tarball
