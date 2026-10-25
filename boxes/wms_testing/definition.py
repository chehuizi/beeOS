"""boxes.wms_testing.definition - WMS Testing Box Definition 数据

WMS 测试盒——拿开发盒的制品，用**WMS 业务场景**验它：收货上架、拣货复核、盘点、批次效期这些真实单据跑一遍。

按 Fulfillment 5 维度 + 度量组织：
- 基础元信息 + placement（归属 / 接谁 / 投给谁 / 属于哪个域）
- 数据形状（schemas）
- 履约意图 Intent（task）
- 履约合同 Contract（result + 4 条机械 acceptance + 2 条 exception）
- 履约政策 Policy（queen）
- 履约度量 Metrics

**成熟度：skeleton**。4 条判据都是它**将来**要验的东西，现在
还没实现。`maturity="skeleton"` 让看板显式标「未实现」且不把它算进
验收率——「未实现」和「实现了但不达标」必须分得开。

**不声明任何指标**：骨架盒没有产出，就没有可测的量。声明了指标却
没有采集器，那正是「声明了却拿不到」。
"""

from __future__ import annotations

from core.models import (
    AcceptanceRule,
    BeeBoxDefinition,
    ExceptionRule,
    MetricsDef,
    PlacementDef,
    QueenAuthorization,
    QueenDef,
    QueenRules,
    ResultDef,
    TaskDef,
)
from boxes.wms_testing.schemas import ALL_SCHEMAS

WMS_TESTING_BOX = BeeBoxDefinition(
    id="wms_testing_box",
    version=1,
    description=(
        "WMS 测试盒——拿开发盒的制品，用**WMS 业务场景**验它：收货上架、拣货复核、盘点、批次效期这些真实单据跑一遍。\n\n它验的不是「代码跑没跑起来」，是「仓储现场的规则守没守住」——比如同一 SKU 同库位有没有被占用两次、效期先出有没有被绕过。"
    ),

    schemas=ALL_SCHEMAS,

    placement=PlacementDef(
        display_name="WMS Testing Box",
        value_stream="Software Delivery",
        role="WMS Verification Fulfillment",
        domain="WMS",
        feeds_into=['wms_ops_box', 'wms_system_box'],
    ),

    task=[
        TaskDef(
            type="handle_testing_request",
            task_schema="schema_test_task_input",
            beeline_id="beeline_wms_testing_v1",
            beeline_version=1,
            # 本次履约处理的业务对象标识（归档按它分组）
            #
            # 需求集是整份上游产出（`ref:` 字段装的是一份对象），
            # 「这批用例服务哪条需求」的答案在它的 set_id 上。
            subject_ref_field="requirement_ref",
            subject_ref_subfield="set_id",
            subject_kind="requirement",
            trigger=(
                "开发盒出制品要验收\nOR 发版前跑全量回归\nOR 生产出了单据异常要复现"
            ),
        ),
    ],

    result=ResultDef(
        type="wms_verified",
        result_schema="schema_test_report",
        # **live**：这条腿是真的了——连真的 WMS 实例、造真的单据、
        # 采真的实测值（见 boxes/wms_testing/probes.py）。
        #
        # 以前是 skeleton：产出是桩，刻意不产出判据要的 metric，
        # 让看板显式标「未实现」。现在腿在了，徽章就得摘掉——
        # **「未实现」和「实现了但不达标」必须分得开**。
        maturity="live",
        acceptance=[
            AcceptanceRule(
                metric="requirement_case_coverage", op="eq", value=True,
            ),
            AcceptanceRule(
                metric="scenario_coverage", op="eq", value=True,
            ),
            AcceptanceRule(
                metric="no_blocking_defect", op="eq", value=True,
            ),
            # **阈值是 99，不是 0.99**。
            #
            # `case_pass_rate` 是**百分数**（0-100），跟建模盒的
            # `requirement_coverage` 同一个量纲。写成 `gte 0.99` 的话，
            # **33.33% 也算通过**——判据在假设指标的形状，
            # 而实际跑出来「6 条里过了 2 条」被判成 passed。
            # 这不是理论问题：真跑一次就看见了。
            AcceptanceRule(
                metric="case_pass_rate", op="gte", value=99,
            ),
            # **「这次验出了点东西」**
            #
            # 少了它，一次什么都没验出来的运行会显示成
            # 「4 条通过 + 1 条用不上」——读起来像「基本没问题」。
            # 那是假信号：真相比「全都没问题」难看得多。
            AcceptanceRule(
                metric="verification_determined", op="eq", value=True,
            ),
            # **「这次验的是哪份构建」**
            #
            # 每条观测都必须带着制品摘要（开发盒交付的那份构建）。
            # 少了它，「临期 SKU 数是 1」是无主的——不知道测的是哪份代码，
            # 就没法说这次验算不算数。
            AcceptanceRule(
                metric="artifact_identified", op="eq", value=True,
            ),
        ],
        exceptions=[
            ExceptionRule(
                condition="open_defect_count > 0", action="escalate",
            ),
            ExceptionRule(
                condition="case_pass_rate < 0.95", action="rollback",
            ),
        ],
        view="generic",
    ),

    queen=QueenDef(
        authorization=QueenAuthorization(
            task_routing="allow",
            exception_handling="allow",
            # **observe_only 不是省事**：这只盒子的腿还没实现，
            # 让它自己改进只会把「没做」优化成「做得更像」。
            continuous_improvement="observe_only",
        ),
        rules=QueenRules(
            task_routing={},
            exception_handling={
                "escalation_rules": [
                    {"trigger": "open_defect_count > 0", "action": "escalate_to_human"},
                    {"trigger": "case_pass_rate < 0.95", "action": "escalate_to_human"},
                ],
            },
            continuous_improvement={},
        ),
    ),

    # 骨架盒不声明指标：没有产出就没有可测的量。
    metrics=MetricsDef(),
)


def get_definition() -> BeeBoxDefinition:
    return WMS_TESTING_BOX
