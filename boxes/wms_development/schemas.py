"""boxes.wms_development.schemas - WMS Development Box 的业务 schemas

WMS 开发盒——**按设计包改代码**：读方案 → 定位源码 → 改 → 构建 → 制品。

这条腿是四跳：

    dev_intake → dev_code → dev_build → build_packager

每一跳的产出形状都在这里声明。**判据 metric 名就是结果 schema 的字段名**——
`acceptance` 指向一个 schema 里没有的字段，等于判了一条判不出来的判据。
生成期就硬校验（见 tests/test_box_standard.py）。

**判据的答案全部来自外部神谕**（编译器 / 仓库自带测试套件 / git），
不由这只盒子自述——所以结果里没有一条 metric 是「模型说它做到了」。
"""

from __future__ import annotations

from core.models import FieldDef, SchemaDef

# ---- 预期（开发盒自己声明的形状）----
#
# 开发盒要知道「这次改代码是为了满足什么」，就得拿得到预期。
# 预期由建模盒产出（它是「可判定」的载体），开发盒是消费方，
# 所以形状在这里**重新声明**而不是 ref 建模盒的 schema id——
# ref 只能引用本 definition 内（见 docs/beebox-definition-schema.md §4）。
#
# 抄一份的代价是「上游改了这里会旧」，但那个代价是**响的**：
# 交接失败、当场报出来。悬空引用的代价是**哑的**：永远没人发现。
DEV_EXPECTATION = FieldDef(
    name="expectation",
    type="object",
    properties=[
        FieldDef(name="expectation_id", type="string", required=False),
        FieldDef(name="requirement_id", type="string", required=False),
        FieldDef(name="wms_kind", type="string", required=False),
        FieldDef(name="statement", type="string", required=False),
        FieldDef(name="evidence", type="string", required=False),
    ],
)

# ---- 消费方自己声明的形状：设计包 ----
DESIGN_PACKAGE_INPUT_SCHEMA = SchemaDef(
    id="schema_design_package_input",
    description="开发盒要消费的设计包（形状由开发盒自己声明）",
    fields=[
        FieldDef(name="package_id", type="string", required=True),
        # set_id 是「这份设计对应哪条需求」——开发盒的 subject_ref 从这儿取
        FieldDef(name="set_id", type="string", required=True),
        FieldDef(name="business_goal", type="string", required=False),
        FieldDef(name="requirement_count", type="integer", required=False),
        # 模型元素本体：开发盒只当它是「要改哪些东西」的依据。
        # 声明的是**分组结构 + 每个元素开发盒真会读的那几个键**——
        # 多余字段不报错（模型可扩展），但少一个分组就是形状不对。
        FieldDef(name="model_elements", type="object", required=True, properties=[
            FieldDef(name="entities", type="array", required=False, items=FieldDef(
                name="entity", type="object", properties=[
                    FieldDef(name="entity_id", type="string", required=False),
                    FieldDef(name="name", type="string", required=False),
                    FieldDef(name="wms_kind", type="string", required=False),
                    FieldDef(name="trace_to", type="string", required=False),
                ])),
            FieldDef(name="specifications", type="array", required=False, items=FieldDef(
                name="specification", type="object", properties=[
                    FieldDef(name="rule_id", type="string", required=False),
                    FieldDef(name="name", type="string", required=False),
                    FieldDef(name="wms_constraint", type="string", required=False),
                    FieldDef(name="trace_to", type="string", required=False),
                ])),
            FieldDef(name="domain_services", type="array", required=False, items=FieldDef(
                name="domain_service", type="object", properties=[
                    FieldDef(name="process_id", type="string", required=False),
                    FieldDef(name="name", type="string", required=False),
                    FieldDef(name="wms_kind", type="string", required=False),
                    FieldDef(name="trace_to", type="string", required=False),
                ])),
            FieldDef(name="domain_events", type="array", required=False, items=FieldDef(
                name="domain_event", type="object", properties=[
                    FieldDef(name="event_id", type="string", required=False),
                    FieldDef(name="name", type="string", required=False),
                    FieldDef(name="wms_kind", type="string", required=False),
                    FieldDef(name="trace_to", type="string", required=False),
                ])),
            FieldDef(name="domain_metrics", type="array", required=False, items=FieldDef(
                name="domain_metric", type="object", properties=[
                    FieldDef(name="metric_id", type="string", required=False),
                    FieldDef(name="name", type="string", required=False),
                    FieldDef(name="wms_kind", type="string", required=False),
                    FieldDef(name="trace_to", type="string", required=False),
                ])),
        ]),
        # **预期**：没有它，开发盒只知道「WMS 概念」，不知道
        # 「这次要满足的是哪条业务要求、它长什么样」。
        FieldDef(
            name="expectations", type="array", required=False,
            items=DEV_EXPECTATION,
        ),
    ],
)

