"""boxes.wms_testing.schemas - WMS Testing Box 的业务 schemas

WMS 测试盒——拿开发盒的制品，用**WMS 业务场景**验它：收货上架、拣货复核、盘点、批次效期这些真实单据跑一遍。

**骨架盒**：schema 是真的（input / 沿途 / 产出 的形状先立住），但产出由
`runner` 的桩返回——它**刻意不产出判据要读的 metric**。

**判据 metric 名就是这里的字段名。** 这不是风格：`acceptance` 指向一个
schema 里没有的字段，等于判了一条判不出来的判据。生成期就硬校验。
"""

from __future__ import annotations

from core.models import FieldDef, SchemaDef

TEST_TASK_INPUT_SCHEMA = SchemaDef(
    id="schema_test_task_input",
    description="测试任务（制品 + 需求集 + 场景集）（一次测试任务的投料）",
    fields=[
        FieldDef(name="test_task_id", type="string", required=True),
        FieldDef(name="artifact_ref", type="ref:schema_build_artifact", required=True),
        FieldDef(name="requirement_ref", type="ref:schema_requirement_set_package", required=True),
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
    TEST_TASK_INPUT_SCHEMA,
    TEST_EXECUTION_SCHEMA,
    TEST_VERDICT_SCHEMA,
    TEST_REPORT_SCHEMA,
]
