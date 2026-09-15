# BeeBox runtime schema

> **状态**：v0.1 草稿 · 修订中
> **日期**：2026-09-15
> **对应**：[beebox-runtime-design.md](./beebox-runtime-design.md) · [beebox-design.md §1.6 运行关系](./beebox-design.md#16-运行关系) · §3.3 instance

BeeBox runtime 的结构化 schema 定义。**runtime 的职责 = 把 release 部署成 instance 并持续运行**——这是用户视角的单一职责，environment / deployment / 健康检查 / 升级 / 兼容性判定都是 runtime 的内部能力，不暴露为独立对象。

---

## 1. runtime 是什么

runtime 是 1 类对象——把 1 个或多个 release 部署成 1...N 个 instance 并持续运行：

| 维度 | 含义 |
|---|---|
| **唯一职责** | 把 release 部署成 instance 并持续运行 |
| **环境标识** | runtime 标识其运行环境（dev / staging / prod / 不同区域）|
| **版本演进** | runtime 升级 = 创建新 runtime（runtime_version 变更）|

1 个 beeOS 平台可有 1...N 个 runtime——按用途 / 区域 / 租户划分：

| 用途 | 典型例子 |
|---|---|
| **dev** | 每个开发者 1 个独立 runtime |
| **staging** | 1 个团队共享 |
| **prod** | 生产服务（可多区域 / 多云厂商）|

---

## 2. 顶层结构

runtime 顶层包含**基础元信息**和**运行时属性**：

| 块 | 含义 | 字段 |
|---|---|---|
| **基础元信息** | runtime 标识 + 用途 | `id` / `type` / `description` |
| **环境属性** | runtime 跑在哪里 | `region` / `cloud_provider` |
| **运行时配置** | runtime 版本 + 具体配置 | `runtime_version` / `config` |
| **部署状态** | runtime 当前生命周期状态 | `status` |

---

## 3. schema 形状

```yaml
runtime:
  # ---- 基础元信息 ----
  id: string                   # runtime 唯一标识（如 runtime_prod_aliyun_hangzhou）
  type: enum                   # 用途类型：dev / staging / prod
  description: string         # 人类可读说明

  # ---- 环境属性 ----
  region: string               # 物理区域（如 cn-hangzhou / us-west-2）
  cloud_provider: enum         # 云厂商：aws / gcp / aliyun / azure / on_premise

  # ---- 运行时配置 ----
  runtime_version: string      # runtime 平台版本（semver，如 1.2.3）
  config:                      # runtime 在该环境内的具体配置
    network: object               # 网络配置（VPC / 子网 / 安全组等）
    resource_limits: object       # 资源上限（CPU / 内存 / 磁盘等）
    credentials_ref: string       # 凭证引用（具体形式由 runtime 平台决定）
    ...                           # 其他 runtime 平台需要的配置

  # ---- 部署状态 ----
  status: enum                 # 启动中 / 就绪 / 排空中 / 已停止
```

---

## 4. 字段约束

- **必填字段**：`id` / `type` / `region` / `cloud_provider` / `runtime_version` / `status` 不可省略
- **id 唯一**：`id` 在 beeOS 平台内全局唯一
- **type 决定边界**：`type` 标识 runtime 用途，runtime 平台按 type 实施隔离策略（dev runtime 不允许访问 prod 数据等）
- **type 一旦设定不修改**——runtime 用途变更 = 创建新 runtime（避免误用）
- **status 状态机**：`启动中 → 就绪 → 排空中 → 已停止`，不允许跳变（异常情况强杀除外）
- **runtime 不可变**——`region` / `cloud_provider` / `runtime_version` / `config` 创建后不可修改（修改 = 创建新 runtime，见 §5 升级）
- **id 派生规则**：`id` 不强制包含 runtime_version（runtime_version 是字段不是 id 一部分）；同一标识下创建 runtime = 创建新 runtime 对象

---

## 5. 升级 / 兼容性

runtime 升级 = 创建新 runtime（修改任何不可变字段 = 创建新 runtime）：

| 操作 | 做法 |
|---|---|
| **runtime 版本升级** | 创建新 runtime（带新 `runtime_version`）|
| **runtime 配置变更** | 创建新 runtime（不允许原地修改老 runtime）|
| **runtime 环境变更** | 创建新 runtime（不允许原地修改老 runtime）|

**关键点**：
- 老 runtime 永不变——instance 在老 runtime 上跑 release 直到 in-flight 全部完成
- 兼容性边界由 runtime 平台内部给出（runtime 决定哪个 release 兼容哪个 runtime_version）
- 兼容 → 老 instance 可迁移到新 runtime；不兼容 → 老 instance 在老 runtime 上跑完后下线

详见 [beebox-runtime-design.md §5 升级 / 兼容性](./beebox-runtime-design.md#5-升级--兼容性)。

---

## 6. 跟 runtime 内部组件的关系

runtime 除了自身对象，还有 5 类**内部组件**——runtime 平台运行时跑起来的"内部代码"，没有独立 schema：

| 内部组件 | 职责 |
|---|---|
| **trigger handler** | 接收 task（HTTP endpoint / MQ topic / RPC handler / 平台调度）|
| **executor** | 按 beeline 编排跑 task run |
| **worker pool** | bee 类型的 worker 实例池 |
| **queen engine** | 执行 queen 智能体（合同约束 + 授权 + 自治规则）|
| **monitor / audit** | 运行时数据收集 + audit 字段记录 |

**关键点**：
- 内部组件是**平台能力**，不跟 runtime 一起声明——runtime 平台自带，不需要 platform 管理员配置
- 内部组件的**配置参数**（worker pool 大小、trigger handler 监听端口等）放在 `config` 里（具体形式由 runtime 平台决定）
- 业务侧（beeBox / beeline / queen）只跟内部组件**交互**，不声明内部组件

---

## 7. 跟设计稿的对照

| schema 字段 | design 章节 |
|---|---|
| `runtime` 顶层（4 块） | [beebox-runtime-design.md §1 runtime 是什么](./beebox-runtime-design.md#1-runtime-是什么) |
| `type` / `region` / `cloud_provider` | [beebox-design.md §1.6 运行关系](./beebox-design.md#16-运行关系) · 多 runtime 场景 |
| `runtime_version` / `config` | [beebox-runtime-design.md §3 配置](./beebox-runtime-design.md#3-配置) |
| `status` 状态机 | [beebox-runtime-design.md §4 状态](./beebox-runtime-design.md#4-状态) |
| 内部组件（trigger / executor / worker / queen engine / monitor / audit） | [beebox-runtime-design.md §2 内部组件](./beebox-runtime-design.md#2-内部组件) · [beebox-design.md §3.5 履约实现](./beebox-design.md#35-履约实现) |
| 升级 / 兼容性 | [beebox-runtime-design.md §5 升级 / 兼容性](./beebox-runtime-design.md#5-升级--兼容性) · [beebox-design.md §3.3.5 跟 runtime 的关系](./beebox-design.md#335-跟-runtime-的关系) |