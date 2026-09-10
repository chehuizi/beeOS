# beeOS 开发计划（M0 单机版）

> **状态**：v0.1 草案 · 待审
> **依据**：[`kernel-design.md`](./kernel-design.md) 唯一设计稿
> **目标**：M0 单机版 beeOS 端到端跑通（设计验证 + 最小可用）

## 0. 范围

**M0 单机版**（按 §8）：
- 3 进程：1 个 beeBox + 1 个 kanban + 1 个 workshop
- 资产层 = 内嵌（beeBox 进程内）
- 单机 / 单 beeBox / 单用户
- 业务物料流转（5 库区）+ 系统物料只读（凭证 / 连接 / 限流）

**M0 不做**（按 §9.2 / §10，留到 V1+）：
- 团队版 / 企业版（资产远端 / 调度器 / Router / 多 beeBox）
- Cache 失效推送（v0.1 用自动失效）
- signoff 完整事件驱动（v0.1 用轮询 Resume）
- Bee Planner 调 LLM（v0.1 用模板匹配）
- 多租户 / 多业务领域

## 1. 技术栈

| 维度 | 选型 | 理由 |
|---|---|---|
| 语言 | Python 3.11+ | asyncio 原生 / dataclass / typing |
| 进程间通信 | HTTP（FastAPI）+ WebSocket | 简单、跨平台、调试方便 |
| 持久化 | JSON 文件（v0.1）+ 内存缓存 | 0 依赖；v0.2 升级 SQLite |
| 包管理 | uv | 快 / lockfile / 跨平台 |
| 测试 | pytest + pytest-asyncio | 主流 / 异步友好 |
| CLI | `uv run beeos`（待 §9.2 拍板）| 单一入口拉起 3 进程 |
| 日志 | structlog | 结构化 / 易于审计 |

## 2. 代码组织（monorepo）

```
beeOS/
├── apps/
│   ├── beebox/                  # beeBox 进程
│   │   ├── pyproject.toml
│   │   └── src/beebox/
│   │       ├── __main__.py      # 进程入口
│   │       ├── runtime/         # 4 大内核组件
│   │       │   ├── task_receiver.py
│   │       │   ├── beeline_cache.py
│   │       │   ├── bee_planner.py
│   │       │   └── beeline_executor.py
│   │       ├── executor/         # operation 执行引擎
│   │       │   ├── operations/   # 5 种 type 实现
│   │       │   │   ├── data_io.py
│   │       │   │   ├── transform.py
│   │       │   │   ├── agent.py
│   │       │   │   ├── qc.py
│   │       │   │   └── signoff.py
│   │       │   └── runner.py    # op 调度循环
│   │       ├── assets/          # 资产层内嵌（3 类）
│   │       │   ├── bom_center.py
│   │       │   ├── beeline_lib.py
│   │       │   └── bee_registry.py
│   │       └── zones/           # 库区 / Bin
│   │           ├── raw.py        # 原料区
│   │           ├── line.py       # 线边区
│   │           ├── qc.py         # 质检区
│   │           ├── finished.py   # 成品区
│   │           ├── return_.py    # 退货区
│   │           └── system.py     # 系统库区
│   ├── kanban/                  # kanban 控制台进程
│   │   └── src/kanban/
│   │       ├── __main__.py
│   │       └── web/             # Web UI（看板视图）
│   ├── workshop/                # workshop 设计控制台进程
│   │   └── src/workshop/
│   │       ├── __main__.py
│   │       └── web/             # Web UI（设计器）
│   ├── shared/                  # 跨进程共享代码
│   │   └── src/shared/
│   │       ├── schemas/         # 3 类资产 schema 定义
│   │       │   ├── bom.py
│   │       │   ├── beeline.py
│   │       │   └── bee.py
│   │       ├── protocol/        # 进程间通信协议
│   │       │   └── http_api.py  # HTTP API 路径 + 类型
│   │       └── models/          # 共享数据模型
│   │           ├── material.py
│   │           ├── operation.py
│   │           └── task.py
│   └── cli/                     # beeos CLI
│       └── src/beeos/
│           └── __main__.py      # 拉起 3 进程
├── tests/
│   ├── unit/                    # 模块级单测
│   ├── integration/             # 进程间通信 / 端到端
│   └── e2e/                     # 完整 demo 场景
├── scripts/
│   ├── verify_mermaid.py        # 设计稿 mermaid 验证（已存在）
│   └── dev/                     # 开发脚本
│       ├── run_local.sh          # 本地跑 3 进程
│       └── seed_demo_data.py     # 种 demo 数据
├── docs/
│   ├── kernel-design.md         # 设计稿（唯一）
│   └── development-plan.md      # 本文档
├── Makefile
├── pyproject.toml                # workspace 配置
└── uv.lock
```

