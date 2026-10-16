"""boxes.wms_analysis.schemas - WMS Analysis Box 的业务 schemas

WMS 线上效果分析盒——上线之后拿真实运行数据回头看：**当初那条需求，
线上到底满足了没有**。它读的是 ops 写回的真实状态，拿需求声明的预期值
去比，落差就是下一轮需求的种子。

**判据 metric 名就是这里的字段名。** 这不是风格：`acceptance` 指向一个
schema 里没有的字段，等于判了一条判不出来的判据。生成期就硬校验。
"""

from __future__ import annotations

from core.models import FieldDef, SchemaDef

ANALYSIS_INPUT_SCHEMA = SchemaDef(
    id="schema_analysis_input",
    description="分析投料（需求预期 + 线上实测指标）（一次效果分析的投料）",
    fields=[
        FieldDef(name="analysis_id", type="string", required=True),
        FieldDef(name="requirement_ref", type="string", required=True),
        FieldDef(name="expected_metrics", type="array", required=True, items=FieldDef(name="item", type="string")),
        FieldDef(name="observed_window", type="string", required=True),
    ],
)

ANALYSIS_COMPARISON_SCHEMA = SchemaDef(
    id="schema_analysis_comparison",
    description="预期 vs 实测的逐项比对",
    fields=[
        FieldDef(name="analysis_id", type="string", required=True),
        FieldDef(name="compared_metric_count", type="integer", required=True),
        FieldDef(name="gap_count", type="integer", required=True),
    ],
)

ANALYSIS_FINDING_SCHEMA = SchemaDef(
    id="schema_analysis_finding",
    description="分析结论（哪些达标、哪些有落差、落差来自哪）",
    fields=[
        FieldDef(name="analysis_id", type="string", required=True),
        FieldDef(name="converged", type="boolean", required=True),
        FieldDef(name="next_requirement_seed", type="string", required=True),
    ],
)

ANALYSIS_RECORD_SCHEMA = SchemaDef(
    id="schema_analysis_record",
    description="效果分析记录（归档用）",
    fields=[
        FieldDef(name="compared_metric_count", type="integer", required=True),
        FieldDef(name="gap_count", type="integer", required=True),
        FieldDef(name="convergence_rate", type="boolean", required=True),
        FieldDef(name="requirement_traceability", type="boolean", required=True),
        FieldDef(name="gap_attribution", type="boolean", required=True),
        FieldDef(name="next_requirement_seeded", type="boolean", required=True),
    ],
)


ALL_SCHEMAS = [
    ANALYSIS_INPUT_SCHEMA,
    ANALYSIS_COMPARISON_SCHEMA,
    ANALYSIS_FINDING_SCHEMA,
    ANALYSIS_RECORD_SCHEMA,
]
