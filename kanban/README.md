# kanban - 履约盒子运行面板（CLI + Web）

> 按 [beebox-design.md §4.2] 实现的运行面板（PoC 4 CLI 版 + PoC 5 Web 版）。
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
  web.py        # Web 看板（python -m kanban.web，HTTP server + HTML + JS）
runtime/
  store.py      # TaskRunStore（JSONL 持久化 + 查询）
```

## CLI 入口

```bash
# CLI 看板（默认读 logs/task_runs.jsonl）
.venv/bin/python -m kanban.cli

# 只看业务建模盒
.venv/bin/python -m kanban.cli --box business_modeling_box

# 自定义 store + recent 数量
.venv/bin/python -m kanban.cli --store path/to/store.jsonl --recent 20
```

## Web 入口

```bash
# 启动 Web 看板（默认 127.0.0.1:8765）
.venv/bin/python -m kanban.web

# 自定义端口 + store
.venv/bin/python -m kanban.web --port 9876 --store logs/task_runs.jsonl

# 外部可访问（不推荐，仅本地开发）
.venv/bin/python -m kanban.web --host 0.0.0.0 --port 8765
```

然后浏览器打开 `http://localhost:8765`

### Web 特性

- **boxes 过滤器**：顶部 chip 一键切换（全部 / 单 box）
- **自动刷新**：JS 每 2 秒 fetch `/api/data`，DOM 增量更新（无 full reload）
- **状态着色**：
  - `completed` = 蓝 / `failed` = 红
  - `accepted` = 绿 / `rejected` = 红 / `pending` = 灰
- **响应式卡片**：status / acceptance / metrics 各为卡片网格
- **表格视图**：recent task runs + active exceptions

### Web API

| 端点 | 用途 |
|---|---|
| `GET /` | HTML 页面 |
| `GET /api/data` | JSON 全量 dashboard 数据 |
| `GET /api/data?box=xxx` | JSON 单 box 数据 |

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
| `aggregate_metrics(box_id)` | 聚合指标（总数 / 通过率 / 平均时长）|
| `list_boxes()` | 所有出现过的 box |

## 限制

- 当前 store 是"调用方主动 append"——executor 本身没自动接入
- 后续可以让 executor 在 task_run 终态时自动落盘
- 没有 instance / runtime 视图（设计稿有，PoC 还没做）
- Web 看板无认证（仅本地开发用）|