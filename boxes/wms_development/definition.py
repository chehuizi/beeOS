"""boxes.wms_development.definition - WMS Development Box Definition 数据

WMS 开发盒——**按设计包改代码**，把方案变成能构建、能跑起来的代码：
定位源码 → 改 → 编译 → 跑仓库自带的测试套件 → 制品。

按 Fulfillment 5 维度 + 度量组织：
- 基础元信息 + placement（归属 / 接谁 / 投给谁 / 属于哪个域）
- 数据形状（schemas）
- 履约意图 Intent（task）
- 履约合同 Contract（result + 4 条机械 acceptance + 2 条 exception）
- 履约政策 Policy（queen）
- 履约度量 Metrics

**成熟度：live**。4 条判据的答案全部来自**外部神谕**——
编译器（编译过没过）、仓库自带的测试套件（单测）、git（改了哪些文件、
提交能不能追回去）。没有一条是这只盒子自述的。

**不声明任何指标**：没有稳定产出的可测的量，就不声明——
声明了指标却没有采集器，那正是「声明了却拿不到」。
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
        "WMS 开发盒——**按设计包改代码**：把方案变成能构建、能跑起来的代码。\n\n"
        "它的产出（制品 + 构建记录）交给测试盒验证，同时进系统档案——"
        "「现在跑的这版是从哪个提交构建出来的」这件事，得由它回答。\n\n"
        "判据跑的是外部神谕：编译器、仓库自带的测试套件、git——"
        "模型说什么不算数。"
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
            #
            # 标识**藏在设计包对象里**——设计包是整份上游产出（`ref:` 字段
            # 装的就是一份对象），而「这份设计对应哪条需求」的答案在它的
            # set_id 上。所以要指明从对象的哪个键取。
            subject_ref_field="design_package_ref",
            subject_ref_subfield="set_id",
            # 这只盒子的业务对象是**需求**（隔着设计包一跳，需求仍是同一个）。
            subject_kind="requirement",
            trigger=(
                "设计包评审通过要落代码\nOR 线上缺陷要打补丁\nOR 发版前要出可复现的构建产物"
            ),
        ),
    ],

    result=ResultDef(
        type="wms_code_built",
        result_schema="schema_build_artifact",
        # **live**：这条腿是真的了——它真的起隔离工作区、真的让模型改代码、
        # 真的编译、真的跑仓库自带的测试套件（见 boxes/wms_development/）。
        #
        # 以前是 skeleton：产出是桩，刻意不产出判据要的 metric，
        # 让看板显式标「未实现」。现在腿在了，徽章就得摘掉——
        # **「未实现」和「实现了但不达标」必须分得开**。
        maturity="live",
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
            # 编译不过 = 不交付。**不用 rollback**：runtime 里的 rollback
            # 只是个状态标记，并不会真的撤销提交——用它等于宣称
            # 「已补偿」而实际没人补偿（声明了却没人执行那一族）。
            ExceptionRule(
                condition="build_failed == true", action="no_deliver",
            ),
            ExceptionRule(
                condition="unit_test_pass_rate < 0.95", action="escalate",
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
            },            continuous_improvement={},
        ),
    ),

    # 骨架盒不声明指标：没有产出就没有可测的量。
    metrics=MetricsDef(),
)


def get_definition() -> BeeBoxDefinition:
    return WMS_DEVELOPMENT_BOX
