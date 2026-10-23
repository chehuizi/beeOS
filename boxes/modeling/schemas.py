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

from boxes.modeling.domain import (
    WMS_CONSTRAINT_KINDS,
    WMS_ENTITY_KINDS,
    WMS_METRIC_KEYWORDS,
    WMS_REPORT_KINDS,
)

WMS_METRIC_KINDS: tuple[str, ...] = tuple(WMS_METRIC_KEYWORDS)

# **全部 WMS 概念**（实体 / 约束 / 指标 / 报表）。
#
# 以前每个元素类型的 wms_kind 各自限死一类（实体只能填实体、规则只能填约束），
# 那是拿**位置**（需求在流程图里的位置）去限定**语义**（这句话讲的是哪个
# WMS 概念）。真 LLM 实测：业务方说「同一个 SKU 必须先出快过期的批次」，
# 需求盒把它放在节点上（位置=process），而设计盒正确地认出了 `fefo`——
# 位置标签不该否决语义判断，所以枚举放开成全量。
#
# 报表也算一类：业务方说「临期 30 天内 SKU 要能在库存看板看到」，
# 讲的是 `inventory_dashboard` 这个报表，不是实体也不是指标。
WMS_ALL_KINDS: tuple[str, ...] = (
    tuple(WMS_ENTITY_KINDS)
    + tuple(WMS_CONSTRAINT_KINDS)
    + tuple(WMS_METRIC_KINDS)
    + tuple(WMS_REPORT_KINDS)
)


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


# ============================================================
# 模型元素的形状（**一份定义，两处引用**）
# ============================================================
#
# 以前 `schema_model_elements`（op 产出）和模型包内嵌的 `model_elements`
# 是两个不同的形状，**而包声明的是前者、装的是后者**——
# 产出的包不满足它自己声明的 result schema，12 条契约错没人看见
# （`ref:` 以前不做深校验，所以谁都没发现）。
#
# 现在：元素 item 只定义一次，op 产出和模型包共用；包用 DDD 五组的名字
# （specifications / domain_services…），op 用流程图的名字（rules / processes…），
# **但 item 的形状是同一份**。

_WMS_BINDINGS = FieldDef(
    name="wms_bindings",
    type="array",
    required=False,
    items=FieldDef(
        name="binding",
        type="object",
        properties=[
            FieldDef(name="kind", type="string"),
            # 证据必须逐字来自需求描述——机械层校验过，这里是给人看的
            FieldDef(name="evidence", type="string"),
        ],
    ),
    description="这个元素绑到的 WMS 概念（每条带原文证据）；wms_kind 只是主项",
)

ENTITY_ITEM = FieldDef(
    name="entity",
    type="object",
    properties=[
        FieldDef(name="entity_id", type="string"),
        FieldDef(name="name", type="string"),
        FieldDef(name="pattern", type="enum", values=list(MODEL_PATTERNS)),  # entity
        # **这个盒子是 WMS 建模盒**：元素必须说得出自己是 WMS 的
        # 哪个概念。说不出（null）就不算 WMS 模型的一分子。
        FieldDef(
            name="wms_kind",
            type="enum",
            values=list(WMS_ALL_KINDS) + [None],
            required=False,
            description="WMS 领域概念类型；认不出填 null（会被 wms_binding 判红）",
        ),
        _WMS_BINDINGS,
        FieldDef(name="trace_to", type="string"),  # 关联 requirement_id
    ],
)

EVENT_ITEM = FieldDef(
    name="event",
    type="object",
    properties=[
        FieldDef(name="event_id", type="string"),
        FieldDef(name="name", type="string"),
        FieldDef(name="pattern", type="enum", values=list(MODEL_PATTERNS)),  # domain_event
        FieldDef(
            name="wms_kind",
            type="enum",
            values=list(WMS_ALL_KINDS) + [None],
            required=False,
            description="这个事件在 WMS 里是哪个概念的变动；认不出填 null",
        ),
        _WMS_BINDINGS,
        FieldDef(name="trace_to", type="string"),
    ],
)

RULE_ITEM = FieldDef(
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
        _WMS_BINDINGS,
        FieldDef(name="trace_to", type="string"),
    ],
)

