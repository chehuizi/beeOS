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
    return BUSINESS_MODELING_BOX