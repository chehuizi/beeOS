"""boxes.wms_development.schemas - WMS Development Box 的业务 schemas

WMS 开发盒——把设计包变成**能构建、能跑起来**的代码：提交 → 构建 → 自动化测试 → 制品。

**骨架盒**：schema 是真的（input / 沿途 / 产出 的形状先立住），但产出由
`runner` 的桩返回——它**刻意不产出判据要读的 metric**。

**判据 metric 名就是这里的字段名。** 这不是风格：`acceptance` 指向一个
schema 里没有的字段，等于判了一条判不出来的判据。生成期就硬校验。
"""

from __future__ import annotations

from core.models import FieldDef, SchemaDef

# ---- 消费方自己声明的形状 ----
#
# 以前这里是 `type="ref:schema_business_model_package"`——**指向别的盒子的
# schema id**。文档（docs/beebox-definition-schema.md §4）写得很清楚：
# 「schema 内 ref: 引用必须指向本 definition schemas 块内真实存在的 schema id」。
#
# 悬空引用的后果不是报错，是**没人校验**：那个 id 在本盒里根本不存在，
# 于是「设计包该长什么样」这件事谁都没定义，投料里塞什么都行。
# 它跟之前那 4 条永不触发的 exception 是同一族病：**声明了约束，但没人执行**。
#
# 现在由**消费者自己声明**它要收的形状——schema 归消费者所有。
# 交接时 runtime/handoff.py 拿这份声明去校验上游产出，
# 「接过来的根本不是那回事」当场就被拒。
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
                    FieldDef(name="entity_id", type="string"),
                    FieldDef(name="name", type="string"),
                    FieldDef(name="trace_to", type="string"),
                ])),
            FieldDef(name="specifications", type="array", required=False, items=FieldDef(
                name="specification", type="object", properties=[
                    FieldDef(name="rule_id", type="string"),
                    FieldDef(name="name", type="string"),
                    FieldDef(name="trace_to", type="string"),
                ])),
            FieldDef(name="domain_services", type="array", required=False, items=FieldDef(
                name="domain_service", type="object", properties=[
                    FieldDef(name="process_id", type="string"),
                    FieldDef(name="name", type="string"),
                    FieldDef(name="trace_to", type="string"),
                ])),
            FieldDef(name="domain_events", type="array", required=False, items=FieldDef(
                name="domain_event", type="object", properties=[
                    FieldDef(name="event_id", type="string"),
                    FieldDef(name="name", type="string"),
                    FieldDef(name="trace_to", type="string"),
                ])),
            FieldDef(name="domain_metrics", type="array", required=False, items=FieldDef(
                name="domain_metric", type="object", properties=[
                    FieldDef(name="metric_id", type="string"),
                    FieldDef(name="name", type="string"),
                    FieldDef(name="trace_to", type="string"),
                ])),
        ]),
    ],
)

DEV_TASK_INPUT_SCHEMA = SchemaDef(
    id="schema_dev_task_input",
    description="开发任务（设计包 + 目标提交范围）（一次开发任务的投料）",
    fields=[
        FieldDef(name="dev_task_id", type="string", required=True),
        FieldDef(name="design_package_ref", type="ref:schema_design_package_input", required=True),
        FieldDef(name="commit_range", type="string", required=True),
        FieldDef(name="target_env", type="string", required=True),
    ],
)

BUILD_TREE_SCHEMA = SchemaDef(
    id="schema_build_tree",
    description="构建出来的源码树（代码已编译，尚未验单测）",
    fields=[
        FieldDef(name="dev_task_id", type="string", required=True),
        FieldDef(name="commit_count", type="integer", required=True),
        FieldDef(name="build_log_ref", type="string", required=True),
    ],
)

BUILD_CHECK_SCHEMA = SchemaDef(
    id="schema_build_check",
    description="构建校验结论（单测 / 复现性 / 评审 / 可追溯）",
    fields=[
        FieldDef(name="dev_task_id", type="string", required=True),
        FieldDef(name="test_run_ref", type="string", required=True),
        FieldDef(name="artifact_digest", type="string", required=True),
    ],
)

BUILD_ARTIFACT_SCHEMA = SchemaDef(
    id="schema_build_artifact",
    description="构建产物与构建记录",
    fields=[
        FieldDef(name="commit_count", type="integer", required=True),
        FieldDef(name="build_duration_s", type="number", required=True),
        FieldDef(name="unit_test_pass_rate", type="number", required=True),
        FieldDef(name="artifact_digest", type="string", required=True),
        FieldDef(name="build_failed", type="boolean", required=True),
        FieldDef(name="build_reproducibility", type="boolean", required=True),
        FieldDef(name="unit_test_green", type="boolean", required=True),
        FieldDef(name="review_coverage", type="boolean", required=True),
        FieldDef(name="artifact_traceable", type="boolean", required=True),
    ],
)


ALL_SCHEMAS = [
    DESIGN_PACKAGE_INPUT_SCHEMA,
    DEV_TASK_INPUT_SCHEMA,
    BUILD_TREE_SCHEMA,
    BUILD_CHECK_SCHEMA,
    BUILD_ARTIFACT_SCHEMA,
]