**workspace 拆分**（`pyproject.toml`）：
- 4 个独立 package：`beebox` / `kanban` / `workshop` / `shared` / `cli`
- 用 uv workspace 管理

## 3. 里程碑（7 阶段）

| 阶段 | 目标 | 周期估计 | 验证 |
|---|---|---|---|
| **M0.1** | 项目骨架 + 进程模型 + 进程间 HTTP 通信 | 1 周 | 3 进程能拉起，hello world 跨进程调用通 |
| **M0.2** | 资产层内嵌（3 类 schema + 持久化 JSON）| 1.5 周 | 单元测试：BOM / beeline / bee 注册表 CRUD |
| **M0.3** | 核心组件 4 个（Task Receiver / Beeline Cache / Bee Planner / Beeline Executor）| 2 周 | 集成测试：task 接收 → 定位 beeline → 加载模板 |
| **M0.4** | operation 执行引擎 + 5 种 type | 2 周 | 单元测试：每种 op 单独跑通；data_io / transform / agent / qc / signoff |
| **M0.5** | signoff 挂起/恢复（v0.1 轮询版）+ 异常回流到退货区 | 1 周 | 集成测试：signoff 释放线程 → 人工在 kanban 通过 → task 恢复继续 |
| **M0.6** | 控制台（kanban / workshop）Web UI 基础版 | 2 周 | UI 演示：kanban 5 列布局 + workshop 6 模块 |
| **M0.7** | 端到端 demo（拉科目 → 银行对账 → qc 校验 → 签核 → 完成）| 1 周 | e2e 测试覆盖 demo 全流程 |

**总计：~10.5 周**（2-3 个月）

## 4. 各里程碑详细任务

### M0.1 项目骨架

- [ ] monorepo 结构（`apps/` 4 个 package + `tests/` + `scripts/`）
- [ ] `pyproject.toml` workspace 配置（uv）
- [ ] 每个 package 的 `__main__.py`（最小 hello world）
- [ ] 进程间 HTTP 通信（FastAPI + httpx client）
- [ ] `apps/cli/` 拉起 3 进程（subprocess 启动 + 健康检查）
- [ ] Makefile 加 `make dev`（一键跑本地 3 进程）
- [ ] CI（GitHub Actions）：lint + test

### M0.2 资产层

- [ ] `apps/shared/src/shared/schemas/bom.py` — BOM schema（业务物料 + 系统物料）
- [ ] `apps/shared/src/shared/schemas/beeline.py` — beeline 模板 schema
- [ ] `apps/shared/src/shared/schemas/bee.py` — bee 注册表 schema
- [ ] `apps/beebox/src/beebox/assets/` — 3 类资产的内嵌实现（Python dict + JSON 持久化）
- [ ] CRUD 接口（内部 API）
- [ ] 单元测试：3 类资产 CRUD + 持久化

### M0.3 核心组件

- [ ] `task_receiver.py` — 接收 task，分配 task ID，写 task state
- [ ] `beeline_cache.py` — 加载 beeline 模板到内存，beeline miss 时触发 planner
- [ ] `bee_planner.py` — 简单模板匹配（v0.1 不调 LLM，从预定义模板库选）
- [ ] `beeline_executor.py` — 调度 operation 循环，处理挂起 / 恢复
- [ ] 集成测试：完整 E2E 流程（参照 §7.2 task E2E 流程图）

### M0.4 operation 执行引擎

- [ ] 5 种 op type 实现：
  - [ ] `data_io.py` — 拉数据 / 存结果（最简实现：读 JSON 写 JSON）
  - [ ] `transform.py` — 字段映射（最简实现：jmespath 表达式）
  - [ ] `agent.py` — 调 bee（v0.1 bee 是 mock，返回固定结果）
  - [ ] `qc.py` — 校验（balance_check / integrity_check / business_rule 三个内置 handler）
  - [ ] `signoff.py` — 状态挂起（v0.1 轮询）
- [ ] `runner.py` — op 调度循环（按 seq 顺序 + 处理 signoff 挂起）
- [ ] 单元测试：每种 op 单独测试 + 组合测试

### M0.5 signoff 挂起 / 恢复

