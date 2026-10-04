"""
boxes.requirement_capture.schemas - 需求捕获盒的业务 schemas

为什么有这只盒子：
- 建模盒的 task in 是**已结构化**的 BusinessRequirementSet，它不做自然语言理解
- 自然语言 → 结构化这一步原先是 kanban/trigger.py 里的一个影子函数：
  没有 task_run、没有落盘、失败是静默的
- 它的验收判据跟建模盒**零重叠**（判据不同 = 值得独立成盒）

定义 3 个业务 schema：
  - schema_business_narrative —— 自然语言业务表述（任务输入）
  - schema_extracted_facts —— 抽取出的业务事实 + 分类结果（中间产物）
  - schema_requirement_set_package —— 需求集包（最终结果 + 4 项验证证据）

设计要点：
- 每条业务需求带 trace_to（指向原文句子 id）→ 可机械验"是否幻觉"
- 原文句子带 sentence_id → 可机械验"是否每句都被覆盖"
- acceptance 4 条全部机械可验，0 条主观
"""

from __future__ import annotations

from core.models import FieldDef, SchemaDef


# ============================================================
# 业务枚举取值表（投料契约校验用）
# ============================================================

REQUIREMENT_TYPES = ("object", "rule", "process", "metric", "goal", "event")
REQUIREMENT_PRIORITIES = ("must_have", "should_have", "could_have")

EXTRACTORS = ("llm", "rule")   # 需求抽取的执行者：模型 / 规则降级


# ============================================================
# 输入层：自然语言业务表述
# ============================================================

BUSINESS_NARRATIVE = SchemaDef(
    id="schema_business_narrative",
    description="自然语言业务表述（业务方原话）",
    fields=[
        FieldDef(name="narrative", type="string"),
        FieldDef(name="set_id", type="string", required=False),
        FieldDef(name="submitted_by", type="string", required=False),
    ],
)


# ============================================================
# 处理层：抽取出的业务事实
# ============================================================

_EXTRACTED_REQUIREMENT = FieldDef(
    name="requirement",
    type="object",
    properties=[
        FieldDef(name="requirement_id", type="string"),
        FieldDef(name="description", type="string"),
        FieldDef(name="requirement_type", type="enum", values=list(REQUIREMENT_TYPES)),
        FieldDef(name="priority", type="enum", values=list(REQUIREMENT_PRIORITIES)),
        # 溯源：指向原文句子的 sentence_id —— no_hallucination 靠它
        FieldDef(name="trace_to", type="string"),
    ],
)

_SOURCE_SENTENCE = FieldDef(
    name="sentence",
    type="object",
    properties=[
        FieldDef(name="sentence_id", type="string"),
        FieldDef(name="text", type="string"),
        FieldDef(name="role", type="enum", values=["goal", "fact"]),
    ],
)

EXTRACTED_FACTS = SchemaDef(
    id="schema_extracted_facts",
    description="抽取出的业务事实（含原文溯源）",
    fields=[
        FieldDef(name="set_id", type="string"),
        FieldDef(name="business_goal", type="string"),
        FieldDef(name="extractor", type="enum", values=list(EXTRACTORS)),
        FieldDef(name="source_sentences", type="array", items=_SOURCE_SENTENCE),
        FieldDef(name="requirements", type="array", items=_EXTRACTED_REQUIREMENT),
        FieldDef(name="requirement_count", type="integer"),
    ],
)


# ============================================================
# 输出层：需求集包
# ============================================================

REQUIREMENT_SET_PACKAGE = SchemaDef(
    id="schema_requirement_set_package",
    description="需求集包（建模盒的输入 + 4 项抽取验证证据）",
    fields=[
        FieldDef(name="set_id", type="string"),
        FieldDef(name="business_goal", type="string"),
        FieldDef(name="context", type="string", required=False),
        FieldDef(name="extractor", type="enum", values=list(EXTRACTORS)),
        FieldDef(name="source_sentences", type="array", items=_SOURCE_SENTENCE),
        FieldDef(name="requirements", type="array", items=_EXTRACTED_REQUIREMENT),
        FieldDef(name="requirement_count", type="integer"),
        # ---- 4 项 acceptance 判据（全部机械可验）----
        FieldDef(name="contract_compliance", type="boolean"),
        FieldDef(name="source_coverage", type="number"),
        FieldDef(name="trace_integrity", type="boolean"),
        FieldDef(name="no_hallucination", type="boolean"),
        # ---- 证据细节 ----
        FieldDef(name="evidence_detail", type="object", properties=[
            FieldDef(name="uncovered_sentences", type="array",
                     items=FieldDef(name="s", type="string")),
            FieldDef(name="broken_traces", type="array",
                     items=FieldDef(name="b", type="string")),
            FieldDef(name="hallucinated", type="array",
                     items=FieldDef(name="h", type="string")),
            FieldDef(name="degrade_reason", type="string", required=False),
        ]),
    ],
)


ALL_SCHEMAS = [
    BUSINESS_NARRATIVE,
    EXTRACTED_FACTS,
    REQUIREMENT_SET_PACKAGE,
]
