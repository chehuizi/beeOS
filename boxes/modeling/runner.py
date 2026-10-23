"""
boxes.modeling.runner - 业务建模盒的 7 个 operation 实现

从 runtime/mock_runner.py 搬来。这些 handler 以前住在内核里，
跟另外两只盒子的实现混在同一张分派表上——内核因此比任何一只盒子
都懂业务，盒子带不走自己的实现。搬过来之后建模盒是一个自包含的单元。

**它现在是 mock 腿，不是真腿。** DDD_PATTERNS 里的映射
（object→entity / rule→specification / ...）是写死的套路，
不是从需求语义推出来的；rule_consistency 恒为 True 也是因为
生成的 condition/action 是占位符、无语义可判矛盾。
真要让建模盒配得上「原子单位」，还得给它一条真建模腿。
"""

from __future__ import annotations

from typing import Any

from boxes.modeling.domain import (
    WMS_CONSTRAINTS,
    WMS_ENTITIES,
    label_of,
)
from core.models import NOT_APPLICABLE
from runtime.runner import HandlerRunner


# ============================================================
# 业务建模盒 mock handlers（Software Line）
# ============================================================


REQUIREMENT_TYPES = ("object", "rule", "process", "metric", "goal", "event")

# goal 类需求不产出 model 元素（goal 承载在 package.business_goal 上），
# 覆盖率分母只算可建模的 5 类（object / rule / process / metric / event）
MODELABLE_TYPES = ("object", "rule", "process", "metric", "event")


def _simulate_parse_requirements(input_data: dict[str, Any]) -> dict[str, Any]:
    """解析业务需求集：结构校验 + 透传有效需求载荷

    无效需求（缺 id / type 不在枚举内）记入 parse_errors 并从有效集剔除。
    """
    valid = []
    parse_errors = []
    for r in input_data.get("requirements", []):
        rid = r.get("requirement_id")
        if not rid:
            parse_errors.append("requirement missing requirement_id")
            continue
        if r.get("requirement_type") not in REQUIREMENT_TYPES:
            parse_errors.append(f"invalid requirement_type for {rid}")
            continue
        valid.append(r)
    return {
        "set_id": input_data.get("set_id", ""),
        "business_goal": input_data.get("business_goal", ""),
        "requirement_count": len(valid),
        "valid_ids": [r["requirement_id"] for r in valid],
        "requirements": valid,
        "parse_errors": parse_errors,
    }


def _simulate_classify_requirements(input_data: dict[str, Any]) -> dict[str, Any]:
    """按声明的 requirement_type 分组（不是 id 猜测）

    输出 modelable_ids：可建模 4 类（object / rule / process / metric）的需求 id，
    作为 verify_coverage 的覆盖率分母；goal 类需求不产出 model 元素，
    承载在 package.business_goal 上，不计入覆盖率。
    requirements 载荷透传（下游 generate_model_elements 用 description 命名元素）。
    """
    by_type = {t: [] for t in REQUIREMENT_TYPES}
    for r in input_data.get("requirements", []):
        by_type[r["requirement_type"]].append(r["requirement_id"])
    modelable_ids = [rid for t in MODELABLE_TYPES for rid in by_type[t]]
    return {
        "set_id": input_data.get("set_id", ""),
        "business_goal": input_data.get("business_goal", ""),
        "requirement_count": input_data.get("requirement_count", 0),
        "valid_ids": input_data.get("valid_ids", []),
        "requirements": input_data.get("requirements", []),
        "by_type": by_type,
        "modelable_ids": modelable_ids,
    }


# 需求类型 → DDD 战术模式
# object = Entity（实体——不指派聚合根：聚合根是一致性边界，没有不变量分析就不指派）；
# rule = Specification（规格）；process = DomainService（领域服务）；
# event = DomainEvent（领域事件）；metric = DomainMetric（扩展元素——
# DDD 战术模式不覆盖度量，盒子如实标注，不硬塞进 DDD 概念）
DDD_PATTERNS = {
    "object": ("entity",),
    "rule": ("specification",),
    "process": ("domain_service",),
    "metric": ("domain_metric",),
    "event": ("domain_event",),
}


