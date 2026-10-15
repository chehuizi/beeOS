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
            # 声明本盒收哪些格式。HEIC 在列：iPhone 拍的小票默认就是这个，
            # 拒收等于让最常见的那种票据投不进来。转码由 runner 在
            # read_receipt 那一腿做（boxes/receipt/_as_viewable_image），
            # 到模型手里一定是 JPEG。
            media_accept=[
                "image/heic", "image/heif",
                "image/jpeg", "image/png", "image/webp", "image/gif",
            ],
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
        # unit_price 抄「原价」那一格。MUJI POS 折后商品印的是
        # `1x498×5.83折=290.00`：498 是原价，290 是折后行金额，
        # 两者不等且都对——差的那部分由 discount_rate 解释。
        FieldDef(name="unit_price", type="number", description="原价（不是折后价）"),
        FieldDef(name="amount", type="number", description="这一行票面印的金额（折后）"),
        # 行级折扣。**照抄票面印的折数**：「×5.83折」填 5.83、「8折」填 8。
        # 存折数不存 0.583：模型只负责抄，÷10 换算由盒子做——
        # 让模型自己算出 0.583，这一步就没有校验价值了。
        # 没折扣就填 null（不是 10，10 是「没有折扣」的编造说法）。
        FieldDef(
            name="discount_rate",
            type="number",
            required=False,
            description="行级折扣折数（照抄「×5.83折」=5.83）；无折扣为 null",
        ),
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
        # exception `items_count == 0`（没读出任何商品行 → 不交付）读的量。
        # 以前产出里只有 items 数组、没有这个计数，于是那条规则指向一个
        # 不存在的字段，**从来没触发过**：一张只拍到票头、没拍到商品的
        # 照片照样会被交付出去。数组长度条件求不了，标量才有得比。
        FieldDef(name="items_count", type="integer",
                 description="读出的商品行数（为 0 说明这张票没读出商品行）"),
        FieldDef(name="subtotal", type="number", description="小计 / 商品合计"),
        # 零售 POS 小票**没有税**，只有优惠（支付宝优惠 / 满减 / 抹零）。
        # 漏掉这一栏，票上最常见的那个数字就无处安放——模型只能给 null，
        # 然后「小计+税=实付」必然对不上。实测踩坑：盒马 NB 小票
        # 商品合计 28.80、支付宝优惠 -0.18、实付 28.62，就卡在这一栏。
        # tax 和 discount 都留着：POS 小票只有 discount，增值税发票只有 tax。
        FieldDef(name="tax", type="number", description="税额（零售 POS 小票通常没有）"),
        FieldDef(name="discount", type="number", description="优惠 / 折扣 / 减免（正数=减免了多少）"),
        # 合计里**已经含进去**的优惠额。跟 discount 是两回事：
        #   盒马 `商品合计 28.80 / 支付宝优惠 -0.18 / 实付 28.62` → 优惠是从合计里减的
        #   六贤记 `合计:39.70(2.60) / 折让金额:2.60 / 付款 39.70` → 折让**含在**39.70里
        # 两种都写「优惠 2.60」，减不减它结果差 2.60。
        # 括号里那个数是**票面印着的**，抄它就行，不用推。
        FieldDef(
            name="included_discount",
            type="number",
            required=False,
            description="合计里已含的优惠（票面「合计:39.70(2.60)」里的 2.60）；"
                        "优惠是在合计之后另减的则填 null",
        ),
        FieldDef(name="total", type="number", description="实付合计"),
        FieldDef(name="payment_method", type="string"),
        # ---- 可机械验证的校验证据（详见 runner）----
        # 算术自洽：每行 qty × 单价 = 金额；Σ行金额 = 小计；小计 + 税 − 优惠 = 实付
        FieldDef(name="field_completeness", type="boolean", description="必填字段齐全"),
        # 三态，不是 boolean：票上压根没印单价栏时（餐馆结账单很常见）
        # 这条判据**用不上**，值是 "n/a"。它不算失败，但也不能当成 true——
        # 消费者必须能分辨「验过了」和「没得验」，否则就是把没验说成验过。
        FieldDef(
            name="line_arithmetic",
            type="boolean_or_na",
            description="每行 数量×单价 = 金额；票上无单价栏时为 \"n/a\"（不适用，非通过）",
        ),
        FieldDef(name="sum_arithmetic", type="boolean", description="Σ行金额 = 小计"),
        FieldDef(name="total_arithmetic", type="boolean", description="小计 + 税 − 优惠 = 实付"),
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