"""
kanban.cli - 履约盒子运行面板（CLI）

按 [beebox-design.md §4.2] 实现：
- 只读 + 运维操作
- 显示 runtime / instance / task run / operation / 异常 / 指标
- 实时性 + 历史回溯

CLI 输出（终端友好的纯文本）：
  beeOS Kanban — Operational Dashboard
  ====================================
  
  Boxes Registered
    - inventory_shortage (Operations Line)
    - business_modeling (Software Line)
  
  Status Counts
    completed:  10
    failed:      2
  
  Acceptance Outcomes
    accepted:                   8  (80.0%)
    rejected:                   2  (20.0%)
  
  Recent Task Runs (last 10)
    task_run_id  box                 status     acceptance   duration  ops
    -----------  ------------------  ---------  -----------  --------  ---
    a1b2c3d4     inventory_shortage  completed  accepted     120ms     3
    ...
  
  Active Exceptions
    ⚠ task_run d4e5f6: status=failed, exceptions=1
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from runtime.store import TaskRunStore


def _print_section(title: str) -> None:
    print()
    print(f"  {title}")
    print(f"  {'-' * len(title)}")


def render_dashboard(store: TaskRunStore) -> str:
    """渲染完整看板为纯文本"""
    lines: list[str] = []
    lines.append("beeOS Kanban — Operational Dashboard")
    lines.append("=" * 38)

    # Boxes
    lines.append("")
    lines.append("  Boxes Registered")
    lines.append("  ----------------")
    boxes = store.list_boxes()
    if boxes:
        for box in boxes:
            lines.append(f"  - {box}")
    else:
        lines.append("  (no boxes yet)")

    # Status counts
    lines.append("")
    lines.append("  Status Counts")
    lines.append("  -------------")
    status_counts = store.count_by_status()
    if status_counts:
        for status, count in sorted(status_counts.items()):
            lines.append(f"  {status:20s}  {count}")
    else:
        lines.append("  (no task runs yet)")

    # Acceptance
    lines.append("")
    lines.append("  Acceptance Outcomes")
    lines.append("  -------------------")
    acc_counts = store.count_by_acceptance()
    if acc_counts:
        for status, count in sorted(acc_counts.items()):
            lines.append(f"  {status:20s}  {count}")
    else:
        lines.append("  (no acceptance results yet)")

    # Aggregate metrics
    lines.append("")
    lines.append("  Aggregate Metrics")
    lines.append("  -----------------")
    metrics = store.aggregate_metrics()
    for key, value in metrics.items():
        lines.append(f"  {key:20s}  {value}")

    # Recent task runs
    lines.append("")
    lines.append("  Recent Task Runs (last 10)")
    lines.append("  --------------------------")
    recent = store.list_recent(limit=10)
    if recent:
        lines.append(f"  {'task_run_id':14s}  {'box':18s}  {'status':10s}  {'acceptance':12s}  {'duration':10s}  {'ops':>4s}")
        lines.append(f"  {'-' * 14}  {'-' * 18}  {'-' * 10}  {'-' * 12}  {'-' * 10}  {'-' * 4}")
        for r in recent:
            dur = r['duration_ms']
            dur_str = f"{dur}ms" if dur > 0 else "<1ms"
            lines.append(
                f"  {r['task_run_id'][:12]:14s}  "
                f"{r['box_id']:18s}  "
                f"{r['status']:10s}  "
                f"{(r['acceptance_status'] or 'pending'):12s}  "
                f"{dur_str:>10s}   "
                f"{r['op_count']:>4d}"
            )
    else:
        lines.append("  (no task runs yet)")

    # Active exceptions
    lines.append("")
    lines.append("  Active Exceptions (last 5)")
    lines.append("  --------------------------")
    exceptions = store.list_exceptions(limit=5)
    if exceptions:
        for r in exceptions:
            lines.append(
                f"  ⚠ task_run {r['task_run_id'][:8]}: "
                f"box={r['box_id']}, status={r['status']}, "
                f"exceptions={r['exception_count']}"
            )
    else:
        lines.append("  (no active exceptions)")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="beeOS Kanban CLI dashboard")
    parser.add_argument(
        "--store",
        default="logs/task_runs.jsonl",
        help="TaskRun JSONL store path",
    )
    parser.add_argument(
        "--recent",
        type=int,
        default=10,
        help="Number of recent task runs to show",
    )
    args = parser.parse_args(argv)

    store = TaskRunStore(path=args.store)
    print(render_dashboard(store))
    return 0


if __name__ == "__main__":
    sys.exit(main())