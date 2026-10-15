"""boxes.wms_ops.definition - WMS Ops Box Definition 数据

WMS 运维盒——管**上线之后**的事：变更审批、发布执行、故障处置、回滚能力、以及把线上真实状态回写进系统档案。

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
from boxes.wms_ops.schemas import ALL_SCHEMAS

WMS_OPS_BOX = BeeBoxDefinition(
    id="wms_ops_box",
    version=1,
    description=(
        "WMS 运维盒——管**上线之后**的事：变更审批、发布执行、故障处置、回滚能力、以及把线上真实状态回写进系统档案。\n\n它是唯一一个**读得到生产数据**的盒子：前面四只都在说「应该是什么样」，它说「现在实际是什么样」。"
    ),

    schemas=ALL_SCHEMAS,

    placement=PlacementDef(
        display_name="WMS Ops Box",
        value_stream="Software Delivery",
        role="WMS Operations Fulfillment",
        domain="WMS",
        feeds_into=['wms_system_box'],
    ),

    task=[
        TaskDef(
            type="handle_ops_request",
            task_schema="schema_ops_task_input",
            beeline_id="beeline_wms_ops_v1",
            beeline_version=1,
            trigger=(
                "要发版走变更审批\nOR 生产告警要处置\nOR 要回滚上一版\nOR 线上拓扑变了要回写系统档案"
            ),
        ),
    ],

    result=ResultDef(
        type="wms_operated",
        result_schema="schema_ops_record",
        maturity="skeleton",
        acceptance=[
            AcceptanceRule(
                metric="change_approval_rate", op="eq", value=True,
            ),
            AcceptanceRule(
                metric="rollback_readiness", op="eq", value=True,
            ),
            AcceptanceRule(
                metric="incident_documented", op="eq", value=True,
            ),
            AcceptanceRule(
                metric="runtime_state_recorded", op="eq", value=True,
            ),
        ],
        exceptions=[
            ExceptionRule(
                condition="rollback_ready == false", action="no_deliver",
            ),
            ExceptionRule(
                condition="incident_count > 3", action="escalate",
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
                    {"trigger": "rollback_ready == false", "action": "escalate_to_human"},
                    {"trigger": "incident_count > 3", "action": "escalate_to_human"},
                ],
            },
            continuous_improvement={},
        ),
    ),

    # 骨架盒不声明指标：没有产出就没有可测的量。
    metrics=MetricsDef(),
)


def get_definition() -> BeeBoxDefinition:
    return WMS_OPS_BOX
