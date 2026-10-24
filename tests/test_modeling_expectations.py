"""tests/test_modeling_expectations.py - 可判定的预期（需求侧的预期载体）

**这组测试验的是「载体」**：模型认出来的每个 WMS 概念，
是不是真的变成了一条「这条需求怎样才算被满足」的可判定预期。

为什么要有它：
以前需求集里只有需求描述（散文），没有任何东西说得出**怎么算被满足**。
于是「验证业务价值」会退化成「验这个名字存不存在」——名字在，通过。
那是验证词汇表，不是验证业务。

**这组测试验的不是「测试盒测得准不准」**——那是 Step 3 的事。
这组验的是：载体存在、忠实于原文、没在链路中途被吃掉、
以及下游两只盒声明了它们要消费的形状。

**每条守卫都必须能独立触发它要守的那个失败**：
别的闸先拦下来 = 这条守卫不承重。
"""
from __future__ import annotations

import pytest

from boxes.modeling.domain import statement_for_kind
from boxes.modeling.runner import (
    _simulate_check_consistency,
    _simulate_generate_evidence,
    _simulate_generate_model_elements,
    _simulate_package_model,
    _simulate_verify_coverage,
)
from runtime import llm


REQS = [
    {"requirement_id": "r_obj", "requirement_type": "object",
     "description": "库存记录要按 SKU 和库位分别记账"},
    {"requirement_id": "r_rule", "requirement_type": "rule",
     "description": "同一 SKU 出库按效期升序分配"},
]

ELEMENT_INPUT = {
    "set_id": "s1",
    "requirements": REQS,
    "valid_ids": ["r_obj", "r_rule"],
    "modelable_ids": ["r_obj", "r_rule"],
    "by_type": {"object": ["r_obj"], "rule": ["r_rule"]},
}


def _stub(payload: dict):
    def fake(prompt, **kw):
        return payload
    return fake


@pytest.fixture(autouse=True)
def _no_real_llm(monkeypatch):
    monkeypatch.setattr(
        llm, "complete_json",
        lambda *a, **kw: (_ for _ in ()).throw(llm.LLMError("test: stub it yourself")),
    )


def _chain(monkeypatch, bindings: list):
    """跑完整链：generate → verify → consistency → evidence → package

    **evidence 那跳不能省**：省了的话，package 拿到的就是
    consistency 的输出而不是 evidence 的——而判据值住在 evidence 那一跳，
    于是 `expectation_completeness` 读到的是 None → 默认 False。
    「少跑一跳」和「吃掉一传」长得很像：都是拿到默认值而不报错。
    """
    monkeypatch.setattr(llm, "complete_json", _stub({"bindings": bindings}))
    elements = _simulate_generate_model_elements(dict(ELEMENT_INPUT))
    covered = _simulate_verify_coverage(elements)
    consistent = _simulate_check_consistency(covered)
    evidence = _simulate_generate_evidence(consistent)
    return elements, consistent, _simulate_package_model(evidence)


# ============================================================
# 载体本身
# ============================================================


