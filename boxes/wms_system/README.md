# WMS 系统档案盒（wms_system_box）

> 把「真实在跑的那套 WMS 是什么样」写成一份带出处的档案。

## 业务定位

它是 WMS 域的**唯一汇点**：五只过程盒的产出最终都要跟这份档案对上。

**它存结果，不存过程。** 需求、设计、开发、测试、运维各自做自己那一段的过程，
过程产物留在它们自己那里；本盒只沉淀结果——这套系统由什么构成、
对外契约是什么、哪些部分此刻真的可观测。

## 档案里没有一个裸数字

每条维度自带 `status`，三选一：

| status | 含义 |
|---|---|
| `observed` | 这次真的探到了 |
| `declared_from_baseline` | 只有声明值、这次没能验证——必须写出处 |
| `unverifiable` | 拿不到——必须写为什么 |

今天 6 条维度里只有 3 条能观测，部署版本和外部依赖是以现在的系统形态查不到的——
那句话**说在档案里**，不说在档案外。

## 3 跳 beeline

```
system_intake → system_probe → system_profile_packager
```

## 判据与例外

**5 条判据**：

| 判据 | 口径 |
|---|---|
| `health_endpoint_reachable` | 健康端点通 |
| `service_status_up` | 服务状态是 up |
| `database_reachable` | 数据库连得上 |
| `readonly_probe_answer_rate` | 只读探针应答率（阈值 100）|
| `aspect_status_complete` | 每条维度都带 status（没有裸数字）|

**3 条例外**：健康端点不通 → 升级；数据库连不上 → 升级；
探针应答率为 0 → 不交付。

## 文件

- `__init__.py` — 入口（`get_definition`）
- `schemas.py` — 4 个 schema
- `definition.py` — BeeBoxDefinition 实例
- `baseline/` — 盒子自带的基线资产（`system_baseline.json`）
- `runner.py` — 3 个 op 的 handler
- `measure.py` — 指标采集
- `beeline.py` — beeline_wms_system_v2
