"""
boxes.modeling.definition - 业务建模履约盒 Definition 数据

Software Line 第一只盒子定义。

按 Fulfillment 5 维度 + 度量组织：
- 基础元信息
- 数据形状（schemas）
- 履约意图 Intent（task）
- 履约合同 Contract（result + 5 条机械 acceptance + 3 条 exception）
- 履约政策 Policy（queen authorization + rules）
- 履约度量 Metrics

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
    QueenAuthorization,
    QueenDef,
    QueenRules,
    ResultDef,
    TaskDef,
)
from boxes.modeling.schemas import ALL_SCHEMAS


BUSINESS_MODELING_BOX = BeeBoxDefinition(
    id="business_modeling_box",
    version=1,
    description=(
        "业务建模履约盒——按结构化业务需求产出可机械验证的业务模型包，"
        "包含模型元素（实体 / 规则 / 流程 / 指标）和 5 项验证证据"
    ),

    schemas=ALL_SCHEMAS,

    task=[
        TaskDef(
            type="handle_modeling_request",
            task_schema="schema_business_requirement_set",
            beeline_id="beeline_business_modeling_v1",
            beeline_version=1,
            trigger=(
                "产品经理提交结构化业务需求集\n"
                "OR 业务方要求重新建模\n"
                "OR 已有模型需要扩展新需求"
            ),
        ),
    ],

    result=ResultDef(
        type="business_model_produced",
        result_schema="schema_business_model_package",
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
                target=0.90,
            ),
            MetricDef(
                name="requirement_coverage_quality",
                definition="requirement_coverage == 100 的比例",
                target=0.95,
            ),
            MetricDef(
                name="model_reuse_rate",
                definition="新需求复用已有 model 元素的比例",
                target=0.40,
            ),
        ],
        latency=[
            MetricDef(
                name="modeling_turnaround_time",
                definition="从需求提交到模型包产出时长",
                target=3600,  # 1 hour
            ),
            MetricDef(
                name="p95_modeling_turnaround_time",
                definition="95 分位建模时长",
                target=7200,  # 2 hour
            ),
        ],
        cost=[
            MetricDef(
                name="cost_per_modeling_request",
                definition="单次建模请求成本（含人工折算）",
                target=5.0,
            ),
            MetricDef(
                name="human_escalation_rate",
                definition="升级人工率",
                target=0.20,
            ),
        ],
    ),
)


def get_definition() -> BeeBoxDefinition:
    """返回业务建模履约盒 Definition"""
    return BUSINESS_MODELING_BOX