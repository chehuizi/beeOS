# beeOS 内核设计 v0.1

> **状态**：v0.1 草稿 · 修订中
> **日期**：2026-09-06
> **定位**：beeOS = 数字精益工作 OS

## 0. 一句话

beeOS 三大模块 **beeBox / beeline / bee**，两个控制台 **kanban / workshop**。
beeline 由 **operation** 序列组成（operation 是 beeline 内部组件），agent operation 调 bee；
每个 operation 驱动数字物料在 5 库区间流转。

## 1. 关系总览

```
┌──────────────────────────────────────────────────────┐
│                      beeOS                           │
│                                                      │
│   ┌──────────────┐              ┌──────────────┐     │
│   │   kanban     │              │   workshop   │     │
│   │   (用户)     │              │  (管理)      │     │
│   │   看板视图   │              │  设计视图    │     │
│   └──────┬───────┘              └──────┬───────┘     │
│          │ 读                          │ 写          │
│          │                             │             │
│          ▼                             ▼             │
│   ┌──────────────────────────────────────────┐       │
│   │  beeBox  车间                            │       │
│   │   ├─ 5 库区                              │       │
│   │   ├─ 库位 / 数字物料                     │       │
│   │   ├─ beeline  工艺路线                   │       │
│   │   │   └─ operation 序列                 │       │
│   │   │      ├─ data_io / transform / qc    │       │
│   │   │      └─ agent ──→ bee 工人         │       │
│   │   └─ bee 工人                          │       │
│   └──────────────────────────────────────────┘       │
└──────────────────────────────────────────────────────┘
```

## 2. 核心要素

### 三大模块

| 模块 | 类比 | 定位 | 关系 |
|---|---|---|---|
| **beeBox** | 车间 | 容器 / 父 | beeline 在它内部执行；bee 被它调度 |
| **beeline** | 流水线 / 工艺路线 | beeBox 内的工作流 | 由 operation 序列组成；agent operation 调 bee |
| **bee** | 工人 | 智能体执行者 | 被 agent operation 调用 |

### beeline 内部组件

| 组件 | 类比 | 定位 | 关系 |
|---|---|---|---|
| **operation** | 工序 | beeline 的一步 | 驱动数字物料在库位间流转 |

## 3. 两个控制台

### 3.1 beeOS **kanban**（用户侧 / 现场视角）

- **面向**：终端用户 / 操作员 / 业务人员
- **核心问题**：现在 task 在哪跑？跑得怎样？哪里堵？哪里出问题？
- **视觉**：Kanban 看板 —— 物料在 5 库区/库位间流转的可视化视图
- **主要内容**：task 状态、operation 进度、库区在制、异常告警、节拍时间
- **精益对位**：Kanban（看板管理）—— 可视化、拉动、暴露问题

**看板视图示意**：

```
┌─────────┐   ┌─────────┐   ┌─────────┐   ┌─────────┐  │  ┌─────────┐
│ 原料区  │ → │ 线边区  │ → │ 质检区  │ → │ 成品区  │  │  │ 退货区  │
│ 入库位A │   │ 加工B/C │   │ 待验D   │   │ 完成E   │  │  │ 异常F   │
│ 5 在制  │   │ 3 在制  │   │ 1 待签  │   │ 12 交付 │  │  │ 2 待处  │
└─────────┘   └─────────┘   └─────────┘   └─────────┘  │  └─────────┘
```

### 3.2 beeOS **workshop**（管理侧 / 设计视角）

- **面向**：管理员 / 业务分析师 / 工艺工程师
- **核心问题**：这个 beeBox 需要什么 beeline / operation / 物料 / 智能体？
- **视觉**：IDE / 表单 / 拖拽
- **主要内容**：设计 beeBox / 编辑 beeline / 注册 bee / 维护 SOP / 管理 BOM
- **精益对位**：Standard Work Design（标准作业设计）—— 标准化、模板化、复用

**workshop 主要模块**：

| 模块 | 作用 |
|---|---|
| beeBox 设计器 | 定义业务领域 / 5 库区 / 库位 / 数字物料 schema |
| beeline 编辑器 | 拖拽 / 编排 operation 序列 |
| operation 库 | 各类 operation 模板（data_io / transform / agent / qc / signoff）|
| bee 注册表 | 管理 bee 智能体（能力 / 输入输出 / 适用 operation）|
| SOP 文档库 | 上传/编辑/版本化 SOP 文档，关联到 operation |
| BOM 管理中心 | 跨 beeBox 共享 beeline + 物料清单 |

### 3.3 读写关系

| 控制台 | 方向 | 操作 |
|---|---|---|
| kanban | 只读 + 触发 | 看 task、触发 task、签核、认领异常 |
| workshop | 读写 | 设计 beeBox、定义 beeline、注册 bee、编辑 SOP |

> **设计在 workshop，运行在 kanban；workshop 写，kanban 读。**

## 4. beeBox 内部结构

```
beeBox（车间，归属 1 个业务领域）
├─ 库区（Zone，5 类固定）
│   ├─ 原料区（Raw）        外部输入 / 原始数据
│   ├─ 线边区（Line-side）  加工中 / 中间结果
│   ├─ 成品区（Finished）  最终产出
│   ├─ 质检区（QC）        验证 / 审核 / 签核
│   └─ 退货区（Return）    异常 / 返工
├─ 库位（Location）：每个库区下细分
└─ 数字物料（Digital Material）：库位上放的数据 + 类型
```

