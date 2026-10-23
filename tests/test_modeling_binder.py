"""tests/test_modeling_binder.py - WMS 概念绑定的机械校验层

**这组测试验的不是「模型聪不聪明」**——那是语义质量，由手工验证覆盖。
这组验的是**机械层会不会守住**：

1. 模型给的 `kind` 必须是本盒词表里真有的概念（自造的 → 作废）
2. `kind` 必须属于该需求类型允许的类别（rule 不能绑成实体）
3. `evidence` 必须**逐字**出现在需求描述里（改写过的不算）
4. 认不出就是认不出（空列表 → 不绑，**不是**报错）
5. LLM 不可用 / 返回不是 JSON → **中止**，不降级
6. 绑不上也要产出模型元素——「没覆盖」和「不是 WMS 概念」是两件事

**为什么第 6 条要单独钉**：
把「绑不上」当成「不产出元素」，会让一条绑不上的需求把**两条**判据一起拖下水
（覆盖率 + WMS 绑定），而覆盖率那个数从此不再表示覆盖率。
"""
from __future__ import annotations

import json

import pytest

from boxes.modeling.binder import BindingError, bind_requirements
from boxes.modeling.domain import WMS_CONSTRAINTS, WMS_ENTITIES
from runtime import llm


REQS = [
    {"requirement_id": "r_obj", "requirement_type": "object",
     "description": "库存记录要按 SKU 和库位分别记账"},
    {"requirement_id": "r_rule", "requirement_type": "rule",
     "description": "同一 SKU 出库按效期升序分配"},
]


def _stub(payload: dict):
    """把 LLM 换成返回指定内容"""
    def fake(prompt, **kw):
        return payload
    return fake


@pytest.fixture(autouse=True)
def _no_real_llm(monkeypatch):
    """默认禁掉真 LLM——每条用例自己 stub 要返回什么"""
    monkeypatch.setattr(
        llm, "complete_json",
        lambda *a, **kw: (_ for _ in ()).throw(llm.LLMError("test: stub it yourself")),
    )


# ============================================================
# 机械校验层
# ============================================================


class TestTheMechanicalLayerHoldsTheLine:
    def test_a_made_up_concept_is_rejected(self, monkeypatch):
        """模型自造一个概念 id → 作废，不绑

        「不在词表里的概念」= 幻觉。绑上去等于让模型说了一个不存在的 WMS 概念，
        而下游（开发盒要据此找代码位置）会照着它去找。

        **断言的是具体原因**（「不在 WMS 概念表里」），不是「有报错就行」——
        自造的概念和跨类别的概念走同一条出口时，报错必须说清是哪一种，
        否则读者会以为模型只是把类别搞错了。
        """
        monkeypatch.setattr(llm, "complete_json", _stub({"bindings": [
            {"requirement_id": "r_obj",
             "kinds": [{"kind": "super_sku_v2", "evidence": "SKU"}]},
        ]}))
        bindings, errors = bind_requirements(REQS)
        assert "r_obj" not in bindings
        reasons = [e["reason"] for e in errors if e["requirement_id"] == "r_obj"]
        assert any("不在 WMS 概念表里" in r for r in reasons), (
            f"自造的概念没有被说清是「自造的」：{reasons}"
        )

    def test_a_concept_of_the_wrong_category_is_rejected(self, monkeypatch):
        """rule 类型不许绑到实体上

        类别由**声明的 requirement_type** 定，不由模型选。
        一条规则说「讲的是 SKU 这个实体」，那是模型没看懂类型，不是需求的问题。
        """
        monkeypatch.setattr(llm, "complete_json", _stub({"bindings": [
            {"requirement_id": "r_rule",
             "kinds": [{"kind": "sku", "evidence": "同一 SKU 出库按效期升序分配"}]},
        ]}))
        bindings, errors = bind_requirements(REQS)
        assert "r_rule" not in bindings
        assert any("sku" in e["reason"] for e in errors)

    def test_evidence_must_be_a_literal_substring(self, monkeypatch):
        """证据不是原文的逐字片段 → 作废

        这是「模型凭印象编了句话来当证据」。改写过的证据证明不了任何事。
        """
        monkeypatch.setattr(llm, "complete_json", _stub({"bindings": [
            {"requirement_id": "r_rule",
             "kinds": [{"kind": "fefo", "evidence": "按失效日期先出库"}]},
        ]}))
        bindings, errors = bind_requirements(REQS)
        assert "r_rule" not in bindings
        assert any("原文" in e["reason"] for e in errors)

    def test_empty_evidence_is_rejected(self, monkeypatch):
        monkeypatch.setattr(llm, "complete_json", _stub({"bindings": [
            {"requirement_id": "r_rule", "kinds": [{"kind": "fefo", "evidence": ""}]},
        ]}))
        bindings, errors = bind_requirements(REQS)
        assert "r_rule" not in bindings
        assert errors

    def test_a_requirement_id_that_does_not_exist_is_dropped(self, monkeypatch):
        """模型编了一个不存在的需求 id → 丢掉，不报错

        它不对应任何一条真实需求，也就无从点名——丢掉就对了。
        """
        monkeypatch.setattr(llm, "complete_json", _stub({"bindings": [
            {"requirement_id": "r_不存在", "kinds": [{"kind": "fefo", "evidence": "x"}]},
        ]}))
        bindings, errors = bind_requirements(REQS)
        assert bindings == {}
        assert errors == []

    def test_duplicate_kinds_are_deduped(self, monkeypatch):
        monkeypatch.setattr(llm, "complete_json", _stub({"bindings": [
            {"requirement_id": "r_rule", "kinds": [
                {"kind": "fefo", "evidence": "效期升序"},
                {"kind": "fefo", "evidence": "效期升序"},
            ]},
        ]}))
        bindings, _ = bind_requirements(REQS)
        assert len(bindings["r_rule"]) == 1

    def test_not_recognized_is_not_an_error(self, monkeypatch):
        """认不出 → 不绑，**且不报错**

        「认不出」是业务结果（这批需求可能真不是 WMS 需求），
        由 `wms_binding` 判据去红。它不是履约失败。
        """
        monkeypatch.setattr(llm, "complete_json", _stub({"bindings": [
            {"requirement_id": "r_obj", "kinds": []},
            {"requirement_id": "r_rule", "kinds": []},
        ]}))
        bindings, errors = bind_requirements(REQS)
        assert bindings == {}
        assert errors == []


