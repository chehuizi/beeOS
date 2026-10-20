"""boxes.wms_testing - WMS Testing Box 的作业路线

WMS 测试盒——拿开发盒的制品，用**WMS 业务场景**验它：收货上架、拣货复核、盘点、批次效期这些真实单据跑一遍。

op 链：
  1. `test_intake` — 接收测试任务（制品 + 需求 + 场景）（schema_test_task_input → schema_test_execution）
  2. `test_execute` — 跑场景 + 统计覆盖率与缺陷（schema_test_execution → schema_test_verdict）
  3. `test_packager` — 打包成测试报告（schema_test_verdict → schema_test_report）

**这三个 op 现在还没有实现**（runner 里是桩）。beeline 是声明：它先把
要走的路线和每一步的数据形状钉住，腿才跟着路走。

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


WMS_TESTING_BEELINE = Beeline(
    id="beeline_wms_testing_v1",
    version=1,
    description=(
        "WMS Testing Box 履约作业路线：test_intake → test_execute → test_packager"
    ),

    operations=[
        Operation(
            op_id="test_intake",
            type="test_intake",
            input_from="external",
            input="schema_test_task_input",
            output="schema_test_execution",
            next=[NextRef(op_id="test_execute")],
            bee=Bee(type="test_intake_handler"),
            idempotency=IDEMPOTENT_BY_TASK,
        ),

        Operation(
            op_id="test_execute",
            type="test_execute",
            input_from="test_intake",
            input="schema_test_execution",
            output="schema_test_verdict",
            next=[NextRef(op_id="test_packager")],
            bee=Bee(type="test_execute_handler"),
            idempotency=IDEMPOTENT_BY_TASK,
        ),

        Operation(
            op_id="test_packager",
            type="test_packager",
            input_from="test_execute",
            input="schema_test_verdict",
            output="schema_test_report",
            next=[],
            bee=Bee(type="test_packager_handler"),
            idempotency=IDEMPOTENT_BY_TASK,
        )
    ],
)


def get_beeline() -> Beeline:
    return WMS_TESTING_BEELINE
