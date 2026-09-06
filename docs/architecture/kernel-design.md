# beeOS 内核设计 v0.1

> **状态**：v0.1 草稿 · 修订中
> **日期**：2026-09-06
> **定位**：beeOS = 数字精益工作 OS

## 0. 一句话

beeOS 三大模块 **beeBox（容器）/ beeline（流水线）/ bee（工人）**，两个控制台 **kanban（用户）/ workshop（管理）**。
**beeline 在 beeBox 内跑**，由 **operation（工序）** 序列组成（operation 是 beeline 内部组件），**agent operation 调 bee**；
每个 operation 驱动数字物料在 **beeBox 的 5 库区**间流转。

## 1. 关系总览

```mermaid
graph TB
    beeOS["beeOS"]

    kanban["kanban<br/>（用户）<br/>看板视图"]
    workshop["workshop<br/>（管理）<br/>设计视图"]

    beeBox["beeBox · 车间"]
    zones["5 库区（原料 / 线边 / 质检 / 成品 / 退货）"]
    locations["库位"]
    materials["数字物料"]
    beeline["beeline · 工艺路线"]
    operations["operation 序列<br/>（工序）"]
    opBasic["data_io / transform / qc / signoff"]
    opAgent["agent"]
    bee["bee · 工人"]

    beeOS --> kanban
    beeOS --> workshop
    kanban -.读.-> beeBox
    workshop -.写.-> beeBox

    beeBox --> zones
    beeBox --> beeline
    beeBox --> bee
    zones --> locations
    locations --> materials
    beeline --> operations
    operations --> opBasic
    operations --> opAgent
    opAgent -.调.-> bee

    classDef console fill:#fef3c7,stroke:#f59e0b,color:#78350f
    classDef box fill:#dbeafe,stroke:#2563eb,color:#1e3a8a
    classDef flow fill:#dcfce7,stroke:#16a34a,color:#14532d
    classDef worker fill:#fce7f3,stroke:#db2777,color:#831843

    class kanban,workshop console
    class beeBox,zones,locations,materials box
    class beeline,operations,opBasic,opAgent flow
    class bee worker
```

## 2. 核心要素

### 三大模块

| 模块 | 类比 | 定位 | 关系 |
|---|---|---|---|
| **beeBox** | 车间 | 容器 | beeline 在它内部执行；bee 被它调度 |
| **beeline** | 流水线 / 工艺路线 | beeBox 内的工作流 | 由 operation 序列组成；agent operation 调 bee |
| **bee** | 工人 | 智能体执行者 | 被 agent operation 调用 |

### beeline 内部组件

| 组件 | 类比 | 定位 | 关系 |
|---|---|---|---|
| **operation** | 工序 | beeline 的一步 | 驱动数字物料在库位间流转 |

## 3. 两个控制台

### 3.1 beeOS **kanban**（用户侧 / 现场视角）

- **面向**：终端用户 / 操作员 / 业务人员
- **核心问题**：现在 task 在 beeBox 里怎么跑？跑到哪了？哪里堵？哪里出问题？
- **输入**（看什么）：
  - task 列表：每个 task 的状态（Queued / Running / Done / Failed / AwaitingHuman）
  - task 当前 operation：跑到第几步
  - task 当前位置：在 5 库区/库位的哪个（task 内数字物料的位置）
  - 异常 task：哪些 task 异常、待人工处理
  - 节拍时间：每个 task / operation 的耗时
- **操作**（能做什么）：
  - 触发新 task
  - 认领异常 task（退货区）
  - 签核 task（质检区 / 成品区）
- **视觉**：Kanban 看板 —— task 在 beeBox 5 库区间流转的可视化视图
- **精益对位**：Kanban（看板管理）—— 可视化、拉动、暴露问题

**看板视图示意**：

```mermaid
flowchart LR
    subgraph R["原料区 · 入库位 A"]
        R1["task#001<br/>op1 拉科目余额<br/>⏱ 2m"]
        R2["task#002<br/>op1 拉科目余额<br/>⏱ 1m"]
    end
    subgraph L["线边区 · 加工 B/C"]
        L1["task#003<br/>op2 agent 银行对账<br/>⏱ 5m"]
        L2["task#004<br/>op2 agent 银行对账<br/>⏱ 4m"]
    end
    subgraph Q["质检区 · 待验 D"]
        Q1["task#005<br/>op4 signoff 经理签核<br/>⏳ AwaitingHuman"]
    end
    subgraph F["成品区 · 完成 E"]
        F1["task#006 ✓<br/>elapsed 8m"]
        F2["task#007 ✓<br/>elapsed 7m"]
    end
    subgraph X["退货区 · 异常 F"]
        X1["task#008 ✗<br/>对账不平 拒收"]
    end

    R --> L --> Q --> F
    Q -.拒.-> X

    classDef raw fill:#dbeafe,stroke:#2563eb,color:#1e3a8a
    classDef line fill:#e0e7ff,stroke:#4f46e5,color:#312e81
    classDef qc fill:#fef3c7,stroke:#f59e0b,color:#78350f
    classDef fin fill:#dcfce7,stroke:#16a34a,color:#14532d
    classDef ret fill:#fee2e2,stroke:#dc2626,color:#7f1d1d

    class R1,R2 raw
    class L1,L2 line
    class Q1 qc
    class F1,F2 fin
    class X1 ret
```

### 3.2 beeOS **workshop**（管理侧 / 设计视角）

- **面向**：管理员 / 业务分析师 / 工艺工程师
- **核心问题**：这个 beeBox 需要什么 beeline / operation / 数字物料 / bee？
- **输入**（基于什么改）：
  - 现有 beeOS 资产：已注册的 beeBox / beeline / bee / BOM
  - 业务需求：新增场景、调整工艺、注册新 bee
