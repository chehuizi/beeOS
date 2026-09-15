# BeeBox runtime schema

> **状态**：v0.1 草稿 · 修订中
> **日期**：2026-09-15
> **对应**：[beebox-runtime-design.md](./beebox-runtime-design.md) · [beebox-design.md §3.3 instance](./beebox-design.md#33-beebox-instance) · §3.4 运行时实现

runtime 平台的资源对象 schema 定义。runtime 平台是 beeOS 平台的基础设施层，提供 environment / deployment 两类**声明式资源对象**——platform 管理员通过声明这两类对象，告诉 runtime 平台"在哪里跑什么版本"。trigger / executor / worker / queen engine / monitor / audit 是平台内部组件，没有独立 schema。

---

## 1. 包含对象

runtime schema 包含 2 类对象：

| 对象 | 含义 | 谁声明 |
|---|---|---|
| **runtime_environment** | 实际运行环境 + 隔离边界（dev / staging / prod / 不同云厂商） | platform 管理员 |
| **runtime_deployment** | 某 runtime 版本在 environment 的一次部署 | platform 管理员 / runtime 自动 |

两者关系：runtime_deployment 引用 runtime_environment（deployment 跑在 1 个 environment 内）。

---

## 2. runtime_environment schema

runtime environment 是 runtime 平台在 1 个隔离边界内运行的整体配置——声明"runtime 在哪里跑、跑在哪类环境"。

### 2.1 顶层结构

| 块 | 含义 | 字段 |
|---|---|---|
| **基础元信息** | environment 标识 + 用途 | `id` / `type` / `description` |
| **环境属性** | 实际环境的物理属性 | `region` / `cloud_provider` |
| **运行时配置** | runtime 在该 environment 内的具体配置 | `config` |

每块都是 environment 自己的——environment 之间互不引用（隔离边界）。

### 2.2 schema 形状

```yaml
runtime_environment:
  # ---- 基础元信息 ----
  id: string                   # environment 唯一标识（如 env_prod_aliyun_hangzhou）
  type: enum                   # 用途类型：dev / staging / prod
  description: string         # 人类可读说明

  # ---- 环境属性 ----
  region: string               # 物理区域（如 cn-hangzhou / us-west-2）
  cloud_provider: enum         # 云厂商：aws / gcp / aliyun / azure / on_premise

  # ---- 运行时配置 ----
  config:                      # runtime 在该 environment 内的具体配置
    network: object            # 网络配置（VPC / 子网 / 安全组等）
    resource_limits: object    # 资源上限（CPU / 内存 / 磁盘等）
    credentials_ref: string    # 凭证引用（具体形式由 runtime 实现决定）
    ...                        # 其他 runtime 实现需要的配置
```

### 2.3 字段约束

- **必填字段**：`id` / `type` / `region` / `cloud_provider` 不可省略
- **id 唯一**：`id` 在 runtime 平台内全局唯一
- **type 决定边界**：`type` 标识 environment 用途，runtime 平台按 type 实施隔离策略（dev 不允许访问 prod 数据等）
- **type 一旦设定不修改**——environment 用途变更 = 创建新 environment（避免误用）

---

## 3. runtime_deployment schema

runtime deployment 是某 runtime 版本在 1 个 environment 内的 1 次部署——声明"runtime 哪个版本跑在哪个 environment 里"。

### 3.1 顶层结构

| 块 | 含义 | 字段 |
|---|---|---|
| **基础元信息** | deployment 标识 | `id` / `created_at` |
| **部署内容** | 部署到哪个 environment + 哪个 runtime 版本 | `environment_id` / `runtime_version` |
| **部署状态** | deployment 当前生命周期状态 | `status` |

### 3.2 schema 形状

```yaml
runtime_deployment:
  # ---- 基础元信息 ----
  id: string                   # deployment 唯一标识（按 environment_id + runtime_version + 序号派生，如 env_prod__runtime_v1.0.0__deploy_001）
  created_at: timestamp        # 创建时间戳

  # ---- 部署内容 ----
  environment_id: string       # 引用 runtime_environment（按被引用对象字段形式 = id）
  runtime_version: string      # runtime 版本（semver，如 1.2.3）

  # ---- 部署状态 ----
  status: enum                 # 启动中 / 就绪 / 排空中 / 已停止
```

### 3.3 引用机制

| 引用类型 | 必填字段 | 备注 |
|---|---|---|
| **environment 引用** | `environment_id` | environment 没有 version（environment 本身不带版本，运行时配置变更 = 创建新 environment）；deployment 引用只用 `id` |

引用校验（创建 deployment 时）：
- environment 引用：`environment_id` 必须在 runtime_environment 仓库里真实存在

### 3.4 字段约束

- **必填字段**：`environment_id` / `runtime_version` / `status` 不可省略（`id` / `created_at` 由平台生成）
- **environment 引用存在**：`environment_id` 必须在 runtime_environment 仓库里真实存在
- **runtime_version 合法**：`runtime_version` 必须是 runtime 平台支持的版本（runtime 平台内置版本清单）
- **status 状态机**：`启动中 → 就绪 → 排空中 → 已停止`，不允许跳变（异常情况强杀除外）
- **deployment 不可变**——`environment_id` / `runtime_version` 创建后不可修改（修改 = 创建新 deployment，见 §3.5 升级）
- **id 派生规则**：`id` 由 `environment_id + runtime_version + 序号` 派生——同 environment + 同 runtime_version 可创建多个 deployment（升级过渡期）

---

## 4. 升级 / 兼容性

runtime 升级 = 创建新 deployment（修改 deployment 内容 = 创建新 deployment）：

| 操作 | 做法 |
|---|---|
| **runtime 版本升级** | 在同 environment 内创建新 deployment（带新 `runtime_version`）|
| **deployment 配置变更** | 创建新 deployment（不允许原地修改老 deployment）|
| **environment 配置变更** | 创建新 environment（不允许原地修改老 environment）|

**关键点**：
- 老 deployment / 老 environment 永不变——instance 在老 deployment 上跑 release 直到 in-flight 全部完成
- 兼容性边界由 runtime 平台内部给出（runtime platform 决定哪个 release 兼容哪个 deployment）
- 兼容 → 老 instance 可迁移到新 deployment；不兼容 → 老 instance 在老 deployment 上跑完后下线

详见 [beebox-runtime-design.md §6 升级 / 兼容性](./beebox-runtime-design.md#6-升级--兼容性)。

---

## 5. 跟 runtime 内部组件的关系

runtime 平台除了 environment / deployment 两类**声明式资源对象**，还有 5 类**内部组件**——平台运行时跑起来的"内部代码"，没有独立 schema：

| 内部组件 | 职责 | 跟资源对象的关系 |
|---|---|---|
| **trigger handler** | 接收 task（HTTP endpoint / MQ topic / RPC handler / 平台调度） | 由 deployment 实例化；instance 启动后 trigger handler 就绪 |
| **executor** | 按 beeline 编排跑 task run | 由 deployment 实例化；instance 启动后 executor 就绪 |
| **worker pool** | bee 类型的 worker 实例池 | 由 executor 管理；bee 类型来自 release.process.beelines[*].operations[*].bee |
| **queen engine** | 执行 queen 智能体（合同约束 + 授权 + 自治规则） | 由 deployment 实例化；通过 [beebox-queen-schema.md](./beebox-queen-schema.md) 描述的 queen 对象配置 |
| **monitor / audit** | 运行时数据收集 + audit 字段记录 | 由 deployment 实例化；输出数据存 runtime 平台的存储 |

**关键点**：
- 内部组件是**平台能力**，不跟 environment / deployment 一起声明——runtime 平台自带，不需要 platform 管理员配置
- 内部组件的**配置参数**（worker pool 大小、trigger handler 监听端口等）放在 runtime_deployment.config 里（具体形式由 runtime 实现决定）
- 业务侧（beeBox / beeline / queen）只跟内部组件**交互**，不声明内部组件

---

## 6. 跟设计稿的对照

| schema 字段 | design 章节 |
|---|---|
| `runtime_environment` 顶层 | [beebox-runtime-design.md §2.1 环境层](./beebox-runtime-design.md#21-环境层) · [beebox-design.md §3.4.5 多 environment 场景](./beebox-design.md#344-多-environment-场景) |
| `runtime_deployment` 顶层 | [beebox-runtime-design.md §2.2 部署层](./beebox-runtime-design.md#22-部署层) · [beebox-design.md §3.4.1 deployment 启动](./beebox-design.md#341-runtime-deployment-启动) |
| `status` 状态机 | [beebox-design.md §3.4.1 deployment 状态](./beebox-design.md#341-runtime-deployment-启动) · §3.3.3 instance 生命周期 |
| `environment_id` 引用 | [beebox-runtime-design.md §4 跟 beeOS 平台的关系](./beebox-runtime-design.md#4-跟-beeos-平台的关系) |
| 内部组件（trigger / executor / worker / queen engine / monitor / audit） | [beebox-runtime-design.md §2.3-2.5](./beebox-runtime-design.md) · [beebox-design.md §3.5 履约实现](./beebox-design.md#35-履约实现) |
| 升级 / 兼容性 | [beebox-runtime-design.md §6](./beebox-runtime-design.md#6-升级--兼容性) · [beebox-design.md §3.4.3 升级](./beebox-design.md#343-运行时升级) · §3.4.4 兼容性边界 |