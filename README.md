# beeOS

> **私有化部署的 AI 数字员工平台**——让 50-500 人专业服务公司 1 个资深员工顶 3 个人的活。

[![CI](https://github.com/chehuizi/beeOS/workflows/CI/badge.svg)](https://github.com/chehuizi/beeOS/actions)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## ✨ 这是什么

beeOS 是"AI 数字员工的私有化车间"。把"会计月结"这种专业场景的所有技能、知识、凭证封装成 **Box 模板**，开箱即用跑在客户的服务器上。

- 🔒 **私有化部署**：客户数据不出门
- ⚡ **1 人天交付**：从装到上线 8 小时
- 🤖 **多模型 AB**：DeepSeek + 通义，单点故障 30 分钟切走
- 📦 **行业 Box**：MVP 第一个 Box = `MonthCloseBox`（会计月结自动化）

---

## 🚀 快速开始

### 前置条件

- Linux（systemctl）+ PostgreSQL + Redis（系统包；macOS 开发者用 `brew services`）
- 4 核 CPU / 8 GB RAM / 50 GB 磁盘（推荐 8 核 / 16 GB / 200 GB）
- 公网出口（用于 LLM API）

### 启动

```bash
# 1. 克隆
git clone https://github.com/chehuizi/beeOS.git
cd beeOS

# 2. 复制环境变量
cp .env.example .env
# 编辑 .env，填入 LLM API Key

# 3. 启动（Linux/systemctl）
make up-services  # 启动本机 PG + Redis
make dev-queen    # 裸跑 Queen（另一终端）

# 4. 访问
# Portal:    http://localhost/  (nginx 80 服务静态 HTML)
# Queen API: http://localhost:8080/health
```

### 常用命令

```bash
make help           # 查看所有命令
make up-services    # 启动本机 PG + Redis（Linux/systemctl）
make down-services  # 停止本机 PG + Redis
make ps             # 查看 4 个 systemd unit 状态
make dev-queen      # 裸跑 Queen（需 PG/Redis 已就绪）
make logs-queen     # journalctl -u beeos-queen -f
make test           # 跑测试
make lint           # lint 检查
make type-check     # mypy
make db-shell       # 打开本机 PostgreSQL
make redis-shell    # 打开本机 Redis
```

---

## 🏗️ 项目结构

```
beeOS/
├── apps/                          # 应用服务
│   ├── queen/                     # 调度服务（FastAPI）
│   ├── bee/                       # 执行引擎（ReAct）
│   ├── boxes/month-close/         # 业务 Box（月结 M1）
│   └── portal/                    # Web 前端（静态 HTML + Alpine.js，nginx 服务）
├── packages/
│   └── beeos-core/                # 跨服务共享代码
│       ├── config.py              # 配置
│       ├── db.py                  # 数据库
│       ├── guardian.py            # 凭证加密 + 注入检测
│       ├── logging.py             # 日志
│       └── models.py              # ORM 模型
├── deploy/
│   ├── systemd/                   # systemd unit（beeos-queen.service）
│   ├── nginx/                     # nginx 反代 + 静态 Portal 配置
│   └── scripts/                   # 部署脚本
├── docs/
│   ├── business-model.md          # 商业模型 v0.1
│   ├── architecture/              # 3 份架构 + 全景图 + 1-pager
│   └── design/                    # 原始讨论归档
├── pyproject.toml                 # uv workspace 根
└── Makefile                       # 便捷命令
```

---

## 📚 文档

| 文档 | 受众 |
|---|---|
| [1-pager](docs/architecture/exec-summary.md) | 潜在合伙人 / 行业顾问 |
| [全景图](docs/architecture/overview.md) | 团队对齐 |
| [技术架构](docs/architecture/tech-architecture.md) | 工程师 |
| [产品架构](docs/architecture/product-architecture.md) | 产品经理 |
| [业务架构](docs/architecture/business-architecture.md) | 创始人 |
| [商业模型 v0.1](docs/business-model.md) | 创始人 |
| [术语表](docs/architecture/glossary.md) | 全员 |

---

## 🧭 路线图

| 阶段 | 目标 | 状态 |
|---|---|---|
| **M1**（当前） | 骨架 + 1 个 Bee / 1 个 Box 跑通 demo | ✅ 骨架完成 |
| **M2** | 3 家种子客户免费试用 | 🚧 进行中 |
| **M3** | 5-10 付费客户 / ¥25-50 万 ARR | ⏳ |
| **M4-M6** | 3 个 Box + 多 Bee 编排 | ⏳ |
| **M9** | 现金流回正 | ⏳ |
| **M12** | 30 付费 / ¥150 万 ARR | ⏳ |

---

## 🤝 贡献

本项目正在寻找：

- 🌟 **技术合伙人**：全栈 + LLM 经验，5-15% 股权
- 🏢 **行业合伙人**：5 年+ 会计 / 代账经验，3-8% 股权
- 📣 **早期员工 / 顾问**：期权 + 赌方向

详见 [1-pager](docs/architecture/exec-summary.md)。

---

## 📄 License

MIT — 见 [LICENSE](LICENSE)

---

## 🐝 命名由来

- **Bee（蜜蜂）**：干活的小员工
- **Box（盒子）**：专用工作间
- **Hive（蜂巢）**：状态/注册中心
- **Queen（蜂王）**：调度大脑
- **MonthCloseBox**：MVP 第一个业务 Box——会计月结

详见 [术语表](docs/architecture/glossary.md)。
