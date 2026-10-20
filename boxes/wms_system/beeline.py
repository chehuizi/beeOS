"""boxes.wms_system - WMS System Box 的作业路线

WMS 系统档案盒——把「真实在跑的那套 WMS 是什么样」写成一份带出处的档案。

op 链（v2）：
  1. `system_intake`            挂基线、解析观测目标（投料/环境/基线三级取址）
  2. `system_probe`             真发 HTTP：探活 + 逐条探只读端点
  3. `system_profile_packager`  装配档案，标注每条维度的来源与状态

## 为什么是 v2 而不是改 v1

v1 的路线是 `system_intake → system_verify → system_profile_packager`，
其中 `system_verify` 是一个**没有实现**的桩——它不检查任何东西。

这不是「同一个 op 的实现变好了」，是**这一步的职责变了**：
v1 想做的是「验组件齐不齐、接口有没有契约、版本能不能追到真实部署」，
那些校验大部分需要跨盒产出或运行时清单，今天做不了；
v2 做的是「真的去线上探，探不到的显式标成拿不到」。

路线变了就该换版本号。保留一条从未产出过结果的 v1 等于给后来的人
留一个「v1 也能跑」的错觉。v1 的声明意图记在下面，不留代码。

## 中间 schema 的取舍

v1 的 `system_verify` 名不副实——它既不 verify 也不 observe。
v2 把它换成 `system_probe`，输出 schema 也从 `schema_system_verification`
（只有三个计数，看不出验了什么）换成 `schema_system_observation`
（每条端点带 reached / status_code / envelope_ok / error）。

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


WMS_SYSTEM_BEELINE = Beeline(
    id="beeline_wms_system_v2",
    version=2,
    description=(
        "WMS System Box 履约作业路线：system_intake → system_probe → system_profile_packager"
    ),

    operations=[
        Operation(
            op_id="system_intake",
            type="system_intake",
            input_from="external",
            input="schema_system_probe_input",
            output="schema_system_probe_plan",
            next=[NextRef(op_id="system_probe")],
            bee=Bee(type="system_intake_handler"),
            idempotency=IDEMPOTENT_BY_TASK,
        ),

        Operation(
            op_id="system_probe",
            type="system_probe",
            input_from="system_intake",
            input="schema_system_probe_plan",
            output="schema_system_observation",
            next=[NextRef(op_id="system_profile_packager")],
            bee=Bee(type="system_probe_handler"),
            idempotency=IDEMPOTENT_BY_TASK,
        ),

        Operation(
            op_id="system_profile_packager",
            type="system_profile_packager",
            input_from="system_probe",
            input="schema_system_observation",
            output="schema_system_profile",
            next=[],
            bee=Bee(type="system_profile_packager_handler"),
            idempotency=IDEMPOTENT_BY_TASK,
        ),
    ],
)


def get_beeline() -> Beeline:
    return WMS_SYSTEM_BEELINE