class TestTheExpectationCarrier:
    def test_every_binding_becomes_one_expectation(self, monkeypatch):
        """模型认出来的每个 WMS 概念 = 一条预期

        隔离：这条只查 (requirement_id, wms_kind) 的**集合相等**。
        「statement 是不是词表里的」「evidence 是不是原文」
        各有各的守卫。
        """
        _, consistent, package = _chain(monkeypatch, [
            {"requirement_id": "r_obj", "kinds": [
                {"kind": "sku", "evidence": "SKU"}]},
            {"requirement_id": "r_rule", "kinds": [
                {"kind": "fefo", "evidence": "效期升序"}]},
        ])
        got = {(e["requirement_id"], e["wms_kind"])
               for e in package["expectations"]}
        assert got == {("r_obj", "sku"), ("r_rule", "fefo")}, (
            f"每个绑定都该有一条预期，实际是 {got}"
        )

    def test_the_expectation_statement_comes_from_the_vocabulary(self, monkeypatch):
        """statement 必须是**词表里的正式表述**，不是现编的一句话

        「预期」要能被下游机械地对照，所以它必须指向一个**已登记**的概念。
        编一句「系统应当优先发货」出来，下游拿它对不上任何东西。

        隔离：这条只查 statement 的来源（== 词表里的那句）。
        """
        _, _, package = _chain(monkeypatch, [
            {"requirement_id": "r_rule", "kinds": [
                {"kind": "fefo", "evidence": "效期升序"}]},
        ])
        exp = package["expectations"][0]
        assert exp["statement"] == statement_for_kind("fefo"), (
            f"预期的表述不是词表里那一句（{exp['statement']!r}）——"
            "编出来的一句话下游对不上任何东西"
        )
        assert exp["statement"], "预期没有表述——下游拿它对不上任何东西"

    def test_the_expectation_evidence_is_a_literal_substring(self, monkeypatch):
        """evidence 必须是需求描述的**逐字**片段

        这是「预期忠于业务原话」的锚点。改写过的证据证明不了任何事。

        隔离：这条只查 evidence 与需求描述的子串关系。
        """
        _, _, package = _chain(monkeypatch, [
            {"requirement_id": "r_rule", "kinds": [
                {"kind": "fefo", "evidence": "效期升序"}]},
        ])
        exp = package["expectations"][0]
        description = next(r["description"] for r in REQS
                           if r["requirement_id"] == exp["requirement_id"])
        assert exp["evidence"] in description, (
            f"预期证据 {exp['evidence']!r} 不是需求描述 {description!r} 的逐字片段"
        )
        assert exp["evidence"], "预期没有证据——不知道它来自哪句原话"

    def test_an_unbound_requirement_produces_no_expectation(self, monkeypatch):
        """绑不上的需求**不产生预期**——而且不因此判红

        「认不出」是业务结果（这条需求可能真不是 WMS 概念），
        由 `wms_binding` 判红负责。它不该在这里变成另一条判据。

        隔离：这条只查「绑不上的需求不产生预期」。
        """
        _, consistent, package = _chain(monkeypatch, [
            {"requirement_id": "r_obj", "kinds": [
                {"kind": "sku", "evidence": "SKU"}]},
            {"requirement_id": "r_rule", "kinds": []},
        ])
        assert {e["requirement_id"] for e in package["expectations"]} == {"r_obj"}, (
            "绑不上的需求不该产生预期"
        )
        # 绑不上的那条需求，是 wms_binding 判红，不是预期载体判红
        assert package["wms_binding"] is False
        assert package["expectation_completeness"] is True

    def test_the_same_requirement_concept_pair_yields_one_expectation(self):
        """同一个 (需求, 概念) 只出一条预期，且 id 唯一

        同一个需求可能同时绑到几个概念（库存要按 SKU 和库位分别记账），
        那时每个概念一条；但**同一个** (需求, 概念) 出现两次
        就会让下游把一条预期验两遍。

        **为什么直接测派生函数，而不跑整条链**：
        整条链里每个元素只带自己那一个概念（`wms_bindings` 是
        「这个元素代表哪个概念」，不是「这条需求绑了哪些概念」），
        所以同一对天然只出现一次——**那条路走不到去重逻辑**，
        拿它当守卫就是「看着在」：去重坏了它照样绿。

        这里手造两个带同一个 (需求, 概念) 的元素，
        去重逻辑坏掉就立刻红。

        隔离：这条只测 `_expectations_from_elements` 一个函数。
        """
        from boxes.modeling.runner import _expectations_from_elements

        dup = [{"trace_to": "r1",
                "wms_bindings": [{"kind": "sku", "evidence": "SKU"}]}]
        out = _expectations_from_elements(dup, dup, [], [], [])
        pairs = [(e["requirement_id"], e["wms_kind"]) for e in out]
        assert pairs == [("r1", "sku")], (
            f"同一个 (需求, 概念) 出了多条预期：{pairs}"
        )
        ids = [e["expectation_id"] for e in out]
        assert len(ids) == len(set(ids)), f"预期 id 重复了：{ids}"

    def test_the_carrier_reaches_the_top_level_of_the_package(self, monkeypatch):
        """载体必须在包的**顶层**——下游按 schema 取数据，不去挖 evidence

        埋在 evidence_detail 里的话，照 schema 写的消费方取不到它。
        """
        _, _, package = _chain(monkeypatch, [
            {"requirement_id": "r_rule", "kinds": [
                {"kind": "fefo", "evidence": "效期升序"}]},
        ])
        assert package.get("expectations"), "包顶层没有预期载体"
        assert package["evidence"]["expectations"], "evidence 里也没有预期载体"


