# BeeBox definition schema

> **状态**：v0.1 草稿 · 修订中
> **日期**：2026-09-13
> **对应**：[beebox-design.md §3.1 BeeBox definition](./beebox-design.md#31-beeBox-definition)

beeBox definition 的结构化 schema 定义。**这是接口规格，不是设计稿**——讲"字段长什么样、怎么校验、怎么引用"，不讲"为什么这样设计"。设计原理见 `beebox-design.md` §3.1。

---

## 1. 顶层结构

definition 顶层包含**基础元信息 + 数据形状 + 履约生命周期 5 块（按 Fulfillment 4 维度 + 度量）**：

| 块 | Fulfillment 维度 | 含义 | 字段 |
|---|---|---|---|
| **基础元信息** | — | beeBox 基础标识 | `id` / `version` / `description` |
| **数据形状** | — | definition 自带的 schema 列表 | `schemas` |
| **履约意图** | Intent | beeBox 接收什么 task | `task` |
| **履约合同** | Contract | beeBox 交付什么业务结果（业务定义 + 验收标准 + 例外条款）| `result` |
| **履约政策** | Policy | beeBox 的 queen 自治运营配置（authorization / rules）| `queen` |
| **履约程序** | Procedure | 每类 task 1:1 绑定 1 条 beeline 作为 procedure 实现 | `task[].beeline_id` + `beeline_version`（task 块内）|
| **履约度量** | Metrics | 质量 / 时效 / 成本 度量方式 + 目标值 | `metrics` |

**queen / schemas 都是 definition 自身内容**——不引用外部对象，跟着 definition 走、跟着 release 打包。

beeline 是独立维护的对象（id + version），definition 通过 `beeline_id` + `beeline_version` 引用。

---

## 2. schema 形状

```yaml
beeBox_definition:
  # ---- 基础元信息 ----
  id: string                 # definition 唯一标识
  version: integer           # definition 版本（递增序列号）
  description: string       # 人类可读说明

  # ---- 数据形状：definition 自带的 schema 列表 ----
  schemas:                  # 0...N 个 schema（按 id 定位，无 version）
    - id: string             # schema 唯一标识（在 definition 内唯一）
      description: string    # 人类可读说明
      fields:                # 1...N 个字段
        - name: string
          type: string        # type 取值见下
          required: boolean   # 默认 true
          description: string # 可选

  # ---- 履约意图（Intent）：beeBox 接收什么 task ----
  task:                      # 1...N 类 task
    - type: string           # task 类型标识
      task_schema: string    # 引用 schemas 块内的 schema id
      beeline_id: string     # 1:1 绑定的 beeline（履约程序 Procedure 的实现）
      beeline_version: integer  # 绑定的 beeline 的具体 version
      trigger: string        # 触发条件描述

  # ---- 履约合同（Contract）：交付什么业务结果 ----
  result:
    type: string             # 业务结果类型标识
    result_schema: string    # 引用 schemas 块内的 schema id
    acceptance:              # 验收标准（单次判据）
      - metric: string
        op: enum             # gte / lte / eq / in / match
        value: any
      ...
    exceptions:              # 例外条款
      - condition: string
        action: enum         # no_deliver / rollback / escalate

  # ---- 履约度量（Metrics）：质量 / 时效 / 成本 持续统计 ----
  metrics:
    quality:                 # 履约质量
      - name: string
        definition: string
        target: number       # 目标值
      ...
    latency:                 # 履约时效
      - name: string
        definition: string
        target: number
      ...
    cost:                    # 履约成本
      - name: string
        definition: string
      ...

  # ---- 履约政策（Policy）：beeBox 的 queen 自治运营配置 ----
  queen:                     # 履约政策——由 runtime 平台 queen engine 执行
    authorization:           # 授权策略（每类自治能力允许档位）
      task_routing: enum          # 任务流动：allow / observe_only / off
      exception_handling: enum    # 运行异常：allow / observe_only / off
      continuous_improvement: enum # 持续改善：allow / observe_only / off
    rules:                   # 自治规则（业务化规则描述，可选）
      task_routing: object        # 任务流动规则（路由策略 / WIP 限制 / backpressure）
      exception_handling: object  # 运行异常规则（检测条件 / 处理动作）
      continuous_improvement: object  # 持续改善规则（监控指标 / 改进触发 / 改进动作）

  # Queen 自治边界（不可配置、queen 默认遵守）：
  # 可以：调整并发量 / 选择 procedure / retry 决策 / route 决策 / escalate / pause
  # 不能：修改 contract / 修改 acceptance / 修改 release / 修改 policy
  # 详见 design.md §3.4.7
```

### schema type 取值（4 类）

| 类型 | 写法 | 备注 |
|---|---|---|
| **基础类型** | `string` / `number` / `boolean` / `integer` | 直接用类型名 |
| **对象类型** | `object` | 复杂结构用 `properties` 嵌套描述 |
| **数组类型** | `array` | `items` 描述元素 |
| **schema 引用** | `ref:<schema_id>` | 引用本 definition 的 schemas 块内的 schema id |

### 示例（订单请求）

```yaml
- id: schema_order_request
  description: 订单请求
  fields:
    - name: order_id
      type: string
      required: true
    - name: amount
      type: number
      required: true
    - name: items
      type: array
      items:
        type: ref:schema_order_item
    - name: shipping_address
      type: ref:schema_address
```

---

## 3. 引用机制

definition 只对**独立维护的对象**做引用——beeline 等。引用按被引用对象自身的字段形式——被引用对象是什么字段，引用就用什么字段；definition 不持有被引用对象的实现。

| 引用类型 | 必填字段 | 选填字段 | 备注 |
|---|---|---|---|
| **beeline 引用** | `id`, `version` | — | beeline 有 id + version（递增序列号）；引用发生在 task 块（task 1:1 绑定 1 条 beeline）|

**queen / schemas 是 definition 自身内容**——不引用外部对象，跟着 definition 走、跟着 release 打包。

引用校验（保存 definition 时）：
- beeline 引用：`id` + `version` 必须真实存在
- schema 引用：definition 内 schemas 块的所有引用（task_schema / result_schema / schema 内 ref）必须指向 schemas 块内真实存在的 schema id

---

## 4. 字段约束

- **必填字段**：`id`, `version`, `task`, `result`, `queen` 不可省略（`schemas` 可选；引用 task_schema / result_schema 时必填 schemas）
- **schemas id 唯一**：definition 内 schemas 列表的 id 唯一
- **schema 引用存在**：`task_schema` / `result_schema` / schema 内 `ref:` 引用必须指向本 definition schemas 块内真实存在的 schema id
- **task 必填 beeline**：`task` 列表每条都必填 `beeline_id` + `beeline_version`（1:1 绑定履约程序 Procedure）
- **beeline 引用存在**：`task.beeline_id` + `beeline_version` 必须在 beeline 仓库里真实存在
- **authorization 合法**：每类授权只能是 `allow` / `observe_only` / `off` 之一
- **queen rules 可选**：每类 rules 字段可选（不填 = 该类无具体规则，按平台默认行为）
- **metric 命名**：`quality` / `latency` / `cost` 三类分别命名，命名空间隔离
- **version 演进**：definition version 是递增序列号——修改 definition 不影响已发布 release（详见 beebox-design.md §3.1.5）

---

## 5. 跟设计稿的对照

| schema 字段 | Fulfillment 维度 | design 章节 |
|---|---|---|
| `schemas` | — | §3.1.2 数据形状（definition 自带 schema 列表）|
| `task` | Intent | §3.1.2 履约意图 |
| `result` | Contract | §3.1.2 履约合同（对应 §2.1 单次履约）|
| `queen` | Policy | §3.1.2 履约政策 |
| `task[].beeline_id` / `beeline_version` | Procedure | §3.1.2 履约程序（beeline 是 procedure 实现）|
| `metrics` | Metrics | §3.1.2 履约度量（对应 §2.1 履约指标）|
| 引用机制（按被引用对象字段形式） | — | §3.1.3 引用机制 |
| `version` / 序列号 | — | §3.1.5 与 release 的关系（definition 修改不影响已发布 release）|