def _simulate_generate_model_elements(input_data: dict[str, Any]) -> dict[str, Any]:
    """按分类生成 DDD 战术设计元素（名称取自需求描述，pattern 标注战术模式，trace_to 回指需求）

    **WMS 绑定这一段换成了 LLM**（见 boxes/modeling/binder.py）：
    原来的纯关键词匹配认不出「同一 SKU 出库按效期升序分配」这种最普通的说法，
    连 fefo 自己的 statement 都绑不上——主线需求因此在第 2 只盒子就断了。

    现在：**LLM 归概念，机械层校验**（概念得在词表里、证据得是原文子串），
    认不出就是认不出（不绑），落到 `wms_binding` 的红里，不猜。

    作废的绑定（自造概念 / 证据不是原文）记进 `binding_errors`，
    一路带到 evidence_detail——**红要能定位到具体是哪条需求、为什么**。
    """
    from boxes.modeling.binder import bind_requirements

    by_type = input_data.get("by_type", {})
    requirements = input_data.get("requirements", [])
    req_by_id = {r.get("requirement_id"): r for r in requirements}

    # ---- LLM 绑定（一次调用，批量）----
    bindings, binding_errors = bind_requirements(requirements)

    def kinds_of(rid: str) -> list[dict[str, str]]:
        """这条需求绑到的 WMS 概念

        **绑不上也要产出模型元素**（wms_kind=None）——
        「模型没覆盖这条需求」（覆盖率）和「这条需求不是 WMS 概念」（WMS 绑定）
        是两件不同的事，混成一件的话，一条绑不上的需求会把两条判据一起拖下水，
        而覆盖率那个数从此不再表示覆盖率。
        """
        return bindings.get(rid, [])

    def name_of(rid: str, prefix: str) -> str:
        desc = (req_by_id.get(rid) or {}).get("description", "").strip()
        return desc or f"{prefix}_{rid}"

    def _ids(prefix: str, rid: str, index: int) -> str:
        """同一个需求绑到多个概念时，元素 id 要唯一"""
        return f"{prefix}_{rid}" if index == 0 else f"{prefix}_{rid}_{index}"

    def _kinds_or_placeholder(rid: str) -> list[dict[str, str] | None]:
        ks = kinds_of(rid)
        return list(ks) if ks else [None]

    # 实体：一条需求可能同时提到几个 WMS 概念（库存要按 SKU 和库位分别记账）
    # → 每个概念一个模型元素，wms_kind 指明是哪个。
    entities = []
    for rid in by_type.get("object", []):
        for i, k in enumerate(_kinds_or_placeholder(rid)):
            entities.append({
                "entity_id": _ids("ent", rid, i),
                "name": name_of(rid, "Entity"),
                "pattern": DDD_PATTERNS["object"][0],
                "wms_kind": k["kind"] if k else None,
                "trace_to": rid,
            })

    # 规则：一条需求常常藏不止一条 WMS 规则。
    # LLM 直接给出这条需求约束了哪几个 WMS 约束——比按句读符切一刀更准，
    # 而且**有据可查**（每条绑定都带原文证据）。
    rules = []
    for rid in by_type.get("rule", []):
        for i, k in enumerate(_kinds_or_placeholder(rid)):
            rules.append({
                "rule_id": _ids("rule", rid, i),
                "name": name_of(rid, "Rule"),
                "condition": f"<condition for {rid}>",
                "action": f"<action for {rid}>",
                "pattern": DDD_PATTERNS["rule"][0],
                "wms_constraint": k["kind"] if k else None,
                "trace_to": rid,
            })

    # process / event 同样要能绑。「收货后按库位策略上架」清清楚楚是
    # WMS 流程，只因为类型不是 object 就判它没绑上——判据在假设需求的形状。
    processes = []
    for rid in by_type.get("process", []):
        for i, k in enumerate(_kinds_or_placeholder(rid)):
            processes.append({
                "process_id": _ids("proc", rid, i),
                "name": name_of(rid, "Process"),
                "pattern": DDD_PATTERNS["process"][0],
                "wms_kind": k["kind"] if k else None,
                "trace_to": rid,
            })

    # 指标也要能绑 WMS：wms_kind 放 WMS 指标名（wms_pick_accuracy…），
    # 跟实体 kinds 同一列。schema 里 values 是实体 kinds + [None]，
    # 这里得把指标名也列进去，否则契约跟产出对不上。
    metrics = []
    for rid in by_type.get("metric", []):
        for i, k in enumerate(_kinds_or_placeholder(rid)):
            metrics.append({
                "metric_id": _ids("met", rid, i),
                "name": name_of(rid, "Metric"),
                "target": 0.85,
                "pattern": DDD_PATTERNS["metric"][0],
                "wms_kind": k["kind"] if k else None,
                "trace_to": rid,
            })

    events = []
    for rid in by_type.get("event", []):
        for i, k in enumerate(_kinds_or_placeholder(rid)):
            events.append({
                "event_id": _ids("evt", rid, i),
                "name": name_of(rid, "Event"),
                "pattern": DDD_PATTERNS["event"][0],
                "wms_kind": k["kind"] if k else None,
                "trace_to": rid,
            })

    return {
        "set_id": input_data.get("set_id", ""),
        "business_goal": input_data.get("business_goal", ""),
        "requirement_count": input_data.get("requirement_count", 0),
        "valid_ids": input_data.get("valid_ids", []),
        "modelable_ids": input_data.get("modelable_ids", []),
        "entities": entities,
        "rules": rules,
        "processes": processes,
        "metrics": metrics,
        "events": events,
        # 绑定作废的原因——**要能说清「为什么没绑上」**，
        # 而不是只给一个「没绑上」的布尔值。
        "binding_errors": binding_errors,
    }


