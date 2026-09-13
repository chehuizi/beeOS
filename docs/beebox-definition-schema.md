# BeeBox definition schema

> **状态**：v0.1 草稿 · 修订中
> **日期**：2026-09-13
> **对应**：[beebox-design.md §3.1 BeeBox definition](./beebox-design.md#31-beebox-definition)

beeBox definition 的结构化 schema 定义。**这是接口规格，不是设计稿**——讲"字段长什么样、怎么校验、怎么引用"，不讲"为什么这样设计"。设计原理见 `beebox-design.md` §3.1。

---

## 1. 顶层结构

definition 按**履约生命周期**组织成 4 块：

| 块 | 含义 | 字段 |
|---|---|---|
| **履约对象** | beeBox 接收什么 task | `task` |
| **履约过程** | 怎么履约（beeline 列表 + 资源）| `process` |
| **履约结果** | beeBox 交付什么业务结果 | `result` |
| **履约度量** | 质量 / 时效 / 成本 度量方式 + 目标值 | `metrics` |

每块都是 definition 自己的——不需要跨 definition 复用（beeline / bee / schema 本身就用引用机制，复用通过引用实现）。

---

## 2. schema 形状

```yaml
beeBox_definition:
  # ---- 基础元信息 ----
  id: string                 # definition 唯一标识
  version: integer           # definition 版本（递增序列号）
  description: string       # 人类可读说明

  # ---- 履约对象：beeBox 接收什么 task ----
  task:                      # 1...N 类 task
    - type: string           # task 类型标识
      schema_ref:            # task 数据结构引用
        id: string
        version: semver
      trigger: string        # 触发条件描述

  # ---- 履约过程：怎么履约 ----
  process:
    # 流程：1...N 条 beeline
    beelines:
      - id: string
        version_constraint: semver_range  # 引用约束（如 ^1.0.0）
        applies_to:                       # 适用哪些 task type
          - task_type_ref: string

    # 资源：bee / schema / 外部系统
    bees:
      - type: string           # bee 类型标识
        params: object         # 必要参数
    schemas:
      - id: string             # 物料 schema 标识
        version: semver
    external_systems:
      - id: string             # 外部系统接入点
        interface: string      # 接口描述

  # ---- 履约结果：交付什么业务结果 ----
  result:
    type: string             # 业务结果类型标识
    schema_ref:              # 业务结果数据结构引用
      id: string
      version: semver
    acceptance:              # 验收标准（单次判据）
      - metric: string
        op: enum             # gte / lte / eq / in / match
        value: any
      ...
    exceptions:              # 例外条款
      - condition: string
        action: enum         # no_deliver / rollback / escalate

  # ---- 履约度量：质量 / 时效 / 成本 持续统计 ----
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
        target: number
      ...
```

---

## 3. 引用机制

所有跨对象引用都遵循 **`{id, version}`** 二元组——definition 不持有被引用对象的实现。

| 引用类型 | 必填字段 | 选填字段 | 备注 |
|---|---|---|---|
| **beeline 引用** | `id`, `version_constraint` | `applies_to` | version_constraint 支持 semver range（`^1.0.0`、`~1.2.0`）|
| **bee 引用** | `type` | `params` | bee 自身独立维护 |
| **schema 引用** | `id`, `version` | — | 固定到具体 version，不支持 range |
| **外部系统引用** | `id`, `interface` | — | 接入点 + 接口描述 |

引用校验（保存 definition 时）：
- 所有引用的 `id` 必须真实存在（目标对象已注册）
- `version_constraint` 必须能解析到至少 1 个真实 version
- `schema_ref.version` 必须存在

---

## 4. 字段约束

- **必填字段**：`id`, `version`, `task`, `result`, `process` 不可省略
- **至少 1 条 beeline**：`process.beelines` 至少 1 条
- **beeline 覆盖**：`task` 列表里每条 task 必须被至少 1 条 beeline 的 `applies_to` 覆盖（不留死区）
- **metric 命名**：`quality` / `latency` / `cost` 三类分别命名，命名空间隔离
- **version 演进**：definition version 是递增序列号——修改 definition 不影响已发布 release（详见 beebox-design.md §3.1.5）

---

## 5. 跟设计稿的对照

| schema 字段 | design 章节 |
|---|---|
| `task` | §3.1.2 履约对象 |
| `process` | §3.1.2 履约过程 |
| `result` | §3.1.2 履约结果（对应 §2.1 单次履约）|
| `metrics` | §3.1.2 履约度量（对应 §2.1 履约指标）|
| 引用 `id+version` 模式 | §3.1.3 引用机制 |
| `version` / 序列号 | §3.1.5 与 release 的关系（definition 修改不影响已发布 release）|
