"""tests.fixtures.sample_box.beeline - 夹具盒的作业路线

结构上覆盖运行时要测的那几件事：
- 顺序推进（classify → resolve）
- **分支**（resolve 成功后走 notify，否则走 escalate）
- 两个终态（notify / escalate 都是终点）
- 幂等性策略（required / forbidden 各一种）

语义上什么都不讲——它不是产品。
"""

from __future__ import annotations

from core.beeline_models import (
    Bee,
    Beeline,
    Idempotency,
    NextRef,
    Operation,
)

IDEMPOTENT_BY_SAMPLE = Idempotency(mode="required", key="sample_id")
NOTIFY_ONCE = Idempotency(mode="forbidden")


SAMPLE_BEELINE = Beeline(
    id="beeline_sample_v1",
    version=1,
    description="夹具盒作业路线——分类 → 处理 →（成功则通知，否则升级）",

    operations=[
        Operation(
            op_id="sample_classify",
            type="sample_classify",
            input_from="external",
            input="schema_sample_request",
            output="schema_sample_result",
            next=[NextRef(op_id="sample_resolve")],
            bee=Bee(type="sample_classifier"),
            idempotency=IDEMPOTENT_BY_SAMPLE,
        ),
        Operation(
            op_id="sample_resolve",
            type="sample_resolve",
            input_from="sample_classify",
            input="schema_sample_result",
            output="schema_sample_result",
            next=[
                NextRef(op_id="sample_notify", when="sample_state == resolved"),
                NextRef(op_id="sample_escalate"),
            ],
            bee=Bee(type="sample_resolver"),
            idempotency=IDEMPOTENT_BY_SAMPLE,
        ),
        Operation(
            op_id="sample_notify",
            type="sample_notify",
            input_from="sample_resolve",
            input="schema_sample_result",
            output="schema_sample_result",
            next=[],
            bee=Bee(type="sample_notifier"),
            idempotency=NOTIFY_ONCE,
        ),
        Operation(
            op_id="sample_escalate",
            type="sample_escalate",
            input_from="sample_resolve",
            input="schema_sample_result",
            output="schema_sample_result",
            next=[],
            bee=Bee(type="sample_escalator"),
            idempotency=IDEMPOTENT_BY_SAMPLE,
        ),
    ],
)


def get_beeline() -> Beeline:
    """返回夹具盒的 beeline"""
    return SAMPLE_BEELINE
