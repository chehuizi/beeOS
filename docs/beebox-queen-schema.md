# BeeBox queen schema

> **状态**：v0.1 草稿 · 修订中
> **日期**：2026-09-15
> **对应**：[beebox-design.md §1.4 边界关系 · queen](./beebox-design.md#114-边界关系)

queen 的结构化 schema 定义。**queen 是 beeBox 的自治运营智能体**——在履约合同和授权策略范围内，主动管理 beeBox 的任务流动 / 运行异常 / 持续改善。

1 个 beeBox 指定 1 个 queen（1:1，§1.4 边界关系）。queen 通过 runtime 平台提供的智能体引擎执行——queen 自己只定义"做什么 / 不做什么 / 怎么做"，具体执行由 runtime 平台实现。

---

## 1. 顶层结构

```yaml
queen:
  # ---- 基础元信息 ----
  queen_id: string              # queen 唯一标识
  version: integer              # 递增序列号
  description: string          # 人类可读说明

  # ---- 合同约束 ----
  contract_ref: string          # 引用 beeBox 的履约合同（beeBox definition 顶层 queen 字段同源）

  # ---- 授权策略 ----
  authorization:
    task_routing: enum          # 任务流动：allow / observe_only / off
    exception_handling: enum    # 运行异常：allow / observe_only / off
    continuous_improvement: enum # 持续改善：allow / observe_only / off

  # ---- 自治规则 ----
  rules:
    task_routing: object        # 任务流动规则（路由策略 / backpressure / WIP 限制）
    exception_handling: object  # 运行异常规则（检测条件 / 处理动作）
    continuous_improvement: object  # 持续改善规则（监控指标 / 改进触发条件 / 改进动作）
```

---

## 2. 字段说明

### 基础元信息

- **queen_id**——queen 的唯一标识（beeBox 范围唯一）
- **version**——queen 自身的版本（独立演进，不跟 release 走）
- **description**——人类可读说明

### 合同约束

- **contract_ref**——queen 受 beeBox 履约合同约束（同 beeBox definition 的 contract 一致）
- queen 任何决策不能突破合同承诺（比如合同承诺"质量 ≥ 99%"，queen 不能主动调成 95%）

### 授权策略

每类自治能力有 3 个授权档位：

| 授权 | 含义 |
|---|---|
| **allow** | queen 主动执行（自主决策 + 落地）|
| **observe_only** | queen 观察 + 建议，不自动落地（人工确认才执行）|
| **off** | queen 不参与（对应能力关闭）|

**3 类自治能力**：
- **task_routing**——管理 task 流动（怎么路由 / 排队 / 限流）
- **exception_handling**——处理运行异常（异常检测 / 自动处理动作）
- **continuous_improvement**——基于运行数据调优（调整 definition / beeline / 配置）

### 自治规则

每类自治能力对应 1 个 `rules` 字段——定义具体怎么做（业务化规则描述）：

- **task_routing.rules**——task 路由策略、WIP 限制阈值、backpressure 触发条件
- **exception_handling.rules**——异常检测条件、自动处理动作（重试 / 升级 / 隔离）
- **continuous_improvement.rules**——监控指标、改进触发条件（异常率超阈值 / 时延恶化）、改进动作（调 definition 草稿 / 调 beeline / 调 config）

---

## 3. 字段约束

- **必填字段**：`queen_id`, `version`, `contract_ref`, `authorization` 不可省略
- **queen_id 唯一**：beeBox 平台内 `queen_id` 唯一
- **authorization 合法**：每类授权只能是 `allow` / `observe_only` / `off` 之一
- **contract_ref 存在**：引用必须指向真实存在的合同（beeBox definition 顶层 queen 字段同源）
- **rules 可选**：每类 rules 字段可选（不填 = 该类无具体规则，按平台默认行为）

---

## 4. queen 跟其他对象的关系

| 对象 | 关系 |
|---|---|
| **beeBox** | 1 个 beeBox 1 个 queen（1:1，§1.4） |
| **beeBox definition** | definition 顶层 `queen` 字段引用 `queen_id` |
| **beeBox instance** | queen 管理 instance 池（queen 通过 runtime 平台 API 操作 instance）|
| **履约合同** | queen 受 contract_ref 约束（不能突破合同）|
| **runtime 平台** | queen 通过 runtime 智能体引擎执行（平台实现）|

---

## 5. queen 的归属

queen 是 beeOS 平台的 **1 类独立对象**——有自己的定义 / 版本 / 演进，跟 beeline / schema / bee 同级。

但 queen 跟其他对象不同：

- **beeline / schema / bee**——被 beeBox 引用（设计层引用关系）
- **queen**——管理 beeBox（运营关系，queen 反过来"知道" beeBox）

queen 不在"beeBox 设计层 / 运行时"二分里——**queen 是运营层**，在设计层和运行时之上：

```
┌─────────────────────────┐
│         queen            │  运营层（自治决策）
├─────────────────────────┤
│  workshop / kanban 面板   │  产品入口（人工入口）
├─────────────────────────┤
│  definition / release    │  设计层
├─────────────────────────┤
│  instance / runtime      │  运行时
└─────────────────────────┘
```

---

## 6. 跟设计稿的对照

| queen 字段 | design 章节 |
|---|---|
| `queen_id` | §1.4 边界关系（1 个 beeBox 1 个 queen）|
| `contract_ref` | §2.1 履约合同（queen 受合同约束）|
| `authorization.task_routing` | §1.3 持续流动（拉动 / WIP 限制）|
| `authorization.exception_handling` | §3.5.4 异常处理 |
| `authorization.continuous_improvement` | §1.3 持续改善 |
| `rules.task_routing` | §3.5.1 task run 触发（路由部分由 queen 自治）|
| `rules.exception_handling` | §3.5.4 异常处理（具体规则由 queen 定义）|
| `rules.continuous_improvement` | §1.3 持续改善（看板数据驱动的改进触发）|