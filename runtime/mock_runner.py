"""
runtime.mock_runner - Mock operation runner

按 op.type 模拟业务 operation 执行（不调真实 bee / external_system）。

PoC 原则：业务语义是真的，基础设施可以是假的。
- 输入真实（order_exception 数据）
- 输出真实（resolution_result 数据 + resolution_type / actions_taken）
- 执行是假的（无外部 API 调用）

支持 op type：
- diagnose_exception → 诊断异常类型
- try_alternative_warehouse / try_inter_warehouse_transfer /
  try_replenishment_eta / try_substitute_product → 履约尝试
- calculate_compensation → 计算补偿
- notify_customer → 模拟通知
- escalate_to_human → 升级人工

每个 operation 输出包含：
- 业务字段（按 schema 形状）
- __resolution_type__（fulfilled / partial_fulfilled / unfulfilled）—— executor 用于分支决策
"""

from __future__ import annotations

from typing import Any, Optional


# 模拟数据：按 exception_type 给不同 success 概率（确定性，便于测试）
# 库存不足：替代仓 / 调拨 50% 成功，补货 ETA 70% 成功，替代品 60% 成功
# 其他异常：默认 fallback 到 calculate_compensation
def _simulate_warehouse_lookup(input_data: dict[str, Any]) -> dict[str, Any]:
    return {
        "resolution_type": "fulfilled",
        "actions_taken": [
            {"action": "redirect_to_alternative_warehouse", "timestamp": "now"},
        ],
        "customer_notified": False,
    }


def _simulate_warehouse_transfer(input_data: dict[str, Any]) -> dict[str, Any]:
    return {
        "resolution_type": "fulfilled",
        "actions_taken": [
            {"action": "initiate_inter_warehouse_transfer", "timestamp": "now"},
        ],
        "customer_notified": False,
    }


def _simulate_replenishment_check(input_data: dict[str, Any]) -> dict[str, Any]:
    eta_hours = 48
    return {
        "resolution_type": "partial_fulfilled",
        "actions_taken": [
            {"action": "scheduled_replenishment", "timestamp": "now"},
        ],
        "eta_acceptable": True,
        "eta_hours": eta_hours,
        "customer_notified": False,
    }


def _simulate_product_substitution(input_data: dict[str, Any]) -> dict[str, Any]:
    return {
        "resolution_type": "fulfilled",
        "actions_taken": [
            {"action": "swap_to_substitute_sku", "timestamp": "now"},
        ],
        "customer_notified": False,
    }


def _simulate_compensation_calc(input_data: dict[str, Any]) -> dict[str, Any]:
    # 兜底：退款
    refund_amount = input_data.get("amount", 0)
    return {
        "resolution_type": "refunded",
        "actions_taken": [
            {"action": "refund_issued", "timestamp": "now"},
        ],
        "refund_amount": refund_amount,
        "customer_notified": False,
    }


def _simulate_notify_customer(input_data: dict[str, Any]) -> dict[str, Any]:
    return {
        "resolution_type": input_data.get("resolution_type", "fulfilled"),
        "customer_notified": True,
        "actions_taken": input_data.get("actions_taken", []),
        "refund_amount": input_data.get("refund_amount"),
    }


def _simulate_escalate_human(input_data: dict[str, Any]) -> dict[str, Any]:
    return {
        "resolution_type": "escalated",
        "actions_taken": [
            {"action": "queued_for_human_review", "timestamp": "now"},
        ],
        "human_review_required": False,
        "customer_notified": input_data.get("customer_notified", False),
    }


def _simulate_diagnose_exception(input_data: dict[str, Any]) -> dict[str, Any]:
    return {
        "exception_type": input_data.get("exception_type", "inventory_shortage"),
        "severity": input_data.get("context", {}).get("severity", "medium"),
        "customer_tier": input_data.get("context", {}).get("customer_tier", "standard"),
        "amount": input_data.get("amount", 0),
    }


# ============================================================
# 业务建模盒 mock handlers（Software Line）
# ============================================================


