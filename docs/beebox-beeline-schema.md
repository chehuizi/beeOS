# BeeBox beeline schema

> **状态**：v0.1 草稿 · 修订中
> **日期**：2026-09-13
> **对应**：[beebox-design.md §1.1.4 beeline](./beebox-design.md#114-边界关系) · [beebox-definition-schema.md](./beebox-definition-schema.md)

beeline 的结构化 schema 定义。**beeline 是 1 类任务的标准作业路线，本质是 1 张有向图**——节点是 operation（由 1...N 个组成），`next` 字段是节点之间的有向边（支持顺序 / 并发 / 分支）；每个 operation 各自调用 bee / 外部系统完成具体动作。

beeline 独立维护，**不归 beeBox definition 管**——definition 只引用 beeline_id + version；operation 内部用谁、怎么编排都是 beeline 自己的事。

---

## 1. 顶层结构

```yaml
beeline:
  # ---- 基础元信息 ----
  id: string                 # beeline 唯一标识
  version: integer           # 递增序列号
  description: string       # 人类可读说明

  # ---- operation 编排 ----
  operations:                # 1...N 个 operation（按 next 链连接）
    - op_id: string         # operation 在该 beeline 内唯一
      type: string          # operation 类型（业务化标识，如 "validate_input" / "transform" / "db_write"）
      input_from: string     # 数据来源：external = task 输入；或上一个 op_id
      input: string         # input 数据结构引用（schema id）
      output: string        # output 数据结构引用（schema id）
      next:                 # 下一个 operation（1 个=顺序；多=并发+条件）
        - op_id: string
          when: string      # 条件表达式（可选，无 = 无条件顺序）

      # operation 内部资源（可选）
      bee:                  # operation 用的 bee
        type: string
        params: object
      external_system:      # operation 调用的外部系统
        id: string
        interface: string
```

---

## 2. 编排关系

beeline 是一张**有向图**——operation 是节点，`next` 是边：

| 编排 | next 怎么写 |
|---|---|
| **顺序** | 1 个 `next` 元素，无 `when` |
| **并发（fan-out）** | 多个 `next` 元素，无 `when` |
| **分支** | 多个 `next` 元素，每个带 `when`（互斥）|
| **汇聚（fan-in）** | 多个 op 都有 `next` 指向同一 op |

**示例**（"订单接收"：校验 + 转换 + 入库，校验失败走错误处理）：

```yaml
operations:
  - op_id: validate
    type: validate_input
    input_from: external
    input: schema_order_request
    output: schema_validate_result
    next:
      - op_id: transform
        when: "result == ok"
      - op_id: error_handler
        when: "result == fail"
    bee:
      type: order_validator

  - op_id: transform
    type: data_transform
    input_from: validate
    input: schema_validate_result
    output: schema_transformed_order
    next:
      - op_id: persist
    bee:
      type: order_transformer

  - op_id: persist
    type: db_write
    input_from: transform
    input: schema_transformed_order
    output: schema_persisted_order
    bee:
      type: db_writer
      params:
        table: orders

  - op_id: error_handler
    type: notify
    input_from: validate
    input: schema_validate_result
    bee:
      type: notifier
```

---

## 3. 字段约束

- **必填字段**：`id`, `version`, `operations` 不可省略
- **至少 1 个 operation**：`operations` 至少 1 条
- **op_id 唯一**：`operations` 列表内 `op_id` 唯一
- **next 引用存在**：每个 `next.op_id` 必须指向 `operations` 列表里真实存在的 op
- **无环**：operation 之间的 `next` 链不能形成环（必须是有向无环图）
- **input_from 合法**：`external` 或 op_id（必须在 operations 列表里）
- **每 op 至少 1 资源**：`bee` / `external_system` 至少填 1 个（否则 operation 没东西可调）
- **beeline 引用一致性**：beeline_id 唯一（不重复注册）

---

## 4. 跟设计稿的对照

| beeline 字段 | design 章节 |
|---|---|
| `id` / `version` | §1.1.4 beeline 概念 |
| `operations[].next` | §1.1.4 beeline "1...N 个 operation 组成的有序结构（支持顺序 / 并发 / 分支）" |
| `operations[].bee` | §1.1.4 operation "input / output / type 已声明" |
| `operations[].external_system` | beeline 内部实现细节（无对应设计稿章节，beeline 自己管）|
| `next` 编排 | 跟 §2.2 task run "按 beeline 路线执行对应的 operation 步骤" 对应 |
