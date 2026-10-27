# WMS 测试履约盒（wms_testing_box）

> 拿开发盒的制品，用 **WMS 业务场景**验它——收货上架、拣货复核、盘点、批次效期。

## 业务定位

链条的第四环。它回答的是：**仓储现场的规则守没守住**。

它验的不是「代码跑没跑起来」，而是「同一 SKU 同库位有没有被占用两次」
「效期先出有没有被绕过」这类**业务规则**。

## 3 跳 beeline

```
test_intake → test_execute → test_packager
```

`test_execute` 跑真实单据流（起隔离验证实例 → 建到货单 → 收货带批次效期 →
上架 → 建出库单 → 分配 → 拣货 → 发货），沿路采实测值。

## 判据与例外

**6 条判据**：

| 判据 | 口径 |
|---|---|
| `requirement_case_coverage` | 每条预期都有对应用例且跑了 |
| `scenario_coverage` | 每个场景都跑了 |
| `no_blocking_defect` | 无红断言 |
| `case_pass_rate` | 用例通过率（阈值 99，量纲是百分数）|
| `verification_determined` | 这次真的验出了东西（有确定结论）|
| `artifact_identified` | 测的是哪一份制品，有 digest 记着 |

**2 条例外**：有未关闭缺陷 → 升级；通过率不足 → 回滚。

## 「没验到」的三种形态不许混成通过

探针有四种结论，**分开报**：

| 结论 | 含义 |
|---|---|
| `pass` | 验到了，且守住了 |
| `fail` | 验到了，且没守住（带定位证据）|
| `not_applicable` | 没有探针 / 没有检查器——**我们还没写** |
| `indeterminate` | 检查器跑了但判不了——**现场没条件** |

`not_applicable` 和 `indeterminate` 都**不是通过**。把它们混成通过，
就是「什么都没验」显示成「需求已满足」——那是最坏的一种输出。

配套：判据 `verification_determined` 专门盯「这次有没有验出东西」，
通过率只在有确定结论的子集上算。

## 环境变量

| 变量 | 作用 |
|---|---|
| `BEEOS_WMS_BASE_URL` | 被测 WMS 的地址 |
| `BEEOS_WMS_MODE` | 被测模式（`live` / `verify`）|

## 文件

- `__init__.py` — 入口（`get_definition`）
- `schemas.py` — 9 个 schema
- `definition.py` — BeeBoxDefinition 实例
- `probes.py` — 探针（每种结论一种，不许混）
- `wms_client.py` — WMS 客户端（真发单据流）
- `runner.py` — 3 个 op 的 handler
- `measure.py` — 指标采集
- `beeline.py` — beeline_wms_testing_v1