# ============================================================
# 中止，不降级
# ============================================================


class TestNoDegradation:
    def test_llm_outage_aborts(self):
        """LLM 不可用 → 中止，**不返回空绑定**

        降级到关键词匹配看起来「总有结果」，但那正是这个模块存在的理由——
        关键词匹配就是那个认不出「同一 SKU 出库按效期升序分配」的东西。
        """
        with pytest.raises((llm.LLMError, BindingError)):
            bind_requirements(REQS)

    def test_a_non_dict_result_aborts(self, monkeypatch):
        monkeypatch.setattr(llm, "complete_json", _stub(["not", "a", "dict"]))
        with pytest.raises(BindingError):
            bind_requirements(REQS)

    def test_a_result_without_bindings_aborts(self, monkeypatch):
        monkeypatch.setattr(llm, "complete_json", _stub({"oops": 1}))
        with pytest.raises(BindingError):
            bind_requirements(REQS)

    def test_no_requirements_means_no_call(self, monkeypatch):
        """空需求集不调 LLM——没有要绑的东西"""
        def boom(*a, **kw):
            raise AssertionError("不该调 LLM")

        monkeypatch.setattr(llm, "complete_json", boom)
        assert bind_requirements([]) == ({}, [])


# ============================================================
# 词表本身
# ============================================================


class TestTheCatalogIsReal:
    def test_every_entity_and_constraint_is_reachable_by_the_binder(self, monkeypatch):
        """词表里每个概念都能被绑上——没有「查得到但绑不上」的概念

        以前踩过：指标名判成「自造的概念」——因为 WMS_METRICS 是元组不是字典，
        `kind in WMS_METRICS` 永远 False，5 条需求里唯一绑不上的就是那条指标需求。
        """
        monkeypatch.setattr(llm, "complete_json", _stub({"bindings": [
            {"requirement_id": "r_obj", "kinds": [{"kind": "sku", "evidence": "SKU"}]},
        ]}))
        bindings, errors = bind_requirements(REQS)
        assert errors == [], f"概念表里有绑不上的东西：{errors}"

    def test_the_metric_names_are_bindable(self, monkeypatch):
        from boxes.modeling.domain import WMS_METRICS

        for metric in WMS_METRICS:
            monkeypatch.setattr(llm, "complete_json", _stub({"bindings": [
                {"requirement_id": "r_met", "kinds": [
                    {"kind": metric["name"], "evidence": metric["label"]},
                ]},
            ]}))
            reqs = [{"requirement_id": "r_met", "requirement_type": "metric",
                     "description": metric["label"]}]
            bindings, errors = bind_requirements(reqs)
            assert not errors, f"{metric['name']} 绑不上：{errors}"
            assert bindings["r_met"][0]["kind"] == metric["name"]


# ============================================================
# Runner 层：绑不上也要有元素
# ============================================================


