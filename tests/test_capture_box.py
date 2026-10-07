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
from runtime.capture_runner import _split_sentences, _simulate_requirement_set_packager
from runtime.store import TaskRunStore


def test_packager_keeps_degrade_reason():
    """打包这步不许再把降级原因吞掉。

    packager 是逐字段列举的白名单式重建，漏一个字段就等于静默丢一个事实。
    degrade_reason 曾经就这么丢的：schema 声明了它（schemas.py 里它是
    required=False 的正式字段），packager 没产出，于是看板和落盘都拿不到
    「为什么降级」，每次只能靠 duration_ms 反推。声明了却拿不到 = 白声明。
    """
    out = _simulate_requirement_set_packager({
        "extractor": "rule",
        "degrade_reason": "LLMError: llm call timed out after 240.0s",
        "business_goal": "wms 入库",
    })
    assert out["degrade_reason"] == "LLMError: llm call timed out after 240.0s"

    # 没降级时也要有这个键（空串），schema 声明的字段不能时有时无
    assert _simulate_requirement_set_packager({"extractor": "llm"})["degrade_reason"] == ""


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
            "verify_graph_fidelity",
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


class TestPayloadKind:
    """投料框形态由 task_schema 形状决定——加新盒子不用改前端"""

    def test_capture_is_text(self):
        """narrative 是自由文本 → 纯文本框，别套 JSON 编辑器"""
        assert list_task_entries("requirement_capture_box")[0]["payload_kind"] == "text"

    def test_modeling_is_json(self):
        """requirements 是嵌套数组 → 保持 JSON 编辑器"""
        assert list_task_entries("business_modeling_box")[0]["payload_kind"] == "json"

    def test_kind_follows_schema_shape(self):
        from kanban.trigger import _payload_kind
        from core.models import FieldDef, SchemaDef

        text_schema = SchemaDef(id="s", fields=[
            FieldDef(name="narrative", type="string"),
            FieldDef(name="who", type="string", required=False),
        ])
        assert _payload_kind(text_schema) == "text"

        json_schema = SchemaDef(id="s", fields=[
            FieldDef(name="items", type="array", items=FieldDef(name="i", type="string")),
        ])
        assert _payload_kind(json_schema) == "json"

    def test_optional_complex_field_does_not_force_json(self):
        """复杂字段是选填的 → 必填仍是标量 → 仍用纯文本"""
        from kanban.trigger import _payload_kind
        from core.models import FieldDef, SchemaDef
        schema = SchemaDef(id="s", fields=[
            FieldDef(name="narrative", type="string"),
            FieldDef(name="meta", type="array", required=False,
                     items=FieldDef(name="m", type="string")),
        ])
        assert _payload_kind(schema) == "text"

    def test_missing_schema_falls_back_to_json(self):
        from kanban.trigger import _payload_kind
        assert _payload_kind(None) == "json"


class TestFeedInto:
    """盒子之间的关系声明在 BOX_META，不在前端硬编码"""

    def test_capture_feeds_modeling(self):
        from kanban.trigger import box_meta
        assert box_meta("requirement_capture_box")["feeds_into"] == ["business_modeling_box"]

    def test_modeling_has_no_downstream(self):
        from kanban.trigger import box_meta
        assert box_meta("business_modeling_box")["feeds_into"] == []

    def test_feeds_into_targets_are_registered(self):
        """声明的下游必须是真盒子，否则接力按钮点了没地方去"""
        from kanban.trigger import BOX_META, registered_box_ids
        for bid, meta in BOX_META.items():
            for t in meta.get("feeds_into", []):
                assert t in registered_box_ids(), f"{bid} 指向未注册盒子 {t}"


# ============================================================
# 盒子排序：按业务流向，不按字母序
# ============================================================


