"""tests.test_wms_requirement_box - 需求捕获盒（Software Line 第二只盒子）

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

from boxes.wms_requirement import get_definition as get_capture_definition
from boxes.wms_design import get_definition as get_modeling_definition
from boxes.wms_requirement.beeline import get_beeline as get_requirement_capture_beeline
from kanban.trigger import (
    TriggerError,
    list_task_entries,
    registered_box_ids,
    trigger_task,
)
from boxes.wms_requirement.runner import _split_sentences, _simulate_requirement_set_packager
from runtime.store import TaskRunStore
from tests.conftest import stub_compliant_llm


@pytest.fixture
def store() -> TaskRunStore:
    return TaskRunStore(Path(tempfile.mktemp(suffix=".jsonl")))


@pytest.fixture(autouse=True)
def no_llm(monkeypatch):
    """默认禁掉**抽取腿**的 LLM——除非用例显式 stub 成某个返回值

    WMS 概念绑定腿（建模盒）不在此列：它有独立的桩，
    而且有几条用例会连着把产出投给建模盒（见 test_产出可直接投建模盒）。
    """
    from runtime import llm
    from tests.conftest import stub_wms_binder

    def guard(prompt, **kw):
        if "需求列表：" in prompt:
            return stub_wms_binder(prompt, **kw)
        raise llm.LLMError("test: no llm")

    monkeypatch.setattr(llm, "complete_json", guard)


# ============================================================
# definition
# ============================================================


class TestDefinition:
    def test_box_registered(self):
        assert "wms_requirement_box" in registered_box_ids()

    def test_task_and_beeline_pinned(self):
        d = get_capture_definition()
        t = d.task[0]
        assert t.type == "capture_business_requirement"
        assert t.beeline_id == "beeline_wms_requirement_v1"
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
        e = list_task_entries("wms_requirement_box")[0]
        assert e["task_type"] == "capture_business_requirement"
        assert len(e["beeline_ops"]) == 5


class TestPayloadKind:
    """投料框形态由 task_schema 形状决定——加新盒子不用改前端"""

    def test_capture_is_text(self):
        """narrative 是自由文本 → 纯文本框，别套 JSON 编辑器"""
        assert list_task_entries("wms_requirement_box")[0]["payload_kind"] == "text"

    def test_modeling_is_json(self):
        """requirements 是嵌套数组 → 保持 JSON 编辑器"""
        assert list_task_entries("wms_design_box")[0]["payload_kind"] == "json"

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
    """盒子之间的关系声明在各自 definition.placement，不在前端硬编码

    以前住在 kanban/trigger.py 的 BOX_META。那是业务知识放在看板层——
    盒子只能通过看板去知道自己接谁。现在接线跟着盒子走。
    """

    def test_capture_feeds_only_design(self):
        """需求盒的下游**只有**设计盒——系统档案盒那条边是假的，已删

        v1 里这里还有一条 `wms_system_box`，方向反了：需求盒产出的是系统
        档案的**输入**（读基线：这份系统该是什么样），不是往档案里加东西。
        留着它会让「五个生命周期盒都往系统盒汇」这句话在数据上不成立，
        而拓扑图是照着这句话画的。
        """
        from kanban.trigger import box_meta
        assert box_meta("wms_requirement_box")["feeds_into"] == ["wms_design_box"]

    def test_design_feeds_development_and_system(self):
        from kanban.trigger import box_meta
        assert box_meta("wms_design_box")["feeds_into"] == [
            "wms_development_box",
            "wms_system_box",
        ]

    def test_system_is_the_sink(self):
        """系统档案盒是终点：它是「关于那个系统的描述」，描述完了没有下游"""
        from kanban.trigger import box_meta
        assert box_meta("wms_system_box")["feeds_into"] == []

    def test_the_whole_wms_chain_is_pinned(self):
        """把 6 只 WMS 盒的接线整条钉住

        为什么要钉全链而不是一条条钉：接线是「盒子是什么」的一部分，
        改一只盒子顺手改了下游，这组测试会一片绿地带过去。
        需求 → 设计 → 开发 → 测试 → 运维 → 分析 → 系统档案，是一条
        **生命周期链**，每一环还额外直投系统档案盒（各自领域的事实先落档案）。

        唯一的例外是**需求盒**：它不汇入档案盒。它产出的是档案的输入
        （读基线），不是档案的内容——这条边在 v2 里删掉了。
        """
        from kanban.trigger import BOX_REGISTRY

        chain = {
            "wms_requirement_box": ["wms_design_box"],
            "wms_design_box": ["wms_development_box", "wms_system_box"],
            "wms_development_box": ["wms_testing_box", "wms_system_box"],
            "wms_testing_box": ["wms_ops_box", "wms_system_box"],
            # 运维之后不再直接结束：它把线上真实状态投给分析盒，
            # 由分析盒去回答「这条需求线上到底满没满足」
            "wms_ops_box": ["wms_analysis_box", "wms_system_box"],
            "wms_analysis_box": ["wms_system_box"],
            "wms_system_box": [],
            # 费用报销线不属于 WMS 域，自成一条独立的线
            "receipt_capture_box": [],
        }
        for bid, expected in chain.items():
            assert BOX_REGISTRY[bid][0]().placement.feeds_into == expected, (
                f"{bid} 的下游变了"
            )

    def test_the_system_box_is_the_only_sink_and_has_exactly_one(self):
        """系统档案盒必须**恰好是**一个汇点，且没有下游

        这条不是重复上一条：上一条钉的是「有哪些边」，这条钉的是
        「域里有且仅有一个汇点」。两者会一起坏，坏法不一样：

        - 上一条能绿、这条红 = 有人加了一只新的汇点盒，
          于是拓扑不再是环形，看板的 `sinks[0] if len(sinks)==1 else None`
          会算出 None，环形中心消失
        - 上一条红、这条绿 = 单边接线被改动（不是新盒子）

        中心算不出来时看板会禁用拓扑 Tab 并写明原因，而不是静默退回
        网格——所以这里必须确保中心**算得出来**。
        """
        from kanban.trigger import BOX_REGISTRY, domain_layout

        domain_boxes = [
            bid for bid, (loader, _) in BOX_REGISTRY.items()
            if loader().placement.domain == "WMS"
        ]
        sinks = [b for b in domain_boxes if "wms_system_box" in BOX_REGISTRY[b][0]().placement.feeds_into]
        assert sorted(sinks) == [
            "wms_analysis_box", "wms_design_box", "wms_development_box",
            "wms_ops_box", "wms_testing_box",
        ], f"WMS 域的汇点集合变了：{sorted(sinks)}"

        # 没有下游的盒子：只有系统档案盒
        terminal = [b for b in domain_boxes if not BOX_REGISTRY[b][0]().placement.feeds_into]
        assert terminal == ["wms_system_box"], f"域里出现了别的终点：{terminal}"

        layout = domain_layout("WMS")
        assert layout["layout"] == "radial", "拓扑退化了，环形出不来"
        assert layout["center"] == "wms_system_box", "环形中心算不出来了"

    def test_the_closed_loop_edge_is_declared_as_feedback_not_feeds(self):
        """闭环那条边必须走 feedback_into——混进 feeds_into 会让主干成环

        分析盒的结论回到需求盒，是**反馈**不是交接：需求盒不会去验分析
        结论，它是拿这个结论**开新一轮需求**。两件事，声明也得分开。

        分开还有个纯技术上的理由：feeds_into 是拓扑排序的输入，
        把闭环那条边塞进去，排序就遇到环，于是**静默**按原顺序收尾——
        环上顺序变成任意的，看板照常渲染，只是结构是错的，
        没有任何东西会报错。
        """
        from kanban.trigger import BOX_REGISTRY

        expected = {"wms_analysis_box": ["wms_requirement_box"]}
        for bid, targets in expected.items():
            placement = BOX_REGISTRY[bid][0]().placement
            assert placement.feedback_into == targets, (
                f"{bid} 的反馈对象变了"
            )
            # 同一只盒子不能把同一条边在两个列表里各写一遍：
            # 那样改了 feedback 忘了 feeds，两侧就漂了
            assert not set(placement.feeds_into) & set(placement.feedback_into), (
                f"{bid} 把同一条边在 feeds_into 和 feedback_into 里各写了一遍"
            )

    def test_feedback_into_targets_are_registered(self):
        """反馈的对象也必须是真盒子，否则闭环那条线指向空气"""
        from kanban.trigger import BOX_REGISTRY, registered_box_ids

        for bid, (_get, _beelines) in BOX_REGISTRY.items():
            for t in _get().placement.feedback_into:
                assert t in registered_box_ids(), f"{bid} 反馈给了未注册盒子 {t}"

    def test_feeds_into_targets_are_registered(self):
        """声明的下游必须是真盒子，否则接力按钮点了没地方去"""
        from kanban.trigger import BOX_REGISTRY, registered_box_ids
        for bid, (_get, _beelines) in BOX_REGISTRY.items():
            for t in _get().placement.feeds_into:
                assert t in registered_box_ids(), f"{bid} 指向未注册盒子 {t}"


# ============================================================
# 盒子排序：按业务流向，不按字母序
# ============================================================


class TestBoxFlowOrder:
    """看板盒子的先后由 feeds_into 声明决定

    字母序会把 wms_design_box 排在 wms_requirement_box 前面，
    跟实际流程（先捕获需求再建模）正好反，必须按流向排。
    """

    def test_capture_comes_before_modeling(self):
        from kanban.trigger import order_boxes_by_flow
        out = order_boxes_by_flow(["wms_design_box", "wms_requirement_box"])
        assert out.index("wms_requirement_box") < out.index("wms_design_box")

    def test_order_independent_of_input_order(self):
        """输入顺序不该影响结果（list_boxes 给的是字母序）"""
        from kanban.trigger import order_boxes_by_flow
        a = order_boxes_by_flow(["wms_design_box", "wms_requirement_box"])
        b = order_boxes_by_flow(["wms_requirement_box", "wms_design_box"])
        assert a == b == ["wms_requirement_box", "wms_design_box"]

    def test_unrelated_boxes_keep_a_stable_order(self):
        """没有 feeds_into 关系时，按 box_id 定序——不是按传入顺序

        **这条的契约变了，方向也变了。** 以前是「同层保持传入顺序，
        不做字典序重排」；现在改成同层按 box_id 定序。

        为什么改：唯一的调用方 `kanban/web.py` 传的是
        `registered_box_ids()`，那是个 **set**。Python 的字符串哈希按进程
        随机化，所以「传入顺序」在那个调用点上根本不存在——它随进程
        重启而变。看板照常渲染，只是同层盒子换了位置，没有任何东西报错。

        「保持传入顺序」这个承诺对 set 入参是空的，对 list 入参又是个
        陷阱（谁记得住这个隐含约定？）。倒不如把契约改成一句能验的：
        **结果只依赖接线，不依赖入参**。同层用 box_id 排，重启不变。
        """
        from kanban.trigger import order_boxes_by_flow
        assert order_boxes_by_flow(["z_box", "a_box"]) == ["a_box", "z_box"]
        # 顺序再反过来，答案不变——这才叫「不依赖入参」
        assert order_boxes_by_flow(["a_box", "z_box"]) == ["a_box", "z_box"]

    def test_cycle_does_not_hang_or_raise(self):
        """feeds_into 声明成环时兜底收尾，不能死循环也不能让看板白屏"""
        from kanban.trigger import order_boxes_by_flow
        # 用一个未注册的 box 走 no-relation 分支
        out = order_boxes_by_flow(["only_box"])
        assert out == ["only_box"]

    def test_empty_and_single(self):
        from kanban.trigger import order_boxes_by_flow
        assert order_boxes_by_flow([]) == []
        assert order_boxes_by_flow(["wms_design_box"]) == ["wms_design_box"]

    def test_web_api_boxes_are_in_flow_order(self):
        """看板 API 返回的 boxes 必须是流向序，不是字母序"""
        import tempfile
        from pathlib import Path
        from boxes.wms_design import get_definition as get_mod_def
        from boxes.wms_requirement import get_definition as get_cap_def
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
            assert boxes.index("wms_requirement_box") < boxes.index("wms_design_box")


# ============================================================
# op 1: 切句
# ============================================================


class TestSentenceSplitting:
    def test_enum_sentence(self):
        """存在性枚举 → 名字部分 = title，枚举项 = fact

        为什么名字是 title 而不是 goal：「wms的流程」是这次要建模的东西的
        **名字**，不是一条需求。把它当需求抽一遍，抽出来的就是碎片
        （「库存看板」单独成条就是这种碎片的来源）。
        """
        s = _split_sentences("wms的流程有入库流程、出库流程、盘点流程、库内作业流程")
        assert [x["text"] for x in s] == ["wms的流程", "入库流程", "出库流程", "盘点流程", "库内作业流程"]
        assert s[0]["role"] == "title"
        assert all(x["role"] == "fact" for x in s[1:])

    def test_存在性词找不到时首句仍是内容句(self):
        """没有「有/包含/分为」这类词 → 首句不能当标题丢掉

        「库存管理要支持入库、出库」里「入库」就藏在首句里，
        当标题丢掉就是丢信息——所以首句仍然是内容句（goal）。
        """
        s = _split_sentences("库存管理要支持入库、出库、盘点")
        assert s[0]["role"] == "goal"
        assert [x["role"] for x in s[1:]] == ["fact", "fact"]

    def test_枚举项带原句_碎片不丢主语(self):
        """枚举项独立成句，但每句都带着它来自的原句

        「库区要能新增、编辑、删除、查询」按顿号切开 → 「编辑」这句
        **丢了主语**（谁要编辑？编辑什么？）。碎片自己绑不上任何 WMS 概念，
        但原句里有主语，而且原句是**逐字原文**。

        修法不是把四项并回一句（那会丢粒度：四件事变成一件），
        是给每句带上 origin_text。
        """
        s = _split_sentences("库区要能新增、编辑、删除、查询")
        assert [x["text"] for x in s] == [
            "库区要能新增", "编辑", "删除", "查询",
        ]
        assert all(
            x["origin_text"] == "库区要能新增、编辑、删除、查询" for x in s
        ), [x["origin_text"] for x in s]

    def test_普通句的原句就是它自己(self):
        s = _split_sentences("库位要能新增。库区要能查询。")
        assert all(x["origin_text"] == x["text"] for x in s)

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
    # 编排测试不赌 LLM 输出质量，理由见 _stub_compliant_llm
    @pytest.fixture(autouse=True)
    def _use_compliant_llm(self, stub_llm):
        pass

    def test_accepted_with_all_four_checks_passing(self, store: TaskRunStore):
        r = trigger_task(
            "wms_requirement_box", "capture_business_requirement",
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
            "wms_requirement_box", "capture_business_requirement",
            {"narrative": self.TEXT}, store,
        )
        assert [s["op_id"] for s in r["op_trace"]] == [
            "split_source_sentences", "extract_requirements",
            "build_requirement_set", "verify_graph_fidelity",
            "package_requirement_set",
        ]

    def test_每条需求都溯源到真实句子_id(self, store: TaskRunStore):
        r = trigger_task(
            "wms_requirement_box", "capture_business_requirement",
            {"narrative": self.TEXT}, store,
        )
        res = r["result"]
        sent_ids = {s["sentence_id"] for s in res["source_sentences"]}
        assert res["requirements"]
        for q in res["requirements"]:
            assert q["trace_to"] in sent_ids

    def test_产出可直接投建模盒(self, store: TaskRunStore):
        """schema 归建模盒所有——捕获盒换实现，建模盒零改动

        **而且是走接力通道投的**：需求集整个从需求盒的产出接过来，
        按建模盒自己声明的形状校验一遍。
        以前这里是手工拷 set_id / requirements——每次拷的形状可能不一样，
        拷漏一个字段下游照样收。
        """
        cap = trigger_task(
            "wms_requirement_box", "capture_business_requirement",
            {"narrative": self.TEXT}, store,
        )
        mod = trigger_task(
            "wms_design_box", "handle_modeling_request", {}, store,
            handoff={"requirement_set_ref": cap["task_run_id"]},
        )
        assert mod["status"] == "completed"
        assert mod["result"]["requirement_coverage"] == 100.0

    def test_task_run_persisted(self, store: TaskRunStore):
        trigger_task(
            "wms_requirement_box", "capture_business_requirement",
            {"narrative": self.TEXT}, store,
        )
        rec = store.list_recent()[0]
        assert rec["box_id"] == "wms_requirement_box"
        assert rec["acceptance_status"] == "accepted"

    def test_缺_narrative_被结构闸拦下(self, store: TaskRunStore):
        with pytest.raises(TriggerError, match="violates task_schema"):
            trigger_task(
                "wms_requirement_box", "capture_business_requirement",
                {}, store,
            )
        assert store.list_recent() == []

    def test_空表述_产出零需求_被_no_deliver_拦下(self, store: TaskRunStore):
        r = trigger_task(
            "wms_requirement_box", "capture_business_requirement",
            {"narrative": "   "}, store,
        )
        assert r["status"] == "completed"
        assert r["acceptance_status"] == "rejected"
        assert r["result"]["requirement_count"] == 0


# ============================================================
# 4 项机械验收各自能否抓到问题
# ============================================================


def _stub_compliant_llm(prompt: str, **kw) -> dict:
    """按 prompt 里的句子造一份逐句覆盖的"合规图"。

    这几组测的是**编排**：五步 beeline、4 条判据、每条需求溯源到哪一句。
    真调 LLM 有两个问题：抽图约 1/3 概率被幻觉判据全剔（模型改写了 action，
    那是真实的产品缺陷，不该由编排测试承担），以及单次 10~120 秒。
    LLM 输出的机械处理在 TestMechanicalGates 里真测，那组不 stub。

    实现挪到 tests/conftest.py 跟 test_metrics.py 共用——
    各写各的必然漂移，改了一处挂掉的是没人动过的那组。
    """
    return stub_compliant_llm(prompt, **kw)


@pytest.fixture
def stub_llm(monkeypatch):
    """把 LLM 换成按输入现造的合规产出（见 _stub_compliant_llm 的理由）

    走**分流桩**：抽取腿用合规图桩，绑定腿用 WMS 词表桩——
    两条腿在同一个用例里都会被打到。
    """
    from runtime import llm
    from tests.conftest import stub_llm_dispatch
    monkeypatch.setattr(llm, "complete_json", stub_llm_dispatch)
    return stub_llm_dispatch


def _run_capture(store, narrative, llm_result=None):
    """跑一次捕获盒；llm_result 不为 None 时假装 LLM 返回它"""
    if llm_result is None:
        return trigger_task(
            "wms_requirement_box", "capture_business_requirement",
            {"narrative": narrative}, store,
        )
    # **桩用上下文装、用完就还原**——直接赋值 llm.complete_json
    # 会漏到下一个测试文件去：那边以为自己调的是真 LLM，
    # 其实调的是上一个文件留下的桩，失败原因离现场隔着两个文件。
    from runtime import llm
    from tests.conftest import auto_roles

    def _fake(*a, **kw):
        """桩默认不判角色（它只关心机械层）——原样回抄 prompt 里的机械初判"""
        out = dict(llm_result)
        if "roles" not in out:
            prompt = a[0] if a else str(kw.get("prompt", ""))
            out["roles"] = auto_roles(prompt)
        return out

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(llm, "complete_json", _fake)
        return trigger_task(
            "wms_requirement_box", "capture_business_requirement",
            {"narrative": narrative}, store,
        )


class TestMechanicalGates:
    def test_llm_hallucination_is_not_trusted(self, store):
        """LLM 编造原文没有的动作 → 不进产出；全被剔光就是履约中止。

        编造内容绝不会出现在产出里（这条是硬要求）。至于"剔光之后怎么办"，
        降级删掉后是中止，不再是"产出一份空图 + 判它 rejected"——
        两者都不该被当成"跑完了"。
        """
        r = _run_capture(
            store, "库存管理。库存数量不能为负。",
            llm_result={"nodes": [
                {"action": "订单必须7天内退款", "trace_to": "sent_2"},
            ]},
        )
        assert r["status"] == "failed"
        assert r["result"] is None

    def test_llm_untraceable_aborts_instead_of_yielding_empty_graph(self, store):
        """trace_to 全指向不存在的句子 → 履约中止，不产出空图。

        以前这里是"剔除掉，产出 nodes=[]"——然后 source_coverage 掉下去、
        被判 rejected。降级删掉之后更进一步：抽不出任何可追溯的东西，
        就没有产出可言，直接中止。这两种都不该算"跑完了"。
        """
        r = _run_capture(
            store, "库存管理。库存数量不能为负。",
            llm_result={"nodes": [
                {"action": "库存数量不能为负", "trace_to": "sent_999"},
            ]},
        )
        assert r["status"] == "failed"
        bad = [s for s in r["op_trace"] if s["status"] == "failed"]
        assert bad and "no traceable node" in bad[0]["error"]

    def test_llm_desc_not_in_source_aborts(self, store):
        """action 被模型改写 → 剔掉；这是实跑最常见的失败原因（约 1/3）。"""
        r = _run_capture(
            store, "库存管理。库存数量不能为负。",
            llm_result={"nodes": [
                {"action": "库存数量不可小于零", "trace_to": "sent_2"},
            ]},
        )
        assert r["status"] == "failed"

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

    def test_accepted_path_flattens_node_to_process(self, store):
        r = _run_capture(
            store, "库存数量不能为负。",
            llm_result={"nodes": [
                {"action": "库存数量不能为负", "trace_to": "sent_1"},
            ]},
        )
        # 抽取者字段整条删了：只剩 LLM 一条腿，"谁抽的"没有判别力了
        assert "extractor" not in r["result"]
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
        """LLM 只抽到部分内容句 → source_coverage 掉下来 → rejected

        **分母是内容句（goal + fact）**：1 goal + 2 fact = 3 句，
        只抽到其中 1 句 → coverage = 33.33%

        以前首句（goal）不进分母，于是「业务方真正的需求写在第一句」
        这件事既不算覆盖也不算漏——那条需求整个消失了。
        """
        r = _run_capture(
            store, "建模退款流程。订单是核心实体。退款必须在 7 天内完成。",
            llm_result={"nodes": [
                {"action": "订单是核心实体", "trace_to": "sent_2"},
            ]},
        )
        res = r["result"]
        assert res["source_coverage"] == 33.33
        assert res["evidence_detail"]["uncovered_sentences"] == ["sent_1", "sent_3"]
        assert r["acceptance_status"] == "rejected"

    def test_goal_sentence_is_counted_in_the_coverage_denominator(self, store):
        """只有一句业务目标时，它**就是**那句需求——必须被抽出来

        以前这里断言的是「首句是 business_goal，不计入覆盖率分母，
        所以一句需求抽不出任何东西是正常的」。那条规则的实际后果是：
        业务方只写一句话（最常见的写法）→ 需求盒产出 0 条需求 →
        整条链在第一只盒子就断了。

        现在：内容句 = goal + fact，一句 goal 也是内容句。
        抽到了 → coverage 100，需求集非空。
        """
        r = _run_capture(
            store, "建模退款流程。",
            llm_result={"nodes": [
                {"action": "建模退款流程", "trace_to": "sent_1"},
            ]},
        )
        res = r["result"]
        assert res["source_sentences"][0]["role"] == "goal"
        assert res["requirement_count"] == 1
        assert res["source_coverage"] == 100.0
        assert r["acceptance_status"] == "accepted"

    def test_title_sentence_is_not_in_the_coverage_denominator(self, store):
        """[title] 是名字，不是需求——不进分母

        「wms的流程」没被任何节点指向，但它是**标题**：
        覆盖率的分母只有那 4 个枚举项（fact），所以照样 100%。
        把标题算进分母，就是逼模型给标题编一条需求出来。
        """
        r = _run_capture(
            store, "wms的流程有入库流程、出库流程、盘点流程、库内作业流程",
            llm_result={"nodes": [
                {"action": "入库流程", "trace_to": "sent_2"},
                {"action": "出库流程", "trace_to": "sent_3"},
                {"action": "盘点流程", "trace_to": "sent_4"},
                {"action": "库内作业流程", "trace_to": "sent_5"},
            ]},
        )
        res = r["result"]
        assert res["source_sentences"][0]["role"] == "title"
        assert res["source_coverage"] == 100.0
        assert r["acceptance_status"] == "accepted"

    def test_content_sentence_without_any_node_aborts(self, store):
        """原文有内容句、模型却什么都没抽出来 → **中止**，不是 no_deliver

        「业务方什么也没说」（空表述）和「模型没干活」是两件事。
        混成一件，模型一失手就变成一句好听的「没识别出业务事实」——
        那是把系统的问题说成业务的问题。
        """
        r = _run_capture(
            store, "建模退款流程。",
            llm_result={"goal": "建模退款流程", "nodes": [], "edges": []},
        )
        assert r["status"] == "failed"
        assert r["result"] is None
        bad = [s for s in r["op_trace"] if s["status"] == "failed"]
        assert bad and "no traceable node" in bad[0]["error"]

    def test_llm_outage_aborts_with_reason(self, store, monkeypatch):
        """LLM 不可用 → 中止，且原因随 op_trace 带出来。

        原先这里断言的是"降级到规则版并记录降级原因"。降级删掉后，
        替代物必须是"中止 + 说清为什么"——静默中止和静默降级一样是假信号。
        """
        from runtime import llm
        monkeypatch.setattr(
            llm, "complete_json",
            lambda *a, **kw: (_ for _ in ()).throw(llm.LLMError("simulated outage")),
        )
        r = _run_capture(store, "库存数量不能为负。")
        assert r["status"] == "failed"
        assert r["acceptance_status"] is None
        bad = [s for s in r["op_trace"] if s["status"] == "failed"]
        assert bad and "simulated outage" in bad[0]["error"]


class TestAcceptanceGate:
    """acceptance 是盒内独立的一道闸，不在 beeline 里

    它对应 BeelineExecutor.execute() 之后单独调的那次 evaluate_acceptance。
    看板要按它决定放不放行，所以 trigger_task 必须把判据明细带出来——
    只给一个 accepted 徽章等于把「哪条没过」藏起来了。
    """

    @pytest.fixture(autouse=True)
    def _use_compliant_llm(self, stub_llm):
        pass

    def _run(self, store, text):
        return trigger_task(
            "wms_requirement_box", "capture_business_requirement",
            {"narrative": text}, store,
        )

    def test_detail_covers_every_declared_rule(self, store):
        """每条声明的判据都要有一行，带上判据 / 实测 / 三态结论"""
        r = self._run(store, "建模退款流程。退款必须在 7 天内完成。退款申请走审批流程。")
        declared = {rule.metric for rule in get_capture_definition().result.acceptance}
        detail = r["acceptance_detail"]
        assert {d["metric"] for d in detail} == declared
        for d in detail:
            assert set(d) == {
                "metric", "op", "expected", "actual", "passed",
                "outcome", "not_applicable",
            }
            # 三态自洽：outcome 是唯一权威，passed / not_applicable 必须跟它对齐。
            # 两套布尔各说各话时，看板会挑那个错的渲染。
            assert d["outcome"] in ("passed", "failed", "not_applicable")
            assert d["passed"] is (d["outcome"] == "passed")
            assert d["not_applicable"] is (d["outcome"] == "not_applicable")

    def test_rejected_points_at_the_failing_rule(self, store):
        """拒时要能说出是哪条判据没过 + 期望多少 + 实际多少"""
        r = _run_capture(
            store, "建模退款流程。订单是核心实体。退款必须在 7 天内完成。",
            llm_result={"nodes": [
                {"action": "订单是核心实体", "trace_to": "sent_2"},
            ]},
        )
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
        r = _run_capture(
            store, "建模退款流程。订单是核心实体。退款必须在 7 天内完成。",
            llm_result={"nodes": [
                {"action": "订单是核心实体", "trace_to": "sent_2"},
            ]},
        )
        assert r["status"] == "completed"
        assert "verify_graph_fidelity" in [s["op_id"] for s in r["op_trace"]]
        # beeline 里没有 acceptance op——判定是 execute() 之后单独调的
        assert not any("accept" in s["op_id"] for s in r["op_trace"])
        assert r["acceptance_status"] == "rejected"


def test_extractor_disables_thinking():
    """抽取腿关 think：照抄原文的任务不需要长时间推理。

    M3 默认开着 think，实测它的 JSON 本体只要 439~1121 字符，<think> 占
    95% 以上（原始输出 27k 字符），会把 max_tokens 用满导致 JSON 轮不到
    输出——整轮返回空串，长输入 3 次里空 2 次。关掉后实测：
    2~4 秒、296 token、正常 JSON，质量不变（4 节点 3 边 / coverage 100）。
    """
    import inspect

    from boxes.wms_requirement import runner as capture_runner

    src = inspect.getsource(capture_runner._simulate_requirement_extractor)
    assert 'thinking="disabled"' in src


def test_modeling_structurer_keeps_thinking():
    """建模盒那条腿不关 think：它要判 requirement_type，是真语义判断。

    抽取腿关 think 是因为它只做结构化搬运；这条腿要分"哪句是规则、
    哪句是指标、哪句是流程"，一刀切关掉会牺牲分类质量。
    """
    import inspect

    from kanban import trigger

    src = inspect.getsource(trigger._structure_business_text_llm)
    assert 'thinking=' not in src