PROCESS_ITEM = FieldDef(
    name="process",
    type="object",
    properties=[
        FieldDef(name="process_id", type="string"),
        FieldDef(name="name", type="string"),
        FieldDef(name="pattern", type="enum", values=list(MODEL_PATTERNS)),  # domain_service
        FieldDef(
            name="wms_kind",
            type="enum",
            values=list(WMS_ALL_KINDS) + [None],
            required=False,
            description="这条流程动的 WMS 概念；不是 WMS 流程填 null",
        ),
        _WMS_BINDINGS,
        FieldDef(name="trace_to", type="string"),
    ],
)

METRIC_ITEM = FieldDef(
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
            values=list(WMS_ALL_KINDS) + [None],
            required=False,
            description="WMS 概念名；不是 WMS 指标填 null",
        ),
        _WMS_BINDINGS,
        FieldDef(name="trace_to", type="string"),
    ],
)


MODEL_ELEMENTS = SchemaDef(
    id="schema_model_elements",
    description="生成的模型元素（op 产出：流程图视角的实体 / 规则 / 流程 / 指标 / 事件）",
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
        FieldDef(name="entities", type="array", items=ENTITY_ITEM),
        FieldDef(name="events", type="array", items=EVENT_ITEM),
        FieldDef(name="rules", type="array", items=RULE_ITEM),
        FieldDef(name="processes", type="array", items=PROCESS_ITEM),
        FieldDef(name="metrics", type="array", items=METRIC_ITEM),
        # 绑定作废的原因（自造概念 / 证据不是原文）——判了就要看得见
        FieldDef(
            name="binding_errors",
            type="array",
            required=False,
            items=FieldDef(
                name="binding_error",
                type="object",
                properties=[
                    FieldDef(name="requirement_id", type="string"),
                    FieldDef(name="reason", type="string"),
                ],
            ),
        ),
    ],
)


# 模型包内嵌的模型元素：**同一份 item 定义**，DDD 五组的名字
MODEL_PACKAGE_ELEMENTS = SchemaDef(
    id="schema_model_package_elements",
    description="模型包内嵌的模型元素（DDD 战术设计的五组：实体 / 规格 / 领域服务 / 领域事件 / 领域指标）",
    fields=[
        FieldDef(name="entities", type="array", items=ENTITY_ITEM),
        FieldDef(name="specifications", type="array", items=RULE_ITEM),
        FieldDef(name="domain_services", type="array", items=PROCESS_ITEM),
        FieldDef(name="domain_events", type="array", items=EVENT_ITEM),
        FieldDef(name="domain_metrics", type="array", items=METRIC_ITEM),
    ],
)


# ============================================================
# 可判定的预期（需求侧的预期载体）
# ============================================================
#
# 一条需求「被模型覆盖了」不等于「这条需求被满足了」。
# 以前需求集里只有需求描述（散文），没有任何东西说得出
# **这条需求怎么算被满足**——于是「验证业务价值」会退化成
# 「验这个名字存不存在」。
#
# 预期就是那个「怎么算被满足」：
#   requirement_id —— 它来自哪条需求（可追溯）
#   wms_kind       —— 它落在哪个 WMS 概念上（**可判定**的根据）
#   statement      —— 该概念的正式表述（**机械取自词表**，不编）
#   evidence       —— 业务原话里的逐字片段（**机械取自绑定**）
#
# 为什么由建模盒产出（而不是需求盒）：
# 「可判定」要求预期挂在一个 WMS 概念上——测试盒的探针是按概念找的。
# 只有建模盒知道哪个需求绑到了哪个概念；需求盒那边只有业务原话，
# 做不出可判定的预期，只能做出一句复述。

MODEL_EXPECTATION = FieldDef(
    name="expectation",
    type="object",
    properties=[
        FieldDef(name="expectation_id", type="string"),
        FieldDef(name="requirement_id", type="string"),
        FieldDef(name="wms_kind", type="string"),
        FieldDef(name="statement", type="string"),
        FieldDef(name="evidence", type="string"),
    ],
)