# ============================================================
# 判据：载体在链路中途被吃掉要能判红
# ============================================================


class TestTheCompletenessCriterionCanActuallyFail:
    def test_a_dropped_carrier_turns_the_criterion_red(self, monkeypatch):
        """中间那一跳把载体吃了 → 判据必须红

        载体在 `generate_model_elements` 产出，在 `check_consistency` 核。
        **隔了两跳**——所以这一条验的是「跨跳的传输没坏」。

        隔离：这条**直接构造**「载体被吃掉」的输入，
        不依赖任何别的守卫先红。
        """
        monkeypatch.setattr(llm, "complete_json", _stub({"bindings": [
            {"requirement_id": "r_rule", "kinds": [
                {"kind": "fefo", "evidence": "效期升序"}]},
        ]}))
        elements = _simulate_generate_model_elements(dict(ELEMENT_INPUT))
        assert elements["expectations"], "载体没产出——这条守卫的前提不成立"

        # 模拟中间跳吃掉载体
        covered = _simulate_verify_coverage(elements)
        covered["expectations"] = []
        consistent = _simulate_check_consistency(covered)
        # **evidence 那跳必须跑**：判据值住在 evidence 那一跳，
        # 少跑一跳的话 package 读到的是默认值 False——
        # 那样这条守卫会**因为错误的原因**变红（假红）。
        evidence = _simulate_generate_evidence(consistent)
        package = _simulate_package_model(evidence)

        assert package["expectation_completeness"] is False, (
            "载体被吃掉了，判据还是绿的——那这条判据不承重"
        )

    def test_the_criterion_is_declared_in_the_result_schema(self):
        """判据指向的字段必须在 result schema 里

        acceptance 指向一个 schema 里没有的字段 = 判了一条判不出来的判据。
        判据值得是产出契约的一部分，不然按 schema 生成的消费方拿不到。

        隔离：这条只查 schema，不跑链路。
        """
        from boxes.modeling.schemas import BUSINESS_MODEL_PACKAGE

        by_name = {f.name: f for f in BUSINESS_MODEL_PACKAGE.fields}
        assert "expectation_completeness" in by_name
        assert "expectations" in by_name

    def test_the_criterion_is_declared_in_the_acceptance(self):
        """判据得真的挂上去（不是只写了个字段）

        隔离：这条只查 definition 的 acceptance 列表。
        """
        from boxes.modeling import get_definition

        metrics = [a.metric for a in get_definition().result.acceptance]
        assert "expectation_completeness" in metrics


# ============================================================
# 下游两只盒声明的形状
# ============================================================