def _simulate_parse_requirements(input_data: dict[str, Any]) -> dict[str, Any]:
    """解析业务需求集：提取 id + 校验结构"""
    reqs = input_data.get("requirements", [])
    valid_ids = [r.get("requirement_id") for r in reqs if r.get("requirement_id")]
    parse_errors = []
    if not valid_ids:
        parse_errors.append("no valid requirement ids")
    for r in reqs:
        if r.get("requirement_type") not in ("object", "rule", "process", "metric", "goal"):
            parse_errors.append(f"invalid requirement_type for {r.get('requirement_id')}")
    return {
        "set_id": input_data.get("set_id", ""),
        "requirement_count": len(valid_ids),
        "valid_ids": valid_ids,
        "parse_errors": parse_errors,
    }


def _simulate_classify_requirements(input_data: dict[str, Any]) -> dict[str, Any]:
    """按 type 分组"""
    by_type = {"object": [], "rule": [], "process": [], "metric": [], "goal": []}
    # 上游的 valid_ids 是从 parse 来的；我们要从 requirement_set 重新分类
    # 这里 input 是 parsed_requirements；倒推要拿原 set
    # 简化：直接读 valid_ids + 假定上游带 raw requirements
    valid_ids = input_data.get("valid_ids", [])
    # 模拟分类（按 id 后缀做确定性分配）
    for rid in valid_ids:
        suffix = rid.split("_")[-1] if "_" in rid else rid
        if suffix in ("obj", "1", "2"):
            by_type["object"].append(rid)
        elif suffix in ("rule", "3", "4"):
            by_type["rule"].append(rid)
        elif suffix in ("proc", "5", "6"):
            by_type["process"].append(rid)
        elif suffix in ("metric", "7", "8"):
            by_type["metric"].append(rid)
        else:
            by_type["goal"].append(rid)
    return {
        "set_id": input_data.get("set_id", ""),
        "by_type": by_type,
    }


def _simulate_generate_model_elements(input_data: dict[str, Any]) -> dict[str, Any]:
    """按分类生成 model 元素（每个 valid_id 都生成对应元素）"""
    by_type = input_data.get("by_type", {})
    entities = [
        {"entity_id": f"ent_{rid}", "name": f"Entity_{rid}", "trace_to": rid}
        for rid in by_type.get("object", [])
    ]
    rules = [
        {"rule_id": f"rule_{rid}", "condition": f"<condition for {rid}>", "action": f"<action for {rid}>", "trace_to": rid}
        for rid in by_type.get("rule", [])
    ]
    processes = [
        {"process_id": f"proc_{rid}", "name": f"Process_{rid}", "trace_to": rid}
        for rid in by_type.get("process", [])
    ]
    metrics = [
        {"metric_id": f"met_{rid}", "name": f"Metric_{rid}", "target": 0.85, "trace_to": rid}
        for rid in by_type.get("metric", [])
    ]
    return {
        "set_id": input_data.get("set_id", ""),
        "entities": entities,
        "rules": rules,
        "processes": processes,
        "metrics": metrics,
    }


def _simulate_verify_coverage(input_data: dict[str, Any]) -> dict[str, Any]:
    """验证 requirement coverage：所有 model 元素的 trace_to 都覆盖"""
    all_traces = set()
    for elist in (input_data.get("entities", []), input_data.get("rules", []),
                  input_data.get("processes", []), input_data.get("metrics", [])):
        for el in elist:
            if el.get("trace_to"):
                all_traces.add(el["trace_to"])
    covered = len(all_traces)
    total = covered  # 简化：PoC 假定 parse 输出 = covered（无遗漏需求）
    coverage = 100.0 if total > 0 else 0.0
    return {
        "set_id": input_data.get("set_id", ""),
        "entities": input_data.get("entities", []),
        "rules": input_data.get("rules", []),
        "processes": input_data.get("processes", []),
        "metrics": input_data.get("metrics", []),
        "__coverage__": coverage,  # 给 evidence_generator 用
        "__covered_count__": covered,
    }


