"""boxes.wms_ops.runner - WMS Ops Box 的 3 个 operation 实现

WMS 运维盒——管**上线之后**的事：变更审批、发布执行、故障处置、回滚能力、以及把线上真实状态回写进系统档案。

**这些是桩，而且刻意是「不产出判据要读的 metric」的桩。**

不是偷懒：`evaluate_acceptance` 只在值是 `"n/a"` 时才判「用不上」，
缺 key 一律判红。所以骨架盒交不出东西 → 全红 → REJECTED →
看板上显式写「未实现」。

写成「全部返回 True」的话，这只盒子会显示成 4/4 全过——
那是这个项目里最坏的一种输出：**看起来成功，其实什么都没验。**
"""

from __future__ import annotations

from typing import Any

from runtime.runner import HandlerRunner


def ops_intake_handler(input_data: dict[str, Any]) -> dict[str, Any]:
    """op 1：接收运维任务（变更 / 告警 / 回滚）

    骨架：不返回任何判据 metric
    """
    return {"skeleton": True, "box": "wms_ops_box"}

def ops_handle_handler(input_data: dict[str, Any]) -> dict[str, Any]:
    """op 2：执行处置 + 回写线上真实状态

    骨架：连「该验什么」都还没实现，所以这里没有检查
    """
    return {"skeleton": True}

def ops_packager_handler(input_data: dict[str, Any]) -> dict[str, Any]:
    """op 3：打包成运维记录

    骨架：判据要的 metric 一个都不给
    """
    return {
        "package_id": "skeleton_wms_ops",
        "box_id": "wms_ops_box",
        "maturity": "skeleton",
        # 判据要的 metric 一个都不给——给了就是替没实现的检查点打勾。
        "evidence": {},
    }


WMS_OPS_HANDLERS: dict[str, Any] = {
    "ops_intake": ops_intake_handler,
    "ops_handle": ops_handle_handler,
    "ops_packager": ops_packager_handler,
}

RUNNER = HandlerRunner(WMS_OPS_HANDLERS, owner="wms_ops_box")