def _simulate_verify_coverage(input_data: dict[str, Any]) -> dict[str, Any]:
    """真实覆盖率：可建模需求里有多少被 model 元素的 trace_to 覆盖"""
    modelable_ids = input_data.get("modelable_ids", [])
    traces = set()
    for elist in (input_data.get("entities", []), input_data.get("rules", []),
                  input_data.get("processes", []), input_data.get("metrics", []),
                  input_data.get("events", [])):
        for el in elist:
            if el.get("trace_to"):
                traces.add(el["trace_to"])
    covered = [rid for rid in modelable_ids if rid in traces]
    uncovered = [rid for rid in modelable_ids if rid not in traces]
    total = len(modelable_ids)
    coverage = round(100.0 * len(covered) / total, 2) if total else 0.0
    return {
        "set_id": input_data.get("set_id", ""),
        "business_goal": input_data.get("business_goal", ""),
        "requirement_count": input_data.get("requirement_count", 0),
        "valid_ids": input_data.get("valid_ids", []),
        # **这腿吃了 modelable_ids 却没往下传**：覆盖率算得出来（用的是
        # 局部变量），但后面 WMS 绑定的分母就成 0 了。
        # 「局部算对了就以为整条链都对了」——下游拿到的是 0，不是 None，
        # 所以没有任何地方会报错，只会安安静静地判出一个假的「全都没绑上」。
        "modelable_ids": modelable_ids,
        "entities": input_data.get("entities", []),
        "rules": input_data.get("rules", []),
        "processes": input_data.get("processes", []),
        "metrics": input_data.get("metrics", []),
        "events": input_data.get("events", []),
        # 绑定作废的原因要一路带下去（下面三跳都得显式传，
        # 漏一跳下游就拿到 0 而不是 None——「吃了不传」的同族病）
        "binding_errors": input_data.get("binding_errors", []),
        "__coverage__": coverage,
        "__uncovered__": uncovered,
    }


