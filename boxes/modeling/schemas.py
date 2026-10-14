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

from boxes.modeling.domain import (
    WMS_CONSTRAINT_KINDS,
    WMS_ENTITY_KINDS,
    WMS_METRIC_KEYWORDS,
)

WMS_METRIC_KINDS: tuple[str, ...] = tuple(WMS_METRIC_KEYWORDS)
from core.models import FieldDef, SchemaDef


# ============================================================
# 业务枚举取值表（投料契约校验用——声明即约束）
# ============================================================

REQUIREMENT_TYPES = ("object", "rule", "process", "metric", "goal", "event")
REQUIREMENT_PRIORITIES = ("must_have", "should_have", "could_have")

# DDD 模型元素的五种 pattern
MODEL_PATTERNS = ("entity", "specification", "domain_service", "domain_event", "domain_metric")


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
            type="enum",
            values=list(REQUIREMENT_TYPES),
        ),
        FieldDef(
            name="priority",
            type="enum",
            values=list(REQUIREMENT_PRIORITIES),
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
                    FieldDef(name="requirement_type", type="enum", values=list(REQUIREMENT_TYPES)),
                    FieldDef(name="priority", type="enum", values=list(REQUIREMENT_PRIORITIES)),
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
    description="解析后的需求（结构验证 + 透传有效需求载荷）",
    fields=[
        FieldDef(name="set_id", type="string"),
        FieldDef(name="business_goal", type="string"),
        FieldDef(name="requirement_count", type="integer"),
        FieldDef(name="valid_ids", type="array", items=FieldDef(name="id", type="string")),
        FieldDef(
            name="requirements",
            type="array",
            items=FieldDef(
                name="requirement",
                type="object",
                properties=[
                    FieldDef(name="requirement_id", type="string"),
                    FieldDef(name="description", type="string"),
                    FieldDef(name="requirement_type", type="enum", values=list(REQUIREMENT_TYPES)),
                    FieldDef(name="priority", type="enum", values=list(REQUIREMENT_PRIORITIES)),
                ],
            ),
        ),
        FieldDef(name="parse_errors", type="array", items=FieldDef(name="err", type="string")),
    ],
)


CLASSIFIED_REQUIREMENTS = SchemaDef(
    id="schema_classified_requirements",
    description="分类后的需求（按声明的 requirement_type 分组）",
    fields=[
        FieldDef(name="set_id", type="string"),
        FieldDef(name="business_goal", type="string"),
        FieldDef(name="requirement_count", type="integer"),
        FieldDef(name="valid_ids", type="array", items=FieldDef(name="id", type="string")),
        FieldDef(
            name="modelable_ids",
            type="array",
            items=FieldDef(name="id", type="string"),
        ),  # 可建模 5 类需求 id（覆盖率分母；goal 不计入）
        FieldDef(
            name="by_type",
            type="object",
            properties=[
                # 6 类全列：runtime/classify 按 REQUIREMENT_TYPES 建全部 key，
                # schema 少列一类就成了"声明不管约束"，补不上契约就名存实亡。
                FieldDef(name="object", type="array", items=FieldDef(name="r", type="string")),
                FieldDef(name="rule", type="array", items=FieldDef(name="r", type="string")),
                FieldDef(name="process", type="array", items=FieldDef(name="r", type="string")),
                FieldDef(name="metric", type="array", items=FieldDef(name="r", type="string")),
                FieldDef(name="event", type="array", items=FieldDef(name="r", type="string")),
                FieldDef(name="goal", type="array", items=FieldDef(name="r", type="string")),
            ],
        ),
    ],
)


