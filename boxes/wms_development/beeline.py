"""boxes.wms_development - WMS Development Box 的作业路线

WMS 开发盒——**按设计包改代码**：读方案 → 定位源码 → 改 → 构建 → 制品。

op 链：
  1. `dev_intake`      —— 设计包 + 目标提交范围 → 开发计划（哪些预期 + 哪些源码位置）
                         （schema_dev_task_input → schema_dev_plan）
  2. `dev_code`        —— 在隔离工作区里按方案改代码、提交
                         （schema_dev_plan → schema_code_change）
  3. `dev_build`       —— 编译 + 仓库自带测试套件 + 构建两遍比摘要
                         （schema_code_change → schema_build_check）
  4. `build_packager`  —— 打包成制品 + 给出评审覆盖 / 可追溯判定
                         （schema_build_check → schema_build_artifact）

**为什么是四跳而不是一跳**：每一跳的答案来自**不同的外部神谕**——
模型（改动）、编译器（编译）、测试套件（单测）、git（评审覆盖与可追溯）。
把它们压成一跳，就没法说清「红在哪一步」。

beeline 跟盒子是 **1:1 且不可分割** 的：它引用的 schema id 全部来自本盒的
`schemas.py`，每个 op 的 handler 都在本盒的 `runner.py` 里。所以它住在盒子
里面，而不是仓库顶层一个单独的目录——顶层目录会让人以为存在一种
「可复用的跨盒作业路线」，而实际上一条都没有。
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
        "WMS Development Box 履约作业路线："
        "dev_intake → dev_code → dev_build → build_packager"
    ),

    operations=[
        Operation(
            op_id="dev_intake",
            type="dev_intake",
            input_from="external",
            input="schema_dev_task_input",
            output="schema_dev_plan",
            next=[NextRef(op_id="dev_code")],
            bee=Bee(type="dev_intake_handler"),
            idempotency=IDEMPOTENT_BY_TASK,
        ),

        Operation(
            op_id="dev_code",
            type="dev_code",
            input_from="dev_intake",
            input="schema_dev_plan",
            output="schema_code_change",
            next=[NextRef(op_id="dev_build")],
            bee=Bee(type="dev_code_handler"),
            idempotency=IDEMPOTENT_BY_TASK,
        ),

        Operation(
            op_id="dev_build",
            type="dev_build",
            input_from="dev_code",
            input="schema_code_change",
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
