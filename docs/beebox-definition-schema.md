# BeeBox definition schema

> **状态**：v0.1 草稿 · 修订中
> **日期**：2026-09-13
> **对应**：[beebox-design.md §3.1 BeeBox definition](./beebox-design.md#31-beebox-definition)

beeBox definition 的结构化 schema 定义。**这是接口规格，不是设计稿**——讲"字段长什么样、怎么校验、怎么引用"，不讲"为什么这样设计"。设计原理见 `beebox-design.md` §3.1。

---

## 1. 顶层结构

definition 顶层包含**基础元信息**和**履约生命周期 4 块**：

| 块 | 含义 | 字段 |
|---|---|---|
| **基础元信息** | beeBox 基础标识 | `id` / `version` / `description` / `queen` |
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
  queen: string              # beeBox 责任主体（1 个 beeBox 1 个 queen）

  # ---- 履约对象：beeBox 接收什么 task ----
  task:                      # 1...N 类 task
    - type: string           # task 类型标识
      task_schema: string    # task 的数据结构
      beeline_id: string     # 1:1 绑定的 beeline
      beeline_version: integer  # 绑定的 beeline 的具体 version
      trigger: string        # 触发条件描述

  # ---- 履约过程：怎么履约 ----
  process:
    # 流程：1...N 条 beeline（独立维护，可被多 task 引用）
    beelines:
      - id: string
        version: integer
      # 内部 operation 用什么 bee / 外部系统归 beeline 自己的定义管

  # ---- 履约结果：交付什么业务结果 ----
  result:
    type: string             # 业务结果类型标识
    result_schema: string    # result 的数据结构
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

引用按被引用对象自身的字段形式——被引用对象是什么字段，引用就用什么字段；definition 不持有被引用对象的实现。

| 引用类型 | 必填字段 | 选填字段 | 备注 |
|---|---|---|---|
| **beeline 引用** | `id`, `version` | — | beeline 有 id + version（递增序列号）；引用发生在 task 块（task 1:1 绑定 1 条 beeline）|

引用校验（保存 definition 时）：
- beeline 引用：`id` + `version` 必须真实存在

---

## 4. 字段约束

- **必填字段**：`id`, `version`, `queen`, `task`, `result`, `process` 不可省略
- **queen 唯一**：1 个 beeBox 1 个 queen（`queen` 是 beeBox 的责任主体）
- **task 必填 beeline**：`task` 列表每条都必填 `beeline_id` + `beeline_version`（1:1 绑定）
- **beeline 引用存在**：`task.beeline_id` + `beeline_version` 必须在 `process.beelines` 里真实存在
- **metric 命名**：`quality` / `latency` / `cost` 三类分别命名，命名空间隔离
- **version 演进**：definition version 是递增序列号——修改 definition 不影响已发布 release（详见 beebox-design.md §3.1.5）

---

## 5. 跟设计稿的对照

| schema 字段 | design 章节 |
|---|---|
| `queen` | §1.4 边界关系（beeBox 1:1 owner）· §3 实现设计开头说明 |
| `task` | §3.1.2 履约对象 |
| `process` | §3.1.2 履约过程 |
| `result` | §3.1.2 履约结果（对应 §2.1 单次履约）|
| `metrics` | §3.1.2 履约度量（对应 §2.1 履约指标）|
| 引用机制（按被引用对象字段形式） | §3.1.3 引用机制 |
| `version` / 序列号 | §3.1.5 与 release 的关系（definition 修改不影响已发布 release）|