MODEL_ELEMENTS = SchemaDef(
    id="schema_model_elements",
    description="生成的模型元素（DDD 战术设计：实体 / 规格 / 领域服务 / 领域事件 / 领域指标）",
    fields=[
        FieldDef(name="set_id", type="string"),
        FieldDef(name="business_goal", type="string"),
        FieldDef(name="requirement_count", type="integer"),
        FieldDef(name="valid_ids", type="array", items=FieldDef(name="id", type="string")),
        FieldDef(
            name="modelable_ids",
            type="array",
            items=FieldDef(name="id", type="string"),
        ),
        FieldDef(
            name="entities",
            type="array",
            items=FieldDef(
                name="entity",
                type="object",
                properties=[
                    FieldDef(name="entity_id", type="string"),
                    FieldDef(name="name", type="string"),
                    FieldDef(name="pattern", type="enum", values=list(MODEL_PATTERNS)),  # entity
                    # **这个盒子是 WMS 建模盒**：实体必须说得出自己是 WMS 的
                    # 哪个概念。说不出（null）就不算 WMS 模型的一分子。
                    FieldDef(
                        name="wms_kind",
                        type="enum",
                        values=list(WMS_ENTITY_KINDS) + [None],
                        required=False,
                        description="WMS 领域实体类型；认不出填 null（会被 wms_binding 判红）",
                    ),
                    FieldDef(name="trace_to", type="string"),  # 关联 requirement_id
                ],
            ),
        ),
        FieldDef(
            name="events",
            type="array",
            items=FieldDef(
                name="event",
                type="object",
                properties=[
                    FieldDef(name="event_id", type="string"),
                    FieldDef(name="name", type="string"),
                    FieldDef(name="pattern", type="enum", values=list(MODEL_PATTERNS)),  # domain_event
                    FieldDef(
                        name="wms_kind",
                        type="enum",
                        values=list(WMS_ENTITY_KINDS) + [None],
                        required=False,
                        description="这个事件在 WMS 里是哪个概念的变动；认不出填 null",
                    ),
                    FieldDef(name="trace_to", type="string"),
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
                    FieldDef(name="name", type="string"),
                    FieldDef(name="condition", type="string"),
                    FieldDef(name="action", type="string"),
                    FieldDef(name="pattern", type="enum", values=list(MODEL_PATTERNS)),  # specification
                    # 「当库存 > 0 时…」是通用规则；「同一 SKU 同库位不可重复
                    # 占用」才是 WMS 规则。声明了约束的才算数——这条差别
                    # wms_rule_binding 判据能数得出来。
                    FieldDef(
                        name="wms_constraint",
                        type="enum",
                        values=list(WMS_CONSTRAINT_KINDS) + [None],
                        required=False,
                        description="约束的 WMS 业务类型；不是 WMS 约束填 null",
                    ),
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
                    FieldDef(name="pattern", type="enum", values=list(MODEL_PATTERNS)),  # domain_service
                    FieldDef(
                        name="wms_kind",
                        type="enum",
                        values=list(WMS_ENTITY_KINDS) + [None],
                        required=False,
                        description="这条流程动的 WMS 概念；不是 WMS 流程填 null",
                    ),
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
                    FieldDef(name="pattern", type="enum", values=list(MODEL_PATTERNS)),  # domain_metric（扩展元素）
                    # 指标型需求也得能声明它是哪个 WMS 指标——
                    # 「拣货准确率 99.5%」是条 metric 类型的需求，
                    # 只因为类型不是 object 就判它「没绑上 WMS」是判据在犯病
                    FieldDef(
                        name="wms_kind",
                        type="enum",
                        values=list(WMS_METRIC_KINDS),
                        required=False,
                        description="WMS 指标名；不是 WMS 指标填 null",
                    ),
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
        FieldDef(name="set_id", type="string"),
        FieldDef(name="business_goal", type="string"),
        FieldDef(name="requirement_count", type="integer"),
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
                # WMS 判据的明细：不点名字，读者只知道「红了」，不知道红在哪
                FieldDef(
                    name="requirements_without_wms_binding",
                    type="array",
                    items=FieldDef(name="r", type="string"),
                ),
                FieldDef(
                    name="unbound_requirements",
                    type="array",
                    items=FieldDef(name="r", type="string")),
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
        # ---- 5 项 acceptance 判据（全部机械可验）----
        # 这五个字段以前没声明，但 beeline 真的产出它们，acceptance 也真的读它们。
        # 捕获盒早就是把判据写进产��� schema 的（见 schema_requirement_set_package）：
        # 判据值不是「顺便算出来的副产品」，它属于产出契约的一部分——
        # 不写进来，按 schema 生成的 API 消费方就永远拿不到判据字段，
        # 而验收结论也只能靠猜。
        FieldDef(name="requirement_coverage", type="number"),
        FieldDef(name="rule_consistency", type="boolean"),
        FieldDef(name="reference_integrity", type="boolean"),
        FieldDef(name="structural_compliance", type="boolean"),
        FieldDef(name="metrics_defined", type="boolean"),
        # ---- WMS 域专属的 2 条判据 ----
        # 前 5 条验的是「模型自洽」——需求覆盖率、引用完整性、结构齐不齐。
        # 这两条验的是「**这是不是一个 WMS 模型**」：需求有没有落到 WMS
        # 概念上，规则有没有约束 WMS 业务。少了这两条，一个纯通用的
        # 「当库存 > 0 时……」模型照样全绿，而盒子挂着 WMS 的招牌。
        FieldDef(name="wms_binding", type="boolean"),
        FieldDef(name="wms_rule_binding", type="boolean"),
        # WMS 域摘要：模型里出现了哪些 WMS 概念、各自绑到哪些需求。
        # 不带出来的后果是「算了却看不见」——judge 判了红，读者看不到红在哪。
        FieldDef(
            name="wms_domain",
            type="object",
            properties=[
                FieldDef(name="domain", type="string"),
                FieldDef(
                    name="entities",
                    type="array",
                    items=FieldDef(
                        name="e",
                        type="object",
                        properties=[
                            FieldDef(name="kind", type="string"),
                            FieldDef(name="label", type="string"),
                            FieldDef(name="name", type="string"),
                            FieldDef(name="trace_to", type="string"),
                        ],
                    ),
                ),
                FieldDef(
                    name="constraints",
                    type="array",
                    items=FieldDef(
                        name="c",
                        type="object",
                        properties=[
                            FieldDef(name="kind", type="string"),
                            FieldDef(name="label", type="string"),
                            FieldDef(name="statement", type="string"),
                            FieldDef(name="trace_to", type="string"),
                        ],
                    ),
                ),
                FieldDef(
                    name="bound_requirement_count",
                    type="integer",
                    description="绑到至少一个 WMS 概念的需求数",
                ),
                FieldDef(
                    name="modelable_requirement_count",
                    type="integer",
                    description="可建模需求总数（分母）",
                ),
            ],
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