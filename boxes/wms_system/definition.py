"""boxes.wms_system.definition - WMS System Box Definition 数据

WMS 系统描述盒——**描述真实在跑的那套 WMS**：它有哪些服务、对外暴露什么接口、每个组件部署在什么版本、依赖哪些外部系统。

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
from boxes.wms_system.schemas import ALL_SCHEMAS

WMS_SYSTEM_BOX = BeeBoxDefinition(
    id="wms_system_box",
    version=1,
    description=(
        "WMS 系统描述盒——**描述真实在跑的那套 WMS**：它有哪些服务、对外暴露什么接口、每个组件部署在什么版本、依赖哪些外部系统。\n\n它是 WMS 域的中心：需求、设计、开发、测试、运维五个生命周期盒子产出的东西，最后都要落进这只盒子的系统档案里。它装的**不是**运行中的实例，是**关于那个实例的准确描述**——跟另五只盒子的关系是「都在讲同一个系统」。"
    ),

    schemas=ALL_SCHEMAS,

    placement=PlacementDef(
        display_name="WMS System Box",
        value_stream="Software Delivery",
        role="WMS System Description Fulfillment",
        domain="WMS",
        feeds_into=[],
    ),

    task=[
        TaskDef(
            type="handle_system_request",
            task_schema="schema_system_snapshot_input",
            beeline_id="beeline_wms_system_v1",
            beeline_version=1,
            trigger=(
                "发版后同步系统档案\nOR 新增/下线一个服务要更新接口清单\nOR 架构评审前拉一份当前部署拓扑\nOR 发现档案跟真实部署对不上"
            ),
        ),
    ],

    result=ResultDef(
        type="wms_system_described",
        result_schema="schema_system_profile",
        maturity="skeleton",
        acceptance=[
            AcceptanceRule(
                metric="component_completeness", op="eq", value=True,
            ),
            AcceptanceRule(
                metric="interface_compliance", op="eq", value=True,
            ),
            AcceptanceRule(
                metric="version_traceability", op="eq", value=True,
            ),
            AcceptanceRule(
                metric="version_drift_free", op="eq", value=True,
            ),
        ],
        exceptions=[
            ExceptionRule(
                condition="version_drift_count > 0", action="escalate",
            ),
            ExceptionRule(
                condition="component_count == 0", action="no_deliver",
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
                    {"trigger": "version_drift_count > 0", "action": "escalate_to_human"},
                    {"trigger": "component_count == 0", "action": "escalate_to_human"},
                ],
            },
            continuous_improvement={},
        ),
    ),

    # 骨架盒不声明指标：没有产出就没有可测的量。
    metrics=MetricsDef(),
)


def get_definition() -> BeeBoxDefinition:
    return WMS_SYSTEM_BOX
