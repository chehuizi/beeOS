"""tests.test_modeling_box - 业务建模盒 Definition + Beeline + 端到端

覆盖范围：
- boxes.modeling definition 加载 + 5 机械 acceptance + 3 exception
- beelines.modeling 加载 + 7 ops 顺序链
- 端到端：requirement set → parse → classify → generate → verify → evidence → ACCEPTED
"""

from __future__ import annotations

import pytest

from boxes.modeling import BUSINESS_MODELING_BOX, get_definition
from boxes.modeling.measure import COMPUTERS as MODEL_COMPUTERS
from boxes.modeling.schemas import REQUIREMENT_TYPES
from beelines.modeling import BUSINESS_MODELING_BEELINE, get_beeline
from beelines import get_inventory_shortage_beeline
from core import metrics as m
from runtime import (
    BeelineExecutor,
    AcceptanceStatus,
    TaskRunStatus,
    create_task_run,
    evaluate_acceptance,
)
from runtime.contract import validate_payload


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

    def test_declared_metrics_all_have_a_collector(self):
        """声明的每个指标都必须有实测采集算法

        这条取代了旧的「三个维度都要有指标」——那个断言逼着盒子去声明
        cost 指标，可成本压根没有记账信号源，只能声明一个算不出来的数字。
        维度齐不齐无所谓，指标有没有出处才要紧。
        """
        d = get_definition()
        assert m.unwired(d, MODEL_COMPUTERS) == []
        assert m.orphan_computers(d, MODEL_COMPUTERS) == []


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


# ============================================================
# Mock 数据流语义（parse 透传载荷 / classify 按真实 type / 覆盖率真算）
# ============================================================


