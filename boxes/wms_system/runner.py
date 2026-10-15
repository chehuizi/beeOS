"""boxes.wms_system.runner - WMS System Box 的 3 个 operation 实现

WMS 系统描述盒——**描述真实在跑的那套 WMS**：它有哪些服务、对外暴露什么接口、每个组件部署在什么版本、依赖哪些外部系统。

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


def system_intake_handler(input_data: dict[str, Any]) -> dict[str, Any]:
    """op 1：接收系统快照（组件 / 接口 / 部署 / 版本）

    骨架：不返回任何判据 metric
    """
    return {"skeleton": True, "box": "wms_system_box"}

def system_verify_handler(input_data: dict[str, Any]) -> dict[str, Any]:
    """op 2：验组件齐不齐、接口有没有契约、版本能不能追到真实部署

    骨架：连「该验什么」都还没实现，所以这里没有检查
    """
    return {"skeleton": True}

def system_profile_packager_handler(input_data: dict[str, Any]) -> dict[str, Any]:
    """op 3：打包成系统档案

    骨架：判据要的 metric 一个都不给
    """
    return {
        "package_id": "skeleton_wms_system",
        "box_id": "wms_system_box",
        "maturity": "skeleton",
        # 判据要的 metric 一个都不给——给了就是替没实现的检查点打勾。
        "evidence": {},
    }


WMS_SYSTEM_HANDLERS: dict[str, Any] = {
    "system_intake": system_intake_handler,
    "system_verify": system_verify_handler,
    "system_profile_packager": system_profile_packager_handler,
}

RUNNER = HandlerRunner(WMS_SYSTEM_HANDLERS, owner="wms_system_box")
