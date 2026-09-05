"""beeOS Kernel 测试。"""

import pytest

from bee_kernel.bom import BOM, BOMCache, BOMStep
from bee_kernel.kernel import Kernel
from bee_kernel.mes import MESExecutor
from bee_kernel.task import Task
from bee_kernel.workspace import Workspace, WorkspaceRegistry


# === Task ===

class TestTask:
    def test_minimal_required(self):
        t = Task(workspace_id="WH-001", objective="test")
        assert t.task_id.startswith("task-")
        assert t.priority == 5  # default
        assert t.params == {}
        assert t.submitted_by == "anonymous"

    def test_full(self):
        t = Task(
            workspace_id="WH-001",
            objective="月结",
            params={"period": "2026-07"},
            priority=3,
            submitted_by="alice",
        )
        assert t.params == {"period": "2026-07"}
        assert t.priority == 3

    def test_priority_bounds(self):
        with pytest.raises(ValueError):
            Task(workspace_id="WH-001", objective="x", priority=10)
        with pytest.raises(ValueError):
            Task(workspace_id="WH-001", objective="x", priority=-1)

    def test_strip_whitespace(self):
        t = Task(workspace_id="  WH-001  ", objective="  x  ")
        assert t.workspace_id == "WH-001"
        assert t.objective == "x"

    def test_empty_objective_rejected(self):
        with pytest.raises(ValueError):
            Task(workspace_id="WH-001", objective="")


# === BOM ===

class TestBOM:
    def test_param_template_replaces_variables(self):
        from bee_kernel.bom import BOM, BOMStep
        step = BOMStep(seq=1, tool="x", params={"period": "$period", "user": "$user"})
        bom = BOM(bom_id="B-1", workspace_id="WH-1", name="t", steps=[step])
        out = bom.param_template(step, {"period": "2026-07", "user": "alice"})
        assert out == {"period": "2026-07", "user": "alice"}

    def test_param_template_missing_var_kept(self):
        step = BOMStep(seq=1, tool="x", params={"period": "$period"})
        bom = BOM(bom_id="B-1", workspace_id="WH-1", name="t", steps=[step])
        out = bom.param_template(step, {})
        # 没找到 $period 替换，会保留原样
        assert out["period"] == "$period"


class TestBOMCache:
    def test_load_from_disk(self, tmp_path):
        boms_dir = tmp_path / "boms"
        boms_dir.mkdir()
        (boms_dir / "test.yaml").write_text("""
bom_id: B-TEST
workspace_id: WH-1
name: test
version: v1
steps:
  - seq: 1
    tool: x
""", encoding="utf-8")
        cache = BOMCache(boms_dir)
        assert cache.loaded_count == 1
        bom = cache.get("WH-1", "test")
        assert bom is not None
        assert bom.bom_id == "B-TEST"

    def test_cache_miss(self, tmp_path):
        cache = BOMCache(tmp_path / "boms")
        assert cache.get("WH-1", "missing") is None


# === Workspace ===

class TestWorkspace:
    def test_load_from_yaml(self, tmp_path):
        ws_file = tmp_path / "workspaces.yaml"
        ws_file.write_text("""
workspaces:
  - workspace_id: WH-1
    domain: 业务
    name: 测试车间
    bee_box_ref: foo.bar:Baz
""", encoding="utf-8")
        reg = WorkspaceRegistry(ws_file)
        ws = reg.get("WH-1")
        assert ws is not None
        assert ws.domain == "业务"
        assert ws.bee_box_ref == "foo.bar:Baz"

    def test_get_missing(self, tmp_path):
        reg = WorkspaceRegistry(tmp_path / "workspaces.yaml")
        assert reg.get("WH-X") is None


# === MES ===

class TestMES:
    def test_instantiate(self):
        k = Kernel()
        task = Task(workspace_id="WH-001", objective="x", params={})
        bom = BOM(bom_id="B-1", workspace_id="WH-001", name="x", steps=[
            BOMStep(seq=1, tool="t1"),
        ])
        plan = k.mes.instantiate(task, bom)
        assert plan.exec_id.startswith("exec-")
        assert plan.task_id == task.task_id
        assert plan.bom_id == "B-1"
        assert plan.status == "Queued"

    def test_execute_runs_all_steps(self):
        """端到端：通过 Kernel submit 跑通整个 6 步 BOM。"""
        k = Kernel()
        task = Task(workspace_id="WH-001", objective="会计月结",
                   params={"period": "2026-07", "approver": "alice@x.com"})
        result = k.submit(task)
        assert result.status == "Done"
        assert result.step_count == 6
        # 每步都跑通
        tools = [s["tool"] for s in result.deliverables]
        assert tools == [
            "query_balances", "reconcile_bank", "classify_expenses",
            "generate_reports", "collect_evidence", "request_signoff",
        ]
        # 参数注入：$period 被替换
        assert result.deliverables[0]["params"]["period"] == "2026-07"
        assert result.deliverables[5]["params"]["approver"] == "alice@x.com"

    def test_unknown_workspace_raises(self):
        k = Kernel()
        task = Task(workspace_id="WH-999", objective="x")
        with pytest.raises(KeyError, match="WH-999"):
            k.submit(task)

    def test_unknown_bom_raises(self):
        k = Kernel()
        task = Task(workspace_id="WH-001", objective="nonexistent")
        with pytest.raises(KeyError, match="找不到 BOM"):
            k.submit(task)


# === Kernel 入口 ===

class TestKernel:
    def test_list_boms(self):
        k = Kernel()
        boms = k.list_boms()
        assert len(boms) == 1
        assert boms[0]["bom_id"] == "BOM-MONTH-CLOSE-v1"
        assert boms[0]["workspace_id"] == "WH-001"

    def test_list_workspaces(self):
        k = Kernel()
        ws = k.list_workspaces()
        assert len(ws) == 1
        assert ws[0]["workspace_id"] == "WH-001"
