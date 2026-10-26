"""boxes.wms_ops.definition - WMS Ops Box Definition 数据

WMS 运维盒——管**上线之后**的事：变更审批、发布执行、故障处置、回滚能力、以及把线上真实状态回写进系统档案。

按 Fulfillment 5 维度 + 度量组织：
- 基础元信息 + placement（归属 / 接谁 / 投给谁 / 属于哪个域）
- 数据形状（schemas）
- 履约意图 Intent（task）
- 履约合同 Contract（result + 4 条机械 acceptance + 2 条 exception）
- 履约政策 Policy（queen）
- 履约度量 Metrics

**成熟度：live**。这条腿是真的：它接上上游（测试盒的验收报告 +
开发盒的制品），读**生产实例**（只读），把处置和现场状态记成一条运维记录。

**5 条判据的答案没有一条来自自述**：审批看上游验收报告、回滚看 git /
制品库、处置看告警条数对不对得上、线上状态看生产实例答不答话。

**「用不上」和「通过」分开**：没有变更就没有审批率、没有告警就没有处置
记录——那几条判据的取值是 "n/a"，看板单独列，不混进通过率。

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
        feeds_into=['wms_analysis_box', 'wms_system_box'],
    ),

    task=[
        TaskDef(
            type="handle_ops_request",
            task_schema="schema_ops_task_input",
            beeline_id="beeline_wms_ops_v1",
            beeline_version=1,
            # 本次履约处理的业务对象标识（归档按它分组）
            #
            # 运维盒的 subject 是**运维事件**（一次变更 / 一次故障处置），
            # 不是需求。它的 id 是自己给的（ops_task_id），跟需求号可能撞号
            # ——所以种类要显式声明，不能只靠字符串分。
            subject_ref_field="ops_task_id",
            subject_kind="ops_event",
            trigger=(
                "要发版走变更审批\nOR 生产告警要处置\nOR 要回滚上一版\nOR 线上拓扑变了要回写系统档案"
            ),
        ),
    ],

    result=ResultDef(
        type="wms_operated",
        result_schema="schema_ops_record",
        # **live**：这条腿是真的了——它接上上游、读生产现场、
        # 把处置和现场状态记成一条运维记录。
        #
        # 以前是 skeleton：产出是桩，刻意不产出判据要的 metric，
        # 让看板显式标「未实现」。现在腿在了，徽章就得摘掉——
        # **「未实现」和「实现了但不达标」必须分得开**。
        maturity="live",
        acceptance=[
            # 变更审批：验过 + 指名了那份构建（没有变更 → n/a）
            AcceptanceRule(
                metric="change_approval_rate", op="eq", value=True,
            ),
            # 回滚能力：目标取得到 + 知道现在跑的是哪一版（没有变更 → n/a）
            AcceptanceRule(
                metric="rollback_readiness", op="eq", value=True,
            ),
            # 故障处置：每条告警都有记录（没有告警 → n/a）
            AcceptanceRule(
                metric="incident_documented", op="eq", value=True,
            ),
            # 线上真实状态：真的读到了生产，并记下了观测时刻与内容
            AcceptanceRule(
                metric="runtime_state_recorded", op="eq", value=True,
            ),
            # **「这次真的干了事」**——
            # 没有它，一只什么都没做的盒子会显示成「几条 n/a + 一条通过」。
            AcceptanceRule(
                metric="ops_determined", op="eq", value=True,
            ),
        ],
        exceptions=[
            # 要放行变更却退不回去 → 不投递。
            # **加上 change_count > 0**：没有变更要放行时，
            # 「没给回滚目标」不该把一次纯故障处置判死。
            ExceptionRule(
                condition="rollback_ready == false AND change_count > 0",
                action="no_deliver",
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
