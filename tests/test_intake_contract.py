"""tests.test_intake_contract - 投料契约闸 + rejected 归因

覆盖三步改动的行为契约：

1. 投料口分句（structure_business_text）
   - 顿号枚举拆成多条业务需求
   - 单句无枚举不编造需求
   - 多句 + 首句枚举同时成立
2. 结构契约闸（runtime.contract.validate_payload + trigger_task）
   - 形状非法在投料口抛 TriggerError，且不产生 TaskRun
   - 空需求集结构上合法，放行到 acceptance
3. rejected 归因（RejectionClass）
   - 空输入 / 覆盖不足 / 规则矛盾 / 结构问题四类分流
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from kanban.trigger import TriggerError, structure_business_text, trigger_task
from runtime.acceptance import _classify_failure
from runtime.contract import validate_payload
from runtime.models import RejectionClass
from runtime.store import TaskRunStore


# ============================================================
# 1. 投料口分句
# ============================================================


class TestIntakeSegmentation:
    def test_enumeration_without_terminal_punctuation(self):
        """顿号枚举 + 句尾无句号：原失败场景，4 条流程要被拆出来"""
        r = structure_business_text("wms的流程有入库流程、出库流程、盘点流程、库内作业流程")
        assert r["business_goal"] == "wms的流程"
        assert [x["description"] for x in r["requirements"]] == [
            "入库流程", "出库流程", "盘点流程", "库内作业流程",
        ]

    def test_enumerated_items_classified_as_process(self):
        r = structure_business_text("wms的流程有入库流程、出库流程、盘点流程")
        assert all(x["requirement_type"] == "process" for x in r["requirements"])
        # requirement_id 按类型分组编号
        assert [x["requirement_id"] for x in r["requirements"]] == [
            "req_proc_1", "req_proc_2", "req_proc_3",
        ]

    def test_enum_head_split_on_existence_word(self):
        """存在词右侧粘着的首项也要算需求（"有入库流程"）"""
        r = structure_business_text("wms的流程有入库流程、出库流程")
        assert r["business_goal"] == "wms的流程"
        assert len(r["requirements"]) == 2

    def test_enum_plus_following_sentences(self):
        """首句枚举 + 后续句：枚举项排在前，保持业务表述顺序"""
        r = structure_business_text("wms的流程有入库流程、出库流程。入库要有到货登记。")
        assert r["business_goal"] == "wms的流程"
        assert [x["description"] for x in r["requirements"]] == [
            "入库流程", "出库流程", "入库要有到货登记",
        ]

    def test_plain_multi_sentence_unchanged(self):
        """无顿号的普通多句：首句=目标，其余=需求（原行为不变）"""
        r = structure_business_text("建模订单退款流程。退款必须在 7 天内完成。")
        assert r["business_goal"] == "建模订单退款流程"
        assert r["requirements"][0]["description"] == "退款必须在 7 天内完成"
        assert r["requirements"][0]["requirement_type"] == "rule"

    def test_single_sentence_no_enum_yields_no_requirements(self):
        """单句无枚举：宁可 0 条也不编造"""
        r = structure_business_text("只有一句话没有枚举")
        assert r["business_goal"] == "只有一句话没有枚举"
        assert r["requirements"] == []

    def test_short_fragments_not_split(self):
        """顿号拆出的单字碎片视为噪声，不当需求（保守不拆）"""
        r = structure_business_text("目标要快、准、狠")
        assert r["requirements"] == []

    def test_empty_text(self):
        r = structure_business_text("")
        assert r["business_goal"] == ""
        assert r["requirements"] == []


# ============================================================
# 2. 结构契约闸
# ============================================================


@pytest.fixture
def store() -> TaskRunStore:
    return TaskRunStore(Path(tempfile.mktemp(suffix=".jsonl")))


def _valid_payload() -> dict:
    return {
        "set_id": "s1",
        "business_goal": "wms 的流程",
        "requirements": [
            {
                "requirement_id": "req_proc_1",
                "description": "入库流程",
                "requirement_type": "process",
                "priority": "must_have",
            }
        ],
    }


class TestContractGate:
    def test_valid_payload_passes(self):
        from boxes.modeling import get_definition
        schema = get_definition().get_schema("schema_business_requirement_set")
        assert validate_payload(_valid_payload(), schema) == []

    def test_missing_required_field(self):
        from boxes.modeling import get_definition
        schema = get_definition().get_schema("schema_business_requirement_set")
        errors = validate_payload({"set_id": "s1", "requirements": []}, schema)
        assert any("business_goal" in e for e in errors)

    def test_wrong_type(self):
        from boxes.modeling import get_definition
        schema = get_definition().get_schema("schema_business_requirement_set")
        errors = validate_payload({**_valid_payload(), "requirements": "not-a-list"}, schema)
        assert any("requirements" in e and "array" in e for e in errors)

    def test_illegal_enum_value(self):
        from boxes.modeling import get_definition
        schema = get_definition().get_schema("schema_business_requirement_set")
        bad = _valid_payload()
        bad["requirements"][0]["requirement_type"] = "NOT_A_TYPE"
        errors = validate_payload(bad, schema)
        assert any("NOT_A_TYPE" in e for e in errors)

    def test_nested_item_missing_field(self):
        from boxes.modeling import get_definition
        schema = get_definition().get_schema("schema_business_requirement_set")
        bad = _valid_payload()
        del bad["requirements"][0]["requirement_id"]
        errors = validate_payload(bad, schema)
        assert any("requirement_id" in e for e in errors)

    def test_optional_field_may_be_absent(self):
        from boxes.modeling import get_definition
        schema = get_definition().get_schema("schema_business_requirement_set")
        p = _valid_payload()  # 无 context / traceable_to（都 required=False）
        assert validate_payload(p, schema) == []

    def test_boolean_not_accepted_as_integer(self):
        """bool 是 int 子类，number/integer 校验必须排除 bool"""
        from core.models import FieldDef, SchemaDef
        schema = SchemaDef(
            id="s",
            fields=[FieldDef(name="count", type="integer")],
        )
        assert validate_payload({"count": True}, schema)
        assert validate_payload({"count": 3}, schema) == []

    def test_enum_without_values_only_type_checked(self):
        """未声明取值表的 enum 只校验类型（向后兼容运营线盒子的既有声明）"""
        from core.models import FieldDef, SchemaDef
        schema = SchemaDef(
            id="s",
            fields=[FieldDef(name="kind", type="enum")],
        )
        assert validate_payload({"kind": "anything"}, schema) == []
        assert validate_payload({"kind": 123}, schema)


class TestIntakeRejectsBeforeFulfillment:
    """结构非法的投料在 create_task_run 之前就被拦——不产生 TaskRun"""

    def test_bad_payload_raises_and_creates_nothing(self, store: TaskRunStore):
        with pytest.raises(TriggerError, match="violates task_schema"):
            trigger_task(
                "business_modeling_box", "handle_modeling_request",
                {"set_id": "s1", "requirements": []}, store,
            )
        assert store.list_recent() == []

    def test_unknown_box_raises(self, store: TaskRunStore):
        with pytest.raises(TriggerError, match="unknown box_id"):
            trigger_task("no_such_box", "handle_modeling_request", {}, store)
        assert store.list_recent() == []

    def test_empty_requirements_passes_gate_then_rejected(self, store: TaskRunStore):
        """空需求集结构上合法（0 条违规）→ 放行 → acceptance 按业务判据拦下"""
        r = trigger_task(
            "business_modeling_box", "handle_modeling_request",
            {"set_id": "s1", "business_goal": "wms", "requirements": []}, store,
        )
        assert r["status"] == "completed"
        assert r["acceptance_status"] == "rejected"
        assert r["rejection_class"] == "empty_input"
        # 这次投料真的履约了，所以留下记录
        assert len(store.list_recent()) == 1


# ============================================================
# 3. rejected 归因
# ============================================================


class TestRejectionClassification:
    def test_zero_requirement_count_is_empty_input(self):
        assert _classify_failure(
            [{"metric": "requirement_coverage", "actual": None}],
            {"requirement_count": 0},
        ) == RejectionClass.EMPTY_INPUT

    def test_all_actuals_none_is_empty_input(self):
        assert _classify_failure(
            [{"metric": "requirement_coverage", "actual": None},
             {"metric": "metrics_defined", "actual": None}],
            {"requirement_count": 3},
        ) == RejectionClass.EMPTY_INPUT

    def test_rule_conflict_beats_coverage(self):
        """规则矛盾优先归因——先修规则，补覆盖率没意义"""
        assert _classify_failure(
            [{"metric": "rule_consistency", "actual": False},
             {"metric": "requirement_coverage", "actual": 80}],
            {"requirement_count": 3, "rule_consistency": False, "requirement_coverage": 80},
        ) == RejectionClass.CONFLICT

    def test_coverage_below_target(self):
        assert _classify_failure(
            [{"metric": "requirement_coverage", "actual": 80}],
            {"requirement_count": 3, "requirement_coverage": 80},
        ) == RejectionClass.INSUFFICIENT_COVERAGE

    def test_coverage_metric_name_is_not_hardcoded(self):
        """覆盖率判据换个盒子就叫别的名字——写死名字会把真原因归成 STRUCTURAL"""
        assert _classify_failure(
            [{"metric": "source_coverage", "actual": 80}],
            {"requirement_count": 3, "source_coverage": 80},
        ) == RejectionClass.INSUFFICIENT_COVERAGE

    def test_coverage_without_value_stays_structural(self):
        """名字像覆盖率但没取到值（None）→ 不算覆盖率不足，落到结构类"""
        assert _classify_failure(
            [{"metric": "source_coverage", "actual": None},
             {"metric": "trace_integrity", "actual": False}],
            {"requirement_count": 3},
        ) == RejectionClass.STRUCTURAL

    def test_remaining_metrics_are_structural(self):
        assert _classify_failure(
            [{"metric": "reference_integrity", "actual": False}],
            {"requirement_count": 3, "requirement_coverage": 100},
        ) == RejectionClass.STRUCTURAL


class TestAcceptanceOutcomeCarriesReason:
    def test_accepted_has_no_rejection_class(self, store: TaskRunStore):
        r = trigger_task(
            "business_modeling_box", "handle_modeling_request",
            {
                "set_id": "s1",
                "business_goal": "wms 的流程",
                "requirements": [
                    {"requirement_id": "req_proc_1", "description": "入库流程",
                     "requirement_type": "process", "priority": "must_have"},
                    {"requirement_id": "req_met_1", "description": "处理时长",
                     "requirement_type": "metric", "priority": "must_have"},
                ],
            }, store,
        )
        assert r["acceptance_status"] == "accepted"
        assert r["rejection_class"] is None

    def test_rejection_class_persisted_to_store(self, store: TaskRunStore):
        trigger_task(
            "business_modeling_box", "handle_modeling_request",
            {"set_id": "s1", "business_goal": "wms", "requirements": []}, store,
        )
        rec = store.list_recent()[0]
        assert rec["acceptance_status"] == "rejected"
        assert rec["rejection_class"] == "empty_input"
