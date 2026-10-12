"""
boxes.receipt.definition - 小票识别履约盒 Definition

输入：一张小票照片
输出：结构化小票 + 6 项机械校验证据
验收：解析内容正确

**关于「验收：解析内容完全正确」这句话怎么落到判据上**

原话是「验收解析内容完全正确」。但"内容正确"里只有一部分能机械验：

能验的（本盒 acceptance 全部覆盖，0 主观）：
- field_completeness —— 必填字段齐全（商户或单号至少有一个 + 金额齐全 + 有商品行）
- line_arithmetic     —— 每行 数量 × 原价 × 折扣 ≈ 行金额（**可能不适用，见下**）
- sum_arithmetic      —— Σ行金额 == 小计
- total_arithmetic    —— 小计 + 税 − 优惠 == 实付（**优惠要分两种，见下**）
- date_wellformed     —— 日期时间格式合法
- totals_match        —— 以上全过

**行级折扣：为什么不能写成 数量 × 单价 == 金额**

折后商品的票面长这样（MUJI POS 实测）：

    1x498×5.83折=290.00   →  ¥290.0

原价 498、折后行金额 290、折扣 5.83 折——三个数**都印在票上**，
抄下来全对，但 `1 × 498 = 498 ≠ 290`。产出一点错没有，是判据
假定了「行金额永远等于原价 × 数量」。同一个病根的第三次：
把某类票的形状当成所有票的形状（头两次是没税栏、没单价栏）。

所以商品行带 `discount_rate`（照抄票面折数，5.83 就填 5.83），
判据验 `数量 × 原价 × 折扣率 ≈ 行金额`。

**折扣率是读来的，不是从金额倒推的。** 倒推的话
`amount / (qty × price)` 恒等于它自己，这条判据就废了——
那才是真正的「永远绿的判据」。漏抄折扣（该有折扣却填 null）
会转红：票面金额对不上原价 × 数量，而没有任何东西解释它。

**抹零是这条规则的一部分。** 小票常把行金额抹零到元：
498 × 5.83折 = 290.334，票面印的是 290.00。这不是算错，是 POS
在这一行就取整了。写成「向下取整到元」这条具体规则，而不是
一句「容差放宽点」——写成规则，误差就有上限（<1 元），金额读错
0.9 元这种真错仍然会被拦。它的盲区照实说：**行金额被误读成比
正确值小不到 1 元时会放过**。

**优惠要分两种：不是每张票都从合计里减优惠**

同一个病根的第五次。六贤记烘焙单实测：

    奶皮鞭子   原价 8.80   实收单价 7.50   数量 2   小计 15.00
    合计:39.70(2.60)   折让金额:2.60   付款 39.70

`2.60 = 2 × (8.80 − 7.50)`，折让是**奶皮鞭子那一行**的差价。而
`39.70 = 15.90 + 8.80 + 15.00` 已经等于 Σ行金额——**折让含在合计里**。
按「小计 − 优惠 == 实付」算就是 39.70 − 2.60 = 37.10 ≠ 39.70，恒红。
产出一点错没有，是判据假定了「优惠永远在合计之后另减」。盒马那种
`商品合计 28.80 / 支付宝优惠 -0.18 / 实付 28.62` 才是另减的。

所以拆成两个字段：

- `discount` —— 票上「优惠/减免/折让金额」栏，**不管它是含在合计里还是另减**
- `included_discount` —— 只在票面写成 `合计:39.70(2.60)` 这种「合计(括号里有数)」
  时填括号里那个**金额**，是含在合计里的那部分

判据减的是「另减的那部分」= `discount − included_discount`。括号里那个数是
**票面印着的**，抄它就行，不推——推的话判据恒真。

守卫：`included_discount > discount` 一定是读错了（MUJI 的
`实物总额(9%)` 是税率不是金额，模型可能填进来），这种当没含、照常减。
宁可让票红，也不要拿一个错数把公式凑平。

**日期写法也要归一，不能只认 ISO**

同一个病根的**第六次**。六贤记那张票印的是 `2026.10.10`。判据原来只认
`YYYY-MM-DD`，等于要求模型每次都乖乖转换——而模型对**格式**的服从度远低于
对**数字**的服从度。实测它直接把整行 `2026.10.10 20:36` 塞进 receipt_no、
让 receipt_date 空着，然后 `date_wellformed` 红。产出一点错没有。

所以归一在机械层（`_normalize_date`）：`2026.10.10` / `2026/10/10` /
`20261010` / `2026年10月10日` / 粘着时间 / 两位年都归成同一个 ISO 串。
模型只管把那三组数字抄对。

**认出写法 ≠ 认可内容**——`2026.13.45`、`2026-02-30` 归一完照样解析失败、
照样转红。归一只放宽「写法」，不放宽「日期本身合不合法」。

**自查腿要查漏抄，不只查抄错**

行算术查不出「字段整行读空」：上面那次三行金额全对，`_looks_inconsistent`
一根手指都动不了。所以另有 `_missing_required_fields` 查必填字段空不空。
两种触发**共用一次重问**（「最多问一次」不变），拆成两次代价翻倍，
还会让模型拿到两份互相矛盾的上下文。

第二遍还是空就保持空，交给判据去红——硬塞一个占位值（比如今天日期）
才是造假信号，「声明了却拿不到」那族病的老路。

**判据是三态，不是两态**

6 条里只有 `line_arithmetic` 可能是「不适用」（`n/a`）：餐馆结账单只印
「品项 / 数量 / 金额」，压根没有单价栏，这时无从验「数量 × 单价 == 金额」。

n/a 不算失败（不挡交付），但**也不算通过**——看板把它单列显示，
写「5/6 · 1 条不适用」，而不是拿它凑成「6/6 全过」。后者是在说谎：
没验过的不能算验过了。

判别 n/a 用的启发式，以及它的盲区，写在 `runner._simulate_verify_receipt`
的 `line_arithmetic` 注释里。一行都没有单价 → 大概率票上没这栏；
有的有有的没有 → 票上有这栏、模型漏抄 → 真失败。

验不了的（**已知盲区，显式声明，不写进 acceptance 假装机器能判**）：
「抄出来的字跟小票上印的是不是一字不差」。比如把 12.50 认成 12.05、
把"香菜"认成"香芋"——算术照样自洽，机械判据全绿，但内容是错的。
这一段靠**人工对账**：每次履约后拿原票对解析结果，重点核
`low_confidence_fields` 里模型自认看不清的字段。

这条边界跟捕获盒的 type_boundary 是同一个：无法机械验证的，
就明确说是人工区，别用一条永远拿不到真值的判据把它盖过去。
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
from boxes.receipt.schemas import ALL_SCHEMAS


RECEIPT_CAPTURE_BOX = BeeBoxDefinition(
    id="receipt_capture_box",
    version=1,
    description=(
        "小票识别履约盒——把一张收银小票照片解析成结构化小票"
        "（商户 / 单号 / 时间 / 商品行 / 金额），并交出 6 项机械校验证据。"
        "模型只负责看图抄字，算术一律由盒子自己算。"
    ),

    schemas=ALL_SCHEMAS,

    task=[
        TaskDef(
            type="capture_receipt",
            task_schema="schema_receipt_photo",
            beeline_id="beeline_receipt_capture_v1",
            beeline_version=1,
            trigger=(
                "财务 / 店长拍一张收银小票要录进账\n"
                "OR 批量报销要先把小票转成结构化明细\n"
                "OR 上一次解析金额对不上，要重拍重认"
            ),
        ),
    ],

    result=ResultDef(
        type="receipt_captured",
        result_schema="schema_receipt_parsed",
        view="receipt",
        description="结构化小票 + 6 项机械校验证据",

        acceptance=[
            AcceptanceRule(metric="field_completeness", op="eq", value=True),
            AcceptanceRule(metric="line_arithmetic", op="eq", value=True),
            AcceptanceRule(metric="sum_arithmetic", op="eq", value=True),
            AcceptanceRule(metric="total_arithmetic", op="eq", value=True),
            AcceptanceRule(metric="date_wellformed", op="eq", value=True),
            AcceptanceRule(metric="totals_match", op="eq", value=True),
        ],

        exceptions=[
            ExceptionRule(
                condition="items_count == 0",
                action="no_deliver",
                reason="没读出任何商品行——照片太糊 / 不是小票 / 拍的是二维码，别硬编",
            ),
            ExceptionRule(
                condition="totals_match == false",
                action="escalate",
                reason="金额自相矛盾（小计+税≠合计，或某行数量×单价≠金额），"
                       "模型多半抄错了数字，需要人工对账后再重认",
            ),
        ],
    ),

    queen=QueenDef(
        authorization=QueenAuthorization(
            task_routing="allow",
            exception_handling="allow",
            continuous_improvement="observe_only",
        ),
        rules=QueenRules(
            exception_handling={
                "request_clarification_on": ["items_count_zero"],
                "re_photo_on": ["totals_mismatch", "date_unreadable"],
                # 这个盒子最容易犯的错不是"读不出"，是"读错了还全绿"：
                # 算术自洽不代表字认对了。禁止为了让判据变绿去改数字。
                "forbidden": ["recompute_totals_to_pass", "invent_missing_fields"],
            },
            continuous_improvement={
                "observe": ["field_accuracy", "first_pass_receipt_rate"],
            },
        ),
    ),

    metrics=MetricsDef(
        quality=[
            MetricDef(
                name="first_pass_receipt_rate",
                definition="首次识别即通过全部 6 项机械判据的比例",
                target=90.0,
                unit="pct",
            ),
            MetricDef(
                name="arithmetic_consistency_rate",
                definition="金额算术自洽（小计+税=合计 且 Σ行=小计）的比例",
                target=98.0,
                unit="pct",
            ),
            MetricDef(
                name="field_completeness_rate",
                definition="必填字段齐全（商户/单号/金额/商品行）的比例",
                target=95.0,
                unit="pct",
            ),
        ],
        latency=[
            MetricDef(
                name="receipt_latency",
                definition="单张小票端到端时长（秒）",
                target=30.0,
                unit="s",
                direction="lower_is_better",
            ),
        ],
    ),
)


def get_definition() -> BeeBoxDefinition:
    return RECEIPT_CAPTURE_BOX