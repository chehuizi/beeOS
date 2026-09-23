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
from typing import Any, Optional

from runtime.store import TaskRunStore


def _print_section(title: str) -> None:
    print()
    print(f"  {title}")
    print(f"  {'-' * len(title)}")


def render_dashboard(store: TaskRunStore, box_filter: Optional[str] = None) -> str:
    """渲染完整看板为纯文本

    Args:
        store: TaskRunStore 实例
        box_filter: 可选，按 box_id 过滤（只显示该 box 的记录）
    """
    lines: list[str] = []
    title = "beeOS Kanban — Operational Dashboard"
    if box_filter:
        title += f"  [filter: {box_filter}]"
    lines.append(title)
    lines.append("=" * len(title))

    # Boxes
    lines.append("")
    lines.append("  Boxes Registered")
    lines.append("  ----------------")
    all_boxes = store.list_boxes()
    if box_filter:
        boxes = [b for b in all_boxes if b == box_filter]
    else:
        boxes = all_boxes
    if boxes:
        for box in boxes:
            lines.append(f"  - {box}")
    elif not all_boxes:
        lines.append("  (no boxes yet)")
    else:
        lines.append("  (no box matches filter)")

    # Status counts（应用过滤）
    lines.append("")
    lines.append("  Status Counts")
    lines.append("  -------------")
    if box_filter:
        records = [r for r in store._cache if r["box_id"] == box_filter]
        from collections import Counter
        status_counts = dict(Counter(r["status"] for r in records))
    else:
        status_counts = store.count_by_status()
    if status_counts:
        for status, count in sorted(status_counts.items()):
            lines.append(f"  {status:20s}  {count}")
    else:
        lines.append("  (no task runs yet)")

    # Acceptance（应用过滤）
    lines.append("")
    lines.append("  Acceptance Outcomes")
    lines.append("  -------------------")
    if box_filter:
        records = [r for r in store._cache if r["box_id"] == box_filter]
        from collections import Counter
        acc_counts = dict(Counter(r["acceptance_status"] or "pending" for r in records))
    else:
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
    metrics = store.aggregate_metrics(box_id=box_filter)
    for key, value in metrics.items():
        lines.append(f"  {key:20s}  {value}")

    # Recent task runs（应用过滤）
    lines.append("")
    lines.append("  Recent Task Runs (last 10)")
    lines.append("  --------------------------")
    recent = store.list_recent(limit=10, box_id=box_filter)
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

    # Active exceptions（应用过滤）
    lines.append("")
    lines.append("  Active Exceptions (last 5)")
    lines.append("  --------------------------")
    if box_filter:
        exceptions = [
            r for r in store._cache
            if r["box_id"] == box_filter and (r["exception_count"] > 0 or r["status"] == "failed")
        ]
        exceptions = sorted(exceptions, key=lambda r: r["created_at"], reverse=True)[:5]
    else:
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
    parser.add_argument(
        "--box",
        default=None,
        help="Filter to a single beeBox (by box_id, e.g. business_modeling_box)",
    )
    args = parser.parse_args(argv)

    store = TaskRunStore(path=args.store)
    print(render_dashboard(store, box_filter=args.box))
    return 0


if __name__ == "__main__":
    sys.exit(main())