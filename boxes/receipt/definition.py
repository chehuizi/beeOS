"""
boxes.receipt.definition - 小票识别履约盒 Definition

输入：一张小票照片
输出：结构化小票 + 6 项机械校验证据
验收：解析内容正确

**关于「验收：解析内容完全正确」这句话怎么落到判据上**

原话是「验收解析内容完全正确」。但"内容正确"里只有一部分能机械验：

能验的（本盒 acceptance 全部覆盖，0 主观）：
- field_completeness —— 必填字段齐全（商��或单号至少有一个 + 金额齐全 + 有商品行）
- line_arithmetic     —— 每行 数量 × 单价 == 该行金额
- sum_arithmetic      —— Σ行金额 == 小计
- total_arithmetic    —— 小计 + 税 == 合计
- date_wellformed     —— 日期时间格式合法
- totals_match        —— 以上全过

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