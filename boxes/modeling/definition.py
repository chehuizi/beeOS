"""
boxes.modeling.definition - 业务建模履约盒 Definition 数据

Software Line 第一只盒子定义。

按 Fulfillment 5 维度 + 度量组织：
- 基础元信息
- 数据形状（schemas）
- 履约意图 Intent（task）
- 履约合同 Contract（result + 5 条机械 acceptance + 3 条 exception）
- 履约政策 Policy（queen authorization + rules）
- 履约度量 Metrics（每个指标都有实测采集算法，见 measure.py）

设计哲学：
- acceptance 全部机械可验（5 条规则，0 条主观）
- exception 覆盖典型失败模式（需求超量 / 规则矛盾 / 无业务方）
- queen 配置保守（continuous_improvement = observe_only）
"""

from __future__ import annotations

from core.models import (
    AcceptanceRule,
    BeeBoxDefinition,
    ExceptionRule,
    MetricDef,
    MetricsDef,
    PlacementDef,
    QueenAuthorization,
    QueenDef,
    QueenRules,
    ResultDef,
    TaskDef,
)
from boxes.modeling.schemas import ALL_SCHEMAS


WMS_DESIGN_BOX = BeeBoxDefinition(
    id="wms_design_box",
    version=2,
    description=(
        "WMS 仓储建模履约盒——按结构化仓储需求产出可机械验证的 WMS 模型包："
        "WMS 领域实体（SKU / 货主 / 库位 / 库存 / 批次 / 波次 / 盘点）、"
        "WMS 业务约束（库位占用唯一 / 先进先出 / 安全库存 / 波次合并…）、"
        "WMS 运营指标，以及 7 项验证证据。\n\n"
        "**7 条判据里有 2 条是 WMS 专属的**（wms_binding / wms_rule_binding）："
        "前 5 条验「模型自洽」，这两条验「**这是不是一个 WMS 模型**」。"
        "少了它们，一个纯通用的「当库存 > 0 时…」照样五分之五全绿，"
        "而盒子挂着 WMS 的招牌。\n\n"
        "**已知盲区（如实声明）**：WMS 绑定是关键词机械匹配，认不出同义改写"
        "（「拣货」/「按订单取货」/「order picking」认作三个词）。"
        "认不出的需求会被 wms_binding 判红并点名，不会静默放过——"
        "宁可红，不要假装绑上了。"
    ),

    schemas=ALL_SCHEMAS,

    placement=PlacementDef(
        display_name="WMS Design Box",
        value_stream="Software Delivery",
        role="WMS Design Fulfillment",
        # 需求 → 设计 → 开发 是生命周期主链；设计还直接投给系统档案盒，
        # 因为「系统由哪些服务组成」这个问题，最早由设计给出答案。
        domain="WMS",
        feeds_into=["wms_development_box", "wms_system_box"],
    ),

    task=[
        TaskDef(
            type="handle_modeling_request",
            task_schema="schema_business_requirement_set",
            beeline_id="beeline_wms_design_v1",
            beeline_version=1,
            # 本次履约处理的业务对象标识（归档按它分组）
            subject_ref_field="set_id",
            trigger=(
                "仓储主管提交结构化 WMS 需求集（上架 / 拣货 / 盘点 / 波次合并）\n"
                "OR 货主要求重新出 WMS 模型\n"
                "OR 已有 WMS 模型要扩展新需求\n"
                "OR 仓库换库位策略要重算模型"
            ),
        ),
    ],

    result=ResultDef(
        type="business_model_produced",
        result_schema="schema_business_model_package",
        view="ddd_model",
        acceptance=[
            AcceptanceRule(
                metric="requirement_coverage",
                op="gte",
                value=100,
            ),
            AcceptanceRule(
                metric="rule_consistency",
                op="eq",
                value=True,
            ),
            AcceptanceRule(
                metric="reference_integrity",
                op="eq",
                value=True,
            ),
            AcceptanceRule(
                metric="structural_compliance",
                op="eq",
                value=True,
            ),
            AcceptanceRule(
                metric="metrics_defined",
                op="eq",
                value=True,
            ),
            # ---- WMS 域专属 ----
            #
            # 这两条验的不是「模型自洽」，是「**这是不是一个 WMS 模型**」。
            # 口径：分母是可建模需求，**要求 100% 落到 WMS 概念上**。
            #
            # 为什么卡 100 而不是「≥80% 算过」：这个盒子只服务 WMS 域，
            # 混进来的非 WMS 需求说明**投料错了**，不是「质量差一点」。
            # 判红的证据里会点名是哪几条（requirements_without_wms_binding），
            # 踢出去就行——比悄悄放过一条「用户登录要密码」强。
            AcceptanceRule(
                metric="wms_binding",
                op="eq",
                value=True,
            ),
            AcceptanceRule(
                metric="wms_rule_binding",
                op="eq",
                value=True,
            ),
        ],
        exceptions=[
            ExceptionRule(
                condition="requirement_count > 1000",
                action="escalate",
            ),
            ExceptionRule(
                condition="rule_consistency == false",
                action="rollback",
            ),
            ExceptionRule(
                condition="business_owner_assigned == false",
                action="no_deliver",
            ),
            # 投料里有非 WMS 需求：不是「建模失败」，是**投料错了**。
            # 送回去让业务方重新给需求，别拿通用需求硬凑一个 WMS 模型。
            ExceptionRule(
                condition="requirements_without_wms_binding > 0",
                # action 只有 escalate / rollback / no_deliver 三个值，
                # 没有「退回去改投料」。投料不对就是 escalate——送人。
                action="escalate",
            ),
        ],
    ),

    queen=QueenDef(
        authorization=QueenAuthorization(
            task_routing="allow",
            exception_handling="allow",
            continuous_improvement="observe_only",  # 改善只观察
        ),
        rules=QueenRules(
            task_routing={
                "complexity_thresholds": {
                    "small": 50,    # < 50 需求直接建模
                    "medium": 200,  # 50-200 启用模板
                    "large": 1000,  # 200-1000 启用协作建模
                    # > 1000 触发 escalate
                },
            },
            exception_handling={
                "rule_conflict_resolution": "flag_for_human_review",
                "missing_trace_target": "auto_infer_from_description",
                "ambiguous_classification": "assign_to_most_likely_type",
                "escalation_rules": [
                    {"trigger": "requirement_count > 1000", "action": "escalate_to_human"},
                    {"trigger": "rule_conflicts > 5", "action": "escalate_to_human"},
                ],
            },
            continuous_improvement={
                "monitor_metrics": [
                    "first_pass_acceptance_rate",
                    "requirement_coverage_quality",
                    "model_reuse_rate",
                    "modeling_turnaround_time",
                ],
            },
        ),
    ),

    metrics=MetricsDef(
        quality=[
            MetricDef(
                name="first_pass_acceptance_rate",
                definition="首次建模即通过 acceptance 的比例",
                target=90.0,
                unit="pct",
            ),
            MetricDef(
                name="requirement_coverage_quality",
                definition="requirement_coverage == 100 的比例",
                target=95.0,
                unit="pct",
            ),
        ],
        latency=[
            MetricDef(
                name="modeling_turnaround_time",
                # 如实标注：这个盒子的 beeline 目前走 runtime/mock_runner，
                # 测到的是 mock 执行耗时，不是真实建模耗时。
                # 不这么写，看板上会显示一个 0.0s 的「建模时长」，读起来像建模极快。
                definition="从需求提交到模型包产出时长（当前建模腿走 mock runner，"
                           "此值是 mock 执行耗时，不等于真实建模耗时）",
                target=3600.0,  # 1 hour
                unit="s",
                direction="lower_is_better",
            ),
            MetricDef(
                name="p95_modeling_turnaround_time",
                definition="95 分位建模时长（同上，mock 执行耗时）",
                target=7200.0,  # 2 hour
                unit="s",
                direction="lower_is_better",
            ),
        ],
        # cost 暂时空着：没有成本记账就不声明成本指标，
        # 等于在看板上挂一个永远读不出数的槽。记账做起来再加回来。
    ),
)


def get_definition() -> BeeBoxDefinition:
    """返回业务建模履约盒 Definition"""
    return WMS_DESIGN_BOX