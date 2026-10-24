"""tests/test_modeling_binder.py - WMS 概念绑定的机械校验层

**这组测试验的不是「模型聪不聪明」**——那是语义质量，由手工验证覆盖。
这组验的是**机械层会不会守住**：

1. 模型给的 `kind` 必须是本盒词表里真有的概念（自造的 → 作废）
2. `kind` 的类别由**概念本身**决定，不由需求类型决定（位置不否决语义）
3. `evidence` 必须**逐字**出现在需求描述里（改写过的不算）
4. 认不出就是认不出（空列表 → 不绑，**不是**报错）
5. LLM 不可用 / 返回不是 JSON → **中止**，不降级
6. 绑不上也要产出模型元素——「没覆盖」和「不是 WMS 概念」是两件事
7. 报表概念（库存看板）要真的通到产物里，不只是词表里有个名字

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

    def test_the_position_label_does_not_veto_the_semantics(self, monkeypatch):
        """需求在流程图里的位置**不是**绑定的限制

        需求盒的 requirement_type 是**位置**推出来的（图上的节点→process、
        边的守卫→rule、节点的 measures→metric），不是语义判断。

        真 LLM 实测：业务方说「同一个 SKU 必须先出快过期的批次」，需求盒把
        它放在节点上（类型=process），而设计盒正确地认出了 `fefo`——
        **拿位置标签去否决语义判断，等于把对的答案改成错的**。

        所以：一条 **process 类型**的需求绑到约束类概念，**必须被接受**。

        **为什么这条需求的类型必须是 process**：
        旧的「位置→类别」映射里 `rule→constraint`，而 fefo 本身就是约束类——
        拿一条 rule 类型的需求来测，**把否决加回去照样绿**。
        守卫测不到它要守的那个失败，就只是「看着在」。位置推断把它放成
        process，正是这条守卫必须扛住的场景。
        """
        reqs = [{
            "requirement_id": "r_proc",
            "requirement_type": "process",
            "description": "同一 SKU 出库按效期升序分配",
        }]
        monkeypatch.setattr(llm, "complete_json", _stub({"bindings": [
            {"requirement_id": "r_proc",
             "kinds": [{"kind": "fefo", "evidence": "效期升序"}]},
        ]}))
        bindings, errors = bind_requirements(reqs)
        assert errors == [], f"位置标签不该否决语义判断：{errors}"
        assert bindings["r_proc"][0]["kind"] == "fefo"

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

    def test_every_concept_says_what_it_is(self):
        """词表里每个概念都要有**正式表述**

        预期（expectation）靠 statement 说清「这条需求该怎样才算被满足」。
        概念没有表述 → 预期带着一句空话 → 下游拿着它对不上任何东西，
        而看板上只看到一条空字符串，**看不出来**。

        实体类以前就没有 statement（词表只给了 label 和 keywords），
        于是实体类预期全是空的——真 LLM 跑出来 14 条预期里 7 条是空的。

        隔离：这条只查词表本身。
        """
        from boxes.modeling.domain import (
            WMS_CONSTRAINTS,
            WMS_ENTITIES,
            WMS_METRICS,
            WMS_REPORTS,
            statement_for_kind,
        )

        every = (
            list(WMS_ENTITIES) + list(WMS_CONSTRAINTS)
            + [m["name"] for m in WMS_METRICS] + list(WMS_REPORTS)
        )
        for kind in every:
            assert statement_for_kind(kind), (
                f"概念 {kind} 没有正式表述——它带出来的预期是一句空话"
            )


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


# ============================================================
# 报表概念：词表里有个名字 ≠ 真的通到产物里
# ============================================================


class TestTheReportConceptIsWiredEndToEnd:
    """「库存看板」这类**报表**概念要真的能被绑上、且看得见

    业务方原话：「临期 30 天内的 SKU 要能在库存看板看到」。
    这句话讲的是 `inventory_dashboard`——一个**报表**概念，
    既不是实体，也不是指标，更不是约束。

    以前词表里压根没有「报表」这一类，于是这条需求：
    ① LLM 的候选集里看不到它 → 认不出；
    ② 就算模型硬给出 `inventory_dashboard`，机械层也会判成
       「自造的概念」把它作废；
    ③ 就算绑上了，`wms_domain` 里也没有 reports 这一组 → **绑了却看不见**。

    三步任意一步断了，这条主线需求就绑不上——而它正是本次要验证的那条。
    """

    REPORT_REQ = {
        "requirement_id": "r_rep",
        "requirement_type": "metric",
        "description": "临期 30 天内的 SKU 要能在库存看板看到",
    }

    def test_the_report_kind_is_offered_to_the_model(self, monkeypatch):
        """候选集里必须有报表概念——没有它，模型认不出也是白搭

        隔离：这条守卫盯的是 **prompt 的内容**。机械层那条
        （`not treated as a made-up concept`）盯的是校验结果。
        两条盯的不是同一处。
        """
        seen = {}

        def spy(prompt, **kw):
            seen["prompt"] = prompt
            return {"bindings": []}

        monkeypatch.setattr(llm, "complete_json", spy)
        bind_requirements([self.REPORT_REQ])
        assert "inventory_dashboard" in seen["prompt"], (
            "报表概念没进候选集——模型永远认不出「库存看板」这类需求"
        )

    def test_the_report_kind_is_not_treated_as_a_made_up_concept(self, monkeypatch):
        """机械层不能把报表概念判成「自造的概念」

        隔离：这里**只**走 binder 的机械层，不碰 runner。
        机械层把 `kind in WMS_REPORTS` 去掉，这条立刻转红。
        """
        monkeypatch.setattr(llm, "complete_json", _stub({"bindings": [
            {"requirement_id": "r_rep", "kinds": [
                {"kind": "inventory_dashboard", "evidence": "库存看板"},
            ]},
        ]}))
        bindings, errors = bind_requirements([self.REPORT_REQ])
        assert errors == [], f"报表概念被判成了自造的概念：{errors}"
        assert bindings["r_rep"][0]["kind"] == "inventory_dashboard"

    def test_the_report_kind_is_classified_as_a_report(self):
        """概念类别由**概念本身**决定

        隔离：这条只验 `kind_category`，不验 binder 也不验 runner。
        """
        from boxes.modeling.domain import kind_category

        assert kind_category("inventory_dashboard") == "report"

    def test_every_concept_in_the_catalog_is_in_the_wms_kind_enum(self):
        """四类概念**全部**在 `wms_kind` 枚举里

        枚举是 schema 层的约束：枚举外的值投不进模型元素。
        词表里加了一类概念却忘了开枚举 → 那类需求照样绑不上，
        而且失败点离原因很远（看起来像「模型没认出来」）。

        隔离：这条只查枚举成员，不碰 binder/runner。
        """
        from boxes.modeling.domain import (
            WMS_CONSTRAINTS,
            WMS_ENTITIES,
            WMS_METRICS,
            WMS_REPORTS,
        )
        from boxes.modeling.schemas import WMS_ALL_KINDS

        every = (
            list(WMS_ENTITIES) + list(WMS_CONSTRAINTS)
            + [m["name"] for m in WMS_METRICS] + list(WMS_REPORTS)
        )
        missing = [k for k in every if k not in WMS_ALL_KINDS]
        assert not missing, f"词表里有、枚举里没有的概念：{missing}"

    def test_every_concept_has_a_readable_label(self):
        """每个概念都要有中文标签——看板给读者看的是「库存看板」

        以前 `label_of` 只查实体表和约束表，指标和报表查不到，
        label 直接退回成英文 id，看板上出现一串机器名。

        隔离：这条只验 `label_for_kind`，不碰 runner。
        """
        from boxes.modeling.domain import (
            WMS_CONSTRAINTS,
            WMS_ENTITIES,
            WMS_METRICS,
            WMS_REPORTS,
            label_for_kind,
        )

        every = (
            list(WMS_ENTITIES) + list(WMS_CONSTRAINTS)
            + [m["name"] for m in WMS_METRICS] + list(WMS_REPORTS)
        )
        for kind in every:
            label = label_for_kind(kind)
            assert label and label != kind, (
                f"概念 {kind} 没有中文标签（拿回了 {label!r}）"
            )

    def test_a_report_binding_is_visible_in_the_domain_summary(self, monkeypatch):
        """绑上了**也要看得见**——`wms_domain` 里得有 reports 这一组

        「绑了却看不见」是「算了却看不见」的同族病：
        读者在模型包里找不到这条报表绑定，只能以为需求没被覆盖。

        隔离：这条走 runner 链路，但**不**经过 binder 的机械层
        （LLM 直接给出合法绑定），所以它转红的原因只有一个——
        `wms_domain` 没带出 reports。
        """
        from boxes.modeling.runner import (
            _simulate_check_consistency,
            _simulate_generate_model_elements,
            _simulate_package_model,
            _simulate_verify_coverage,
        )

        monkeypatch.setattr(llm, "complete_json", _stub({"bindings": [
            {"requirement_id": "r_rep", "kinds": [
                {"kind": "inventory_dashboard", "evidence": "库存看板"},
            ]},
        ]}))
        elements = _simulate_generate_model_elements({
            "set_id": "s1",
            "requirements": [self.REPORT_REQ],
            "valid_ids": ["r_rep"],
            "modelable_ids": ["r_rep"],
            "by_type": {"metric": ["r_rep"]},
        })
        covered = _simulate_verify_coverage(elements)
        consistent = _simulate_check_consistency(covered)
        package = _simulate_package_model(consistent)

        reports = package["wms_domain"].get("reports") or []
        assert reports, "报表绑定没有出现在 wms_domain 里——绑了却看不见"
        row = reports[0]
        assert row["kind"] == "inventory_dashboard"
        assert row["label"] == "库存看板"
        assert row["evidence"] == "库存看板", "报表绑定没带原文证据"

    def test_the_domain_summary_declares_the_report_group(self):
        """schema 得声明 reports ——**产出了却没声明**，消费方就取不到

        这是「声明了却拿不到」的镜像：产出里有这个字段，
        但 schema 里没有 → 照 schema 写的消费方永远看不到它。

        隔离：这条只查 schema，不跑链路。
        """
        from boxes.modeling.schemas import BUSINESS_MODEL_PACKAGE

        by_name = {f.name: f for f in BUSINESS_MODEL_PACKAGE.fields}
        domain_field = by_name["wms_domain"]
        props = {p.name for p in domain_field.properties}
        for group in ("entities", "constraints", "metrics", "reports"):
            assert group in props, (
                f"wms_domain 没声明 {group}——产出里带出来了但消费方按 schema 取不到"
            )



# ============================================================
# 报表 → 它承载的那个数（域事实驱动的补全）
# ============================================================


class TestTheReportCarriesItsMetric:
    """「临期 30 天内 SKU 要能在库存看板看到」点了两样东西：
    一个量（临期 SKU 数）和承载它的面（库存看板）。

    LLM 认得出「看板」，有时认不出那个量——真 LLM 实测同一批需求连跑
    4 次漏了 1 次。漏了的后果不是好看不好看：**那条指标不会变成
    可判定的预期，测试盒就不会去验它，业务价值根本没被验证。**

    所以补一步：方向来自词表里声明的「报表承载哪些指标」（WMS 的事实），
    证据仍然必须逐字出现在需求原文里——跟 LLM 绑定的证据规则完全一样。
    """

    DASH = {"requirement_id": "r_proc", "requirement_type": "process",
            "description": "临期 30 天内 SKU 要能在库存看板看到"}
    NO_METRIC = {"requirement_id": "r_proc2", "requirement_type": "process",
                 "description": "库存看板要能看到所有库存"}

    def test_报表绑上了_就补上它承载的数(self, monkeypatch):
        """模型只认出看板 → 补出那个被点名的量"""
        monkeypatch.setattr(llm, "complete_json", _stub({"bindings": [
            {"requirement_id": "r_proc",
             "kinds": [{"kind": "inventory_dashboard", "evidence": "库存看板"}]},
        ]}))
        bindings, errors = bind_requirements([self.DASH])
        kinds = [b["kind"] for b in bindings["r_proc"]]
        assert "inventory_dashboard" in kinds
        assert "wms_expiring_soon_sku_count" in kinds
        assert errors == []

    def test_原文没提那个量_就不补(self, monkeypatch):
        """「库存看板要能看到所有库存」没提临期 → 不补指标

        补的方向是「这张看板上的这个数」，而**原文没点名那个数**，
        补上去就是模型说了需求里没有的话。
        """
        monkeypatch.setattr(llm, "complete_json", _stub({"bindings": [
            {"requirement_id": "r_proc2",
             "kinds": [{"kind": "inventory_dashboard", "evidence": "库存看板"}]},
        ]}))
        bindings, _ = bind_requirements([self.NO_METRIC])
        kinds = [b["kind"] for b in bindings["r_proc2"]]
        assert kinds == ["inventory_dashboard"]

    def test_补出来的证据必须是原文子串(self, monkeypatch):
        """机械闸：补出来的证据逐字在需求描述里"""
        monkeypatch.setattr(llm, "complete_json", _stub({"bindings": [
            {"requirement_id": "r_proc",
             "kinds": [{"kind": "inventory_dashboard", "evidence": "库存看板"}]},
        ]}))
        bindings, _ = bind_requirements([self.DASH])
        desc = self.DASH["description"]
        for b in bindings["r_proc"]:
            assert b["evidence"] in desc, f"证据 {b['evidence']!r} 不是原文"

    def test_已经绑上的指标不会被补第二遍(self, monkeypatch):
        monkeypatch.setattr(llm, "complete_json", _stub({"bindings": [
            {"requirement_id": "r_proc", "kinds": [
                {"kind": "inventory_dashboard", "evidence": "库存看板"},
                {"kind": "wms_expiring_soon_sku_count", "evidence": "临期 30 天内 SKU"},
            ]},
        ]}))
        bindings, _ = bind_requirements([self.DASH])
        kinds = [b["kind"] for b in bindings["r_proc"]]
        assert kinds.count("wms_expiring_soon_sku_count") == 1


class TestTheMetricVocabularyIsNotCopiedTwice:
    """「同一份形状抄两遍就会漂」——指标词表这里抄过两遍"""

    def test_指标词表与指标表是同一份(self):
        """WMS_METRIC_KEYWORDS 必须覆盖 WMS_METRICS 的每一个指标

        以前它是独立抄的一份：指标表加了新指标、这张表没跟上 →
        绑定腿的目录里就没有那个指标的说法 → 绑不上 → 判红，
        而红的原因离现场隔着两层。
        """
        from boxes.modeling.domain import WMS_METRIC_KEYWORDS, WMS_METRICS

        assert set(WMS_METRIC_KEYWORDS) == {m["name"] for m in WMS_METRICS}

    def test_每个指标都有例词(self):
        """例词是给绑定腿看的——没有例词，LLM 看不见这个指标

        实体 / 约束 / 报表都有例词，只有指标没有的时候，
        LLM 读目录看到的是「临期 SKU 数」，而业务方说的是「临期 30 天内 SKU」，
        不是同一串字 → 指标绑不上（真 LLM 实测：同一批需求一次绑上一次没绑上）。
        """
        from boxes.modeling.domain import WMS_METRICS

        for m in WMS_METRICS:
            assert m.get("keywords"), f"{m['name']} 没有例词"
            assert m["label"] in m["keywords"], f"{m['name']} 的例词里没有自己的标签"

    def test_绑定目录里的指标带例词(self):
        """目录里必须真的把例词给出去（不是只存在词表里）"""
        from boxes.modeling.binder import _catalog_for
        from boxes.modeling.domain import WMS_METRICS

        cat = {c["kind"]: c for c in _catalog_for("metric")}
        for m in WMS_METRICS:
            entry = cat[m["name"]]
            assert len(entry["examples"]) > 2, f"{m['name']} 的目录条目没有例词"
            assert "临期" in " ".join(
                entry["examples"]) or m["name"] != "wms_expiring_soon_sku_count"
