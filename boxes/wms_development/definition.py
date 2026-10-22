"""boxes.wms_development.definition - WMS Development Box Definition 数据

WMS 开发盒——把设计包变成**能构建、能跑起来**的代码：提交 → 构建 → 自动化测试 → 制品。

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
from boxes.wms_development.schemas import ALL_SCHEMAS

WMS_DEVELOPMENT_BOX = BeeBoxDefinition(
    id="wms_development_box",
    version=1,
    description=(
        "WMS 开发盒——把设计包变成**能构建、能跑起来**的代码：提交 → 构建 → 自动化测试 → 制品。\n\n它的产出（制品 + 构建记录）交给测试盒验证，同时进系统档案——「现在跑的这版是从哪个提交构建出来的」这件事，得由它回答。"
    ),

    schemas=ALL_SCHEMAS,

    placement=PlacementDef(
        display_name="WMS Development Box",
        value_stream="Software Delivery",
        role="WMS Implementation Fulfillment",
        domain="WMS",
        feeds_into=['wms_testing_box', 'wms_system_box'],
    ),

    task=[
        TaskDef(
            type="handle_development_request",
            task_schema="schema_dev_task_input",
            beeline_id="beeline_wms_development_v1",
            beeline_version=1,
            # 本次履约处理的业务对象标识（归档按它分组）
            subject_ref_field="design_package_ref",
            trigger=(
                "设计包评审通过要落代码\nOR 线上缺陷要打补丁\nOR 发版前要出可复现的构建产物"
            ),
        ),
    ],

    result=ResultDef(
        type="wms_code_built",
        result_schema="schema_build_artifact",
        maturity="skeleton",
        acceptance=[
            AcceptanceRule(
                metric="build_reproducibility", op="eq", value=True,
            ),
            AcceptanceRule(
                metric="unit_test_green", op="eq", value=True,
            ),
            AcceptanceRule(
                metric="review_coverage", op="eq", value=True,
            ),
            AcceptanceRule(
                metric="artifact_traceable", op="eq", value=True,
            ),
        ],
        exceptions=[
            ExceptionRule(
                condition="build_failed == true", action="rollback",
            ),
            ExceptionRule(
                condition="unit_test_pass_rate < 0.95", action="no_deliver",
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
                    {"trigger": "build_failed == true", "action": "escalate_to_human"},
                    {"trigger": "unit_test_pass_rate < 0.95", "action": "escalate_to_human"},
                ],
            },
            continuous_improvement={},
        ),
    ),

    # 骨架盒不声明指标：没有产出就没有可测的量。
    metrics=MetricsDef(),
)


def get_definition() -> BeeBoxDefinition:
    return WMS_DEVELOPMENT_BOX
