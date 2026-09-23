"""
beelines.modeling - 业务建模履约作业路线

Software Line 第一只 beeline。

业务流：
  input: BusinessRequirementSet
    ↓
  parse_requirements         （校验结构 + 提取有效 id）
    ↓
  classify_requirements      （按 type 分组：object / rule / process / metric / goal）
    ↓
  generate_model_elements    （按分类生成 entity / rule / process / metric）
    ↓
  verify_coverage            （检查 requirement_coverage）
    ↓
  check_consistency          （rule_consistency + reference_integrity + structural_compliance）
    ↓
  generate_evidence          （汇总 5 项 evidence）
    ↓
  package_model              （合并成 BusinessModelPackage）
    ↓
  （终态）

编排特征：
- 顺序链（无分支，每步必须完成才能进入下一步）
- 幂等性：所有 op = required（key=set_id，重复建模产出同模型）
"""

from __future__ import annotations

from core.beeline_models import (
    Bee,
    Beeline,
    Idempotency,
    NextRef,
    Operation,
)


IDEMPOTENT_BY_SET = Idempotency(mode="required", key="set_id")


BUSINESS_MODELING_BEELINE = Beeline(
    id="beeline_business_modeling_v1",
    version=1,
    description=(
        "业务建模履约作业路线——按结构化业务需求产出可验证的业务模型包"
    ),

    operations=[
        Operation(
            op_id="parse_requirements",
            type="requirement_parser",
            input_from="external",
            input="schema_business_requirement_set",
            output="schema_parsed_requirements",
            next=[NextRef(op_id="classify_requirements")],
            bee=Bee(type="requirement_parser_impl"),
            idempotency=IDEMPOTENT_BY_SET,
        ),

        Operation(
            op_id="classify_requirements",
            type="requirement_classifier",
            input_from="parse_requirements",
            input="schema_parsed_requirements",
            output="schema_classified_requirements",
            next=[NextRef(op_id="generate_model_elements")],
            bee=Bee(type="requirement_classifier_impl"),
            idempotency=IDEMPOTENT_BY_SET,
        ),

        Operation(
            op_id="generate_model_elements",
            type="model_generator",
            input_from="classify_requirements",
            input="schema_classified_requirements",
            output="schema_model_elements",
            next=[NextRef(op_id="verify_coverage")],
            bee=Bee(type="model_generator_impl"),
            idempotency=IDEMPOTENT_BY_SET,
        ),

        Operation(
            op_id="verify_coverage",
            type="coverage_verifier",
            input_from="generate_model_elements",
            input="schema_model_elements",
            output="schema_model_elements",
            next=[NextRef(op_id="check_consistency")],
            bee=Bee(type="coverage_verifier_impl"),
            idempotency=IDEMPOTENT_BY_SET,
        ),

        Operation(
            op_id="check_consistency",
            type="consistency_checker",
            input_from="generate_model_elements",
            input="schema_model_elements",
            output="schema_model_elements",
            next=[NextRef(op_id="generate_evidence")],
            bee=Bee(type="consistency_checker_impl"),
            idempotency=IDEMPOTENT_BY_SET,
        ),

        Operation(
            op_id="generate_evidence",
            type="evidence_generator",
            input_from="check_consistency",
            input="schema_model_elements",
            output="schema_model_evidence",
            next=[NextRef(op_id="package_model")],
            bee=Bee(type="evidence_generator_impl"),
            idempotency=IDEMPOTENT_BY_SET,
        ),

        Operation(
            op_id="package_model",
            type="model_packager",
            input_from="generate_evidence",
            input="schema_model_evidence",
            output="schema_business_model_package",
            next=[],
            bee=Bee(type="model_packager_impl"),
            idempotency=IDEMPOTENT_BY_SET,
        ),
    ],
)


def get_beeline() -> Beeline:
    """返回业务建模履约 beeline"""
    return BUSINESS_MODELING_BEELINE