def _simulate_check_consistency(input_data: dict[str, Any]) -> dict[str, Any]:
    """机械校验：rule_consistency / reference_integrity / structural_compliance"""
    # PoC：mock 全部返回 true（实际生产要解析 + 静态分析）
    return {
        "set_id": input_data.get("set_id", ""),
        "entities": input_data.get("entities", []),
        "rules": input_data.get("rules", []),
        "processes": input_data.get("processes", []),
        "metrics": input_data.get("metrics", []),
        "__coverage__": input_data.get("__coverage__", 100.0),
        "__covered_count__": input_data.get("__covered_count__", 0),
        "__rule_consistency__": True,
        "__reference_integrity__": True,
        "__structural_compliance__": True,
        "__metrics_defined__": len(input_data.get("metrics", [])) > 0,
    }


def _simulate_generate_evidence(input_data: dict[str, Any]) -> dict[str, Any]:
    """汇总 5 项 evidence"""
    coverage = input_data.get("__coverage__", 100.0)
    return {
        "set_id": input_data.get("set_id", ""),
        "requirement_coverage": coverage,
        "rule_consistency": input_data.get("__rule_consistency__", True),
        "reference_integrity": input_data.get("__reference_integrity__", True),
        "structural_compliance": input_data.get("__structural_compliance__", True),
        "metrics_defined": input_data.get("__metrics_defined__", True),
        "requirement_count": input_data.get("__covered_count__", 0),
        "evidence_detail": {
            "uncovered_requirements": [],
            "rule_conflicts": [],
            "broken_references": [],
            "structure_violations": [],
            "metrics_without_target": [],
        },
    }


def _simulate_package_model(input_data: dict[str, Any]) -> dict[str, Any]:
    """合并成 BusinessModelPackage（task run result 顶层）"""
    return {
        "package_id": f"pkg_{input_data.get('set_id', 'unknown')}",
        "set_id": input_data.get("set_id", ""),
        "requirement_coverage": input_data.get("requirement_coverage", 100.0),
        "rule_consistency": input_data.get("rule_consistency", True),
        "reference_integrity": input_data.get("reference_integrity", True),
        "structural_compliance": input_data.get("structural_compliance", True),
        "metrics_defined": input_data.get("metrics_defined", True),
        "requirement_count": input_data.get("requirement_count", 0),
        "business_owner_assigned": True,  # PoC：业务方必填
    }


# op.type → handler 映射
MOCK_HANDLERS = {
    # 订单异常盒（运营线）
    "validate_exception": _simulate_diagnose_exception,
    "warehouse_inventory_lookup": _simulate_warehouse_lookup,
    "inter_warehouse_transfer": _simulate_warehouse_transfer,
    "replenishment_check": _simulate_replenishment_check,
    "product_substitution": _simulate_product_substitution,
    "compensation_calc": _simulate_compensation_calc,
    "customer_notification": _simulate_notify_customer,
    "human_escalation": _simulate_escalate_human,
    # 业务建模盒（软件线）
    "requirement_parser": _simulate_parse_requirements,
    "requirement_classifier": _simulate_classify_requirements,
    "model_generator": _simulate_generate_model_elements,
    "coverage_verifier": _simulate_verify_coverage,
    "consistency_checker": _simulate_check_consistency,
    "evidence_generator": _simulate_generate_evidence,
    "model_packager": _simulate_package_model,
}


class MockRunner:
    """Mock operation runner

    按 op.type 调用对应 handler；handler 返回业务结果 dict。
    不可识别的 op type 抛出 NotImplementedError。
    """

    def run(
        self,
        op_type: str,
        input_data: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """执行 mock operation

        Args:
            op_type: operation.type（业务化标识）
            input_data: operation 输入（按 input schema）

        Returns:
            operation 输出 dict

        Raises:
            NotImplementedError: 未注册的 op type
        """
        handler = MOCK_HANDLERS.get(op_type)
        if handler is None:
            raise NotImplementedError(
                f"no mock handler for op_type='{op_type}'. "
                f"register it in runtime.mock_runner.MOCK_HANDLERS"
            )
        return handler(input_data or {})

    def supports(self, op_type: str) -> bool:
        return op_type in MOCK_HANDLERS