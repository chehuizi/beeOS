"""boxes.wms_system.schemas - WMS System Box 的业务 schemas

WMS 系统描述盒——**描述真实在跑的那套 WMS**：它有哪些服务、对外暴露什么接口、每个组件部署在什么版本、依赖哪些外部系统。

**骨架盒**：schema 是真的（input / 沿途 / 产出 的形状先立住），但产出由
`runner` 的桩返回——它**刻意不产出判据要读的 metric**。

**判据 metric 名就是这里的字段名。** 这不是风格：`acceptance` 指向一个
schema 里没有的字段，等于判了一条判不出来的判据。生成期就硬校验。
"""

from __future__ import annotations

from core.models import FieldDef, SchemaDef

SYSTEM_SNAPSHOT_INPUT_SCHEMA = SchemaDef(
    id="schema_system_snapshot_input",
    description="待入库的系统快照（组件 / 接口 / 部署 / 版本）（真实在跑的那套 WMS 的一次快照）",
    fields=[
        FieldDef(name="snapshot_id", type="string", required=True),
        FieldDef(name="components", type="array", required=True, items=FieldDef(name="item", type="string")),
        FieldDef(name="interfaces", type="array", required=True, items=FieldDef(name="item", type="string")),
        FieldDef(name="deployment", type="string", required=True),
        FieldDef(name="observed_at", type="string", required=True),
    ],
)

SYSTEM_SNAPSHOT_NORMALIZED_SCHEMA = SchemaDef(
    id="schema_system_snapshot_normalized",
    description="归一后的系统快照（结构已对齐，尚未校验）",
    fields=[
        FieldDef(name="snapshot_id", type="string", required=True),
        FieldDef(name="components", type="array", required=True, items=FieldDef(name="item", type="string")),
        FieldDef(name="interfaces", type="array", required=True, items=FieldDef(name="item", type="string")),
        FieldDef(name="deployment_ref", type="string", required=True),
    ],
)

SYSTEM_VERIFICATION_SCHEMA = SchemaDef(
    id="schema_system_verification",
    description="系统校验结论（组件 / 接口 / 版本三项各自查出来什么）",
    fields=[
        FieldDef(name="snapshot_id", type="string", required=True),
        FieldDef(name="checked_component_count", type="integer", required=True),
        FieldDef(name="checked_interface_count", type="integer", required=True),
    ],
)

SYSTEM_PROFILE_SCHEMA = SchemaDef(
    id="schema_system_profile",
    description="系统档案（真实在跑的 WMS 的准确描述）",
    fields=[
        FieldDef(name="component_count", type="integer", required=True),
        FieldDef(name="interface_count", type="integer", required=True),
        FieldDef(name="version_drift_count", type="integer", required=True),
        FieldDef(name="dependency_count", type="integer", required=True),
        FieldDef(name="component_completeness", type="boolean", required=True),
        FieldDef(name="interface_compliance", type="boolean", required=True),
        FieldDef(name="version_traceability", type="boolean", required=True),
        FieldDef(name="version_drift_free", type="boolean", required=True),
    ],
)


ALL_SCHEMAS = [
    SYSTEM_SNAPSHOT_INPUT_SCHEMA,
    SYSTEM_SNAPSHOT_NORMALIZED_SCHEMA,
    SYSTEM_VERIFICATION_SCHEMA,
    SYSTEM_PROFILE_SCHEMA,
]