MODEL_EXPECTATIONS = SchemaDef(
    id="schema_model_expectations",
    description="可判定的预期（每条 WMS 绑定一条：这条需求该怎样才算被满足）",
    fields=[
        FieldDef(name="expectations", type="array", items=MODEL_EXPECTATION),
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
        # ---- 可判定的预期 ----
        # 「判了就要带出来」：判据读的就是这份载体，
        # 不带出来的话，下游（测试盒 / 分析盒）根本不知道该验什么。
        FieldDef(name="expectations", type="array", items=MODEL_EXPECTATION),
        FieldDef(name="expectation_completeness", type="boolean"),
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
            type="ref:schema_model_package_elements",
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
        # ---- 可判定的预期（需求侧的预期载体）----
        #
        # 「验证业务价值」得先有**可判定的东西**：
        # 每条 WMS 绑定 → 一条预期（这条需求该怎样才算被满足）。
        # 少了它，下游只能拿需求描述去猜「算不算满足」——
        # 那就退化成「验名字存不存在」。
        FieldDef(name="expectations", type="array", items=MODEL_EXPECTATION),
        FieldDef(name="expectation_completeness", type="boolean"),
        # ---- exception 条件读到的量 ----
        # 这三个以前**产出里一直有、schema 里一直没写**。
        # 后果不是产出缺东西，是三条 exception 规则各自指向一个
        # schema 里不存在的字段——而 exception 规则不在 schema 里，
        # 没人会去对，于是它们安安静静地从来没触发过：
        # 需求超过 1000 条不 escalate、业务方没指定不 no_deliver、
        # 有需求绑不上 WMS 概念也不报。规则写着，护不住任何东西。
        FieldDef(name="requirement_count", type="integer",
                 description="本次建模覆盖的需求条数（异常阈值读它）"),
        FieldDef(name="business_owner_assigned", type="boolean",
                 description="需求集是否指定了业务负责人"),
        FieldDef(name="requirements_without_wms_binding", type="integer",
                 description="可建模需求里没能绑上 WMS 实体的条数（应为 0）"),
        # ---- WMS 域专属的 2 条判据 ----
        # 前 5 条验的是「模型自洽」——需求覆盖率、引用完整性、结构齐不齐。
        # 这两条验的是「**这是不是一个 WMS 模型**」：需求有没有落到 WMS
        # 概念上，规则有没有约束 WMS 业务。少了这两条，一个纯通用的
        # 「当库存 > 0 时……」模型照样全绿，而盒子挂着 WMS 的招牌。
        FieldDef(name="wms_binding", type="boolean"),
        FieldDef(name="wms_rule_binding", type="boolean"),
        # 绑定作废的原因（自造概念 / 证据不是原文 / 类别不匹配）。
        # **判了就要看得见**：只给一个「没绑上」的布尔值的话，
        # 读者分不清是「模型没认出来」还是「模型认错了」——
        # 这两种要修的地方完全不同。
        FieldDef(
            name="binding_errors",
            type="array",
            required=False,
            items=FieldDef(
                name="binding_error",
                type="object",
                properties=[
                    FieldDef(name="requirement_id", type="string"),
                    FieldDef(name="reason", type="string"),
                ],
            ),
        ),
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
                            FieldDef(name="evidence", type="string"),
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
                            FieldDef(name="evidence", type="string"),
                        ],
                    ),
                ),
                # 指标组：**产出里有，schema 里也得有**。
                # 只在产出里加字段而不声明，消费方按 schema 取数据就永远
                # 取不到——「产出了却没声明」是「声明了却拿不到」的镜像病。
                FieldDef(
                    name="metrics",
                    type="array",
                    items=FieldDef(
                        name="m",
                        type="object",
                        properties=[
                            FieldDef(name="kind", type="string"),
                            FieldDef(name="label", type="string"),
                            FieldDef(name="statement", type="string"),
                            FieldDef(name="trace_to", type="string"),
                            FieldDef(name="evidence", type="string"),
                        ],
                    ),
                ),
                # 报表组：业务方说「临期 SKU 要能在库存看板看到」，
                # 讲的是 `inventory_dashboard` 这个**报表**——
                # 它既不是实体也不是指标。以前报表概念压根不在词表里，
                # 于是这类需求绑不上任何东西。
                FieldDef(
                    name="reports",
                    type="array",
                    items=FieldDef(
                        name="rp",
                        type="object",
                        properties=[
                            FieldDef(name="kind", type="string"),
                            FieldDef(name="label", type="string"),
                            FieldDef(name="statement", type="string"),
                            FieldDef(name="trace_to", type="string"),
                            FieldDef(name="evidence", type="string"),
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
    MODEL_PACKAGE_ELEMENTS,
    MODEL_EXPECTATIONS,
    MODEL_EVIDENCE,
    BUSINESS_MODEL_PACKAGE,
]