DEV_TASK_INPUT_SCHEMA = SchemaDef(
    id="schema_dev_task_input",
    description="开发任务（设计包 + 目标提交范围）（一次开发任务的投料）",
    fields=[
        FieldDef(name="dev_task_id", type="string", required=True),
        FieldDef(name="design_package_ref", type="ref:schema_design_package_input", required=True),
        # commit_range 形如 "c0a7aa2..HEAD"：前面那截是**基线提交**
        # （改动相对于它算），不是「要改哪一段区间」。
        FieldDef(name="commit_range", type="string", required=False),
        FieldDef(name="target_env", type="string", required=False),
    ],
)

# ---- 沿途形状 ----
DEV_PLAN_SCHEMA = SchemaDef(
    id="schema_dev_plan",
    description="开发计划（要满足哪些预期 + 这些概念落在哪些源码位置）",
    fields=[
        FieldDef(name="dev_task_id", type="string", required=True),
        FieldDef(name="package_id", type="string", required=False),
        FieldDef(name="set_id", type="string", required=False),
        FieldDef(name="concepts", type="array", required=False,
                 items=FieldDef(name="concept", type="string")),
        FieldDef(name="locations", type="array", required=False,
                 items=FieldDef(name="location", type="string")),
        # **没有落点的概念**：不猜，但要看得见——
        # 「方案里有个概念在代码里不存在」是业务事实，不是噪声。
        FieldDef(name="unmapped_concepts", type="array", required=False,
                 items=FieldDef(name="concept", type="string")),
        FieldDef(name="expectations", type="array", required=False,
                 items=DEV_EXPECTATION),
        FieldDef(name="base_ref", type="string", required=True),
        FieldDef(name="target_env", type="string", required=False),
    ],
)

CODE_CHANGE_SCHEMA = SchemaDef(
    id="schema_code_change",
    description="代码改动（改了哪些文件 + 提交落在哪）",
    fields=[
        FieldDef(name="dev_task_id", type="string", required=True),
        FieldDef(name="commit_sha", type="string", required=False),
        FieldDef(name="branch", type="string", required=False),
        FieldDef(name="commit_count", type="integer", required=True),
        FieldDef(name="changed_files", type="array", required=False,
                 items=FieldDef(name="file", type="string")),
        FieldDef(name="codegen_summary", type="string", required=False),
        FieldDef(name="workspace_path", type="string", required=True),
        # **每个概念在源码里的实现状态**（implemented / missing / unknown）
        # ——机械核对出来的，不是模型说的。
        FieldDef(name="concept_states", type="array", required=False, items=FieldDef(
            name="concept_state", type="object", properties=[
                FieldDef(name="concept", type="string"),
                FieldDef(name="state", type="string"),
            ])),
        # 这次要满足的预期落在哪些概念上（只算这些，别把实体也算进去）
        FieldDef(name="expectation_concepts", type="array", required=False,
                 items=FieldDef(name="concept", type="string")),
        # 往下带（下一跳的输入 = 这一跳的输出，漏一个就静默丢）
        FieldDef(name="concepts", type="array", required=False,
                 items=FieldDef(name="concept", type="string")),
        FieldDef(name="locations", type="array", required=False,
                 items=FieldDef(name="location", type="string")),
        FieldDef(name="expectations", type="array", required=False,
                 items=DEV_EXPECTATION),
    ],
)