def _simulate_check_consistency(input_data: dict[str, Any]) -> dict[str, Any]:
    """机械校验：reference_integrity / structural_compliance 真实检查

    rule_consistency 保持 mock True——生成的 condition/action 是占位符，
    无语义可判矛盾（真实实现需要规则解析 + 静态分析）。
    """
    valid_ids = set(input_data.get("valid_ids", []))
    entities = input_data.get("entities", [])
    rules = input_data.get("rules", [])
    processes = input_data.get("processes", [])
    metrics = input_data.get("metrics", [])
    events = input_data.get("events", [])

    # 引用完整性：所有 trace_to 必须指向有效需求 id
    broken = [
        el.get(k) for elist, k in (
            (entities, "entity_id"), (rules, "rule_id"),
            (processes, "process_id"), (metrics, "metric_id"),
            (events, "event_id"),
        )
        for el in elist
        if el.get("trace_to") not in valid_ids
    ]

    # 结构合规：每类元素必填字段齐备
    violations = []
    for e in entities:
        if not e.get("name"):
            violations.append(e.get("entity_id"))
    for r in rules:
        if not r.get("condition") or not r.get("action"):
            violations.append(r.get("rule_id"))
    for p in processes:
        if not p.get("name"):
            violations.append(p.get("process_id"))
    for ev in events:
        if not ev.get("name"):
            violations.append(ev.get("event_id"))
    metrics_without_target = [m.get("metric_id") for m in metrics if m.get("target") is None]
    violations.extend(metrics_without_target)

    # ---- WMS 域判据 ----
    #
    # 前 5 条验的是「模型自洽」：需求有没有被覆盖、引用有没有断、结构齐不齐。
    # 这两条验的是**「这是不是一个 WMS 模型」**——少了它们，一个纯通用的
    # 「当库存 > 0 时…」照样六分之五全绿，而盒子挂着 WMS 的招牌。
    #
    # 口径：绑定的分母是**可建模需求**（跟 requirement_coverage 同一个分母），
    # 不是 entity 数量。实体多不等于覆盖全——一个 SKU 实体挂在一条需求上，
    # 另外九条没人管，照样该红。
    modelable_ids = input_data.get("modelable_ids", [])
    bound_by_entity = {
        e.get("trace_to") for e in entities if e.get("wms_kind") and e.get("trace_to")
    }
    bound_by_rule = {
        r.get("trace_to") for r in rules if r.get("wms_constraint") and r.get("trace_to")
    }
    # 指标也算绑定。少了这一支，「拣货准确率 99.5%」这种清清楚楚的
    # WMS 需求会因为类型是 metric 而判红——判据假定了需求的形状。
    bound_by_metric = {
        m.get("trace_to") for m in metrics if m.get("wms_kind") and m.get("trace_to")
    }
    bound_by_process = {
        pr.get("trace_to") for pr in processes if pr.get("wms_kind") and pr.get("trace_to")
    }
    bound_by_event = {
        ev.get("trace_to") for ev in events if ev.get("wms_kind") and ev.get("trace_to")
    }
    bound = (bound_by_entity | bound_by_rule | bound_by_metric
             | bound_by_process | bound_by_event)
    unbound = [rid for rid in modelable_ids if rid not in bound]

    # 规则侧：至少有一条真的约束了 WMS 业务。
    bound_rules = [r for r in rules if r.get("wms_constraint")]

    # ---- 三态，不是两态 ----
    #
    # 「一批需求里一条 rule 都没有」≠「模型里没有 WMS 规则」。
    # 前者是**投料本来就不讲规则**，后者是真的漏了。混为一谈的话，
    # 任何只讲实体和指标的需求集都会被 wms_rule_binding 判红——
    # 那是判据假定了「每批需求都该有规则」，跟当初
    # 「每张票都有小计栏」是同一种病。
    #
    # 判据：没有任何 rule 型需求 → 这条判据**用不上**（n/a），
    # 不是「没过」。n/a 不算失败但也不算通过，看板分开显示。
    if rules:
        wms_rule_binding = len(bound_rules) > 0
    else:
        wms_rule_binding = NOT_APPLICABLE

    # 同理：可建模需求为 0（整批都是 goal）时，无从谈绑定。
    wms_binding = not unbound if modelable_ids else NOT_APPLICABLE

    return {
        # 同一条链上第三个「吃了不传」的腿（coverage / consistency / evidence）。
        # 每一处单看都跑得通，接起来分母就成 0，然后安安静静判出一个
        # 「全都没绑上」。**局部正确不等于链路正确。**
        "modelable_ids": modelable_ids,
        "__wms_binding__": wms_binding,
        "__wms_rule_binding__": wms_rule_binding,
        "__requirements_without_wms_binding__": unbound,
        "__wms_entity_count__": len([e for e in entities if e.get("wms_kind")]),
        "__wms_rule_count__": len(bound_rules),
        "__bound_modelable_count__": len([r for r in modelable_ids if r in bound]),
        "set_id": input_data.get("set_id", ""),
        "business_goal": input_data.get("business_goal", ""),
        "requirement_count": input_data.get("requirement_count", 0),
        # 模型元素透传（最终 package 要内嵌完整模型）
        "entities": entities,
        "rules": rules,
        "processes": processes,
        "metrics": metrics,
        "events": events,
        # 绑定作废的原因——**这一跳曾经漏了**，于是下游的 evidence_detail
        # 拿到的是空列表：读者只看到「没绑上」，看不到「模型认错了」。
        # 「吃了不传」的同族病，第四例。
        "binding_errors": input_data.get("binding_errors", []),
        "__coverage__": input_data.get("__coverage__", 0.0),
        "__uncovered__": input_data.get("__uncovered__", []),
        "__rule_consistency__": True,
        "__reference_integrity__": not broken,
        "__broken_references__": broken,
        "__structural_compliance__": not violations,
        "__structure_violations__": violations,
        "__metrics_defined__": len(metrics) > 0,
        "__metrics_without_target__": metrics_without_target,
    }


