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


# op.type → handler 映射
MOCK_HANDLERS = {
    "validate_exception": _simulate_diagnose_exception,
    "warehouse_inventory_lookup": _simulate_warehouse_lookup,
    "inter_warehouse_transfer": _simulate_warehouse_transfer,
    "replenishment_check": _simulate_replenishment_check,
    "product_substitution": _simulate_product_substitution,
    "compensation_calc": _simulate_compensation_calc,
    "customer_notification": _simulate_notify_customer,
    "human_escalation": _simulate_escalate_human,
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