- **输出**（产出什么）：
  - 设计好的 beeBox（含 5 库区 / 库位 / 数字物料 schema）
  - 编辑好的 beeline（含 operation 序列）
  - 注册的 bee（智能体）
  - 更新的 BOM
- **视觉**：IDE / 表单 / 拖拽
- **精益对位**：Standard Work Design（标准作业设计）—— 标准化、模板化、复用

**workshop 主要模块**：

| 模块 | 作用 |
|---|---|
| beeBox 设计器 | 定义业务领域 / 5 库区 / 库位 / 数字物料 schema |
| beeline 编辑器 | 拖拽 / 编排 operation 序列 |
| operation 库 | 各类 operation 模板（data_io / transform / agent / qc / signoff）|
| bee 注册表 | 管理 bee 智能体（能力 / 输入输出 / 适用 operation）|
| BOM 中心 | 跨 beeBox 共享数字物料 schema |

### 3.3 读写关系

| 控制台 | 输入（看 / 基于什么） | 操作（做 / 产出什么） |
|---|---|---|
| **kanban** | task 实时状态（task / 当前 operation / 所在库区 / 异常 / 节拍） | 触发 task / 认领异常 / 签核 |
| **workshop** | 现有 beeOS 资产（beeBox / beeline / bee / BOM） | 设计 / 编辑 / 注册 / 上传 |

> **workshop 写 → beeOS 资产；beeOS 状态 → kanban 读。设计在 workshop，运行在 kanban。**

## 4. beeBox 内部结构

```mermaid
graph TB
    beeBox["beeBox · 车间<br/>（归属 1 个业务领域）"]
    zones["库区 · Zone（5 类固定）"]
    raw["原料区 · Raw<br/>外部输入 / 原始数据"]
    line["线边区 · Line-side<br/>加工中 / 中间结果"]
    finished["成品区 · Finished<br/>最终产出"]
    qc["质检区 · QC<br/>验证 / 审核 / 签核"]
    return["退货区 · Return<br/>异常 / 返工"]
    locations["库位 · Location<br/>每个库区下细分"]
    materials["数字物料 · Digital Material<br/>数据 + 类型"]

    beeBox --> zones
    zones --> raw
    zones --> line
    zones --> finished
    zones --> qc
    zones --> return
    zones --> locations
    locations --> materials

    classDef box fill:#dbeafe,stroke:#2563eb,color:#1e3a8a
    classDef ok fill:#dcfce7,stroke:#16a34a,color:#14532d
    classDef bad fill:#fee2e2,stroke:#dc2626,color:#7f1d1d
    classDef neutral fill:#f3f4f6,stroke:#6b7280,color:#1f2937
    class beeBox,zones,locations,materials box
    class raw,line neutral
    class finished,qc ok
    class return bad
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
| bee_ref | 🟡 agent 才有 | 被调的 bee（如 `beex.finance.bank_reconciler`）|
| task | 🟡 | 工人 / 工具干的具体活（如 `reconcile_bank`）|
| qc_rules | 🟡 qc 才有 | 校验规则 |
| exception_handler | 🟡 | 异常处理（退货区 / 重试 / 人工）|

### 5.3 数据流（operation 驱动物料在 5 库区间流转）

```mermaid
flowchart TD
    start([beeline 启动])
    op1["operation 1 · data_io<br/>读：原料区/库位 A<br/>写：线边区/库位 B"]
    op2["operation 2 · agent<br/>读：线边区/库位 B<br/>写：线边区/库位 C<br/>→ 调 bee"]
    op3["operation 3 · qc<br/>读：线边区/库位 C<br/>写：质检区/库位 D"]
    op4{"operation 4 · signoff<br/>读：质检区/库位 D"}
    op4pass["→ 写：成品区/库位 E（通过）"]
    op4fail["→ 写：退货区/库位 F（拒绝）"]
    finish([beeline 结束])

    start --> op1 --> op2 --> op3 --> op4
    op4 -->|通过| op4pass
    op4 -->|拒绝| op4fail
    op4pass --> finish
    op4fail --> finish

    classDef startend fill:#e0e7ff,stroke:#4f46e5,color:#312e81
    classDef agent fill:#fce7f3,stroke:#db2777,color:#831843
    classDef ok fill:#dcfce7,stroke:#16a34a,color:#14532d
    classDef bad fill:#fee2e2,stroke:#dc2626,color:#7f1d1d
    classDef step fill:#dbeafe,stroke:#2563eb,color:#1e3a8a
    class start,finish startend
    class op1,op3 step
    class op2 agent
    class op4pass ok
    class op4fail bad
```

## 6. 5 大内核组件

| 编号 | 名称 | 职责 | 在哪运行 |
|---|---|---|---|
| ① | Task Receiver | 接收任务 | beeBox 入口 |
| ② | beeBox Router | 路由到目标 beeBox | Kernel 顶层 |
| ③ | Beeline Cache | 缓存 beeline 蓝图 | beeBox 内部 |
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
| 标准化作业 | operation 序列本身就是标准作业（输入/输出/类型已声明清楚）|

## 8. 已确定 vs 待澄清

### ✅ 已确定
- 核心要素：beeBox / beeline / bee（三大模块）+ operation（beeline 内部组件）
- 两个控制台：kanban / workshop
- beeline 在 beeBox 内执行
- operation 驱动数字物料在 5 库区间流转
- 5 库区固定 5 类（原料 / 线边 / 质检 / 成品 / 退货）
- 节点命名为 `operation`（operation 即标准作业，不另设 SOP 层）
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