def _simulate_generate_evidence(input_data: dict[str, Any]) -> dict[str, Any]:
    """汇总 5 项 evidence（全部来自上游真实计算）；模型元素透传给 package"""
    return {
        "set_id": input_data.get("set_id", ""),
        "business_goal": input_data.get("business_goal", ""),
        "requirement_count": input_data.get("requirement_count", 0),
        # 模型元素透传（最终 package 要内嵌完整模型）
        "entities": input_data.get("entities", []),
        "rules": input_data.get("rules", []),
        "processes": input_data.get("processes", []),
        "metrics": input_data.get("metrics", []),
        "events": input_data.get("events", []),
        "requirement_coverage": input_data.get("__coverage__", 0.0),
        "rule_consistency": input_data.get("__rule_consistency__", True),
        "reference_integrity": input_data.get("__reference_integrity__", True),
        "structural_compliance": input_data.get("__structural_compliance__", True),
        "metrics_defined": input_data.get("__metrics_defined__", False),
        "wms_binding": input_data.get("__wms_binding__", False),
        "wms_rule_binding": input_data.get("__wms_rule_binding__", False),
        # 分母和计数得一路带到打包腿，否则 wms_domain 里那个
        # 「绑定 0/0」是分母没传下去，不是真的没绑定。
        "modelable_ids": input_data.get("modelable_ids", []),
        "binding_errors": input_data.get("binding_errors", []),
        "wms_entity_count": input_data.get("__wms_entity_count__", 0),
        "wms_rule_count": input_data.get("__wms_rule_count__", 0),
        "bound_modelable_count": input_data.get("__bound_modelable_count__", 0),
        "evidence_detail": {
            "uncovered_requirements": input_data.get("__uncovered__", []),
            "rule_conflicts": [],
            "broken_references": input_data.get("__broken_references__", []),
            "structure_violations": input_data.get("__structure_violations__", []),
            "metrics_without_target": input_data.get("__metrics_without_target__", []),
            # **红要能定位到具体哪条需求**。只给一个布尔值的话，
            # 读者只知道「WMS 绑定没过」，得回头自己一条条翻。
            "requirements_without_wms_binding": input_data.get(
                "__requirements_without_wms_binding__", []),
            "unbound_requirements": input_data.get("__uncovered__", []),
            # 绑定作废的原因（自造概念 / 证据不是原文 / 类别不匹配）。
            # **没有它，读者只看到「没绑上」，不知道是模型没认出来
            # 还是模型认错了**——这两种要修的地方完全不同。
            "binding_errors": input_data.get("binding_errors", []),
        },
    }


