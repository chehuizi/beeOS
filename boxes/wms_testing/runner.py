"""boxes.wms_testing.runner - WMS Testing Box 的 3 个 operation 实现

WMS 测试盒——拿开发盒的制品，用**WMS 业务场景**验它：收货上架、拣货复核、盘点、批次效期这些真实单据跑一遍。

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


def test_intake_handler(input_data: dict[str, Any]) -> dict[str, Any]:
    """op 1：接收测试任务（制品 + 需求 + 场景）

    骨架：不返回任何判据 metric
    """
    return {"skeleton": True, "box": "wms_testing_box"}

def test_execute_handler(input_data: dict[str, Any]) -> dict[str, Any]:
    """op 2：跑场景 + 统计覆盖率与缺陷

    骨架：连「该验什么」都还没实现，所以这里没有检查
    """
    return {"skeleton": True}

def test_packager_handler(input_data: dict[str, Any]) -> dict[str, Any]:
    """op 3：打包成测试报告

    骨架：判据要的 metric 一个都不给
    """
    return {
        "package_id": "skeleton_wms_testing",
        "box_id": "wms_testing_box",
        "maturity": "skeleton",
        # 判据要的 metric 一个都不给——给了就是替没实现的检查点打勾。
        "evidence": {},
    }


WMS_TESTING_HANDLERS: dict[str, Any] = {
    "test_intake": test_intake_handler,
    "test_execute": test_execute_handler,
    "test_packager": test_packager_handler,
}

RUNNER = HandlerRunner(WMS_TESTING_HANDLERS, owner="wms_testing_box")