### 待澄清（beeBox 还"装"什么）

| # | 候选 | 问题 |
|---|---|---|
| A | 适配器（Adapter） | 外部世界（DB / API / 文件）连接 |
| B | 资源 / 凭证 | 连接串 / 凭证 / 限流 |
| C | 业务规则（Rule） | beeBox 内置硬约束 |
| D | **bee 工人池** | bee 是 beeBox 内部常驻？还是按需从外部拉？ |
| E | 异常回流 | 退货区物料回流路径 |
| F | 库位流转规则 | 物料能否跨库区流转 / 流转约束 |
| G | 看板 / 状态 | 精益"看板"在 beeBox 的体现 |
| H | 度量（Metrics） | 节拍 / 在制 / 良率 |

## 5. beeline 与 operation

### 5.1 operation 类型

| type | 是否需要 bee | 例子 |
|---|---|---|
| data_io | ❌ | 拉科目余额、生成 PDF、存结果 |
| transform | ❌ | 字段映射、聚合、格式转换 |
| agent | ✅ **bee 智能体** | 银行对账推理、合同条款提取 |
| qc | ❌ | 平衡校验、完整性校验 |
| signoff | 🟡 人工/混合 | 经理签核 |

> 关键：**不是每个 operation 都需要 bee**。只有需要"判断/推理/对话"的 operation 才放 bee。

### 5.2 operation 必含属性

| 属性 | 必含 | 含义 |
|---|---|---|
| seq | ✅ | operation 序号 |
| type | ✅ | 加工类型 |
| input_location | ✅ | 从哪里读数字物料 |
| output_location | ✅ | 把数字物料落到哪里 |
| sop_ref | 🟡 | 配套 SOP 文档引用（如 `sop/finance/reconcile-v3.md`）|
| bee_ref | 🟡 agent 才有 | 被调的 bee（如 `beex.finance.bank_reconciler`）|
| task | 🟡 | 工人 / 工具干的具体活（如 `reconcile_bank`）|
| qc_rules | 🟡 qc 才有 | 校验规则 |
| exception_handler | 🟡 | 异常处理（退货区 / 重试 / 人工）|

### 5.3 数据流（operation 驱动物料在 5 库区间流转）

```
[beeline 启动]
  │
  ▼
[operation 1: data_io]
  ├─ 读：原料区 / 库位 A（外部输入）
  └─ 写：线边区 / 库位 B（加工中）
  │
  ▼
[operation 2: agent] ──→ 调 bee
  ├─ 读：线边区 / 库位 B
  └─ 写：线边区 / 库位 C（新加工结果）
  │
  ▼
[operation 3: qc]
  ├─ 读：线边区 / 库位 C
  └─ 写：质检区 / 库位 D（待验证）
  │
  ▼
[operation 4: signoff]
  ├─ 读：质检区 / 库位 D
  ├─ 通过 → 写：成品区 / 库位 E
  └─ 拒绝 → 写：退货区 / 库位 F
  │
  ▼
[beeline 结束]
```

## 6. 5 大内核组件

| 编号 | 名称 | 职责 | 在哪运行 |
|---|---|---|---|
| ① | Task Receiver | 接收任务 | beeBox 入口 |
| ② | beeBox Router | 路由到目标 beeBox | Kernel 顶层 |
| ③ | Beeline Cache | 缓存 beeline 蓝图（BOM 中心）| beeBox 内部 |
| ④ | Bee Planner | beeline miss 时规划 | beeBox 内部 |
| ⑤ | Beeline Executor | 在 beeBox 内部执行 beeline operation | beeBox 内部 |

## 7. 精益概念 ↔ beeOS 映射

| 精益概念 | beeOS 落地 |
|---|---|
| 价值流（Value Stream） | beeline |
| 标准化作业 | beeline 蓝图（operation 序列）|
| 自働化（Jidoka） | bee 智能体 + 异常回流 |
| 看板（Kanban） | kanban 控制台 + 库位在制视图 |
| 拉动（Pull） | Task Receiver 接收触发 |
| 单件流（One-piece Flow）| operation 一次执行一份物料 |
| 改善（Kaizen） | 度量（§4 待澄清 H）+ 审计 |
| 节拍时间（Takt Time）| operation.elapsed_ms + 库位在制聚合 |
| 标准作业（SOP）| operation.sop_ref 引用 |

## 8. 已确定 vs 待澄清

### ✅ 已确定
- 核心要素：beeBox / beeline / bee（三大模块）+ operation（beeline 内部组件）
- 两个控制台：kanban / workshop
- beeline 在 beeBox 内执行
- operation 驱动数字物料在 5 库区间流转
- 5 库区固定 5 类（原料 / 线边 / 成品 / 质检 / 退货）
- 节点命名为 `operation`，附 `sop_ref`
- operation 必含：seq / type / input_location / output_location
- 4 种基础 operation 类型：data_io / transform / agent / qc / signoff
- agent operation 才调 bee

### ❓ 待继续打磨（v0.2+）
- §4 beeBox 还"装"什么（A~H 8 个候选）
- **D 项 bee 工人归属**（beeBox 内部常驻 vs 按需拉）
- bee 干的具体活叫 `task`？
- kanban 移动端 / 大屏
- workshop 多租户协作
- 跨 beeBox 协作（1 个 task 能不能跨车间）
- BOM 中心部署形态（远端 / 内嵌 / 本地）
- operation 编排是否支持并行 / 条件分支
- 数字物料粒度（字段 / 记录 / 文件）
