"""tests.fixtures.sample_box.schemas - 夹具盒的两个 schema

形状刻意做得最小：输入一个、输出一个。
夹具盒的职责是**让机制跑起来**，不是演示一套业务数据形状。
"""

from __future__ import annotations

from core.models import FieldDef, SchemaDef

# 输入：一次请求
SAMPLE_REQUEST = SchemaDef(
    id="schema_sample_request",
    description="夹具盒的请求",
    fields=[
        FieldDef(name="sample_id", type="string"),
        FieldDef(name="kind", type="string"),
        FieldDef(name="amount", type="number", required=False),
    ],
)

# 输出：一次处理结果（acceptance 的判据从这里取）
SAMPLE_RESULT = SchemaDef(
    id="schema_sample_result",
    description="夹具盒的处理结果",
    fields=[
        FieldDef(name="sample_id", type="string"),
        # resolved / escalated
        FieldDef(name="sample_state", type="string"),
        FieldDef(name="notified", type="boolean"),
        FieldDef(
            name="steps",
            type="array",
            items=FieldDef(name="step", type="string"),
            required=False,
        ),
    ],
)

ALL_SCHEMAS = [SAMPLE_REQUEST, SAMPLE_RESULT]
