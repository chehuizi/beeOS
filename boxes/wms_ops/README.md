# WMS 运维履约盒（wms_ops_box）

> 管**上线之后**的事：变更审批、发布执行、故障处置、回滚能力、状态回写。

## 业务定位

链条的第五环（旁挂）。它回答的是：**现在实际是什么样**。

前面几只盒子都在说「应该是什么样」，它是唯一一个**读得到生产数据**的盒子。

## 3 跳 beeline

```
ops_intake → ops_handle → ops_packager
```

- `ops_intake` — 变更单 / 告警 / 回滚目标入队
- `ops_handle` — 变更准入核对 + **把线上真实状态回写进系统档案**
- `ops_packager` — 组装运维记录

它**不执行生产发布**：重启生产 WMS 是危险动作，必须人工确认。

## 判据与例外

**5 条判据**：

| 判据 | 口径 |
|---|---|
| `change_approval_rate` | 本次变更有审批记录（审批人 + 时间）|
| `rollback_readiness` | 上一版制品仍在 + 回滚路径可达 |
| `incident_documented` | 每条告警都有处置记录（处置人 + 动作 + 时间）|
| `runtime_state_recorded` | 线上真实状态已回写进档案，带 `source_task_run_id` |
| `ops_determined` | 这次真的核对出了东西 |

**2 条例外**：回滚不可用且有变更 → 不交付；告警数超阈值 → 升级。

## 文件

- `__init__.py` — 入口（`get_definition`）
- `schemas.py` — 6 个 schema
- `definition.py` — BeeBoxDefinition 实例
- `runtime_map.py` — 线上运行态 → 档案维度的映射
- `rollback.py` — 回滚能力核对
- `wms_reader.py` — 读线上真实状态
- `runner.py` — 3 个 op 的 handler
- `measure.py` — 指标采集
- `beeline.py` — beeline_wms_ops_v1
