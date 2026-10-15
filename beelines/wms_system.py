"""beelines.wms_system - WMS System Box 的作业路线

WMS 系统描述盒——**描述真实在跑的那套 WMS**：它有哪些服务、对外暴露什么接口、每个组件部署在什么版本、依赖哪些外部系统。

op 链：
  1. `system_intake` — 接收系统快照（组件 / 接口 / 部署 / 版本）（schema_system_snapshot_input → schema_system_snapshot_normalized）
  2. `system_verify` — 验组件齐不齐、接口有没有契约、版本能不能追到真实部署（schema_system_snapshot_normalized → schema_system_verification）
  3. `system_profile_packager` — 打包成系统档案（schema_system_verification → schema_system_profile）

**这三个 op 现在还没有实现**（runner 里是桩）。beeline 是声明：它先把
要走的路线和每一步的数据形状钉住，腿才跟着路走。
"""

from __future__ import annotations

from core.beeline_models import (
    Bee,
    Beeline,
    Idempotency,
    NextRef,
    Operation,
)


# 同一任务重复执行必须产出同一份结果
IDEMPOTENT_BY_TASK = Idempotency(mode="required", key="task_id")


WMS_SYSTEM_BEELINE = Beeline(
    id="beeline_wms_system_v1",
    version=1,
    description=(
        "WMS System Box 履约作业路线：system_intake → system_verify → system_profile_packager"
    ),

    operations=[
        Operation(
            op_id="system_intake",
            type="system_intake",
            input_from="external",
            input="schema_system_snapshot_input",
            output="schema_system_snapshot_normalized",
            next=[NextRef(op_id="system_verify")],
            bee=Bee(type="system_intake_handler"),
            idempotency=IDEMPOTENT_BY_TASK,
        ),

        Operation(
            op_id="system_verify",
            type="system_verify",
            input_from="system_intake",
            input="schema_system_snapshot_normalized",
            output="schema_system_verification",
            next=[NextRef(op_id="system_profile_packager")],
            bee=Bee(type="system_verify_handler"),
            idempotency=IDEMPOTENT_BY_TASK,
        ),

        Operation(
            op_id="system_profile_packager",
            type="system_profile_packager",
            input_from="system_verify",
            input="schema_system_verification",
            output="schema_system_profile",
            next=[],
            bee=Bee(type="system_profile_packager_handler"),
            idempotency=IDEMPOTENT_BY_TASK,
        )
    ],
)


def get_beeline() -> Beeline:
    return WMS_SYSTEM_BEELINE