class TestTheDownstreamBoxesDeclareWhatTheyConsume:
    def test_the_testing_box_declares_the_expectation_shape(self):
        """测试盒得**自己声明**它要收的预期形状

        schema 归消费者所有。以前这里是 `ref:schema_model_package`
        ——指向别的盒子的 schema id，本盒里根本没有 → 悬空 →
        「预期该长什么样」从来没定义过，投料里塞什么都行。

        隔离：这条只查测试盒自己的 schemas。
        """
        from boxes.wms_testing.schemas import ALL_SCHEMAS as TEST_SCHEMAS

        ids = {s.id for s in TEST_SCHEMAS}
        assert "schema_expectation_set_input" in ids, (
            "测试盒没有声明它要消费的预期形状"
        )
        schema = next(s for s in TEST_SCHEMAS
                      if s.id == "schema_expectation_set_input")
        field = next(f for f in schema.fields if f.name == "expectations")
        item_props = {p.name for p in field.items.properties}
        for needed in ("expectation_id", "requirement_id", "wms_kind",
                       "statement", "evidence"):
            assert needed in item_props, (
                f"测试盒声明的预期形状少了 {needed}——它接不上建模盒的产出"
            )

    def test_the_testing_box_requires_the_expectation_ref(self):
        """`expectation_ref` 必须是**必填的 ref 字段**

        可选的话，没投预期也能开跑——于是测试盒跑出一堆「全绿」，
        而它其实什么都没验。**缺了预期就该拒收。**

        隔离：这条只查测试盒的投料 schema。
        """
        from boxes.wms_testing.schemas import TEST_TASK_INPUT_SCHEMA

        by_name = {f.name: f for f in TEST_TASK_INPUT_SCHEMA.fields}
        assert "expectation_ref" in by_name
        field = by_name["expectation_ref"]
        assert field.required, "expectation_ref 不是必填——缺了也能开跑"
        assert field.type == "ref:schema_expectation_set_input", (
            f"expectation_ref 声明成了 {field.type!r}——"
            "非 ref 字段接不了上游产出"
        )

    def test_the_analysis_expected_metrics_are_structured(self):
        """分析盒的 `expected_metrics` 得是**结构化**的，不是字符串数组

        字符串数组只能做「这个名字在不在」——
        那是验证词汇表，不是验证业务价值。

        隔离：这条只查分析盒的投料 schema。
        """
        from boxes.wms_analysis.schemas import ANALYSIS_INPUT_SCHEMA

        by_name = {f.name: f for f in ANALYSIS_INPUT_SCHEMA.fields}
        field = by_name["expected_metrics"]
        assert field.items.type == "object", (
            f"expected_metrics 的元素类型是 {field.items.type!r}——"
            "字符串数组只能验「名字在不在」"
        )
        props = {p.name for p in field.items.properties}
        for needed in ("expectation_id", "requirement_id", "wms_kind",
                       "statement"):
            assert needed in props, (
                f"expected_metrics 的元素少了 {needed}——"
                "落差就没法归因到「哪个要求没达成」"
            )

    def test_the_analysis_shape_accepts_the_modeling_box_output(self, monkeypatch):
        """建模盒产出的预期，**能装进**分析盒声明的形状

        两边各写一份形状，改一边另一边就旧了——而旧了的表现是
        「交接失败」，不是「字段名对不上」。
        这条守卫让漂移变成一条**响的**失败。

        隔离：这条只做形状对接，不跑分析盒的腿。
        """
        from runtime.contract import validate_payload

        from boxes.wms_analysis.schemas import ALL_SCHEMAS as ANALYSIS_SCHEMAS
        from boxes.wms_analysis.schemas import ANALYSIS_INPUT_SCHEMA

        _, _, package = _chain(monkeypatch, [
            {"requirement_id": "r_rule", "kinds": [
                {"kind": "fefo", "evidence": "效期升序"}]},
        ])
        payload = {
            "analysis_id": "an_1",
            "requirement_ref": "req_set_1",
            "expected_metrics": package["expectations"],
            # 实测观测从测试盒接力过来（分析盒不自己跑探针）
            "observation_ref": {"observations": []},
            "observed_window": "2026-10",
        }
        errors = validate_payload(payload, ANALYSIS_INPUT_SCHEMA,
                                  schemas=ANALYSIS_SCHEMAS)
        assert not errors, f"建模盒的预期装不进分析盒声明的形状：{errors}"
