"""boxes.wms_development.schemas - WMS Development Box 的业务 schemas

WMS 开发盒——把设计包变成**能构建、能跑起来**的代码：提交 → 构建 → 自动化测试 → 制品。

**骨架盒**：schema 是真的（input / 沿途 / 产出 的形状先立住），但产出由
`runner` 的桩返回——它**刻意不产出判据要读的 metric**。

**判据 metric 名就是这里的字段名。** 这不是风格：`acceptance` 指向一个
schema 里没有的字段，等于判了一条判不出来的判据。生成期就硬校验。
"""

from __future__ import annotations

from core.models import FieldDef, SchemaDef

DEV_TASK_INPUT_SCHEMA = SchemaDef(
    id="schema_dev_task_input",
    description="开发任务（设计包 + 目标提交范围）（一次开发任务的投料）",
    fields=[
        FieldDef(name="dev_task_id", type="string", required=True),
        FieldDef(name="design_package_ref", type="ref:schema_business_model_package", required=True),
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
    DEV_TASK_INPUT_SCHEMA,
    BUILD_TREE_SCHEMA,
    BUILD_CHECK_SCHEMA,
    BUILD_ARTIFACT_SCHEMA,
]
