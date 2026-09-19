"""
beelines.inventory_shortage - 库存不足订单履约作业路线

按 [docs/examples/order-exception-box.md §3 beeline] 翻译成 pydantic 模型。

业务流（来自 queen rules.exception_handling.inventory_shortage_priority）：
  diagnose_exception
    -> try_alternative_warehouse   (替代仓)
       -> success: notify_customer
       -> fallback: try_inter_warehouse_transfer
    -> try_inter_warehouse_transfer  (跨仓调拨)
       -> success: notify_customer
       -> fallback: try_replenishment_eta
    -> try_replenishment_eta        (补货 ETA)
       -> acceptable: notify_customer
       -> fallback: try_substitute_product
    -> try_substitute_product       (替代品)
       -> success: notify_customer
       -> fallback: calculate_compensation
    -> calculate_compensation       (补偿计算)
       -> notify_customer
          -> human_review_required: escalate_to_human
             -> (terminal)

编排特征：
- 分支：每个 try_* 有 2 个 next（success/fallback）
- 汇聚（fan-in）：notify_customer 多个上游
- 幂等性：try_* / calculate_compensation / diagnose 用 required（按 order_id 去重）
            notify_customer 用 forbidden（不重复通知）
            escalate_to_human 用 required（不重复升级）
- 无环：每条 try_* 失败降级到下一个 try_*，不回头
"""

from __future__ import annotations

from core.beeline_models import (
    Bee,
    Beeline,
    ExternalSystem,
    Idempotency,
    NextRef,
    Operation,
)


# 幂等性策略集中定义（共享 key 路径）
IDEMPOTENT_BY_ORDER = Idempotency(mode="required", key="order_id")
NOTIFY_ONCE = Idempotency(mode="forbidden")


INVENTORY_SHORTAGE_BEELINE = Beeline(
    id="beeline_inventory_shortage_v3",
    version=12,
    description=(
        "订单库存不足履约作业路线——按 queen 配置的优先级链："
        "替代仓 → 跨仓调拨 → 补货 ETA → 替代品 → 补偿计算 → 通知客户 → 必要时升级人工"
    ),

    operations=[
        # ----- 入口：异常诊断 -----
        Operation(
            op_id="diagnose_exception",
            type="validate_exception",
            input_from="external",
            input="schema_order_exception",
            output="schema_diagnosis_result",
            next=[NextRef(op_id="try_alternative_warehouse")],
            bee=Bee(type="exception_classifier"),
            idempotency=IDEMPOTENT_BY_ORDER,
        ),

        # ----- 优先级链 1：替代仓 -----
        Operation(
            op_id="try_alternative_warehouse",
            type="warehouse_inventory_lookup",
            input_from="diagnose_exception",
            input="schema_diagnosis_result",
            output="schema_resolution_result",
            next=[
                NextRef(op_id="notify_customer", when="resolution_type == fulfilled"),
                NextRef(op_id="try_inter_warehouse_transfer"),  # 无 when = 默认降级
            ],
            bee=Bee(type="warehouse_inventory_api", params={"radius_km": 200}),
            idempotency=IDEMPOTENT_BY_ORDER,
        ),

        # ----- 优先级链 2：跨仓调拨 -----
        Operation(
            op_id="try_inter_warehouse_transfer",
            type="inter_warehouse_transfer",
            input_from="diagnose_exception",
            input="schema_diagnosis_result",
            output="schema_resolution_result",
            next=[
                NextRef(op_id="notify_customer", when="resolution_type == fulfilled"),
                NextRef(op_id="try_replenishment_eta"),
            ],
            bee=Bee(type="warehouse_transfer_api"),
            idempotency=IDEMPOTENT_BY_ORDER,
        ),

        # ----- 优先级链 3：补货 ETA -----
        Operation(
            op_id="try_replenishment_eta",
            type="replenishment_check",
            input_from="diagnose_exception",
            input="schema_diagnosis_result",
            output="schema_resolution_result",
            next=[
                NextRef(op_id="notify_customer", when="eta_acceptable == true"),
                NextRef(op_id="try_substitute_product"),
            ],
            bee=Bee(type="replenishment_api"),
            idempotency=IDEMPOTENT_BY_ORDER,
        ),

        # ----- 优先级链 4：替代品 -----
        Operation(
            op_id="try_substitute_product",
            type="product_substitution",
            input_from="diagnose_exception",
            input="schema_diagnosis_result",
            output="schema_resolution_result",
            next=[
                NextRef(op_id="notify_customer", when="resolution_type == fulfilled"),
                NextRef(op_id="calculate_compensation"),
            ],
            bee=Bee(type="product_catalog_api"),
            idempotency=IDEMPOTENT_BY_ORDER,
        ),

        # ----- 兜底：补偿计算 -----
        Operation(
            op_id="calculate_compensation",
            type="compensation_calc",
            input_from="try_substitute_product",
            input="schema_resolution_result",
            output="schema_resolution_result",
            next=[NextRef(op_id="notify_customer")],
            bee=Bee(
                type="compensation_calculator",
                params={"policy": "queen.exception_handling.refund_auto_approval_threshold"},
            ),
            idempotency=IDEMPOTENT_BY_ORDER,
        ),

        # ----- 汇聚点：通知客户（fan-in 上游） -----
        Operation(
            op_id="notify_customer",
            type="customer_notification",
            input_from="calculate_compensation",  # 默认上游；runtime 按 fan-in 路由
            input="schema_resolution_result",
            output="schema_resolution_result",
            next=[
                NextRef(op_id="escalate_to_human", when="human_review_required == true"),
            ],
            external_system=ExternalSystem(id="notification_service", interface="send"),
            idempotency=NOTIFY_ONCE,
        ),

        # ----- 终态：升级人工（按 queen escalation_rules） -----
        Operation(
            op_id="escalate_to_human",
            type="human_escalation",
            input_from="notify_customer",
            input="schema_resolution_result",
            output="schema_resolution_result",
            next=[],
            bee=Bee(type="human_handoff_queue"),
            idempotency=IDEMPOTENT_BY_ORDER,
        ),
    ],
)


def get_beeline() -> Beeline:
    """返回库存不足订单履约 beeline"""
    return INVENTORY_SHORTAGE_BEELINE