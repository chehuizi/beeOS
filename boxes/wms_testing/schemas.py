"""boxes.wms_testing.schemas - WMS Testing Box 的业务 schemas

WMS 测试盒——拿开发盒的制品，用**WMS 业务场景**验它：收货上架、拣货复核、盘点、批次效期这些真实单据跑一遍。

**骨架盒**：schema 是真的（input / 沿途 / 产出 的形状先立住），但产出由
`runner` 的桩返回——它**刻意不产出判据要读的 metric**。

**判据 metric 名就是这里的字段名。** 这不是风格：`acceptance` 指向一个
schema 里没有的字段，等于判了一条判不出来的判据。生成期就硬校验。
"""

from __future__ import annotations

from core.models import FieldDef, SchemaDef

# ---- 消费方自己声明的形状 ----
#
# 以前这里是 `ref:schema_build_artifact` / `ref:schema_requirement_set_package`
# ——**两个都指向别的盒子的 schema id**（开发盒 / 需求盒）。
# 文档（docs/beebox-definition-schema.md §4）：ref 只能引用本 definition 内。
#
# 悬空的后果是**没人校验**：那个 id 在本盒里不存在，于是「制品该长什么样」
# 「需求集该长什么样」都没定义过，投料里塞什么都行。
#
# 现在由**测试盒自己**声明它要收的两份形状——schema 归消费者所有。
# 交接时 runtime/handoff.py 按这份声明校验上游产出。
#
# 复制一份形状是有代价的（上游改了字段这里会旧），但那个代价是**响的**：
# 旧了就交接失败、当场报出来。而悬空引用的代价是**哑的**——永远没人发现。
BUILD_ARTIFACT_INPUT_SCHEMA = SchemaDef(
    id="schema_build_artifact_input",
    description="测试盒要消费的构建产物（形状由测试盒自己声明）",
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

REQUIREMENT_SET_INPUT_SCHEMA = SchemaDef(
    id="schema_requirement_set_input",
    description="测试盒要消费的需求集（形状由测试盒自己声明）",
    fields=[
        # set_id 是「这批用例服务哪条需求」——测试盒的 subject_ref 从这儿取
        FieldDef(name="set_id", type="string", required=True),
        FieldDef(name="business_goal", type="string", required=False),
        FieldDef(name="requirement_count", type="integer", required=False),
        FieldDef(name="requirements", type="array", required=True, items=FieldDef(
            name="requirement", type="object", properties=[
                FieldDef(name="requirement_id", type="string"),
                FieldDef(name="description", type="string"),
                FieldDef(name="requirement_type", type="string"),
            ],
        )),
    ],
)

TEST_TASK_INPUT_SCHEMA = SchemaDef(
    id="schema_test_task_input",
    description="测试任务（制品 + 需求集 + 场景集）（一次测试任务的投料）",
    fields=[
        FieldDef(name="test_task_id", type="string", required=True),
        FieldDef(name="artifact_ref", type="ref:schema_build_artifact_input", required=True),
        FieldDef(name="requirement_ref", type="ref:schema_requirement_set_input", required=True),
        FieldDef(name="scenarios", type="array", required=True, items=FieldDef(name="item", type="string")),
    ],
)

TEST_EXECUTION_SCHEMA = SchemaDef(
    id="schema_test_execution",
    description="测试执行过程（场景与用例跑下来了，尚未定性）",
    fields=[
        FieldDef(name="test_task_id", type="string", required=True),
        FieldDef(name="case_count", type="integer", required=True),
        FieldDef(name="defect_refs", type="array", required=True, items=FieldDef(name="item", type="string")),
    ],
)

TEST_VERDICT_SCHEMA = SchemaDef(
    id="schema_test_verdict",
    description="测试定性结论（覆盖率 / 缺陷 / 是否放行）",
    fields=[
        FieldDef(name="test_task_id", type="string", required=True),
        FieldDef(name="scenario_count", type="integer", required=True),
        FieldDef(name="open_defect_count", type="integer", required=True),
    ],
)

TEST_REPORT_SCHEMA = SchemaDef(
    id="schema_test_report",
    description="测试报告（场景 / 用例 / 缺陷）",
    fields=[
        FieldDef(name="scenario_count", type="integer", required=True),
        FieldDef(name="case_count", type="integer", required=True),
        FieldDef(name="case_pass_rate", type="number", required=True),
        FieldDef(name="open_defect_count", type="integer", required=True),
        FieldDef(name="requirement_case_coverage", type="boolean", required=True),
        FieldDef(name="scenario_coverage", type="boolean", required=True),
        FieldDef(name="no_blocking_defect", type="boolean", required=True),
    ],
)


ALL_SCHEMAS = [
    BUILD_ARTIFACT_INPUT_SCHEMA,
    REQUIREMENT_SET_INPUT_SCHEMA,
    TEST_TASK_INPUT_SCHEMA,
    TEST_EXECUTION_SCHEMA,
    TEST_VERDICT_SCHEMA,
    TEST_REPORT_SCHEMA,
]