def _simulate_package_model(input_data: dict[str, Any]) -> dict[str, Any]:
    """合并成 BusinessModelPackage（task run result 顶层）

    按 schema_business_model_package 契约内嵌完整模型（DDD 战术设计表达）：
    entities / specifications / domain_services / domain_events / domain_metrics
    """
    return {
        "package_id": f"pkg_{input_data.get('set_id', 'unknown')}",
        "set_id": input_data.get("set_id", ""),
        "business_goal": input_data.get("business_goal", ""),
        "model_elements": {
            "entities": input_data.get("entities", []),
            "specifications": input_data.get("rules", []),
            "domain_services": input_data.get("processes", []),
            "domain_events": input_data.get("events", []),
            "domain_metrics": input_data.get("metrics", []),
        },
        "requirement_coverage": input_data.get("requirement_coverage", 0.0),
        "rule_consistency": input_data.get("rule_consistency", True),
        "reference_integrity": input_data.get("reference_integrity", True),
        "structural_compliance": input_data.get("structural_compliance", True),
        "metrics_defined": input_data.get("metrics_defined", False),
        "wms_binding": input_data.get("wms_binding", False),
        "wms_rule_binding": input_data.get("wms_rule_binding", False),
        # WMS 域摘要：**判了就要看得见**。不带出来的话，看板上只有
        # 一个红叉，读者不知道模型里到底认出了哪些 WMS 概念。
        "wms_domain": {
            "domain": "WMS",
            "entities": [
                {
                    "kind": e.get("wms_kind"),
                    "label": label_of(e.get("wms_kind"), WMS_ENTITIES),
                    "name": e.get("name", ""),
                    "trace_to": e.get("trace_to", ""),
                }
                for e in input_data.get("entities", []) if e.get("wms_kind")
            ],
            "constraints": [
                {
                    "kind": r.get("wms_constraint"),
                    "label": label_of(r.get("wms_constraint"), WMS_CONSTRAINTS),
                    "statement": WMS_CONSTRAINTS.get(r.get("wms_constraint"), {}).get("statement", ""),
                    "trace_to": r.get("trace_to", ""),
                }
                for r in input_data.get("rules", []) if r.get("wms_constraint")
            ],
            "bound_requirement_count": input_data.get("bound_modelable_count", 0),
            "modelable_requirement_count": len(input_data.get("modelable_ids", [])),
        },
        # exception 规则 `requirements_without_wms_binding > 0` 读的量。
        # 以前这条规则指向一个产出里没有的字段，**从来没触发过**——
        # 有需求绑不上 WMS 概念，规则说要 escalate，实际什么都不会发生。
        # 上面 wms_domain 里已经有两个数能算出来，这里只是摆到顶层，
        # 让 exception 条件读得到。
        "requirements_without_wms_binding": max(
            0,
            len(input_data.get("modelable_ids", []))
            - input_data.get("bound_modelable_count", 0),
        ),
        "requirement_count": input_data.get("requirement_count", 0),
        # 绑定作废的原因，摆在顶层——判据/看板/消费方都读得到，
        # 不用去挖 evidence_detail。
        "binding_errors": input_data.get("binding_errors", []),
        # **这块以前一直缺**：`schema_business_model_package` 从头就声明了
        # `evidence: ref:schema_model_evidence`，而打包腿从来没产出过它。
        # 「声明了却拿不到」——schema 里写着有，产出里没有，按 schema 生成的
        # 消费方永远拿不到判据明细。红了也不知道红在哪条需求上。
        # evidence 是 evidence_generator 那一步整个搬过来，不是重算。
        "evidence": {
            "requirement_coverage": input_data.get("requirement_coverage", 0.0),
            "rule_consistency": input_data.get("rule_consistency", True),
            "reference_integrity": input_data.get("reference_integrity", True),
            "structural_compliance": input_data.get("structural_compliance", True),
            "metrics_defined": input_data.get("metrics_defined", False),
            "wms_binding": input_data.get("wms_binding", False),
            "wms_rule_binding": input_data.get("wms_rule_binding", False),
            "evidence_detail": input_data.get("evidence_detail", {}),
        },
        "business_owner_assigned": True,  # PoC：业务方必填
    }

# op.type → handler 映射（本盒私有）
MODEL_HANDLERS: dict[str, Any] = {
    "requirement_parser": _simulate_parse_requirements,
    "requirement_classifier": _simulate_classify_requirements,
    "model_generator": _simulate_generate_model_elements,
    "coverage_verifier": _simulate_verify_coverage,
    "consistency_checker": _simulate_check_consistency,
    "evidence_generator": _simulate_generate_evidence,
    "model_packager": _simulate_package_model,
}

RUNNER = HandlerRunner(MODEL_HANDLERS, owner="wms_design_box")
