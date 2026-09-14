# BeeBox schema schema

> **状态**：v0.1 草稿 · 修订中
> **日期**：2026-09-14
> **对应**：[beebox-design.md §1.5 definition 描述](./beebox-design.md#15-产品生命周期) · [beebox-definition-schema.md](./beebox-definition-schema.md) · [beebox-beeline-schema.md](./beebox-beeline-schema.md)

schema 的结构化 schema 定义。**schema 是数据形状契约**——被多处引用：`task_schema`（definition 履约对象的 task 数据结构）/ `result_schema`（definition 履约结果的 result 数据结构）/ operation `input` 和 `output`（beeline operation 的输入输出）。

---

## 1. 顶层结构

```yaml
schema:
  # ---- 基础元信息 ----
  id: string                 # schema 唯一标识
  description: string       # 人类可读说明

  # ---- 字段定义 ----
  fields:                    # 1...N 个字段（按业务化列表描述）
    - name: string           # 字段名
      type: string           # 字段类型（业务化标识或 schema 引用）
      required: boolean      # 是否必填（默认 true）
      description: string    # 字段说明（可选）

  # ---- 嵌套结构（可选）----
  # 复杂结构用 type 引用其他 schema id（id 字符串），避免在 schema 里嵌套过深
```

**type 取值约定**：
- **基础类型**：`string` / `number` / `boolean` / `integer` —— 直接用类型名
- **对象类型**：用 `object` 标记，复杂结构用 `properties` 嵌套
- **数组类型**：用 `array` 标记，`items` 描述元素
- **schema 引用**：用 `ref:<schema_id>` 格式引用其他 schema（如 `ref:schema_address`）—— 避免重复定义

---

## 2. 字段约束

- **必填字段**：`id`, `fields` 不可省略
- **id 唯一**：schema 平台内 `id` 唯一（不重复注册）
- **字段名唯一**：`fields` 列表内 `name` 唯一
- **type 合法**：type 取值在基础类型 / `object` / `array` / `ref:<schema_id>` 范围内
- **嵌套 schema 引用存在**：所有 `ref:<schema_id>` 引用必须指向平台里真实存在的 schema

---

## 3. 引用机制

schema 是个**数据/资源对象**（无独立 version），被以下场景引用：

| 引用方 | 引用字段 | 含义 |
|---|---|---|
| beeBox definition | `task_schema` | task 的数据结构 |
| beeBox definition | `result_schema` | result 的数据结构 |
| beeline operation | `input` | operation 输入的数据结构 |
| beeline operation | `output` | operation 输出的数据结构 |

**引用形式**：纯字符串 `schema_id`（不引用 version——schema 是数据/资源对象，无独立 version）。

---

## 4. 示例

**简单 schema**（订单请求）：

```yaml
- id: schema_order_request
  description: 订单请求
  fields:
    - name: order_id
      type: string
      required: true
      description: 订单 ID
    - name: amount
      type: number
      required: true
      description: 订单金额
    - name: customer_id
      type: string
      required: true
    - name: note
      type: string
      required: false
      description: 备注
```

**嵌套 schema**（订单明细，含数组 + schema 引用）：

```yaml
- id: schema_order_detail
  description: 订单明细（含 items）
  fields:
    - name: order_id
      type: string
      required: true
    - name: items
      type: array
      required: true
      items:
        type: ref:schema_order_item
    - name: shipping_address
      type: ref:schema_address
```

---

## 5. 跟其他 schema 的对照

| schema 字段 | 引用方 | 引用方 schema 字段 |
|---|---|---|
| `id` | beebox-definition-schema | `task_schema` / `result_schema` |
| `id` | beebox-beeline-schema | `operations[].input` / `operations[].output` |
| `fields` | 平台实现 | 平台自定义的字段描述（beeOS schema 不规定细节）|
