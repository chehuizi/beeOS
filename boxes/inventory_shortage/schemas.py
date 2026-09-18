"""
boxes.inventory_shortage.schemas - 库存不足盒子的业务 schemas

定义 3 个业务 schema：
- order_request：订单请求数据
- order_exception：订单异常事件
- resolution_result：异常处理结果

每个 schema 都被 definition.schemas 引用（task_schema / result_schema），
也被 task / operation input/output 引用。
"""

from __future__ import annotations

from core.models import FieldDef, SchemaDef

# ============================================================
# order_request - 订单请求数据
# ============================================================

ORDER_REQUEST = SchemaDef(
    id="schema_order_request",
    description="订单请求数据",
    fields=[
        FieldDef(name="order_id", type="string"),
        FieldDef(name="customer_id", type="string"),
        FieldDef(
            name="items",
            type="array",
            items=FieldDef(
                name="item",
                type="object",
                properties=[
                    FieldDef(name="sku", type="string"),
                    FieldDef(name="quantity", type="integer"),
                    FieldDef(name="unit_price", type="number"),
                ],
            ),
        ),
        FieldDef(name="shipping_address", type="ref:schema_address"),
        FieldDef(name="amount", type="number"),
    ],
)

# ============================================================
# schema_address - 收货地址（被 order_request 引用）
# ============================================================

ADDRESS = SchemaDef(
    id="schema_address",
    description="收货地址",
    fields=[
        FieldDef(name="street", type="string"),
        FieldDef(name="city", type="string"),
        FieldDef(name="state", type="string"),
        FieldDef(name="zip_code", type="string"),
        FieldDef(name="country", type="string"),
    ],
)

# ============================================================
# order_exception - 订单异常事件（task 输入）
# ============================================================

ORDER_EXCEPTION = SchemaDef(
    id="schema_order_exception",
    description="订单异常事件",
    fields=[
        FieldDef(name="exception_id", type="string"),
        FieldDef(name="order_id", type="string"),
        FieldDef(name="customer_id", type="string"),
        FieldDef(name="amount", type="number"),
        FieldDef(
            name="exception_type",
            type="enum",  # 实际应支持 enum 约束，这里先 string
        ),
        FieldDef(name="detected_at", type="string"),
        FieldDef(
            name="context",
            type="object",
            properties=[
                FieldDef(name="severity", type="enum"),
                FieldDef(name="customer_tier", type="enum"),
            ],
            required=False,
        ),
    ],
)

# ============================================================
# resolution_result - 异常处理结果（task 输出 / acceptance 输入）
# ============================================================

RESOLUTION_RESULT = SchemaDef(
    id="schema_resolution_result",
    description="异常处理结果",
    fields=[
        FieldDef(name="resolution_id", type="string"),
        FieldDef(
            name="resolution_type",
            type="enum",  # fulfilled / partial_fulfilled / refunded / compensated / escalated
        ),
        FieldDef(
            name="actions_taken",
            type="array",
            items=FieldDef(
                name="action",
                type="object",
                properties=[
                    FieldDef(name="action", type="string"),
                    FieldDef(name="timestamp", type="string"),
                ],
            ),
        ),
        FieldDef(name="refund_amount", type="number", required=False),
        FieldDef(name="customer_notified", type="boolean"),
    ],
)

ALL_SCHEMAS = [ORDER_REQUEST, ADDRESS, ORDER_EXCEPTION, RESOLUTION_RESULT]