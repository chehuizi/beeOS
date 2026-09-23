"""tests.test_modeling_box - 业务建模盒 Definition + Beeline + 端到端

覆盖范围：
- boxes.modeling definition 加载 + 5 机械 acceptance + 3 exception
- beelines.modeling 加载 + 7 ops 顺序链
- 端到端：requirement set → parse → classify → generate → verify → evidence → ACCEPTED
"""

from __future__ import annotations

import pytest

from boxes.modeling import BUSINESS_MODELING_BOX, get_definition
from beelines.modeling import BUSINESS_MODELING_BEELINE, get_beeline
from beelines import get_inventory_shortage_beeline
from runtime import (
    BeelineExecutor,
    AcceptanceStatus,
    TaskRunStatus,
    create_task_run,
    evaluate_acceptance,
)


# ============================================================
# 测试 fixture
# ============================================================


def _sample_requirement_set() -> dict:
    """示例业务需求集"""
    return {
        "set_id": "req_set_001",
        "business_goal": "建模订单退款流程",
        "requirements": [
            {"requirement_id": "req_obj_1", "description": "订单实体", "requirement_type": "object", "priority": "must_have"},
            {"requirement_id": "req_obj_2", "description": "退款实体", "requirement_type": "object", "priority": "must_have"},
            {"requirement_id": "req_rule_3", "description": "退款必须在 7 天内", "requirement_type": "rule", "priority": "must_have"},
            {"requirement_id": "req_proc_5", "description": "退款申请流程", "requirement_type": "process", "priority": "must_have"},
            {"requirement_id": "req_metric_7", "description": "退款处理时长", "requirement_type": "metric", "priority": "should_have"},
        ],
    }


# ============================================================
# Definition 验证
# ============================================================


class TestModelingBoxDefinition:
    def test_module_exports(self):
        assert callable(get_definition)
        assert isinstance(BUSINESS_MODELING_BOX, __import__("core.models").models.BeeBoxDefinition)

    def test_id_and_version(self):
        d = get_definition()
        assert d.id == "business_modeling_box"
        assert d.version == 1

    def test_schemas_complete(self):
        d = get_definition()
        ids = {s.id for s in d.schemas}
        # 7 个 schema 必须就位
        for sid in (
            "schema_business_requirement",
            "schema_business_requirement_set",
            "schema_parsed_requirements",
            "schema_classified_requirements",
            "schema_model_elements",
            "schema_model_evidence",
            "schema_business_model_package",
        ):
            assert sid in ids, f"missing schema {sid}"

    def test_task_references_real_schema(self):
        d = get_definition()
        t = d.get_task("handle_modeling_request")
        assert t is not None
        # task_schema 必须真实存在
        assert d.get_schema(t.task_schema) is not None
        # beeline 引用
        assert t.beeline_id == "beeline_business_modeling_v1"
        assert t.beeline_version == 1

    def test_acceptance_all_machine_verifiable(self):
        """5 条 acceptance 必须是机器可验字段（无主观项）"""
        d = get_definition()
        metrics = [a.metric for a in d.result.acceptance]
        # 5 项 + 全是数值 / 布尔类型（无 narrative）
        assert set(metrics) == {
            "requirement_coverage",
            "rule_consistency",
            "reference_integrity",
            "structural_compliance",
            "metrics_defined",
        }
        for a in d.result.acceptance:
            assert a.value in (True, 100, 100.0) or (isinstance(a.value, (int, float)) and a.value >= 100)

    def test_exceptions_cover_typical_failures(self):
        d = get_definition()
        actions = {e.action for e in d.result.exceptions}
        assert actions == {"escalate", "rollback", "no_deliver"}

    def test_queen_within_bounds(self):
        d = get_definition()
        a = d.queen.authorization
        for v in (a.task_routing, a.exception_handling, a.continuous_improvement):
            assert v in {"allow", "observe_only", "off"}

    def test_metrics_three_dimensions(self):
        d = get_definition()
        assert len(d.metrics.quality) >= 1
        assert len(d.metrics.latency) >= 1
        assert len(d.metrics.cost) >= 1


# ============================================================
# Beeline 验证
# ============================================================


class TestModelingBeeline:
    def test_module_exports(self):
        assert callable(get_beeline)
        assert BUSINESS_MODELING_BEELINE.id == "beeline_business_modeling_v1"
        assert BUSINESS_MODELING_BEELINE.version == 1

    def test_seven_op_linear_chain(self):
        b = get_beeline()
        op_ids = [op.op_id for op in b.operations]
        assert op_ids == [
            "parse_requirements",
            "classify_requirements",
            "generate_model_elements",
            "verify_coverage",
            "check_consistency",
            "generate_evidence",
            "package_model",
        ]

    def test_entry_point(self):
        b = get_beeline()
        starts = b.get_start_operations()
        assert len(starts) == 1
        assert starts[0].op_id == "parse_requirements"

    def test_terminal_at_package_model(self):
        b = get_beeline()
        pkg = b.get_operation("package_model")
        assert pkg is not None
        assert pkg.next == []

    def test_all_ops_idempotent_required(self):
        b = get_beeline()
        for op in b.operations:
            assert op.idempotency is not None
            assert op.idempotency.mode == "required"
            assert op.idempotency.key == "set_id"

    def test_beeline_id_matches_definition_reference(self):
        """beeline id + version 必须与 definition.task.beeline_id + beeline_version 一致"""
        box = get_definition()
        beeline = get_beeline()
        t = box.get_task("handle_modeling_request")
        assert t.beeline_id == beeline.id
        assert t.beeline_version == beeline.version


