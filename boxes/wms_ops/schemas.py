"""boxes.wms_ops.schemas - WMS Ops Box 的业务 schemas

WMS 运维盒——管**上线之后**的事：变更审批、发布执行、故障处置、回滚能力、以及把线上真实状态回写进系统档案。

**骨架盒**：schema 是真的（input / 沿途 / 产出 的形状先立住），但产出由
`runner` 的桩返回——它**刻意不产出判据要读的 metric**。

**判据 metric 名就是这里的字段名。** 这不是风格：`acceptance` 指向一个
schema 里没有的字段，等于判了一条判不出来的判据。生成期就硬校验。
"""

from __future__ import annotations

from core.models import FieldDef, SchemaDef

OPS_TASK_INPUT_SCHEMA = SchemaDef(
    id="schema_ops_task_input",
    description="运维任务（变更单 / 告警 / 回滚请求）（一次运维任务的投料）",
    fields=[
        FieldDef(name="ops_task_id", type="string", required=True),
        FieldDef(name="change_request", type="string", required=True),
        FieldDef(name="alerts", type="array", required=True, items=FieldDef(name="item", type="string")),
        FieldDef(name="rollback_target", type="string", required=True),
    ],
)

OPS_HANDLING_SCHEMA = SchemaDef(
    id="schema_ops_handling",
    description="处置过程（变更执行 / 故障处置 / 回滚）",
    fields=[
        FieldDef(name="ops_task_id", type="string", required=True),
        FieldDef(name="change_count", type="integer", required=True),
        FieldDef(name="incident_count", type="integer", required=True),
    ],
)

OPS_VERDICT_SCHEMA = SchemaDef(
    id="schema_ops_verdict",
    description="运维定性结论（审批 / 回滚能力 / 记录 / 是否已回写档案）",
    fields=[
        FieldDef(name="ops_task_id", type="string", required=True),
        FieldDef(name="mttr_minutes", type="number", required=True),
        FieldDef(name="profile_ref", type="string", required=True),
    ],
)

OPS_RECORD_SCHEMA = SchemaDef(
    id="schema_ops_record",
    description="运维记录（变更 / 故障 / 回滚结果）",
    fields=[
        FieldDef(name="change_count", type="integer", required=True),
        FieldDef(name="incident_count", type="integer", required=True),
        FieldDef(name="mttr_minutes", type="number", required=True),
        FieldDef(name="rollback_ready", type="boolean", required=True),
        FieldDef(name="change_approval_rate", type="boolean", required=True),
        FieldDef(name="rollback_readiness", type="boolean", required=True),
        FieldDef(name="incident_documented", type="boolean", required=True),
        FieldDef(name="runtime_state_recorded", type="boolean", required=True),
    ],
)


ALL_SCHEMAS = [
    OPS_TASK_INPUT_SCHEMA,
    OPS_HANDLING_SCHEMA,
    OPS_VERDICT_SCHEMA,
    OPS_RECORD_SCHEMA,
]
