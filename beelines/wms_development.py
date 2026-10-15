"""beelines.wms_development - WMS Development Box 的作业路线

WMS 开发盒——把设计包变成**能构建、能跑起来**的代码：提交 → 构建 → 自动化测试 → 制品。

op 链：
  1. `dev_intake` — 接收开发任务（设计包 + 目标提交范围）（schema_dev_task_input → schema_build_tree）
  2. `dev_build` — 构建 + 跑单测 + 复现性校验（schema_build_tree → schema_build_check）
  3. `build_packager` — 打包成构建产物 + 构建记录（schema_build_check → schema_build_artifact）

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


WMS_DEVELOPMENT_BEELINE = Beeline(
    id="beeline_wms_development_v1",
    version=1,
    description=(
        "WMS Development Box 履约作业路线：dev_intake → dev_build → build_packager"
    ),

    operations=[
        Operation(
            op_id="dev_intake",
            type="dev_intake",
            input_from="external",
            input="schema_dev_task_input",
            output="schema_build_tree",
            next=[NextRef(op_id="dev_build")],
            bee=Bee(type="dev_intake_handler"),
            idempotency=IDEMPOTENT_BY_TASK,
        ),

        Operation(
            op_id="dev_build",
            type="dev_build",
            input_from="dev_intake",
            input="schema_build_tree",
            output="schema_build_check",
            next=[NextRef(op_id="build_packager")],
            bee=Bee(type="dev_build_handler"),
            idempotency=IDEMPOTENT_BY_TASK,
        ),

        Operation(
            op_id="build_packager",
            type="build_packager",
            input_from="dev_build",
            input="schema_build_check",
            output="schema_build_artifact",
            next=[],
            bee=Bee(type="build_packager_handler"),
            idempotency=IDEMPOTENT_BY_TASK,
        )
    ],
)


def get_beeline() -> Beeline:
    return WMS_DEVELOPMENT_BEELINE
