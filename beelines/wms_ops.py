"""beelines.wms_ops - WMS Ops Box 的作业路线

WMS 运维盒——管**上线之后**的事：变更审批、发布执行、故障处置、回滚能力、以及把线上真实状态回写进系统档案。

op 链：
  1. `ops_intake` — 接收运维任务（变更 / 告警 / 回滚）（schema_ops_task_input → schema_ops_handling）
  2. `ops_handle` — 执行处置 + 回写线上真实状态（schema_ops_handling → schema_ops_verdict）
  3. `ops_packager` — 打包成运维记录（schema_ops_verdict → schema_ops_record）

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


WMS_OPS_BEELINE = Beeline(
    id="beeline_wms_ops_v1",
    version=1,
    description=(
        "WMS Ops Box 履约作业路线：ops_intake → ops_handle → ops_packager"
    ),

    operations=[
        Operation(
            op_id="ops_intake",
            type="ops_intake",
            input_from="external",
            input="schema_ops_task_input",
            output="schema_ops_handling",
            next=[NextRef(op_id="ops_handle")],
            bee=Bee(type="ops_intake_handler"),
            idempotency=IDEMPOTENT_BY_TASK,
        ),

        Operation(
            op_id="ops_handle",
            type="ops_handle",
            input_from="ops_intake",
            input="schema_ops_handling",
            output="schema_ops_verdict",
            next=[NextRef(op_id="ops_packager")],
            bee=Bee(type="ops_handle_handler"),
            idempotency=IDEMPOTENT_BY_TASK,
        ),

        Operation(
            op_id="ops_packager",
            type="ops_packager",
            input_from="ops_handle",
            input="schema_ops_verdict",
            output="schema_ops_record",
            next=[],
            bee=Bee(type="ops_packager_handler"),
            idempotency=IDEMPOTENT_BY_TASK,
        )
    ],
)


def get_beeline() -> Beeline:
    return WMS_OPS_BEELINE
