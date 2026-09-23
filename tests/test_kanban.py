"""tests.test_kanban - TaskRunStore + Kanban CLI

覆盖范围：
- TaskRunStore：append / list_recent / count_by_status / count_by_acceptance /
  list_exceptions / aggregate_metrics / list_boxes
- JSONL 持久化（重启后能加载）
- render_dashboard：纯文本输出格式
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from runtime.store import TaskRunStore, task_run_to_record
from runtime import (
    AcceptanceStatus,
    TaskRunStatus,
    BeelineExecutor,
    create_task_run,
    evaluate_acceptance,
)
from boxes.inventory_shortage import get_definition as get_inv_def
from beelines.inventory_shortage import get_beeline as get_inv_beeline
from boxes.modeling import get_definition as get_mod_def
from beelines.modeling import get_beeline as get_mod_beeline
from kanban.cli import render_dashboard


@pytest.fixture
def tmp_store_path(tmp_path: Path) -> Path:
    return tmp_path / "task_runs.jsonl"


@pytest.fixture
def store(tmp_store_path: Path) -> TaskRunStore:
    return TaskRunStore(path=tmp_store_path)


# ============================================================
# TaskRunStore
# ============================================================


class TestTaskRunStore:
    def test_empty_store(self, store: TaskRunStore):
        assert store.list_recent() == []
        assert store.count_by_status() == {}
        assert store.count_by_acceptance() == {}
        assert store.list_exceptions() == []
        assert store.aggregate_metrics() == {"total": 0}
        assert store.list_boxes() == []

    def test_append_writes_to_file(self, store: TaskRunStore, tmp_store_path: Path):
        box = get_inv_def()
        beeline = get_inv_beeline()
        tr = create_task_run(
            "orders.api", "handle_order_exception", box.result.result_schema,
            f"{box.id}@v{box.version}", beeline.id, beeline.version, "rt", "in",
        )
        BeelineExecutor().execute(tr, beeline, {"exception_type": "inventory_shortage"})

        store.append(tr, box_id=box.id, acceptance_status="accepted")

        # 文件被写入
        assert tmp_store_path.exists()
        # 1 行 JSONL
        lines = tmp_store_path.read_text().strip().split("\n")
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["box_id"] == box.id
        assert record["status"] == "completed"

    def test_persistence_across_instances(self, tmp_store_path: Path):
        box = get_inv_def()
        beeline = get_inv_beeline()

        # 实例 1：写入
        s1 = TaskRunStore(path=tmp_store_path)
        tr = create_task_run(
            "o", "i", box.result.result_schema, f"{box.id}@v{box.version}",
            beeline.id, beeline.version, "rt", "in",
        )
        BeelineExecutor().execute(tr, beeline, {"exception_type": "inventory_shortage"})
        s1.append(tr, box_id=box.id, acceptance_status="accepted")
        del s1

        # 实例 2：加载
        s2 = TaskRunStore(path=tmp_store_path)
        records = s2.list_recent()
        assert len(records) == 1
        assert records[0]["box_id"] == box.id

    def test_list_recent_sorted_desc(self, store: TaskRunStore):
        box = get_inv_def()
        beeline = get_inv_beeline()
        for i in range(3):
            tr = create_task_run(
                "o", "i", box.result.result_schema, f"{box.id}@v{box.version}",
                beeline.id, beeline.version, "rt", "in",
            )
            BeelineExecutor().execute(tr, beeline, {"exception_type": "inventory_shortage"})
            store.append(tr, box_id=box.id)

        records = store.list_recent(limit=2)
        assert len(records) == 2
        # 倒序：最新的在前
        assert records[0]["created_at"] >= records[1]["created_at"]

    def test_list_recent_filter_by_box(self, store: TaskRunStore):
        inv_def = get_inv_def()
        inv_beeline = get_inv_beeline()
        mod_def = get_mod_def()
        mod_beeline = get_mod_beeline()

        # 2 inv + 1 mod
        for _ in range(2):
            tr = create_task_run(
                "o", "i", inv_def.result.result_schema, f"{inv_def.id}@v{inv_def.version}",
                inv_beeline.id, inv_beeline.version, "rt", "in",
            )
            BeelineExecutor().execute(tr, inv_beeline, {"exception_type": "inventory_shortage"})
            store.append(tr, box_id=inv_def.id)

        tr = create_task_run(
            "pm", "i", mod_def.result.result_schema, f"{mod_def.id}@v{mod_def.version}",
            mod_beeline.id, mod_beeline.version, "rt", "in",
        )
        BeelineExecutor().execute(tr, mod_beeline, {
            "set_id": "s1", "business_goal": "x",
            "requirements": [{"requirement_id": "r1", "description": "x", "requirement_type": "object", "priority": "must_have"}],
        })
        store.append(tr, box_id=mod_def.id)

        # 全部
        assert len(store.list_recent(limit=10)) == 3
        # 过滤 inv
        inv_records = store.list_recent(limit=10, box_id=inv_def.id)
        assert len(inv_records) == 2
        assert all(r["box_id"] == inv_def.id for r in inv_records)

    def test_count_by_status_and_acceptance(self, store: TaskRunStore):
        box = get_inv_def()
        beeline = get_inv_beeline()
        # 2 个 accepted, 1 个 rejected
        for status in ["accepted", "accepted", "rejected"]:
            tr = create_task_run(
                "o", "i", box.result.result_schema, f"{box.id}@v{box.version}",
                beeline.id, beeline.version, "rt", "in",
            )
            BeelineExecutor().execute(tr, beeline, {"exception_type": "inventory_shortage"})
            store.append(tr, box_id=box.id, acceptance_status=status)

        assert store.count_by_status() == {"completed": 3}
        assert store.count_by_acceptance() == {"accepted": 2, "rejected": 1}

    def test_aggregate_metrics(self, store: TaskRunStore):
        box = get_inv_def()
        beeline = get_inv_beeline()
        for _ in range(5):
            tr = create_task_run(
                "o", "i", box.result.result_schema, f"{box.id}@v{box.version}",
                beeline.id, beeline.version, "rt", "in",
            )
            BeelineExecutor().execute(tr, beeline, {"exception_type": "inventory_shortage"})
            store.append(tr, box_id=box.id, acceptance_status="accepted")

        m = store.aggregate_metrics()
        assert m["total"] == 5
        assert m["completed"] == 5
        assert m["accepted"] == 5
        assert m["acceptance_rate"] == "100.0%"

    def test_list_boxes(self, store: TaskRunStore):
        inv_def = get_inv_def()
        inv_beeline = get_inv_beeline()
        mod_def = get_mod_def()
        mod_beeline = get_mod_beeline()

        for box, beeline, intent in [
            (inv_def, inv_beeline, "handle_order_exception"),
            (mod_def, mod_beeline, "handle_modeling_request"),
        ]:
            input_data = (
                {"exception_type": "inventory_shortage"} if intent == "handle_order_exception"
                else {"set_id": "s1", "business_goal": "x", "requirements": [{"requirement_id": "r1", "description": "x", "requirement_type": "object", "priority": "must_have"}]}
            )
            tr = create_task_run(
                "o", intent, box.result.result_schema, f"{box.id}@v{box.version}",
                beeline.id, beeline.version, "rt", "in",
            )
            BeelineExecutor().execute(tr, beeline, input_data)
            store.append(tr, box_id=box.id)

        assert set(store.list_boxes()) == {inv_def.id, mod_def.id}


# ============================================================
# Kanban CLI 渲染
# ============================================================


class TestKanbanRender:
    def test_empty_dashboard(self, store: TaskRunStore):
        out = render_dashboard(store)
        assert "beeOS Kanban" in out
        assert "(no boxes yet)" in out
        assert "(no task runs yet)" in out
        assert "(no active exceptions)" in out

    def test_dashboard_with_data(self, store: TaskRunStore):
        box = get_inv_def()
        beeline = get_inv_beeline()
        tr = create_task_run(
            "o", "i", box.result.result_schema, f"{box.id}@v{box.version}",
            beeline.id, beeline.version, "rt", "in",
        )
        BeelineExecutor().execute(tr, beeline, {"exception_type": "inventory_shortage"})
        eval_result = evaluate_acceptance(tr, box.result)
        store.append(tr, box_id=box.id, acceptance_status=eval_result.status.value)

        out = render_dashboard(store)
        assert "beeOS Kanban" in out
        assert box.id in out
        assert "completed" in out
        assert "Boxes Registered" in out
        assert "Recent Task Runs" in out
        assert "Aggregate Metrics" in out

    def test_dashboard_shows_acceptance_outcomes(self, store: TaskRunStore):
        box = get_inv_def()
        beeline = get_inv_beeline()
        for _ in range(3):
            tr = create_task_run(
                "o", "i", box.result.result_schema, f"{box.id}@v{box.version}",
                beeline.id, beeline.version, "rt", "in",
            )
            BeelineExecutor().execute(tr, beeline, {"exception_type": "inventory_shortage"})
            store.append(tr, box_id=box.id, acceptance_status="accepted")

        out = render_dashboard(store)
        assert "accepted" in out
        assert "Acceptance Outcomes" in out