- [ ] signoff op 触发挂起：保存 task state，释放 Executor 线程
- [ ] 人工通过 kanban REST API 触发 Resume（v0.1 轮询：Executor 定期查 AwaitingHuman 的 task）
- [ ] 异常处理：任意 op 异常 → 物料到退货区 + task 状态 AwaitingHuman
- [ ] 集成测试：signoff 释放 + 轮询恢复 + 异常回流

### M0.6 控制台 Web UI

- [ ] **kanban** Web UI：
  - [ ] 5 列看板（原料 / 线边 / 质检 / 成品 / 退货）
  - [ ] task 卡片（按状态着色）
  - [ ] 触发新 task / 认领异常 / 签核 task
- [ ] **workshop** Web UI：
  - [ ] 6 模块入口（beeBox 设计器 / beeline 编辑器 / operation 库 / bee 注册表 / beeline 模板库 / BOM 中心）
  - [ ] 基础 CRUD 界面（每个模块）
- [ ] 前后端：v0.1 简化用 server-rendered HTML（jinja2 模板），不引入 React/Vue

### M0.7 端到端 demo

- [ ] demo 数据：`seed_demo_data.py` 种一个"银行对账"场景
  - 业务物料 schema：科目余额 / 银行对账单 / 对账结果
  - beeline 模板：拉科目 → 拉对账单 → agent 对账 → qc 校验 → 经理签核 → 完成
  - 系统物料 schema：银行 API 凭证
- [ ] 完整流程 e2e 测试：触发 task → 跑通 5 个 op → 经理在 kanban 签核 → 完成
- [ ] 文档：`docs/demo.md`（v0.2）说明 demo 怎么跑

## 5. 测试策略

| 层级 | 范围 | 工具 | 频率 |
|---|---|---|---|
| 单元 | 单个模块 / 函数 | pytest | 每次 commit |
| 集成 | 进程内多模块协作 | pytest + asyncio | 每次 commit |
| 端到端 | 多进程 + 完整 demo | pytest + subprocess | 每次 release |

**覆盖率目标**：M0.7 完成时，核心模块（executor / assets / runtime）> 80%。

## 6. 风险与开放问题

| 项 | 风险 | 缓解 |
|---|---|---|
| Python 进程模型 | 异步 runtime 复杂度 | 用 asyncio + 简单状态机，避免复杂并发 |
| 进程间通信 | HTTP 调试 / 性能 | v0.1 简化只暴露必要 API，v0.2 加 WebSocket 实时 |
| signoff 轮询 | 轮询频率 vs 响应延迟权衡 | v0.1 5s 轮询，v0.2 改推送 |
| 资产内嵌 v0.1 限制 | 团队版 / 企业版完全重做 | v0.1 抽象资产接口（Storage trait），v0.2 切换实现 |
| 持久化 JSON | 性能 / 原子写 | 用 `tempfile + rename` 原子写，v0.2 换 SQLite |

## 7. 不做（v0.1 范围外）

明确**不做**的事，避免 scope creep：

- 团队版 / 企业版（资产远端 / 调度器 / Router / 多 beeBox）—— §9.2 / §10
- Cache 失效推送协议 —— §9.2 待澄清
- signoff 完整事件驱动（v0.1 用轮询）—— §9.2 待澄清
- task state 持久化机制（v0.1 内存）—— §9.2 待澄清
- beeOS kernel 子系统设计（5 大子系统）—— §10 待澄清
- 多租户 / 跨 beeBox 协作 —— §9.2 待澄清
- operation 并行 / 条件分支 —— §9.2 待澄清
- kanban 移动端 / 大屏 —— §9.2 待澄清
- workshop 多租户协作 —— §9.2 待澄清

## 8. 验证 & 上线

**M0 上线标志**（团队内 demo + 文档）：
- [ ] M0.7 e2e demo 跑通（截图 + 录屏）
- [ ] `docs/demo.md` 写完
- [ ] README 更新（指向 kernel-design + development-plan）
- [ ] CI 全绿（lint + test + e2e）

**V1 启动条件**（团队版 / 企业版）：
- 至少 1 个外部用户验证 M0 demo
- §9.2 待澄清全部拍板
- V1 部署形态（§8）按需细化

## 9. 进度跟踪

每周更新本节，记录实际进度 vs 计划。

| 周 | 计划 | 实际 | 备注 |
|---|---|---|---|
| W1 | M0.1 项目骨架 | TBD |  |
| ... | | |  |
