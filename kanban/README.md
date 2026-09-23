# kanban - 履约盒子运行面板（CLI）

> 按 [beebox-design.md §4.2] 实现的运行面板（最小 PoC：CLI 版）。
> 设计定位：**只读 + 运维操作**（启动/停止/切流），不编辑设计层对象。

## 与 workshop 的区别

| 面板 | 角色 | 对象 | 何时用 |
|---|---|---|---|
| **workshop** | owner / 管理员 | 设计层（definition / release / beeline / schema）| 想改盒子时 |
| **kanban**（本包）| 运维 / 观察者 | 运行时（task run / 异常 / 指标）| 想看盒子情况时 |

## 模块

```
kanban/
  __init__.py
  cli.py        # CLI 看板（python -m kanban.cli）
runtime/
  store.py      # TaskRunStore（JSONL 持久化 + 查询）
```

## 入口

```bash
# CLI 看板（默认读 logs/task_runs.jsonl）
.venv/bin/python -m kanban.cli

# 自定义 store 路径
.venv/bin/python -m kanban.cli --store path/to/store.jsonl

# 自定义 recent 数量
.venv/bin/python -m kanban.cli --recent 20
```

## CLI 输出

```
beeOS Kanban — Operational Dashboard
======================================

  Boxes Registered
  ----------------
  - business_modeling_box
  - order_exception_box

  Status Counts
  -------------
  completed             7

  Acceptance Outcomes
  -------------------
  rejected              7

  Aggregate Metrics
  -----------------
  total                 7
  acceptance_rate       0.0%
  failure_rate          0.0%
  avg_duration_ms       0

  Recent Task Runs (last 10)
  --------------------------
  task_run_id     box                 status      acceptance    duration     ops
  --------------  ------------------  ----------  ------------  ----------  ----
  95a8684d-560    business_modeling_box  completed   rejected            <1ms      7
  ...

  Active Exceptions (last 5)
  --------------------------
  (no active exceptions)
```

## 数据落盘

TaskRunStore 用 JSONL 追加写（每行一条 record）：

```json
{
  "task_run_id": "abc-123",
  "box_id": "order_exception_box",
  "beeline_id": "beeline_inventory_shortage_v3",
  "beeline_version": 12,
  "intent": "handle_order_exception",
  "originator": "orders.api",
  "status": "completed",
  "acceptance_status": "accepted",
  "started_at": "2026-09-23T23:00:00+00:00",
  "finished_at": "2026-09-23T23:00:00.123456+00:00",
  "created_at": "2026-09-23T23:00:00+00:00",
  "op_count": 3,
  "exception_count": 0,
  "duration_ms": 123
}
```

## 手动 record 示例

```python
from runtime.store import TaskRunStore
from runtime import create_task_run, BeelineExecutor, evaluate_acceptance
from boxes.inventory_shortage import get_definition
from beelines.inventory_shortage import get_beeline

box = get_definition()
beeline = get_beeline()

# 跑 task run
tr = create_task_run(...)
tr = BeelineExecutor().execute(tr, beeline, input_data)
eval_result = evaluate_acceptance(tr, box.result)

# 落盘
store = TaskRunStore(path="logs/task_runs.jsonl")
store.append(tr, box_id=box.id, acceptance_status=eval_result.status.value)
```

## 查询接口

| 方法 | 用途 |
|---|---|
| `list_recent(limit, box_id)` | 最近 task run 列表（倒序，可按 box 过滤）|
| `count_by_status()` | 按 status 计数 |
| `count_by_acceptance()` | 按 acceptance status 计数 |
| `list_exceptions(limit)` | 含异常或失败的 task run |
| `aggregate_metrics()` | 聚合指标（总数 / 通过率 / 平均时长）|
| `list_boxes()` | 所有出现过的 box |

## PoC 4 范围 vs 后续

| 当前已实现 | 下一轮 |
|---|---|
| TaskRunStore JSONL 持久化 | Web 看板（HTML + 自动刷新）|
| CLI 渲染（boxes / status / acceptance / metrics / recent / exceptions）| 实时告警（异常率超阈值推 webhook）|
| 手动 store.append 落盘 | 运维操作（启动/停止 instance / 切流）|
| 11 个测试 | instance + runtime 视图 |

## 限制

- 当前 store 是"调用方主动 append"——executor 本身没自动接入
- 后续可以让 executor 在 task_run 终态时自动落盘
- 没有 instance / runtime 视图（设计稿有，PoC 还没做）|