# ============================================================
# 端到端：definition + beeline + executor + acceptance
# ============================================================


class TestModelingEndToEnd:
    def test_full_flow_accepts(self):
        """完整流程：requirement set → ACCEPTED"""
        box = get_definition()
        beeline = get_beeline()

        input_data = _sample_requirement_set()

        task_run = create_task_run(
            originator="pm.api",
            intent="handle_modeling_request",
            contract_ref=box.result.result_schema,
            release_id=f"{box.id}@v{box.version}",
            beeline_id=beeline.id,
            beeline_version=beeline.version,
            runtime_id="runtime_dev",
            instance_id="instance_local",
        )

        # 执行 beeline
        task_run = BeelineExecutor().execute(task_run, beeline, input_data)

        # 验证：task run COMPLETED
        assert task_run.status == TaskRunStatus.COMPLETED
        # 验证：所有 op 都跑过
        assert set(task_run.operations.keys()) == {
            "parse_requirements",
            "classify_requirements",
            "generate_model_elements",
            "verify_coverage",
            "check_consistency",
            "generate_evidence",
            "package_model",
        }
        for rec in task_run.operations.values():
            assert rec.status == "completed"

        # 验证：result 含 5 项 acceptance 字段
        result = task_run.result
        assert result["requirement_coverage"] == 100.0
        assert result["rule_consistency"] is True
        assert result["reference_integrity"] is True
        assert result["structural_compliance"] is True
        assert result["metrics_defined"] is True

        # 验证：acceptance ACCEPTED
        eval_result = evaluate_acceptance(task_run, box.result)
        assert eval_result.status == AcceptanceStatus.ACCEPTED
        assert eval_result.passed_rules == [
            "requirement_coverage",
            "rule_consistency",
            "reference_integrity",
            "structural_compliance",
            "metrics_defined",
        ]
        assert eval_result.failed_rules == []

    def test_event_log_complete(self):
        box = get_definition()
        beeline = get_beeline()

        task_run = create_task_run(
            "pm.api", "handle_modeling_request", box.result.result_schema,
            f"{box.id}@v{box.version}", beeline.id, beeline.version,
            "rt", "in",
        )
        BeelineExecutor().execute(task_run, beeline, _sample_requirement_set())

        events = [e.event for e in task_run.event_log.entries]
        # 必须包含 status_change + op_start + op_finish
        assert "status_change" in events
        assert "op_start" in events
        assert "op_finish" in events
        # 7 op × 2 = 14 op_* 事件
        assert events.count("op_start") == 7
        assert events.count("op_finish") == 7

    def test_different_lines_dont_cross_contaminate(self):
        """两个产品线（运营 vs 软件）互不污染"""
        # 加载运营线的 box + beeline
        from boxes.inventory_shortage import get_definition as get_inv_def
        inv_def = get_inv_def()
        inv_beeline = get_inventory_shortage_beeline()

        # 加载软件线的 box + beeline
        mod_def = get_definition()
        mod_beeline = get_beeline()

        # id 必须不同
        assert inv_def.id != mod_def.id
        assert inv_beeline.id != mod_beeline.id
        # acceptance 字段必须不同
        inv_metrics = {a.metric for a in inv_def.result.acceptance}
        mod_metrics = {a.metric for a in mod_def.result.acceptance}
        assert "resolution_within_sla" in inv_metrics  # 运营特征
        assert "requirement_coverage" in mod_metrics   # 软件特征
        # 不能混入
        assert "resolution_within_sla" not in mod_metrics
        assert "requirement_coverage" not in inv_metrics

    def test_minimal_requirement_set_works(self):
        """最小可行输入：1 个 object 需求"""
        minimal = {
            "set_id": "req_min",
            "business_goal": "minimal test",
            "requirements": [
                {"requirement_id": "req_obj_1", "description": "x", "requirement_type": "object", "priority": "must_have"},
            ],
        }
        box = get_definition()
        beeline = get_beeline()
        task_run = create_task_run(
            "pm", "handle_modeling_request", box.result.result_schema,
            f"{box.id}@v{box.version}", beeline.id, beeline.version,
            "rt", "in",
        )
        task_run = BeelineExecutor().execute(task_run, beeline, minimal)
        assert task_run.status == TaskRunStatus.COMPLETED
        assert task_run.result["requirement_coverage"] == 100.0