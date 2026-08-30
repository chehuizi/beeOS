# CLAUDE.md

Project guidance for Claude Code (claude.ai/code) when working in this repo.

## Project Overview

**beeOS** — 私有化部署的 AI 数字员工平台。

### M0 架构（当前阶段）

M0 聚焦核心抽象：**BeeBox = 数据结构，Bee = 算法**。Queen/Hive 暂不造。

```
beeos (M0)
  ├─ apps/bee/                         # 算法侧（状态机 + 编排 + 审计）
  │   └─ src/bee/                      #   - orchestrator.py / state.py / audit.py
  └─ apps/boxes/month-close/           # 数据侧（schema + adapters + workflow 声明）
      └─ src/month_close/              #   - schema.py / adapters.py / workflow.py
```

**核心设计原则**：

1. **BeeBox 聚集数据结构** —— 只暴露 schema + 数据工具 + workflow 声明
2. **Bee 聚集算法** —— 状态机 + 编排 + ReAct 循环（V1+）
3. **无 Queen / 无 Hive / 无 PG** —— M0 阶段纯内存 + 本地 JSONL 审计
4. **零基础设施依赖** —— `make dev-m0` 一行命令跑通

V1+ 触发恢复条件见 [`_shelved/README.md`](_shelved/README.md)。

## Local dev

```bash
make install      # uv sync
make dev-box      # 单跑 Box（不依赖 Bee）
make dev-bee      # Bee 加载 Box 跑全流程 + 写本地审计
make test         # 跑全部测试
make smoke        # 端到端：装包 + 跑 Box + 跑 Bee + 测试
```

**前置**：Python 3.12+、uv 工具链。**不再依赖 PG/Redis/systemctl**。

CLI：

- `uv run month-close --period 2026-07` —— 跑 Box
- `uv run month-close --manifest` —— 打印 manifest
- `uv run bee --box month_close --period 2026-07` —— 跑 Bee
- `uv run bee --list` —— 列出已注册 Box

## Deploy

M0 阶段**不部署**。代码仅本地运行 + 测试。

V1 恢复 Queen 后恢复 ECS 部署流程（`scripts/deploy-to-ecs.sh`）。

## Architecture decisions

**M0 核心原则**：

- **BeeBox = 数据，Bee = 算法** —— 不要把决策逻辑写进 Box
- **Box 暴露三件套**：`MANIFEST` dict + `WORKFLOW` 列表 + `run_step(name, ctx, prev)` 函数
- **Bee 跑流程但不调 LLM** —— M0 写死按 WORKFLOW 顺序跑；V1+ 加 ReAct 循环
- **审计本地化** —— `./logs/audit.jsonl` JSONL + SHA-256 哈希链

详见 [`docs/architecture/`](docs/architecture/)（M0 不变更）。

## Key files

```
apps/
  bee/                                  # 算法侧
    src/bee/
      __main__.py                       # CLI 入口
      orchestrator.py                   # Bee 主类（状态机驱动）
      state.py                          # 5 状态机（内存版）
      audit.py                          # 本地 JSONL + 哈希链
      registry.py                       # Box 模块发现

  boxes/month-close/                    # 数据侧
    src/month_close/
      __main__.py                       # Box CLI 入口（独立调试用）
      __init__.py                       # 导出 MANIFEST / WORKFLOW / run_step
      schema.py                         # Pydantic 数据契约
      adapters.py                       # 7 个数据工具（hardcoded）
      workflow.py                       # 6 步声明 + 单步实现

packages/beeos-core/                    # 跨服务共享（M0 精简版）
  config.py                             # pydantic-settings (BEEOOS_ 前缀)
  logging.py                            # structlog 包装

_shelved/                               # V1+ 恢复用（M0 不用）
  README.md                             # 恢复触发条件
  queen/                                # FastAPI 调度服务
  beeos_core/{db,models,guardian}.py    # PG / ORM / 安全
  tests/test_queen_api.py               # Queen API 集成测试

tests/
  conftest.py                           # M0 极简（无 DB fixture）
  unit/test_bee.py                      # Bee 引擎 + 状态机
  unit/test_month_close.py              # Box schema + adapters + workflow

pyproject.toml                          # uv workspace 3 包
```

## Conventions

- **BeeBox 不写决策** —— 决策归属 Bee（状态转换 / 重试 / 异常处理 / ReAct 提示工程）
- **Bee 不写数据** —— Bee 调 `box.run_step()` 拿数据，不直接调 adapter
- **schema 严格 Pydantic** —— V1+ 真实 adapter 替换 hardcoded 时签名不变
- **workflow 是数据不是代码** —— `WORKFLOW` 列表是声明，Bee 读它来跑
- **新增 Box** = 复制 month-close 模板，导出 `MANIFEST`/`WORKFLOW`/`run_step` 即可

## V1+ 触发恢复（不在本阶段实现）

- 第一个真客户要 demo Dashboard → 恢复 `_shelved/queen/`
- 跨进程 Job 追踪 / 并发需求 → 恢复 `_shelved/beeos_core/db.py` + `models.py`
- 多 Bee 并行 + 持久化 → 同时恢复 queen + hive
- 商业化合规审计 → 恢复 `_shelved/beeos_core/guardian.py`（先修固定 nonce bug）
- 跑老 Queen API 测试 → 恢复 `_shelved/tests/test_queen_api.py`
