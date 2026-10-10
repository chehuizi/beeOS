"""
boxes.receipt.schemas - 小票识别盒的数据契约

两块：
- 输入：一张照片（media 字段，声明式——投料口据此弹图片上传，不靠字段名猜）
- 输出：结构化小票 + 可机械验证的校验证据

**为什么判据值写进产出 schema**：acceptance 要读的每个 metric 都得是
产出契约的一部分。不写进来的话，判据永远读不到值——要么恒判通过
（假绿灯），要么恒判失败。捕获盒和建模盒都是这么做的。
"""

from __future__ import annotations

from core.models import FieldDef, SchemaDef


# ============================================================
# 输入层
# ============================================================


RECEIPT_PHOTO = SchemaDef(
    id="schema_receipt_photo",
    description="一张小票照片",
    fields=[
        FieldDef(
            name="photo",
            type="string",
            # 声明成 media → 投料口弹图片上传（拖拽 / 选择 / ⌘V 粘贴），
            # 而不是让人往文本框里粘 300KB 的 base64
            media="image",
            required=True,
            description="小票照片（data URL 或 http URL）",
        ),
        FieldDef(
            name="merchant_hint",
            type="string",
            required=False,
            description="可选：拍票人补充的商户名（小票字迹糊了时的旁证，不作为识别依据）",
        ),
    ],
)


# ============================================================
# 输出层
# ============================================================


RECEIPT_ITEM = SchemaDef(
    id="schema_receipt_item",
    description="小票上的一行商品",
    fields=[
        FieldDef(name="line_no", type="integer"),
        FieldDef(name="name", type="string"),
        FieldDef(name="qty", type="number"),
        FieldDef(name="unit_price", type="number"),
        FieldDef(name="amount", type="number"),
    ],
)


RECEIPT_PARSED = SchemaDef(
    id="schema_receipt_parsed",
    description="结构化小票",
    fields=[
        FieldDef(name="merchant_name", type="string"),
        FieldDef(name="receipt_no", type="string"),
        FieldDef(name="receipt_date", type="string", description="ISO 日期 YYYY-MM-DD"),
        FieldDef(name="receipt_time", type="string", description="HH:MM 或空串"),
        FieldDef(name="items", type="ref:schema_receipt_item"),
        FieldDef(name="subtotal", type="number", description="小计（商品合计，不含税）"),
        FieldDef(name="tax", type="number"),
        FieldDef(name="total", type="number", description="实付合计"),
        FieldDef(name="payment_method", type="string"),
        # ---- 可机械验证的校验证据（详见 runner）----
        # 算术自洽：小计 + 税 = 合计；每行 qty × 单价 = 该行金额
        FieldDef(name="field_completeness", type="boolean", description="必填字段齐全"),
        FieldDef(name="line_arithmetic", type="boolean", description="每行 数量×单价 = 金额"),
        FieldDef(name="sum_arithmetic", type="boolean", description="Σ行金额 = 小计"),
        FieldDef(name="total_arithmetic", type="boolean", description="小计 + 税 = 合计"),
        FieldDef(name="date_wellformed", type="boolean", description="日期格式合法"),
        FieldDef(name="totals_match", type="boolean", description="金额与票据上印的合计一致"),
        FieldDef(name="low_confidence_fields", type="array", items=FieldDef(name="f", type="string"), required=False,
                 description="模型自认看不清、可能有误的字段名（供人工对账时优先核）"),
    ],
)


ALL_SCHEMAS = [
    RECEIPT_PHOTO,
    RECEIPT_ITEM,
    RECEIPT_PARSED,
]