BUILD_CHECK_SCHEMA = SchemaDef(
    id="schema_build_check",
    description="构建校验结论（编译 / 单测 / 复现性，全部来自外部工具）",
    fields=[
        FieldDef(name="dev_task_id", type="string", required=True),
        FieldDef(name="commit_sha", type="string", required=False),
        FieldDef(name="branch", type="string", required=False),
        FieldDef(name="workspace_path", type="string", required=True),
        FieldDef(name="commit_count", type="integer", required=False),
        FieldDef(name="changed_files", type="array", required=False,
                 items=FieldDef(name="file", type="string")),
        FieldDef(name="build_failed", type="boolean", required=True),
        FieldDef(name="build_duration_s", type="number", required=True),
        FieldDef(name="unit_test_pass_rate", type="number", required=True),
        FieldDef(name="unit_test_green", type="boolean", required=True),
        FieldDef(name="test_count", type="integer", required=False),
        FieldDef(name="failure_count", type="integer", required=False),
        FieldDef(name="build_reproducibility", type="boolean", required=True),
        FieldDef(name="reproducibility_digest", type="string", required=False),
        FieldDef(name="artifact_digest", type="string", required=True),
        FieldDef(name="test_run_ref", type="string", required=False),
        FieldDef(name="build_log_ref", type="string", required=False),
        FieldDef(name="jar_path", type="string", required=False),
        FieldDef(name="codegen_summary", type="string", required=False),
        FieldDef(name="concept_states", type="array", required=False, items=FieldDef(
            name="concept_state", type="object", properties=[
                FieldDef(name="concept", type="string"),
                FieldDef(name="state", type="string"),
            ])),
        FieldDef(name="expectation_concepts", type="array", required=False,
                 items=FieldDef(name="concept", type="string")),
        # 往下带
        FieldDef(name="concepts", type="array", required=False,
                 items=FieldDef(name="concept", type="string")),
        FieldDef(name="locations", type="array", required=False,
                 items=FieldDef(name="location", type="string")),
        FieldDef(name="expectations", type="array", required=False,
                 items=DEV_EXPECTATION),
    ],
)

BUILD_ARTIFACT_SCHEMA = SchemaDef(
    id="schema_build_artifact",
    description="构建产物与构建记录",
    fields=[
        # ---- 这 9 个字段名跟测试盒声明的制品形状逐字一致 ----
        # 交接时 runtime/handoff.py 拿**测试盒自己**的声明去校验这份产出，
        # 少一个字段就是交接失败（响的），而不是「接过去了但读不到」（哑的）。
        FieldDef(name="commit_count", type="integer", required=True),
        FieldDef(name="build_duration_s", type="number", required=True),
        FieldDef(name="unit_test_pass_rate", type="number", required=True),
        FieldDef(name="artifact_digest", type="string", required=True),
        FieldDef(name="build_failed", type="boolean", required=True),
        FieldDef(name="build_reproducibility", type="boolean", required=True),
        FieldDef(name="unit_test_green", type="boolean", required=True),
        FieldDef(name="review_coverage", type="boolean", required=True),
        FieldDef(name="artifact_traceable", type="boolean", required=True),
        # ---- 证据：判据红的时候，读者得知道红在哪 ----
        FieldDef(name="commit_sha", type="string", required=False),
        FieldDef(name="branch", type="string", required=False),
        FieldDef(name="changed_files", type="array", required=False,
                 items=FieldDef(name="file", type="string")),
        # **这次改动瞄准了哪些预期**——
        # 叫「瞄准」不叫「实现」：「实现了」得由测试盒跑真实场景来回答，
        # 这儿只能声明目标。自称「已实现」就是自述，自述不是神谕。
        FieldDef(name="targeted_expectations", type="array", required=False,
                 items=FieldDef(name="expectation", type="object", properties=[
                     FieldDef(name="expectation_id", type="string"),
                     FieldDef(name="requirement_id", type="string"),
                     FieldDef(name="wms_kind", type="string"),
                     FieldDef(name="statement", type="string"),
                 ])),
        FieldDef(name="review_reason", type="string", required=False),
        FieldDef(name="trace_reason", type="string", required=False),
        FieldDef(name="codegen_summary", type="string", required=False),
        # **编出来的 jar 落在哪儿**（工作区会被拆，jar 得留下）
        FieldDef(name="artifact_path", type="string", required=False),
    ],
)


ALL_SCHEMAS = [
    DESIGN_PACKAGE_INPUT_SCHEMA,
    DEV_TASK_INPUT_SCHEMA,
    DEV_PLAN_SCHEMA,
    CODE_CHANGE_SCHEMA,
    BUILD_CHECK_SCHEMA,
    BUILD_ARTIFACT_SCHEMA,
]