class TestModelingMockSemantics:
    def _run(self, requirements, set_id="req_sem"):
        box = get_definition()
        beeline = get_beeline()
        task_run = create_task_run(
            "pm", "handle_modeling_request", box.result.result_schema,
            f"{box.id}@v{box.version}", beeline.id, beeline.version,
            "rt", "in",
        )
        return BeelineExecutor().execute(
            task_run, beeline,
            {"set_id": set_id, "business_goal": "g", "requirements": requirements},
        )

    def test_classify_by_declared_type_not_id_suffix(self):
        """分类按声明的 requirement_type，id 尾号不影响分类"""
        task_run = self._run([
            # 尾号 4 旧逻辑会误判为 rule，但声明是 metric
            {"requirement_id": "req_4", "description": "x", "requirement_type": "metric", "priority": "must_have"},
            # 尾号 7 旧逻辑会误判为 metric，但声明是 object
            {"requirement_id": "req_7", "description": "x", "requirement_type": "object", "priority": "must_have"},
        ])
        by_type = task_run.operations["classify_requirements"].output["by_type"]
        assert by_type["metric"] == ["req_4"]
        assert by_type["object"] == ["req_7"]

    def test_five_types_all_present_accepted(self):
        """五类需求齐全（含 goal）：goal 不计入覆盖率，5 类全覆盖 → ACCEPTED"""
        reqs = [
            {"requirement_id": f"r_{t}", "description": "x", "requirement_type": t, "priority": "must_have"}
            for t in ("object", "rule", "process", "metric", "goal")
        ]
        box = get_definition()
        task_run = self._run(reqs)
        assert task_run.result["requirement_coverage"] == 100.0
        assert task_run.result["metrics_defined"] is True
        assert evaluate_acceptance(task_run, box.result).status == AcceptanceStatus.ACCEPTED

    def test_by_type_schema_declares_every_requirement_type(self):
        """by_type 的 schema 必须覆盖全部 6 类 type（含 event / goal）

        runtime/classify 按 REQUIREMENT_TYPES 建全部 key；schema 少列一类，
        声明就名存实亡——契约校验形同虚设，且没人会发现 event 组悄悄丢了。
        """
        schema = get_definition().get_schema("schema_classified_requirements")
        by_type = next(f for f in schema.fields if f.name == "by_type")
        declared = {p.name for p in by_type.properties}
        assert declared == set(REQUIREMENT_TYPES)

    def test_by_type_output_passes_contract_validation(self):
        """classify 的真实输出能过 schema_classified_requirements 校验"""
        schema = get_definition().get_schema("schema_classified_requirements")
        task_run = self._run([
            {"requirement_id": f"r_{t}", "description": "x", "requirement_type": t, "priority": "must_have"}
            for t in ("object", "rule", "process", "metric", "event", "goal")
        ])
        out = task_run.operations["classify_requirements"].output
        assert validate_payload(out, schema) == []

    def test_no_metric_requirement_rejected(self):
        """无 metric 类需求 → metrics_defined=False → REJECTED"""
        box = get_definition()
        task_run = self._run([
            {"requirement_id": "r_obj", "description": "x", "requirement_type": "object", "priority": "must_have"},
        ])
        assert task_run.result["requirement_coverage"] == 100.0
        assert task_run.result["metrics_defined"] is False
        assert evaluate_acceptance(task_run, box.result).status == AcceptanceStatus.REJECTED

    def test_invalid_type_excluded_from_coverage(self):
        """type 非法的需求被 parse 剔除，不进覆盖率分母"""
        task_run = self._run([
            {"requirement_id": "r_ok", "description": "x", "requirement_type": "object", "priority": "must_have"},
            {"requirement_id": "r_bad", "description": "x", "requirement_type": "bogus", "priority": "must_have"},
        ])
        parse_out = task_run.operations["parse_requirements"].output
        assert parse_out["valid_ids"] == ["r_ok"]
        assert parse_out["parse_errors"] == ["invalid requirement_type for r_bad"]
        assert task_run.result["requirement_coverage"] == 100.0
        assert task_run.result["requirement_count"] == 1

    def test_coverage_verifier_detects_uncovered(self):
        """coverage_verifier 真实对比：未被 trace 覆盖的需求进入 uncovered"""
        from runtime.mock_runner import MockRunner
        out = MockRunner().run("coverage_verifier", {
            "set_id": "s",
            "modelable_ids": ["r1", "r2", "r3"],
            "entities": [{"entity_id": "e1", "name": "E", "trace_to": "r1"}],
            "rules": [{"rule_id": "ru2", "condition": "c", "action": "a", "trace_to": "r2"}],
            "processes": [],
            "metrics": [],
        })
        assert out["__coverage__"] == round(100.0 * 2 / 3, 2)
        assert out["__uncovered__"] == ["r3"]

    def test_consistency_checker_detects_broken_reference(self):
        """consistency_checker 真实检查：trace_to 指向无效需求 → reference_integrity=False"""
        from runtime.mock_runner import MockRunner
        out = MockRunner().run("consistency_checker", {
            "set_id": "s",
            "valid_ids": ["r1"],
            "entities": [{"entity_id": "e1", "name": "E", "trace_to": "r_ghost"}],
            "rules": [],
            "processes": [],
            "metrics": [],
        })
        assert out["__reference_integrity__"] is False
        assert out["__broken_references__"] == ["e1"]

    def test_business_goal_flows_to_package(self):
        """business_goal 从输入一路透传到最终 package"""
        task_run = self._run([
            {"requirement_id": "r_obj", "description": "x", "requirement_type": "object", "priority": "must_have"},
        ])
        assert task_run.result["business_goal"] == "g"
    def test_event_type_produces_domain_event(self):
        """event 类需求 → 领域事件元素（DDD 战术设计），计入覆盖率；不指派聚合根"""
        task_run = self._run([
            {"requirement_id": "r_obj", "description": "订单", "requirement_type": "object", "priority": "must_have"},
            {"requirement_id": "r_evt", "description": "退款已完成", "requirement_type": "event", "priority": "must_have"},
            {"requirement_id": "r_met", "description": "时长", "requirement_type": "metric", "priority": "must_have"},
        ])
        events = task_run.operations["generate_model_elements"].output["events"]
        assert events == [
            {"event_id": "evt_r_evt", "name": "退款已完成",
             "pattern": "domain_event", "trace_to": "r_evt"}
        ]
        assert task_run.result["requirement_coverage"] == 100.0
        model = task_run.result["model_elements"]
        assert model["domain_events"][0]["pattern"] == "domain_event"
        # 聚合根是一致性边界，没有不变量分析就不指派
        assert "aggregate_roots" not in model
        # object 一律建模为实体
        assert all(e["pattern"] == "entity" for e in model["entities"])
