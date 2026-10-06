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


# ============================================================
# 流程图结构（业务语义真的一层）
# ============================================================
# 为什么要从"打 type 标签的平铺列表"升级成图：
#
# 业务方描述一件事，说的是"流程图"——节点（做什么）+ 边（走到哪）+ 守卫（什么条件下）。
# 把它摊平成 requirements[] 再打一个 requirement_type 标签，丢的是**结构**：
#   「step3 保存入库单，允许入库数量和实入数量不一致」
# 原文里这是一个节点 + 一条守卫，绑在一起；摊平后变成两条并列需求，
# 谁也不知道那条约束挂在哪个动作上。
#
# 更要紧的是：type 一旦成了平铺列表上的标签，就成了"要模型猜、且猜不准"的东西
# （rule / metric / process 的边界实测仍会错），逼出人工下拉确认。
# 换成图之后类型由**位置**决定——在 edges 里就是守卫，在 nodes 里就是节点，
# 不需要猜，也不需要人判。
#
# 旧的 object/rule/process/metric/event 不是被删了，是各自落到图里的位置：
#   process → node          object → node.writes / node.reads
#   rule    → edge.guard    metric → node.measures
#   event   → edge.trigger  goal   → business_goal（图的名字，不是元素）

_FLOW_NODE = FieldDef(
    name="node",
    type="object",
    properties=[
        FieldDef(name="node_id", type="string"),
        # action 必须是原文片段（no_hallucination 靠它）
        FieldDef(name="action", type="string"),
        # 这个动作产出 / 消耗的实体（原 object 语义落到这里）
        FieldDef(name="writes", type="array", items=FieldDef(name="w", type="string"), required=False),
        FieldDef(name="reads", type="array", items=FieldDef(name="r", type="string"), required=False),
        # 这个动作要量的指标（原 metric 语义落到这里）
        FieldDef(name="measures", type="array", items=FieldDef(name="m", type="string"), required=False),
        # 图只有一个节点时的兜底：无处可挂的前置约束
        FieldDef(name="guard", type="string", required=False),
        FieldDef(name="trace_to", type="string"),
    ],
)

_FLOW_EDGE = FieldDef(
    name="edge",
    type="object",
    properties=[
        FieldDef(name="edge_id", type="string"),
        FieldDef(name="from", type="string"),
        FieldDef(name="to", type="string"),
        # 转移条件（原 rule / event 语义落到这里）—— 没有就是无条件直连
        FieldDef(name="guard", type="string", required=False),
        FieldDef(name="trigger", type="string", required=False),
        FieldDef(name="trace_to", type="string", required=False),
    ],
)

FLOW_GRAPH = SchemaDef(
    id="schema_flow_graph",
    description="流程图：节点 + 边 + 守卫（业务表述的真实形状）",
    fields=[
        FieldDef(name="goal", type="string"),
        FieldDef(name="nodes", type="array", items=_FLOW_NODE),
        FieldDef(name="edges", type="array", items=_FLOW_EDGE),
    ],
)

EXTRACTED_FACTS = SchemaDef(
    id="schema_extracted_facts",
    description="抽取出的业务事实（流程图 + 兼容平铺列表 + 原文溯源）",
    fields=[
        FieldDef(name="set_id", type="string"),
        FieldDef(name="business_goal", type="string"),
        FieldDef(name="extractor", type="enum", values=list(EXTRACTORS)),
        FieldDef(name="source_sentences", type="array", items=_SOURCE_SENTENCE),
        # ---- 流程图（主结构）----
        FieldDef(name="nodes", type="array", items=_FLOW_NODE),
        FieldDef(name="edges", type="array", items=_FLOW_EDGE),
        # ---- 兼容层：给还没升级的建模盒（它仍按 requirement_type 分组）----
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
        # ---- 流程图（主结构）：先看图，平铺列表只是它的兼容投影 ----
        FieldDef(name="nodes", type="array", items=_FLOW_NODE),
        FieldDef(name="edges", type="array", items=_FLOW_EDGE),
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
    FLOW_GRAPH,
    EXTRACTED_FACTS,
    REQUIREMENT_SET_PACKAGE,
]
