"""tests.test_capture_box - 需求捕获盒（Software Line 第二只盒子）

覆盖：
- definition：4 条 acceptance + 两盒判据零重叠（分盒判据本身要可测）
- beeline：5 op 顺序链 + DAG 校验通过
- 5 个 op：切句 / 抽取 / 组装 / 4 项机械验收 / 打包
- 端到端：捕获盒产出直接投建模盒（schema 归消费者，捕获盒零改动）
- 降级：LLM 挂掉 / 编造 / 漏句子 / 出错类型，各该 fail 还是该降级

不真调 LLM：LLM 行为在 test_llm_structurer 覆盖，这里测机械部分。
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from boxes.requirement_capture import get_definition as get_capture_definition
from boxes.modeling import get_definition as get_modeling_definition
from beelines import get_requirement_capture_beeline
from kanban.trigger import (
    TriggerError,
    list_task_entries,
    registered_box_ids,
    trigger_task,
)
from runtime.capture_runner import _split_sentences
from runtime.store import TaskRunStore


@pytest.fixture
def store() -> TaskRunStore:
    return TaskRunStore(Path(tempfile.mktemp(suffix=".jsonl")))


@pytest.fixture(autouse=True)
def no_llm(monkeypatch):
    """默认禁掉 LLM——除非用例显式 stub 成某个返回值"""
    from runtime import llm
    monkeypatch.setattr(
        llm, "complete_json",
        lambda *a, **kw: (_ for _ in ()).throw(llm.LLMError("test: no llm")),
    )


# ============================================================
# definition
# ============================================================


class TestDefinition:
    def test_box_registered(self):
        assert "requirement_capture_box" in registered_box_ids()

    def test_task_and_beeline_pinned(self):
        d = get_capture_definition()
        t = d.task[0]
        assert t.type == "capture_business_requirement"
        assert t.beeline_id == "beeline_requirement_capture_v1"
        assert t.beeline_version == 1
        assert d.get_schema(t.task_schema) is not None

    def test_four_mechanical_acceptance_rules(self):
        rules = {r.metric: (r.op, r.value) for r in get_capture_definition().result.acceptance}
        assert rules == {
            "contract_compliance": ("eq", True),
            "source_coverage": ("gte", 100.0),
            "trace_integrity": ("eq", True),
            "no_hallucination": ("eq", True),
        }

    def test_acceptance_metrics_are_zero_overlap_with_modeling_box(self):
        """分盒判据：判据不同才值得独立成盒。这条本身要能测。"""
        capture = {r.metric for r in get_capture_definition().result.acceptance}
        modeling = {r.metric for r in get_modeling_definition().result.acceptance}
        assert capture & modeling == set(), f"判据重叠了：{capture & modeling}"

    def test_capture_output_satisfies_modeling_input_contract(self):
        """产出 schema 归消费者所有——捕获盒必须满足建模盒的输入契约"""
        d = get_capture_definition()
        out = d.get_schema(d.result.result_schema)
        assert out is not None
        names = {f.name for f in out.fields}
        assert {"set_id", "business_goal", "requirements"} <= names


class TestBeeline:
    def test_five_ops_in_order(self):
        ops = [o.op_id for o in get_requirement_capture_beeline().operations]
        assert ops == [
            "split_source_sentences",
            "extract_requirements",
            "build_requirement_set",
            "verify_source_fidelity",
            "package_requirement_set",
        ]

    def test_single_llm_op(self):
        """只有 extract_requirements 走 LLM——其余 4 个是机械的"""
        assert "requirement_extractor" in get_requirement_capture_beeline().operations[1].type
        for o in get_requirement_capture_beeline().operations[2:]:
            assert o.bee.type.endswith("_impl")

    def test_task_entry_exposes_ops(self):
        e = list_task_entries("requirement_capture_box")[0]
        assert e["task_type"] == "capture_business_requirement"
        assert len(e["beeline_ops"]) == 5


# ============================================================
# op 1: 切句
# ============================================================


class TestSentenceSplitting:
    def test_enum_sentence(self):
        s = _split_sentences("wms的流程有入库流程、出库流程、盘点流程、库内作业流程")
        assert [x["text"] for x in s] == ["wms的流程", "入库流程", "出库流程", "盘点流程", "库内作业流程"]
        assert s[0]["role"] == "goal"
        assert all(x["role"] == "fact" for x in s[1:])

    def test_sentence_ids_are_sequential(self):
        s = _split_sentences("第一句。第二句。第三句。")
        assert [x["sentence_id"] for x in s] == ["sent_1", "sent_2", "sent_3"]
        assert [x["role"] for x in s] == ["goal", "fact", "fact"]

    def test_empty_narrative(self):
        assert _split_sentences("") == []


# ============================================================
# 端到端（降级路径，LLM 禁用）
# ============================================================


class TestCaptureEndToEnd:
    TEXT = "wms的流程有入库流程、出库流程、盘点流程、库内作业流程"

    def test_accepted_with_all_four_checks_passing(self, store: TaskRunStore):
        r = trigger_task(
            "requirement_capture_box", "capture_business_requirement",
            {"narrative": self.TEXT}, store,
        )
        assert r["status"] == "completed"
        assert r["acceptance_status"] == "accepted"
        res = r["result"]
        assert res["contract_compliance"] is True
        assert res["source_coverage"] == 100.0
        assert res["trace_integrity"] is True
        assert res["no_hallucination"] is True

    def test_all_five_ops_executed(self, store: TaskRunStore):
        r = trigger_task(
            "requirement_capture_box", "capture_business_requirement",
            {"narrative": self.TEXT}, store,
        )
        assert [s["op_id"] for s in r["op_trace"]] == [
            "split_source_sentences", "extract_requirements",
            "build_requirement_set", "verify_source_fidelity",
            "package_requirement_set",
        ]

    def test_每条需求都溯源到真实句子_id(self, store: TaskRunStore):
        r = trigger_task(
            "requirement_capture_box", "capture_business_requirement",
            {"narrative": self.TEXT}, store,
        )
        res = r["result"]
        sent_ids = {s["sentence_id"] for s in res["source_sentences"]}
        assert res["requirements"]
        for q in res["requirements"]:
            assert q["trace_to"] in sent_ids

    def test_产出可直接投建模盒(self, store: TaskRunStore):
        """schema 归建模盒所有——捕获盒换实现，建模盒零改动"""
        cap = trigger_task(
            "requirement_capture_box", "capture_business_requirement",
            {"narrative": self.TEXT}, store,
        )
        pkg = cap["result"]
        mod = trigger_task(
            "business_modeling_box", "handle_modeling_request",
            {k: pkg[k] for k in ("set_id", "business_goal", "context", "requirements")},
            store,
        )
        assert mod["status"] == "completed"
        assert mod["result"]["requirement_coverage"] == 100.0

    def test_task_run_persisted(self, store: TaskRunStore):
        trigger_task(
            "requirement_capture_box", "capture_business_requirement",
            {"narrative": self.TEXT}, store,
        )
        rec = store.list_recent()[0]
        assert rec["box_id"] == "requirement_capture_box"
        assert rec["acceptance_status"] == "accepted"

    def test_缺_narrative_被结构闸拦下(self, store: TaskRunStore):
        with pytest.raises(TriggerError, match="violates task_schema"):
            trigger_task(
                "requirement_capture_box", "capture_business_requirement",
                {}, store,
            )
        assert store.list_recent() == []

    def test_空表述_产出零需求_被_no_deliver_拦下(self, store: TaskRunStore):
        r = trigger_task(
            "requirement_capture_box", "capture_business_requirement",
            {"narrative": "   "}, store,
        )
        assert r["status"] == "completed"
        assert r["acceptance_status"] == "rejected"
        assert r["result"]["requirement_count"] == 0


# ============================================================
# 4 项机械验收各自能否抓到问题
# ============================================================


def _run_capture(store, narrative, llm_result=None):
    """跑一次捕获盒；llm_result 不为 None 时假装 LLM 返回它"""
    if llm_result is not None:
        from runtime import llm
        llm.complete_json = lambda *a, **kw: llm_result
    return trigger_task(
        "requirement_capture_box", "capture_business_requirement",
        {"narrative": narrative}, store,
    )


class TestMechanicalGates:
    def test_llm_hallucination_is_dropped_not_trusted(self, store):
        """LLM 编造原文没有的需求 → 被 sentence+原文双重校验剔除"""
        r = _run_capture(
            store, "库存数量不能为负。",
            llm_result={"requirements": [
                {"description": "订单必须7天内退款", "requirement_type": "rule",
                 "trace_to": "sent_1"},
            ]},
        )
        descs = [q["description"] for q in r["result"]["requirements"]]
        assert "订单必须7天内退款" not in descs

    def test_llm_untraceable_is_dropped(self, store):
        """trace_to 指向不存在的句子 → 剔除"""
        r = _run_capture(
            store, "库存数量不能为负。",
            llm_result={"requirements": [
                {"description": "库存数量不能为负", "requirement_type": "rule",
                 "trace_to": "sent_999"},
            ]},
        )
        assert r["result"]["requirements"] == []

    def test_llm_desc_not_in_source_is_dropped(self, store):
        """description 不在原句里（模型改写）→ 剔除"""
        r = _run_capture(
            store, "库存数量不能为负。",
            llm_result={"requirements": [
                {"description": "库存数量不可小于零", "requirement_type": "rule",
                 "trace_to": "sent_1"},
            ]},
        )
        assert r["result"]["requirements"] == []

    def test_llm_illegal_type_is_normalized(self, store):
        r = _run_capture(
            store, "库存数量不能为负。",
            llm_result={"requirements": [
                {"description": "库存数量不能为负", "requirement_type": "POLICY",
                 "trace_to": "sent_1"},
            ]},
        )
        assert r["result"]["requirements"][0]["requirement_type"] == "object"

    def test_llm_accepted_path_reports_llm_extractor(self, store):
        r = _run_capture(
            store, "库存数量不能为负。",
            llm_result={"requirements": [
                {"description": "库存数量不能为负", "requirement_type": "rule",
                 "trace_to": "sent_1"},
            ]},
        )
        assert r["result"]["extractor"] == "llm"
        assert r["result"]["requirements"][0]["requirement_type"] == "rule"

    def test_ids_are_system_generated_not_from_llm(self, store):
        """编号是系统标识，LLM 无权决定"""
        r = _run_capture(
            store, "库存数量不能为负。",
            llm_result={"requirements": [
                {"description": "库存数量不能为负", "requirement_type": "rule",
                 "trace_to": "sent_1", "requirement_id": "i_try_to_choose_this"},
            ]},
        )
        assert r["result"]["requirements"][0]["requirement_id"] == "req_rule_1"

    def test_partial_extraction_lowers_coverage(self, store):
        """LLM 只抽到部分事实句 → source_coverage 掉下来 → rejected

        首句是 business_goal，不计入覆盖率分母——所以这里要 1 goal + 2 fact，
        只抽到其中 1 个 fact，coverage = 50%
        """
        r = _run_capture(
            store, "建模退款流程。订单是核心实体。退款必须在 7 天内完成。",
            llm_result={"requirements": [
                {"description": "订单是核心实体", "requirement_type": "object",
                 "trace_to": "sent_2"},
            ]},
        )
        res = r["result"]
        assert res["source_coverage"] == 50.0
        assert res["evidence_detail"]["uncovered_sentences"] == ["sent_3"]
        assert r["acceptance_status"] == "rejected"

    def test_goal_sentence_not_counted_in_coverage_denominator(self, store):
        """只被 goal 覆盖的句子不算漏——目标本身就是它"""
        r = _run_capture(
            store, "建模退款流程。",
            llm_result={"requirements": []},
        )
        assert r["result"]["source_sentences"][0]["role"] == "goal"
        # 没有 fact 句子 → 分母 0 → coverage 0.0，需求数为 0 → no_deliver
        assert r["result"]["requirement_count"] == 0
        assert r["acceptance_status"] == "rejected"

    def test_degrade_reason_recorded(self, store):
        r = _run_capture(store, "库存数量不能为负。")
        assert r["result"]["extractor"] == "rule"
        assert "no llm" in r["result"]["evidence_detail"]["degrade_reason"]
