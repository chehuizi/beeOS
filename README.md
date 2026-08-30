# Beeline

> **让 AI 数字员工飞直线的运行时** · [agentbeeline.com](https://agentbeeline.com)
>
> 让 50-500 人专业服务公司 1 个资深员工顶 3 个人的活。

[![CI](https://github.com/chehuizi/beeOS/workflows/CI/badge.svg)](https://github.com/chehuizi/beeOS/actions)
[![Python 3.12+](https://img.shields.io/badge/python%203.12+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

> 🚧 **品牌重塑进行中**：GitHub 仓库仍叫 `chehuizi/beeOS`（M1 命名），但**对外品牌已统一为 "Beeline"**，域名 `agentbeeline.com`。M3 有外部链接后再统一 repo 名。

---

## ✨ 这是什么

**Beeline**（原内部代号 beeOS）是"AI 数字员工的私有化运行时"。把"会计月结"这种专业场景的所有技能、知识、凭证封装成 **beeBox 模板**，开箱即用跑在客户的服务器上。

- 🐝 **运行时 + 工作空间分离**：`bee` = runtime 引擎，`beeBox` = workload 应用
- 🔒 **私有化部署**：客户数据不出门
- ⚡ **1 人天交付**：从装到上线 8 小时
- 🤖 **多模型 AB**：DeepSeek + 通义，单点故障 30 分钟切走
- 📦 **行业 Box**：MVP 第一个 Box = `MonthCloseBox`（会计月结自动化）

**当前阶段（M0）**：`bee` runtime + `beeBox` workload 最小配对，37/37 测试通过，已部署 demo server。Queen/Hive 等调度层暂存 `_shelved/`，V1+ 触发恢复。

---

## 🚀 快速开始

### 前置条件

- Python 3.12+
- `uv` 工具链（https://docs.astral.sh/uv/）
- **M0 阶段零基础设施依赖**（无 PG / Redis / Docker / systemctl）

### 安装与运行

```bash
# 1. 克隆
git clone https://github.com/chehuizi/beeOS.git
cd beeOS

# 2. 安装依赖
make install       # uv sync

# 3. 跑月结（M0 demo）
make dev-box       # 单跑 Box（不依赖 Bee）
make dev-bee       # Bee 加载 Box 跑全流程 + 写本地审计

# 4. 一行端到端
make smoke         # 装包 + 跑 Box + 跑 Bee + 跑测试
```

### CLI 工具

```bash
uv run bee --list                       # 列出已注册 Box
uv run bee --box month_close --period 2026-07  # 跑月结
uv run month-close --manifest           # 打印 Box 清单
uv run month-close --period 2026-08 --approver alice@x.com  # 自定义参数
```

### 部署到服务器

```bash
# M0 部署（无 systemd 操作）
bash scripts/deploy-m0.sh

# M0 demo server 启停
bash scripts/m0-server.sh start|stop|status|restart
```

### 常用命令

```bash
make help           # 查看所有命令
make test           # 跑测试（37 个）
make lint           # Ruff lint
make format         # Ruff auto-format
make type-check     # mypy
make clean          # 清缓存和审计日志
```

---

## 🏗️ 项目结构

```
beeOS/  (内部 repo 名；对外 Beeline)
├── apps/
│   ├── bee/                         # bee — runtime 引擎
│   │   └── src/bee/
│   │       ├── __main__.py          # CLI: bee --box ...
│   │       ├── orchestrator.py      # Bee 主类（状态机驱动）
│   │       ├── state.py             # 5 状态机（内存版）
│   │       ├── audit.py             # 本地 JSONL + 哈希链
│   │       ├── registry.py          # Box 模块发现
│   │       └── server.py            # M0 demo FastAPI server
│   └── boxes/month-close/           # beeBox — workload 应用
│       └── src/month_close/
│           ├── __main__.py          # CLI: month-close --period ...
│           ├── __init__.py          # 导出 MANIFEST / WORKFLOW / run_step
│           ├── schema.py            # Pydantic 数据契约
│           ├── adapters.py          # 7 个数据工具（hardcoded）
│           └── workflow.py          # 6 步声明 + 单步实现
├── packages/
│   └── beeos-core/                  # 跨服务共享（M0 精简版：config + logging）
│       ├── config.py                # pydantic-settings
│       └── logging.py               # structlog
├── _shelved/                        # V1+ 恢复用（M0 不用）
│   ├── queen/                       # FastAPI 调度服务（待 V1 恢复）
│   └── beeos_core/{db,models,guardian}.py  # ORM / 加密 / 鉴权
├── deploy/
│   ├── nginx/beeos.conf            # nginx 反代（M1 80 端口 + M0 /m0/ 路径）
│   └── systemd/                    # M1 systemd unit（M0 不用）
├── scripts/
│   ├── deploy-m0.sh                # M0 部署脚本（不动 systemd）
│   ├── m0-server.sh                # M0 demo server 启停
│   └── deploy-to-ecs.sh            # M1 部署脚本（V1 恢复后用）
├── docs/architecture/              # 3 份架构 + 全景图 + 1-pager
├── pyproject.toml                  # uv workspace 3 包
└── Makefile                        # 便捷命令
```

---

## 🧭 路线图

| 阶段 | 目标 | 状态 |
|---|---|---|
| **M0**（当前） | bee runtime + beeBox 最小配对 | ✅ 完成 |
| **M2** | 3 家种子客户免费试用 | 🚧 进行中 |
| **M3** | 5-10 付费客户 / ¥25-50 万 ARR | ⏳ |
| **M4-M6** | 3 个 Box + 多 Bee 编排（V1 Queen 恢复） | ⏳ |
| **M9** | 现金流回正 | ⏳ |
| **M12** | 30 付费 / ¥150 万 ARR | ⏳ |

---

## 🐝 命名

- **Beeline**：产品品牌，"agent takes the beeline = 高效直接的 AI agent"
- **agentbeeline.com**：产品域名
- **bee**：runtime 引擎（worker / orchestrator）
- **beeBox**：workload 应用（月结 / 税务 / 审计 各种 box）
- **Queen / Hive / Pollen / Guardian / Granary / Bridge**：runtime 子组件（V1+ 启用）

详见 [术语表](docs/architecture/glossary.md)。

---

## 📄 License

MIT — 见 [LICENSE](LICENSE)
