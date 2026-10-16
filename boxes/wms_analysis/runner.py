"""boxes.wms_analysis.runner - WMS Analysis Box 的 3 个 operation 实现

WMS 线上效果分析盒——拿真实运行数据回头看：当初那条需求，线上到底满足了没有。

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


def analysis_intake_handler(input_data: dict[str, Any]) -> dict[str, Any]:
    """op 1：取投料（需求预期 + ops 回写的线上实测）

    骨架：不返回任何判据 metric
    """
    return {"skeleton": True, "box": "wms_analysis_box"}


def analysis_compare_handler(input_data: dict[str, Any]) -> dict[str, Any]:
    """op 2：逐项比对预期与实测，标出落差

    骨架：连「该比哪几项」都还没实现
    """
    return {"skeleton": True}


def analysis_packager_handler(input_data: dict[str, Any]) -> dict[str, Any]:
    """op 3：打包成效果分析记录 + 下一轮需求种子

    骨架：判据要的 metric 一个都不给
    """
    return {
        "package_id": "skeleton_wms_analysis",
        "box_id": "wms_analysis_box",
        "maturity": "skeleton",
        # 判据要的 metric 一个都不给——给了就是替没实现的检查点打勾。
        "evidence": {},
    }


WMS_ANALYSIS_HANDLERS: dict[str, Any] = {
    "analysis_intake": analysis_intake_handler,
    "analysis_compare": analysis_compare_handler,
    "analysis_packager": analysis_packager_handler,
}

RUNNER = HandlerRunner(WMS_ANALYSIS_HANDLERS, owner="wms_analysis_box")