class TestTheRunnerKeepsCoverageAndBindingApart:
    def test_an_unbound_requirement_still_produces_a_model_element(self, monkeypatch):
        """绑不上 → 元素照样产出（wms_kind=None）

        「模型没覆盖这条需求」和「这条需求不是 WMS 概念」是**两件事**。
        混成一件的话，一条绑不上的需求会把覆盖率也拖下水，
        而覆盖率那个数从此不再表示覆盖率。
        """
        from boxes.modeling.runner import _simulate_generate_model_elements

        monkeypatch.setattr(llm, "complete_json", _stub({"bindings": [
            {"requirement_id": "r_obj", "kinds": [{"kind": "sku", "evidence": "SKU"}]},
            {"requirement_id": "r_rule", "kinds": []},
        ]}))
        out = _simulate_generate_model_elements({
            "set_id": "s1",
            "requirements": REQS,
            "valid_ids": ["r_obj", "r_rule"],
            "modelable_ids": ["r_obj", "r_rule"],
            "by_type": {"object": ["r_obj"], "rule": ["r_rule"]},
        })
        assert len(out["entities"]) == 1
        assert out["entities"][0]["wms_kind"] == "sku"
        # 规则绑不上 → 元素在，wms_constraint 是 None
        assert len(out["rules"]) == 1
        assert out["rules"][0]["wms_constraint"] is None
        assert out["rules"][0]["trace_to"] == "r_rule"

    def test_binding_errors_survive_the_whole_chain(self, monkeypatch):
        """绑定作废的原因要一路带到 evidence_detail

        「吃了不传」的同族病：中间某一跳没带，下游拿到的是空列表而不是 None，
        于是安安静静地少了一份证据——而看板上只看到一个没有原因的布尔值。
        """
        from boxes.modeling.runner import (
            _simulate_check_consistency,
            _simulate_classify_requirements,
            _simulate_generate_evidence,
            _simulate_generate_model_elements,
            _simulate_parse_requirements,
            _simulate_verify_coverage,
        )

        monkeypatch.setattr(llm, "complete_json", _stub({"bindings": [
            {"requirement_id": "r_rule",
             "kinds": [{"kind": "不是真概念", "evidence": "效期升序"}]},
        ]}))
        parsed = _simulate_parse_requirements({
            "set_id": "s1", "requirements": REQS,
        })
        classified = _simulate_classify_requirements(parsed)
        elements = _simulate_generate_model_elements(classified)
        assert elements["binding_errors"], "作废的绑定没有记下来"

        covered = _simulate_verify_coverage(elements)
        assert covered["binding_errors"], "覆盖率那一步把原因吃掉了"
        consistent = _simulate_check_consistency(covered)
        assert consistent["binding_errors"], "一致性那一步把原因吃掉了"
        evidence = _simulate_generate_evidence(consistent)
        assert evidence["evidence_detail"]["binding_errors"], (
            "证据那一步把原因吃掉了——读者只看到「没绑上」，不知道是模型认错了"
        )

    def test_the_llm_is_actually_in_charge(self, monkeypatch):
        """绑定结果来自 LLM，不是来自词表匹配

        桩返回「库存记录要按 SKU 和库位分别记账 → location」——
        关键词匹配**永远不会**给出这个结果（那是 sku）。
        模型说是 location 就是 location——**词表只是候选集，不是判据**。
        """
        from boxes.modeling.runner import _simulate_generate_model_elements

        monkeypatch.setattr(llm, "complete_json", _stub({"bindings": [
            {"requirement_id": "r_obj",
             "kinds": [{"kind": "location", "evidence": "库位"}]},
        ]}))
        out = _simulate_generate_model_elements({
            "set_id": "s1",
            "requirements": REQS,
            "valid_ids": ["r_obj", "r_rule"],
            "modelable_ids": ["r_obj", "r_rule"],
            "by_type": {"object": ["r_obj"], "rule": ["r_rule"]},
        })
        assert out["entities"][0]["wms_kind"] == "location"
        assert out["entities"][0]["wms_kind"] != "sku"

    def test_the_prompt_offers_the_whole_catalog(self, monkeypatch):
        """给模型看的候选集 = 整个词表

        候选集漏了哪个概念，哪个概念就永远绑不上。
        """
        seen = {}

        def spy(prompt, **kw):
            seen["prompt"] = prompt
            return {"bindings": []}

        monkeypatch.setattr(llm, "complete_json", spy)
        bind_requirements(REQS)
        prompt = seen["prompt"]
        for kind in list(WMS_ENTITIES) + list(WMS_CONSTRAINTS):
            assert kind in prompt, f"概念 {kind} 没进 prompt——它永远绑不上"
