"""
boxes.inventory_shortage.definition - 库存不足订单履约盒 Definition 数据

按 [docs/examples/order-exception-box.md §2] 翻译成 pydantic 模型。
完整覆盖：
- 基础元信息
- 数据形状（schemas）
- 履约意图 Intent（task）
- 履约合同 Contract（result + acceptance + exceptions）
- 履约政策 Policy（queen authorization + rules）
- 履约度量 Metrics（quality + latency + cost）
"""

from __future__ import annotations

from core.models import (
    AcceptanceRule,
    BeeBoxDefinition,
    ExceptionRule,
    MetricDef,
    MetricsDef,
    QueenAuthorization,
    QueenDef,
    QueenRules,
    ResultDef,
    TaskDef,
)
from boxes.inventory_shortage.schemas import ALL_SCHEMAS


ORDER_EXCEPTION_BOX = BeeBoxDefinition(
    id="order_exception_box",
    version=3,
    description=(
        "订单异常履约盒——按政策处理订单异常，"
        "在 SLA 内给客户一个符合政策的可验证结果"
    ),

    schemas=ALL_SCHEMAS,

    task=[
        TaskDef(
            type="handle_order_exception",
            task_schema="schema_order_exception",
            beeline_id="beeline_inventory_shortage_v3",
            beeline_version=12,
            trigger=(
                "库存系统检测到 inventory_shortage\n"
                "OR 支付系统推送 payment_failed\n"
                "OR 物流系统推送 shipping_delay\n"
                "OR 客服系统推送 customer_complaint"
            ),
        ),
    ],

    result=ResultDef(
        type="order_exception_resolved",
        result_schema="schema_resolution_result",
        acceptance=[
            AcceptanceRule(
                metric="resolution_within_sla",
                op="lte",
                # SLA 阈值：VIP 30min, Standard 4h, Enterprise 2h
                # （实际值按 customer_tier 动态计算）
                value=30,  # 分钟
            ),
            AcceptanceRule(metric="customer_notified", op="eq", value=True),
            AcceptanceRule(metric="refund_amount_correct", op="eq", value=True),
            AcceptanceRule(metric="order_state_consistent", op="eq", value=True),
            AcceptanceRule(metric="evidence_complete", op="eq", value=True),
            AcceptanceRule(metric="human_escalation_if_needed", op="eq", value=True),
        ],
        exceptions=[
            ExceptionRule(
                condition="refund_amount > 500",
                action="escalate",
            ),
            ExceptionRule(
                condition="customer_tier == enterprise AND severity == critical",
                action="escalate",
            ),
            ExceptionRule(
                condition="resolution_failed_after_3_attempts",
                action="rollback",
            ),
            ExceptionRule(
                condition="customer_complaint AND no_acceptable_option",
                action="no_deliver",
            ),
        ],
    ),

    queen=QueenDef(
        authorization=QueenAuthorization(
            task_routing="allow",
            exception_handling="allow",
            continuous_improvement="observe_only",  # 改善只观察不执行
        ),
        rules=QueenRules(
            task_routing={
                "sla_by_customer_tier": {
                    "vip": 30,        # VIP 30min
                    "standard": 240,  # 标准 4h
                    "enterprise": 120,  # 企业 2h
                },
            },
            exception_handling={
                "inventory_shortage_priority": [
                    "try_alternative_warehouse",
                    "try_inter_warehouse_transfer",
                    "try_replenishment_eta",
                    "try_substitute_product",
                    "calculate_compensation",
                    "refund_or_recommend",
                ],
                "refund_auto_approval_threshold": 50,
                "refund_queen_judgment_range": [50, 500],
                "refund_human_approval_threshold": 500,
                "customer_complaint_response_time": {
                    "vip": 15,
                    "standard": 60,
                },
                "escalation_rules": [
                    {"trigger": "severity == critical", "action": "escalate_to_human"},
                    {"trigger": "customer_tier == enterprise", "action": "escalate_to_human"},
                    {"trigger": "refund_amount > 500", "action": "escalate_to_human"},
                ],
            },
            continuous_improvement={
                "monitor_metrics": [
                    "first_time_resolution_rate",
                    "average_resolution_time",
                    "sla_breach_rate",
                    "human_escalation_rate",
                    "cost_per_exception",
                ],
            },
        ),
    ),

    metrics=MetricsDef(
        quality=[
            MetricDef(
                name="first_time_resolution_rate",
                definition="首次处理解决率（不需要重试或升级）",
                target=0.85,
            ),
            MetricDef(name="sla_breach_rate", definition="SLA 违背率", target=0.05),
            MetricDef(name="compensation_accuracy", definition="补偿金额准确率", target=0.99),
        ],
        latency=[
            MetricDef(name="average_resolution_time", definition="平均处理时长", target=600),
            MetricDef(name="p95_resolution_time", definition="95 分位处理时长", target=1800),
            MetricDef(name="first_response_time", definition="首次响应时间", target=60),
        ],
        cost=[
            MetricDef(
                name="cost_per_exception",
                definition="单次异常处理成本（资源消耗 + 人工干预折算）",
                target=0.50,
            ),
            MetricDef(name="human_escalation_rate", definition="升级人工率", target=0.15),
        ],
    ),
)


def get_definition() -> BeeBoxDefinition:
    """返回订单异常履约盒 Definition"""
    return ORDER_EXCEPTION_BOX