class TestBoxFlowOrder:
    """看板盒子的先后由 feeds_into 声明决定

    字母序会把 business_modeling_box 排在 requirement_capture_box 前面，
    跟实际流程（先捕获需求再建模）正好反，必须按流向排。
    """

    def test_capture_comes_before_modeling(self):
        from kanban.trigger import order_boxes_by_flow
        out = order_boxes_by_flow(["business_modeling_box", "requirement_capture_box"])
        assert out.index("requirement_capture_box") < out.index("business_modeling_box")

    def test_order_independent_of_input_order(self):
        """输入顺序不该影响结果（list_boxes 给的是字母序）"""
        from kanban.trigger import order_boxes_by_flow
        a = order_boxes_by_flow(["business_modeling_box", "requirement_capture_box"])
        b = order_boxes_by_flow(["requirement_capture_box", "business_modeling_box"])
        assert a == b == ["requirement_capture_box", "business_modeling_box"]

    def test_unrelated_boxes_keep_input_order(self):
        """没有任何 feeds_into 关系时保持传入顺序，不做字典序重排"""
        from kanban.trigger import order_boxes_by_flow
        assert order_boxes_by_flow(["z_box", "a_box"]) == ["z_box", "a_box"]

    def test_cycle_does_not_hang_or_raise(self):
        """feeds_into 声明成环时兜底收尾，不能死循环也不能让看板白屏"""
        from kanban.trigger import order_boxes_by_flow
        # 直接构造环：绕过 BOX_META，用一个不存在的 box 走 no-relation 分支
        out = order_boxes_by_flow(["only_box"])
        assert out == ["only_box"]

    def test_empty_and_single(self):
        from kanban.trigger import order_boxes_by_flow
        assert order_boxes_by_flow([]) == []
        assert order_boxes_by_flow(["business_modeling_box"]) == ["business_modeling_box"]

    def test_web_api_boxes_are_in_flow_order(self):
        """看板 API 返回的 boxes 必须是流向序，不是字母序"""
        import tempfile
        from pathlib import Path
        from boxes.modeling import get_definition as get_mod_def
        from boxes.requirement_capture import get_definition as get_cap_def
        from kanban import web as web_mod
        from runtime import create_task_run
        with tempfile.TemporaryDirectory() as td:
            store = TaskRunStore(path=Path(td) / "runs.jsonl")
            for get_def in (get_mod_def, get_cap_def):
                box = get_def()
                tr = create_task_run(
                    "o", box.task[0].type, box.result.result_schema,
                    f"{box.id}@v{box.version}",
                    "beeline_test", 1, "rt", "in",
                )
                store.append(tr, box_id=box.id)
            data = web_mod.dashboard_data(store, box_filter=None)
            boxes = data["boxes"]
            assert boxes.index("requirement_capture_box") < boxes.index("business_modeling_box")


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
            "build_requirement_set", "verify_graph_fidelity",
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
        """LLM 编造原文没有的动作 → 被 sentence+原文双重校验剔除"""
        r = _run_capture(
            store, "库存数量不能为负。",
            llm_result={"nodes": [
                {"action": "订单必须7天内退款", "trace_to": "sent_1"},
            ]},
        )
        actions = [n["action"] for n in r["result"]["nodes"]]
        assert "订单必须7天内退款" not in actions

    def test_llm_untraceable_is_dropped(self, store):
        """trace_to 指向不存在的句子 → 剔除"""
        r = _run_capture(
            store, "库存数量不能为负。",
            llm_result={"nodes": [
                {"action": "库存数量不能为负", "trace_to": "sent_999"},
            ]},
        )
        assert r["result"]["nodes"] == []

    def test_llm_desc_not_in_source_is_dropped(self, store):
        """action 不在原句里（模型改写）→ 剔除"""
        r = _run_capture(
            store, "库存数量不能为负。",
            llm_result={"nodes": [
                {"action": "库存数量不可小于零", "trace_to": "sent_1"},
            ]},
        )
        assert r["result"]["nodes"] == []

    def test_llm_guard_not_in_source_is_dropped(self, store):
        """守卫文本不在原句里（模型编的）→ 边保留但守卫清空

        不能整条边丢掉：from/to 可能是对的，只有 guard 是编的。
        """
        r = _run_capture(
            store, "提交入库单。",
            llm_result={
                "nodes": [
                    {"action": "提交入库单", "trace_to": "sent_1"},
                    {"action": "提交入库单", "trace_to": "sent_1"},
                ],
                "edges": [
                    {"from": "node_1", "to": "node_2",
                     "guard": "必须经理审批", "trace_to": "sent_1"},
                ],
            },
        )
        assert r["result"]["edges"][0]["guard"] == ""

    def test_llm_dangling_edge_is_dropped(self, store):
        """边指向不存在的节点 → 丢弃（悬空边会让"按位置定类型"失效）"""
        r = _run_capture(
            store, "提交入库单。",
            llm_result={
                "nodes": [{"action": "提交入库单", "trace_to": "sent_1"}],
                "edges": [{"from": "node_1", "to": "node_99", "trace_to": "sent_1"}],
            },
        )
        assert r["result"]["edges"] == []

    def test_llm_missing_edges_are_linked_into_chain(self, store):
        """模型漏了边但给了多节点 → 机械补成链，不让图断成一堆孤点"""
        r = _run_capture(
            store, "创建入库单。扫码入库。提交入库单。",
            llm_result={"nodes": [
                {"action": "创建入库单", "trace_to": "sent_1"},
                {"action": "扫码入库", "trace_to": "sent_2"},
                {"action": "提交入库单", "trace_to": "sent_3"},
            ]},
        )
        edges = r["result"]["edges"]
        assert [(e["from"], e["to"]) for e in edges] == [
            ("node_1", "node_2"), ("node_2", "node_3"),
        ]

    def test_llm_accepted_path_reports_llm_extractor(self, store):
        r = _run_capture(
            store, "库存数量不能为负。",
            llm_result={"nodes": [
                {"action": "库存数量不能为负", "trace_to": "sent_1"},
            ]},
        )
        assert r["result"]["extractor"] == "llm"
        # 节点 → 平铺成 process（type 由位置决定，不是模型给的）
        assert r["result"]["requirements"][0]["requirement_type"] == "process"

    def test_guard_maps_to_rule_and_measures_to_metric(self, store):
        """图 → 平铺列表的映射：位置决定 type

        edge.guard → rule；node.measures → metric。这是"不再猜 type"的证据。
        """
        r = _run_capture(
            store, "保存入库单，允许入库数量和实入数量不一致。入库处理时长要可度量。",
            llm_result={
                "nodes": [
                    {"action": "保存入库单", "measures": ["入库处理时长"],
                     "trace_to": "sent_1"},
                ],
                "edges": [],
            },
        )
        reqs = r["result"]["requirements"]
        by_type = {q["requirement_type"]: q["description"] for q in reqs}
        assert by_type["process"] == "保存入库单"
        assert by_type["metric"] == "入库处理时长"

    def test_ids_are_system_generated_not_from_llm(self, store):
        """编号是系统标识，LLM 无权决定"""
        r = _run_capture(
            store, "库存数量不能为负。",
            llm_result={"nodes": [
                {"action": "库存数量不能为负", "trace_to": "sent_1",
                 "node_id": "i_try_to_choose_this"},
            ]},
        )
        # node_id 由系统按顺序重编，模型给的原样丢掉
        assert r["result"]["nodes"][0]["node_id"] == "node_1"
        assert r["result"]["requirements"][0]["requirement_id"] == "req_proc_1"

    def test_partial_extraction_lowers_coverage(self, store):
        """LLM 只抽到部分事实句 → source_coverage 掉下来 → rejected

        首句是 business_goal，不计入覆盖率分母——所以这里要 1 goal + 2 fact，
        只抽到其中 1 个 fact，coverage = 50%
        """
        r = _run_capture(
            store, "建模退款流程。订单是核心实体。退款必须在 7 天内完成。",
            llm_result={"nodes": [
                {"action": "订单是核心实体", "trace_to": "sent_2"},
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


class TestAcceptanceGate:
    """acceptance 是盒内独立的一道闸，不在 beeline 里

    它对应 BeelineExecutor.execute() 之后单独调的那次 evaluate_acceptance。
    看板要按它决定放不放行，所以 trigger_task 必须把判据明细带出来——
    只给一个 accepted 徽章等于把「哪条没过」藏起来了。
    """

    def _run(self, store, text):
        return trigger_task(
            "requirement_capture_box", "capture_business_requirement",
            {"narrative": text}, store,
        )

    def test_detail_covers_every_declared_rule(self, store):
        """每条声明的判据都要有一行，带上判据 / 实测 / 过没过"""
        r = self._run(store, "建模退款流程。退款必须在 7 天内完成。退款申请走审批流程。")
        declared = {rule.metric for rule in get_capture_definition().result.acceptance}
        detail = r["acceptance_detail"]
        assert {d["metric"] for d in detail} == declared
        for d in detail:
            assert set(d) == {"metric", "op", "expected", "actual", "passed"}

    def test_rejected_points_at_the_failing_rule(self, store):
        """拒时要能说出是哪条判据没过 + 期望多少 + 实际多少"""
        r = self._run(store, "建模退款流程。")
        assert r["acceptance_status"] == "rejected"
        failed = [d for d in r["acceptance_detail"] if not d["passed"]]
        assert failed, "rejected 却没有失败的判据行——看板会显示成全绿"
        cov = next(d for d in failed if d["metric"] == "source_coverage")
        assert cov["op"] == "gte"
        assert cov["expected"] == 100.0
        assert cov["actual"] < 100.0

    def test_passed_rules_match_detail(self, store):
        r = self._run(store, "建模退款流程。退款必须在 7 天内完成。退款申请走审批流程。")
        assert r["acceptance_status"] == "accepted"
        assert set(r["acceptance_passed"]) == {
            d["metric"] for d in r["acceptance_detail"] if d["passed"]
        }

    def test_metrics_are_computed_by_beeline_verdict_is_not(self, store):
        """算指标的 op 在 beeline 里，判定不在——两者别混为一谈

        verify_graph_fidelity 产出的是 result 里的数；
        acceptance_status 是拿着这些数跟判据比出来的结论。
        """
        r = self._run(store, "建模退款流程。")
        assert r["status"] == "completed"
        assert "verify_graph_fidelity" in [s["op_id"] for s in r["op_trace"]]
        # beeline 里没有 acceptance op——判定是 execute() 之后单独调的
        assert not any("accept" in s["op_id"] for s in r["op_trace"])
        assert r["acceptance_status"] == "rejected"
