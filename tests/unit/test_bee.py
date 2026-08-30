"""Bee 引擎测试（M0：状态机 + 编排 + 注册表）。"""

import pytest

from bee import Bee, BeeConfig, JobStateMachine, JobStatus, list_supported
from bee.registry import get_manifest, get_workflow


class TestRegistry:
    """Box 注册表：bee_type → Box 模块。"""

    def test_month_close_registered(self):
        assert "month_close" in list_supported()

    def test_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown bee_type"):
            get_manifest("nonexistent")

    def test_get_manifest_returns_dict(self):
        m = get_manifest("month_close")
        assert m["box_type"] == "month_close"
        assert m["name"] == "MonthCloseBox"
        assert "schemas" in m
        assert "tools" in m

    def test_get_workflow_returns_6_steps(self):
        wf = get_workflow("month_close")
        assert len(wf) == 6
        step_names = [s["name"] for s in wf]
        assert step_names == [
            "pull_balances", "reconcile", "classify",
            "generate_reports", "collect_evidence", "request_signoff",
        ]


class TestStateMachine:
    """5 状态机转换合法性。"""

    def test_initial_status(self):
        sm = JobStateMachine()
        assert sm.status == JobStatus.QUEUED

    def test_queued_to_running(self):
        sm = JobStateMachine()
        sm.transition(JobStatus.RUNNING)
        assert sm.status == JobStatus.RUNNING

    def test_running_to_done(self):
        sm = JobStateMachine(JobStatus.RUNNING)
        sm.transition(JobStatus.DONE)
        assert sm.status == JobStatus.DONE

    def test_running_to_failed(self):
        sm = JobStateMachine(JobStatus.RUNNING)
        sm.transition(JobStatus.FAILED)
        assert sm.status == JobStatus.FAILED

    def test_failed_can_retry(self):
        sm = JobStateMachine(JobStatus.FAILED)
        sm.transition(JobStatus.QUEUED)
        assert sm.status == JobStatus.QUEUED

    def test_done_is_terminal(self):
        sm = JobStateMachine(JobStatus.DONE)
        with pytest.raises(ValueError, match="Illegal transition"):
            sm.transition(JobStatus.RUNNING)

    def test_illegal_queued_to_done(self):
        """Queued 不能直接跳到 Done。"""
        sm = JobStateMachine()
        with pytest.raises(ValueError, match="Illegal transition"):
            sm.transition(JobStatus.DONE)


class TestBeeConfig:
    def test_defaults(self):
        cfg = BeeConfig()
        assert cfg.max_steps == 100
        assert cfg.max_execution_seconds == 1800
        assert cfg.audit_path == "./logs/audit.jsonl"


class TestBeeRuntime:
    """Bee.run() 端到端。"""

    @pytest.mark.asyncio
    async def test_run_month_close_returns_done(self, tmp_path):
        bee = Bee(BeeConfig(audit_path=str(tmp_path / "audit.jsonl")))
        result = await bee.run(
            box_type="month_close",
            context={"period": "2026-07"},
        )
        assert result.status == JobStatus.DONE
        assert result.period == "2026-07"
        assert len(result.steps) == 6
        assert result.error is None

    @pytest.mark.asyncio
    async def test_run_unknown_box_returns_failed(self, tmp_path):
        bee = Bee(BeeConfig(audit_path=str(tmp_path / "audit.jsonl")))
        result = await bee.run(box_type="nonexistent", context={})
        assert result.status == JobStatus.FAILED
        assert "Unknown box_type" in (result.error or "")

    @pytest.mark.asyncio
    async def test_run_passes_approver_to_signoff(self, tmp_path):
        bee = Bee(BeeConfig(audit_path=str(tmp_path / "audit.jsonl")))
        result = await bee.run(
            box_type="month_close",
            context={"period": "2026-08", "approver": "carol@x.com"},
        )
        assert result.status == JobStatus.DONE
        signoff_step = result.steps[-1]
        assert signoff_step["step"] == "request_signoff"
        assert signoff_step["output"]["approver"] == "carol@x.com"

    @pytest.mark.asyncio
    async def test_run_writes_audit_log(self, tmp_path):
        audit_path = tmp_path / "audit.jsonl"
        bee = Bee(BeeConfig(audit_path=str(audit_path)))
        await bee.run(box_type="month_close", context={"period": "2026-09"})

        assert audit_path.exists()
        lines = audit_path.read_text().strip().split("\n")
        # 至少 1 条 job.start + 6 条 step.done + 1 条 job.complete = 8
        assert len(lines) >= 8
        # 每行都是合法 JSON + 含 curr_hash
        import json
        for line in lines:
            entry = json.loads(line)
            assert "curr_hash" in entry
            assert "prev_hash" in entry
