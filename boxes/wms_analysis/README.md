# WMS 效果分析盒（wms_analysis_box）

> 上线之后回头验需求——拿真实运行数据，跟当初那条需求声明的预期值逐项比。

## 业务定位

链条的终点，也是唯一对**业务价值**负责的盒子。

前五只盒子走的是「需求 → 设计 → 开发 → 测试 → 运维」，走完一遍还是没人回答
「线上到底满没满足需求」。这只盒子回答它。

落差直接变成下一轮需求的种子——所以它**反馈给需求盒**（`feedback_into`），
而不是交给需求盒去验。

## 3 跳 beeline

```
analysis_intake → analysis_compare → analysis_packager
```

- `analysis_intake` — 取预期（`expectations`）+ 实测（测试报告 / 运维回写的线上状态）
- `analysis_compare` — 逐条预期比对
- `analysis_packager` — 出分析记录 + 下一轮需求种子

## 判据与例外

**4 条判据**：

| 判据 | 口径 |
|---|---|
| `convergence_rate` | 达标预期数 / 总数 |
| `requirement_traceability` | 每条预期追得到需求 id + 原文句子 + 实测来源 |
| `gap_attribution` | 落差归因三选一，机械判（见下）|
| `next_requirement_seeded` | 有落差时必须存在一条新需求草案指向它 |

**归因怎么机械判**：

| 现场 | 归因 |
|---|---|
| 有测试报告且是红的 | 实现缺陷 |
| 没有测试报告 | 未验证 |
| 测试绿但线上不满足 | 环境 / 数据差异 |

**2 条例外**：一条都没比对 → 不交付；有落差但没播种新需求 → 升级。

## 文件

- `__init__.py` — 入口（`get_definition`）
- `schemas.py` — 6 个 schema
- `definition.py` — BeeBoxDefinition 实例
- `runner.py` — 3 个 op 的 handler
- `measure.py` — 指标采集
- `beeline.py` — beeline_wms_analysis_v1
