"""
boxes.inventory_shortage.runner - 库存不足订单盒的 8 个 operation 实现

从 runtime/mock_runner.py 搬来（见 boxes/modeling/runner.py 顶部为什么搬）。

执行是假的：没有真实仓储 API 调用，返回的是确定性模拟结果。
业务语义是真的：每一步的输出形状按 schema 契约来。

注意这只盒目前未注册进 BOX_REGISTRY（看板不显示、trigger 不接收），
代码保留在仓库里，恢复时把 RUNNER 挂回 kanban.trigger.RUNNER_REGISTRY 即可。
"""

from __future__ import annotations

from typing import Any

from runtime.runner import HandlerRunner


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

# op.type → handler 映射（本盒私有）
INVENTORY_HANDLERS: dict[str, Any] = {
    "validate_exception": _simulate_diagnose_exception,
    "warehouse_inventory_lookup": _simulate_warehouse_lookup,
    "inter_warehouse_transfer": _simulate_warehouse_transfer,
    "replenishment_check": _simulate_replenishment_check,
    "product_substitution": _simulate_product_substitution,
    "compensation_calc": _simulate_compensation_calc,
    "customer_notification": _simulate_notify_customer,
    "human_escalation": _simulate_escalate_human,
}

RUNNER = HandlerRunner(INVENTORY_HANDLERS, owner="inventory_shortage")
