"""tests.test_runtime - Runtime + TaskRun + Executor + Acceptance

覆盖范围：
- TaskRun 状态机（白名单转移）
- HandlerRunner：op 分派机制；各盒子的 op 实现由盒子自带
- BeelineExecutor：起点查找 + 分支推进 + 终态判定
- Acceptance：rule 评估 + 4 状态转移 + exception action
- 端到端：definition + beeline + executor + acceptance
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from tests.fixtures.sample_box import get_definition
from tests.fixtures.sample_box.runner import RUNNER as SAMPLE_RUNNER
from tests.fixtures.sample_box.beeline import get_beeline as get_sample_beeline
from core.beeline_models import Beeline, Bee, NextRef, Operation
from core.models import AcceptanceRule, ExceptionRule, ResultDef
from runtime import (
    BeelineExecutor,
    HandlerRunner,
    TaskRunStatus,
    AcceptanceStatus,
    create_task_run,
    evaluate_acceptance,
    default_queen_escalation_handler,
)
from runtime.models import ALLOWED_TRANSITIONS


# ============================================================
# TaskRun 状态机
# ============================================================


class TestTaskRunStateMachine:
    def test_create_task_run_default_triggered(self):
        t = create_task_run(
            originator="orders.api",
            intent="handle_x",
            contract_ref="schema_x",
            release_id="box@v1",
            beeline_id="beeline_x",
            beeline_version=1,
            runtime_id="r1",
            instance_id="i1",
        )
        assert t.status == TaskRunStatus.TRIGGERED

    def test_legal_transition_triggered_to_running(self):
        t = create_task_run("o", "i", "c", "r", "b", 1, "rt", "in")
        t.transition_to(TaskRunStatus.RUNNING, reason="start")
        assert t.status == TaskRunStatus.RUNNING
        assert t.identity.started_at is not None

    def test_legal_transition_running_to_completed(self):
        t = create_task_run("o", "i", "c", "r", "b", 1, "rt", "in")
        t.transition_to(TaskRunStatus.RUNNING)
        t.transition_to(TaskRunStatus.COMPLETED, reason="done")
        assert t.status == TaskRunStatus.COMPLETED
        assert t.identity.finished_at is not None

    def test_illegal_transition_blocked(self):
        t = create_task_run("o", "i", "c", "r", "b", 1, "rt", "in")
        # 不能从 triggered 直接跳 completed
        with pytest.raises(ValueError, match="illegal task run transition"):
            t.transition_to(TaskRunStatus.COMPLETED)

    def test_terminal_states_cannot_transition(self):
        t = create_task_run("o", "i", "c", "r", "b", 1, "rt", "in")
        t.transition_to(TaskRunStatus.RUNNING)
        t.transition_to(TaskRunStatus.COMPLETED)
        with pytest.raises(ValueError, match="illegal"):
            t.transition_to(TaskRunStatus.RUNNING)

    def test_event_log_records_status_changes(self):
        t = create_task_run("o", "i", "c", "r", "b", 1, "rt", "in")
        t.transition_to(TaskRunStatus.RUNNING, reason="start")
        t.transition_to(TaskRunStatus.COMPLETED, reason="done")
        status_changes = [e for e in t.event_log.entries if e.event == "status_change"]
        assert len(status_changes) == 2
        assert status_changes[0].detail["to"] == "running"
        assert status_changes[1].detail["to"] == "completed"

    def test_is_terminal(self):
        t = create_task_run("o", "i", "c", "r", "b", 1, "rt", "in")
        assert not t.is_terminal()
        t.transition_to(TaskRunStatus.RUNNING)
        t.transition_to(TaskRunStatus.COMPLETED)
        assert t.is_terminal()


# ============================================================
# HandlerRunner + 库存不足盒的 runner
# ============================================================


class TestHandlerRunner:
    def test_supports_known_op_types(self):
        runner = SAMPLE_RUNNER
        for op_type in [
            "sample_classify",
            "sample_resolve",
            "sample_notify",
            "sample_escalate",
        ]:
            assert runner.supports(op_type), f"missing {op_type}"

    def test_classify_returns_classification(self):
        runner = SAMPLE_RUNNER
        out = runner.run("sample_classify", {
            "sample_id": "s1",
            "kind": "demo",
            "amount": 100,
        })
        assert out["sample_id"] == "s1"
        assert out["kind"] == "demo"
        assert out["amount"] == 100

    def test_resolve_returns_resolved(self):
        runner = SAMPLE_RUNNER
        out = runner.run("sample_resolve", {"sample_id": "s1"})
        assert out["sample_state"] == "resolved"
        assert out["notified"] is False  # 未通知，等 sample_notify

    def test_notify_marks_notified(self):
        runner = SAMPLE_RUNNER
        out = runner.run("sample_notify", {
            "sample_state": "resolved",
            "steps": ["resolve"],
        })
        assert out["notified"] is True

    def test_unknown_op_type_raises(self):
        runner = SAMPLE_RUNNER
        with pytest.raises(NotImplementedError):
            runner.run("nonexistent_op_type", {})


# ============================================================
# BeelineExecutor
# ============================================================


def _make_simple_beeline() -> Beeline:
    """简单 beeline：a → b → c（终态）"""
    return Beeline(
        id="b_simple",
        version=1,
        description="simple linear chain",
        operations=[
            Operation(
                op_id="a",
                type="sample_classify",  # 夹具盒的 runner 认得
                input_from="external",
                input="schema_in",
                output="schema_a_out",
                next=[NextRef(op_id="b")],
                bee=Bee(type="t"),
            ),
            Operation(
                op_id="b",
                type="sample_notify",  # 夹具盒的 runner 认得
                input_from="a",
                input="schema_a_out",
                output="schema_b_out",
                next=[NextRef(op_id="c")],
                bee=Bee(type="t"),
            ),
            Operation(
                op_id="c",
                type="sample_escalate",  # 夹具盒的 runner 认得
                input_from="b",
                input="schema_b_out",
                output="schema_c_out",
                next=[],
                bee=Bee(type="t"),
            ),
        ],
    )


def _make_branched_beeline() -> Beeline:
    """分支 beeline：a → b (when ok) | c (fallback)"""
    return Beeline(
        id="b_branch",
        version=1,
        description="branched chain",
        operations=[
            Operation(
                op_id="a",
                type="sample_resolve",  # 输出 sample_state=resolved
                input_from="external",
                input="s",
                output="s",
                next=[
                    NextRef(op_id="b", when="sample_state == resolved"),
                    NextRef(op_id="c"),
                ],
                bee=Bee(type="t"),
            ),
            Operation(
                op_id="b",
                type="sample_notify",
                input_from="a",
                input="s",
                output="s",
                next=[],
                bee=Bee(type="t"),
            ),
            Operation(
                op_id="c",
                type="sample_escalate",
                input_from="a",
                input="s",
                output="s",
                next=[],
                bee=Bee(type="t"),
            ),
        ],
    )


class TestBeelineExecutor:
    def test_executor_runs_linear_chain(self):
        beeline = _make_simple_beeline()
        task_run = create_task_run("o", "i", "c", "r", beeline.id, 1, "rt", "in")

        executor = BeelineExecutor(SAMPLE_RUNNER)
        result = executor.execute(task_run, beeline, {})

        assert result.status == TaskRunStatus.COMPLETED
        assert set(result.operations.keys()) == {"a", "b", "c"}
        assert all(op.status == "completed" for op in result.operations.values())

    def test_executor_picks_branched_path(self):
        # sample_resolve 总是返回 sample_state=resolved
        beeline = _make_branched_beeline()
        task_run = create_task_run("o", "i", "c", "r", beeline.id, 1, "rt", "in")

        executor = BeelineExecutor(SAMPLE_RUNNER)
        result = executor.execute(task_run, beeline, {})

        assert result.status == TaskRunStatus.COMPLETED
        # 走了 b 分支（没走 c）
        assert "a" in result.operations
        assert "b" in result.operations
        assert "c" not in result.operations

    def test_executor_fails_on_multiple_starts(self):
        # beeline 有 2 个 start op（model 不禁止这个，executor 必须处理）
        beeline = Beeline(
            id="b_multi_start",
            version=1,
            description="x",
            operations=[
                Operation(
                    op_id="a",
                    type="sample_classify",
                    input_from="external",
                    input="s",
                    output="s",
                    next=[],
                    bee=Bee(type="t"),
                ),
                Operation(
                    op_id="b",
                    type="sample_notify",
                    input_from="external",
                    input="s",
                    output="s",
                    next=[],
                    bee=Bee(type="t"),
                ),
            ],
        )
        task_run = create_task_run("o", "i", "c", "r", beeline.id, 1, "rt", "in")
        executor = BeelineExecutor(SAMPLE_RUNNER)
        result = executor.execute(task_run, beeline, {})
        assert result.status == TaskRunStatus.FAILED
        assert "multiple start ops" in (result.event_log.entries[-1].detail or {}).get("reason", "")

    def test_executor_records_event_log(self):
        beeline = _make_simple_beeline()
        task_run = create_task_run("o", "i", "c", "r", beeline.id, 1, "rt", "in")

        executor = BeelineExecutor(SAMPLE_RUNNER)
        result = executor.execute(task_run, beeline, {})

        events = [e.event for e in result.event_log.entries]
        # 应该包含：status_change (triggered→running) + 3×(op_start + op_finish) + status_change (running→completed)
        assert "status_change" in events
        assert "op_start" in events
        assert "op_finish" in events
        assert events.count("op_start") == 3
        assert events.count("op_finish") == 3

    def test_executor_handles_unknown_op_type(self):
        beeline = Beeline(
            id="b_bad_op",
            version=1,
            description="x",
            operations=[
                Operation(
                    op_id="bad",
                    type="nonexistent_op_type",
                    input_from="external",
                    input="s",
                    output="s",
                    next=[],
                    bee=Bee(type="t"),
                ),
            ],
        )
        task_run = create_task_run("o", "i", "c", "r", beeline.id, 1, "rt", "in")
        executor = BeelineExecutor(SAMPLE_RUNNER)
        result = executor.execute(task_run, beeline, {})
        assert result.status == TaskRunStatus.FAILED
        assert "bad" in result.operations
        assert result.operations["bad"].status == "failed"


# ============================================================
# Acceptance 评估
# ============================================================


def _make_contract_with_acceptance(rules: list[AcceptanceRule]) -> ResultDef:
    return ResultDef(
        type="x_resolved",
        result_schema="schema_x",
        acceptance=rules,
        exceptions=[],
    )


class TestAcceptance:
    def test_all_pass_returns_accepted(self):
        contract = _make_contract_with_acceptance([
            AcceptanceRule(metric="ok", op="eq", value=True),
            AcceptanceRule(metric="count", op="gte", value=5),
        ])
        task_run = create_task_run("o", "i", "c", "r", "b", 1, "rt", "in")
        task_run.transition_to(TaskRunStatus.RUNNING)
        task_run.transition_to(TaskRunStatus.COMPLETED)
        task_run.result = {"ok": True, "count": 10}

        eval_result = evaluate_acceptance(task_run, contract)
        assert eval_result.status == AcceptanceStatus.ACCEPTED
        assert eval_result.passed_rules == ["ok", "count"]
        assert eval_result.failed_rules == []

    def test_any_fail_returns_rejected_default(self):
        contract = _make_contract_with_acceptance([
            AcceptanceRule(metric="ok", op="eq", value=True),
        ])
        task_run = create_task_run("o", "i", "c", "r", "b", 1, "rt", "in")
        task_run.transition_to(TaskRunStatus.RUNNING)
        task_run.transition_to(TaskRunStatus.COMPLETED)
        task_run.result = {"ok": False}

        eval_result = evaluate_acceptance(task_run, contract)
        assert eval_result.status == AcceptanceStatus.REJECTED
        assert eval_result.passed_rules == []
        assert len(eval_result.failed_rules) == 1

    def test_failure_with_no_deliver_exception(self):
        contract = ResultDef(
            type="x_resolved",
            result_schema="schema_x",
            acceptance=[AcceptanceRule(metric="ok", op="eq", value=True)],
            exceptions=[ExceptionRule(condition="ok == false", action="no_deliver")],
        )
        task_run = create_task_run("o", "i", "c", "r", "b", 1, "rt", "in")
        task_run.transition_to(TaskRunStatus.RUNNING)
        task_run.transition_to(TaskRunStatus.COMPLETED)
        task_run.result = {"ok": False}

        eval_result = evaluate_acceptance(task_run, contract)
        assert eval_result.status == AcceptanceStatus.REJECTED
        assert eval_result.exception_action == "no_deliver"

    def test_failure_with_rollback_exception(self):
        contract = ResultDef(
            type="x_resolved",
            result_schema="schema_x",
            acceptance=[AcceptanceRule(metric="ok", op="eq", value=True)],
            exceptions=[ExceptionRule(condition="ok == false", action="rollback")],
        )
        task_run = create_task_run("o", "i", "c", "r", "b", 1, "rt", "in")
        task_run.transition_to(TaskRunStatus.RUNNING)
        task_run.transition_to(TaskRunStatus.COMPLETED)
        task_run.result = {"ok": False}

        eval_result = evaluate_acceptance(task_run, contract)
        assert eval_result.status == AcceptanceStatus.COMPENSATING_ACCEPTANCE
        assert eval_result.exception_action == "rollback"

    def test_failure_with_escalate_calls_queen(self):
        queen_called = []

        def queen_fn(task_run, failed):
            queen_called.append(True)
            return "human_review"

        contract = ResultDef(
            type="x_resolved",
            result_schema="schema_x",
            acceptance=[AcceptanceRule(metric="ok", op="eq", value=True)],
            exceptions=[ExceptionRule(condition="ok == false", action="escalate")],
        )
        task_run = create_task_run("o", "i", "c", "r", "b", 1, "rt", "in")
        task_run.transition_to(TaskRunStatus.RUNNING)
        task_run.transition_to(TaskRunStatus.COMPLETED)
        task_run.result = {"ok": False}

        eval_result = evaluate_acceptance(task_run, contract, queen_decision_fn=queen_fn)
        assert queen_called == [True]
        assert eval_result.queen_decision == "human_review"
        assert eval_result.exception_action == "escalate"

    def test_acceptance_requires_completed_status(self):
        contract = _make_contract_with_acceptance([])
        task_run = create_task_run("o", "i", "c", "r", "b", 1, "rt", "in")
        # status 还是 triggered
        with pytest.raises(ValueError, match="requires task_run.status=completed"):
            evaluate_acceptance(task_run, contract)


# ============================================================
# 端到端：definition + beeline + executor + acceptance
# ============================================================


class TestEndToEnd:
    def test_sample_box_full_flow(self):
        box = get_definition()
        beeline = get_sample_beeline()

        input_data = {
            "sample_id": "s_001",
            "kind": "demo",
            "amount": 350.0,
        }

        task_run = create_task_run(
            originator="orders.api",
            intent="handle_sample_request",
            contract_ref=box.result.result_schema,
            release_id=f"{box.id}@v{box.version}",
            beeline_id=beeline.id,
            beeline_version=beeline.version,
            runtime_id="runtime_dev_local",
            instance_id="instance_local_001",
        )

        executor = BeelineExecutor(SAMPLE_RUNNER)
        task_run = executor.execute(task_run, beeline, input_data)

        # task run 必须 COMPLETED
        assert task_run.status == TaskRunStatus.COMPLETED
        # beeline 必须至少跑了 sample_classify
        assert "sample_classify" in task_run.operations
        # 所有 op 都 completed（没有失败）
        for rec in task_run.operations.values():
            assert rec.status == "completed", f"op {rec.op_id} failed"

        # acceptance 跑得通（即使 REJECTED 是合法结果）
        eval_result = evaluate_acceptance(
            task_run,
            box.result,
            queen_decision_fn=lambda tr, failed: default_queen_escalation_handler(
                tr, failed, box.queen.rules.model_dump()
            ),
        )
        assert eval_result.status in (
            AcceptanceStatus.ACCEPTED,
            AcceptanceStatus.REJECTED,
            AcceptanceStatus.COMPENSATING_ACCEPTANCE,
        )
        # event_log 至少 1 条
        assert len(task_run.event_log.entries) > 0

    def test_sample_box_event_log_trail(self):
        box = get_definition()
        beeline = get_sample_beeline()
        task_run = create_task_run(
            "o", "handle_sample_request", box.result.result_schema,
            f"{box.id}@v{box.version}", beeline.id, beeline.version,
            "rt", "in",
        )
        BeelineExecutor(SAMPLE_RUNNER).execute(task_run, beeline, {"sample_id": "s_001"})

        events = [e.event for e in task_run.event_log.entries]
        # 必须含 status_change + op_start + op_finish
        assert "status_change" in events
        assert "op_start" in events
        assert "op_finish" in events
        # 必须出现 triggered → running → completed 两次状态变化
        status_changes = [e.detail["to"] for e in task_run.event_log.entries if e.event == "status_change"]
        assert "running" in status_changes
        assert "completed" in status_changes

    def test_queen_escalation_handler_basic(self):
        task_run = create_task_run("o", "i", "c", "r", "b", 1, "rt", "in")
        task_run.result = {"refund_amount": 600}

        queen_rules = {
            "exception_handling": {
                "escalation_rules": [
                    {"trigger": "refund_amount > 500", "action": "escalate_to_human"},
                ],
            },
        }
        decision = default_queen_escalation_handler(task_run, [], queen_rules)
        assert decision == "queen.escalate_to_human"

    def test_queen_escalation_no_rule_match(self):
        task_run = create_task_run("o", "i", "c", "r", "b", 1, "rt", "in")
        task_run.result = {"refund_amount": 100}

        queen_rules = {
            "exception_handling": {
                "escalation_rules": [
                    {"trigger": "refund_amount > 500", "action": "escalate_to_human"},
                ],
            },
        }
        decision = default_queen_escalation_handler(task_run, [], queen_rules)
        assert decision == "auto_resolve"