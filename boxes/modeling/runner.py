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
    """按分类生成 DDD 战术设计元素（名称取自需求描述，pattern 标注战术模式，trace_to 回指需求）"""
    by_type = input_data.get("by_type", {})
    req_by_id = {r.get("requirement_id"): r for r in input_data.get("requirements", [])}

    def name_of(rid: str, prefix: str) -> str:
        desc = (req_by_id.get(rid) or {}).get("description", "").strip()
        return desc or f"{prefix}_{rid}"

    entities = [
        {
            "entity_id": f"ent_{rid}",
            "name": name_of(rid, "Entity"),
            "pattern": DDD_PATTERNS["object"][0],
            "trace_to": rid,
        }
        for rid in by_type.get("object", [])
    ]
    rules = [
        {
            "rule_id": f"rule_{rid}",
            "name": name_of(rid, "Rule"),
            "condition": f"<condition for {rid}>",
            "action": f"<action for {rid}>",
            "pattern": DDD_PATTERNS["rule"][0],
            "trace_to": rid,
        }
        for rid in by_type.get("rule", [])
    ]
    processes = [
        {
            "process_id": f"proc_{rid}",
            "name": name_of(rid, "Process"),
            "pattern": DDD_PATTERNS["process"][0],
            "trace_to": rid,
        }
        for rid in by_type.get("process", [])
    ]
    metrics = [
        {
            "metric_id": f"met_{rid}",
            "name": name_of(rid, "Metric"),
            "target": 0.85,
            "pattern": DDD_PATTERNS["metric"][0],
            "trace_to": rid,
        }
        for rid in by_type.get("metric", [])
    ]
    events = [
        {
            "event_id": f"evt_{rid}",
            "name": name_of(rid, "Event"),
            "pattern": DDD_PATTERNS["event"][0],
            "trace_to": rid,
        }
        for rid in by_type.get("event", [])
    ]
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
        "entities": input_data.get("entities", []),
        "rules": input_data.get("rules", []),
        "processes": input_data.get("processes", []),
        "metrics": input_data.get("metrics", []),
        "events": input_data.get("events", []),
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

    return {
        "set_id": input_data.get("set_id", ""),
        "business_goal": input_data.get("business_goal", ""),
        "requirement_count": input_data.get("requirement_count", 0),
        # 模型元素透传（最终 package 要内嵌完整模型）
        "entities": entities,
        "rules": rules,
        "processes": processes,
        "metrics": metrics,
        "events": events,
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
        "evidence_detail": {
            "uncovered_requirements": input_data.get("__uncovered__", []),
            "rule_conflicts": [],
            "broken_references": input_data.get("__broken_references__", []),
            "structure_violations": input_data.get("__structure_violations__", []),
            "metrics_without_target": input_data.get("__metrics_without_target__", []),
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
        "requirement_count": input_data.get("requirement_count", 0),
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

RUNNER = HandlerRunner(MODEL_HANDLERS, owner="business_modeling_box")
