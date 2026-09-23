"""
runtime.store - TaskRun JSONL 持久化 + 查询

设计要点：
- JSONL 追加写（每行一个 task_run 序列化记录）
- 启动时从文件加载全部历史记录（in-memory cache）
- 写时同步落盘（保证持久性）
- 查询接口：list_recent / count_by_status / list_exceptions / aggregate_metrics

Record 形状（精简版，比完整 TaskRun 小）：
  task_run_id, box_id, beeline_id, status, acceptance_status,
  started_at, finished_at, op_count, exception_count, duration_ms
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from runtime.models import TaskRun, TaskRunStatus, AcceptanceStatus


def task_run_to_record(task_run: TaskRun, box_id: str, acceptance_status: Optional[str] = None) -> dict[str, Any]:
    """TaskRun → 精简持久化 record"""
    duration_ms = 0
    if task_run.identity.started_at and task_run.identity.finished_at:
        duration_ms = int((task_run.identity.finished_at - task_run.identity.started_at).total_seconds() * 1000)

    exception_count = sum(
        1 for e in task_run.event_log.entries
        if e.event == "op_finish" and (e.detail or {}).get("status") == "failed"
    )

    return {
        "task_run_id": task_run.identity.task_run_id,
        "box_id": box_id,
        "beeline_id": task_run.identity.beeline_id,
        "beeline_version": task_run.identity.beeline_version,
        "intent": task_run.identity.intent,
        "originator": task_run.identity.originator,
        "status": task_run.status.value,
        "acceptance_status": acceptance_status,
        "started_at": task_run.identity.started_at.isoformat() if task_run.identity.started_at else None,
        "finished_at": task_run.identity.finished_at.isoformat() if task_run.identity.finished_at else None,
        "created_at": task_run.identity.created_at.isoformat(),
        "op_count": len(task_run.operations),
        "exception_count": exception_count,
        "duration_ms": duration_ms,
    }


class TaskRunStore:
    """TaskRun JSONL 持久化 + 查询

    用法：
        store = TaskRunStore(path="logs/task_runs.jsonl")
        store.append(task_run, box_id="inventory_shortage", acceptance_status="accepted")
        recent = store.list_recent(limit=10)
    """

    def __init__(self, path: str | Path = "logs/task_runs.jsonl") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._cache: list[dict[str, Any]] = []
        self._load()

    def _load(self) -> None:
        """启动时从文件加载"""
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    self._cache.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    def append(
        self,
        task_run: TaskRun,
        box_id: str,
        acceptance_status: Optional[str] = None,
    ) -> dict[str, Any]:
        """追加一条 task run 记录（同步落盘）"""
        record = task_run_to_record(task_run, box_id, acceptance_status)
        self._cache.append(record)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return record

    def list_recent(self, limit: int = 20, box_id: Optional[str] = None) -> list[dict[str, Any]]:
        """最近的 task run（倒序）"""
        records = self._cache
        if box_id is not None:
            records = [r for r in records if r["box_id"] == box_id]
        # 按 created_at 倒序
        return sorted(records, key=lambda r: r["created_at"], reverse=True)[:limit]

    def count_by_status(self) -> dict[str, int]:
        """按 status 统计"""
        return dict(Counter(r["status"] for r in self._cache))

    def count_by_acceptance(self) -> dict[str, int]:
        """按 acceptance status 统计"""
        return dict(Counter(
            r["acceptance_status"] or "pending"
            for r in self._cache
        ))

    def list_exceptions(self, limit: int = 10) -> list[dict[str, Any]]:
        """含异常的 task run"""
        return sorted(
            [r for r in self._cache if r["exception_count"] > 0 or r["status"] == "failed"],
            key=lambda r: r["created_at"],
            reverse=True,
        )[:limit]

    def aggregate_metrics(self, box_id: Optional[str] = None) -> dict[str, Any]:
        """聚合统计

        Args:
            box_id: 可选，按 box_id 过滤
        """
        records = self._cache
        if box_id is not None:
            records = [r for r in records if r["box_id"] == box_id]

        total = len(records)
        if total == 0:
            return {"total": 0}

        completed = [r for r in records if r["status"] == "completed"]
        failed = [r for r in records if r["status"] == "failed"]
        accepted = [r for r in records if r["acceptance_status"] == "accepted"]
        rejected = [r for r in records if r["acceptance_status"] == "rejected"]

        durations = [r["duration_ms"] for r in completed if r["duration_ms"] > 0]
        avg_duration = sum(durations) / len(durations) if durations else 0

        return {
            "total": total,
            "completed": len(completed),
            "failed": len(failed),
            "accepted": len(accepted),
            "rejected": len(rejected),
            "acceptance_rate": f"{len(accepted) / total * 100:.1f}%" if total > 0 else "0.0%",
            "failure_rate": f"{len(failed) / total * 100:.1f}%" if total > 0 else "0.0%",
            "avg_duration_ms": int(avg_duration),
        }

    def list_boxes(self) -> list[str]:
        """所有出现过的 box_id"""
        return sorted({r["box_id"] for r in self._cache})