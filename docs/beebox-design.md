# beeBox 设计 v0.1

> **状态**：v0.1 草稿 · 修订中
> **日期**：2026-09-11
> **定位**：beeBox = 持续交付 1 个明确业务结果的数字精益工作单元

---

## 0. 一句话

beeBox 是持续交付 1 个明确业务结果的数字精益工作单元。

---

## 1. 产品定位

### 1.1 第一性定义

> **beeBox 是持续交付 1 个明确业务结果的数字精益工作单元。**

拆开看 4 个关键词：

- **业务结果**——可被验收的产出（不是任务、不是流程、不是操作）
- **持续交付**——不是一次性完成，是持续地、可重复地交付
- **数字精益工作单元**——精益 cell 的数字版本，装在机器上的、可远程观察的
- **单元**——1 个独立可安装、可运行、可度量、可改善的产品单元

### 1.2 4 个产品属性

| 属性 | 含义 |
|---|---|
| **可安装** | 一份包安装出 1 个可工作的 beeBox |
| **可运行** | 启动后持续接收触发、持续产出业务结果 |
| **可度量** | 运行时数据可观测、可统计 |
| **可持续改善** | 运行时数据反哺业务改进（看板 → 调优 → 验证）|

### 1.3 3 个核心价值

| 价值 | 含义 |
|---|---|
| **业务结果导向** | 1 个 beeBox 围绕 1 个明确业务结果（不是任务、不是流程、不是操作）|
| **持续流动** | 通过拉动 / WIP 限制 / 队列透明 / 瓶颈暴露，持续减少等待与积压 |
| **持续改善** | 运行时数据反哺业务改进（看板数据 → 调优 → 验证）|

### 1.4 边界关系

| 概念 | 范围 | 关系 |
|---|---|---|
| **operation** | 1 个不可再分的加工动作（input / output / type 已声明）| 最小执行单元（原子工序）|
| **beeline** | 1 类任务的标准作业路线 | 1...N 个 operation 的有序序列 |
| **beeBox** | 1 个可独立运营和验收的数字工作 cell | 1...N 条 beeline 组成 |
| **企业价值流** | 端到端业务流 | 1...N 个 beeBox 串联 |

例子（电商订单履行）：
- beeBox 1：订单接收（验收 = 入库率）
- beeBox 2：仓库分拣（验收 = 准确率）
- beeBox 3：物流发货（验收 = 及时率）
- 3 个 beeBox 串联 = 订单履行企业价值流

### 1.5 4 层模型

| 层级 | 含义 | 关系 |
|---|---|---|
| **beeBox definition** | 设计蓝图（业务结果定义 / 验收标准 / 改善指标 / beeline 列表）| 1 def → N release |
| **beeBox release** | 一次不可变、可交付的版本 | 1 release → N instance |
| **beeBox instance** | 安装后正在运行的实例 | 1 instance → N task run |
| **task run** | 一次具体业务交付 | instance 内的一次执行（走完 1 条 beeline = N 个 operation 步骤）|

**关键概念区分（beeBox release vs beeline_version）**：

| 维度 | 含义 | 类比 |
|---|---|---|
| `beeBox release` | beeBox **运行时**的版本（代码 / 二进制 / 部署包）| MySQL server release（软件）|
| `beeline_version` | beeBox 内**数据资产**（工艺路线）的版本 | MySQL database schema version（数据）|

> 两个版本号**正交**——`beeline_version` 变化不需要 `beeBox release` 变化，反之亦然。

**版本绑定（不可变性原则）**：
- task run **创建时**绑定（snapshot）`beeBox release` + `beeline_version`；bind 之后不再变
- 绑定范围 = 整条 beeline（含 N 个 operation 版本）—— operation 版本隐式跟随 `beeline_version`
- instance 升级后只影响新创建的 task run；运行中的 task run 继续用原版本（不被升级打断）
- 升级 `beeline_version` = 仅数据资产升级，`beeBox release` 编号不变
- 升级 `beeBox release` = 仅运行时升级，已存在的 `beeline_version` 不变（除非该 release 内含 beeline 变更）
- 回滚 = 部署上一个 release 或回退 `beeline_version`；不改变已 bind task run 的版本（保持 in-flight 不变性）

**审计字段**（每个 task run 必含）：
- `task_run.beeBox_release` — 任务创建时的 beeBox release 标识
- `task_run.beeLine_version` — 任务创建时的 beeline 版本标识

### 1.6 交付合同

每个 beeBox 都带一份"交付合同"：

- **业务结果定义**——承诺交付什么（可被验收的产出）
- **验收标准**——怎么算"做完了"（量化指标 + 抽样方法）
- **改善指标**——怎么算"做得好"（运行效率 / 异常率 / 时延）
- **例外条款**——什么情况不交付 / 退回（异常 / 失败 / 超出范围时的回退路径）

### 1.7 跟"非运行体"的关键差异

| 维度 | 非运行体（definition / release / 文档 / 设计图 / 配置） | **beeBox instance（运行体）** |
|---|---|---|
| 形态 | 静态描述 / 版本制品 | 在跑 |
| 触发 | 人工启动 | 持续接收触发 |
| 流动 | 不流动 | 物料按工艺路线自动流转 |
| 反馈 | 无 | 运行时数据 → 反馈到改善 |
| 异常 | 靠人盯 | 异常立刻暴露（看板）+ 自働化回流 |

### 1.8 三个对立面（澄清 beeBox 不是什么）

| 对比对象 | beeBox 不一样在哪 |
|---|---|
| ❌ 业务流程文档 / SOP | SOP 是"描述跑法"；beeBox 是"持续交付业务结果" |
| ❌ 通用工作流引擎（Camunda / Airflow）| 工作流引擎只编排任务、不承诺业务结果；beeBox 自带业务结果定义 + 验收标准 |
| ❌ RPA（机器人流程自动化）| RPA 模拟"人在哪个软件点哪里"；beeBox 编排"业务怎么流动、交付什么结果"——RPA 改操作，beeBox 改业务 |
