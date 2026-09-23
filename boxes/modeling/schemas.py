"""
boxes.modeling.schemas - 业务建模盒子的业务 schemas

定义 6 个业务 schema：

输入层：
  - schema_business_requirement_set —— 业务需求集（任务输入）
  - schema_business_requirement —— 单个业务需求

处理层：
  - schema_parsed_requirements —— 解析后的需求（parse_requirements 输出）
  - schema_classified_requirements —— 分类后的需求（classify_requirements 输出）
  - schema_model_elements —— 生成的模型元素（generate_model_elements 输出）

输出层：
  - schema_business_model_package —— 业务模型包（最终结果 + 证据）

设计要点：
- 输入结构化（需求带 id / type / 优先级）→ 验收可机械化
- 输出带 traceability（每个 model 元素记录 trace_to requirement_id）→ 覆盖率可验
- acceptance 字段全部机械可验（覆盖率 / 一致性 / 引用 / 结构 / 指标）
"""

from __future__ import annotations

from core.models import FieldDef, SchemaDef


# ============================================================
# 输入层：业务需求
# ============================================================


BUSINESS_REQUIREMENT = SchemaDef(
    id="schema_business_requirement",
    description="单个业务需求",
    fields=[
        FieldDef(name="requirement_id", type="string"),
        FieldDef(name="description", type="string"),
        FieldDef(
            name="requirement_type",
            type="enum",  # object / rule / process / metric / goal
        ),
        FieldDef(
            name="priority",
            type="enum",  # must_have / should_have / could_have
        ),
        FieldDef(name="traceable_to", type="string", required=False),  # 上游引用
    ],
)


BUSINESS_REQUIREMENT_SET = SchemaDef(
    id="schema_business_requirement_set",
    description="业务需求集（任务输入）",
    fields=[
        FieldDef(name="set_id", type="string"),
        FieldDef(name="business_goal", type="string"),
        FieldDef(name="context", type="string", required=False),
        FieldDef(
            name="requirements",
            type="array",
            items=FieldDef(
                name="requirement",
                type="object",
                properties=[
                    FieldDef(name="requirement_id", type="string"),
                    FieldDef(name="description", type="string"),
                    FieldDef(name="requirement_type", type="enum"),
                    FieldDef(name="priority", type="enum"),
                    FieldDef(name="traceable_to", type="string", required=False),
                ],
            ),
        ),
    ],
)


# ============================================================
# 处理层：中间产物
# ============================================================


PARSED_REQUIREMENTS = SchemaDef(
    id="schema_parsed_requirements",
    description="解析后的需求（结构验证 + id 校验）",
    fields=[
        FieldDef(name="set_id", type="string"),
        FieldDef(name="requirement_count", type="integer"),
        FieldDef(name="valid_ids", type="array", items=FieldDef(name="id", type="string")),
        FieldDef(name="parse_errors", type="array", items=FieldDef(name="err", type="string")),
    ],
)


CLASSIFIED_REQUIREMENTS = SchemaDef(
    id="schema_classified_requirements",
    description="分类后的需求（按 type 分组）",
    fields=[
        FieldDef(name="set_id", type="string"),
        FieldDef(
            name="by_type",
            type="object",
            properties=[
                FieldDef(name="object", type="array", items=FieldDef(name="r", type="string")),
                FieldDef(name="rule", type="array", items=FieldDef(name="r", type="string")),
                FieldDef(name="process", type="array", items=FieldDef(name="r", type="string")),
                FieldDef(name="metric", type="array", items=FieldDef(name="r", type="string")),
                FieldDef(name="goal", type="array", items=FieldDef(name="r", type="string")),
            ],
        ),
    ],
)


MODEL_ELEMENTS = SchemaDef(
    id="schema_model_elements",
    description="生成的模型元素（实体 / 规则 / 流程 / 指标）",
    fields=[
        FieldDef(name="set_id", type="string"),
        FieldDef(
            name="entities",
            type="array",
            items=FieldDef(
                name="entity",
                type="object",
                properties=[
                    FieldDef(name="entity_id", type="string"),
                    FieldDef(name="name", type="string"),
                    FieldDef(name="trace_to", type="string"),  # 关联 requirement_id
                ],
            ),
        ),
        FieldDef(
            name="rules",
            type="array",
            items=FieldDef(
                name="rule",
                type="object",
                properties=[
                    FieldDef(name="rule_id", type="string"),
                    FieldDef(name="condition", type="string"),
                    FieldDef(name="action", type="string"),
                    FieldDef(name="trace_to", type="string"),
                ],
            ),
        ),
        FieldDef(
            name="processes",
            type="array",
            items=FieldDef(
                name="process",
                type="object",
                properties=[
                    FieldDef(name="process_id", type="string"),
                    FieldDef(name="name", type="string"),
                    FieldDef(name="trace_to", type="string"),
                ],
            ),
        ),
        FieldDef(
            name="metrics",
            type="array",
            items=FieldDef(
                name="metric",
                type="object",
                properties=[
                    FieldDef(name="metric_id", type="string"),
                    FieldDef(name="name", type="string"),
                    FieldDef(name="target", type="number"),
                    FieldDef(name="trace_to", type="string"),
                ],
            ),
        ),
    ],
)


# ============================================================
# 输出层：业务模型包（含证据）
# ============================================================


MODEL_EVIDENCE = SchemaDef(
    id="schema_model_evidence",
    description="模型验证证据（机械可验 5 项）",
    fields=[
        FieldDef(name="requirement_coverage", type="number"),  # 0-100
        FieldDef(name="rule_consistency", type="boolean"),
        FieldDef(name="reference_integrity", type="boolean"),
        FieldDef(name="structural_compliance", type="boolean"),
        FieldDef(name="metrics_defined", type="boolean"),
        FieldDef(
            name="evidence_detail",
            type="object",
            properties=[
                FieldDef(name="uncovered_requirements", type="array", items=FieldDef(name="r", type="string")),
                FieldDef(name="rule_conflicts", type="array", items=FieldDef(name="c", type="string")),
                FieldDef(name="broken_references", type="array", items=FieldDef(name="b", type="string")),
                FieldDef(name="structure_violations", type="array", items=FieldDef(name="v", type="string")),
                FieldDef(name="metrics_without_target", type="array", items=FieldDef(name="m", type="string")),
            ],
        ),
    ],
)


BUSINESS_MODEL_PACKAGE = SchemaDef(
    id="schema_business_model_package",
    description="业务模型包（最终交付物）",
    fields=[
        FieldDef(name="package_id", type="string"),
        FieldDef(name="set_id", type="string"),
        FieldDef(name="business_goal", type="string"),
        FieldDef(
            name="model_elements",
            type="ref:schema_model_elements",
        ),
        FieldDef(
            name="evidence",
            type="ref:schema_model_evidence",
        ),
    ],
)


ALL_SCHEMAS = [
    BUSINESS_REQUIREMENT_SET,
    BUSINESS_REQUIREMENT,
    PARSED_REQUIREMENTS,
    CLASSIFIED_REQUIREMENTS,
    MODEL_ELEMENTS,
    MODEL_EVIDENCE,
    BUSINESS_MODEL_PACKAGE,
]