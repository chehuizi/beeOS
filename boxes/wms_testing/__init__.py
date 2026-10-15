"""boxes.wms_testing - WMS Testing Box

WMS 测试盒——拿开发盒的制品，用**WMS 业务场景**验它：收货上架、拣货复核、盘点、批次效期这些真实单据跑一遍。

**成熟度：skeleton（骨架）**。判据已声明、腿还没实现——所以这只盒子
现在跑出来一定是红的。看板上标着「未实现」，且不计入验收率。

骨架盒的做法是**反过来**的：真声明判据（它将来要验的东西），
产出**不带**这些 metric → `evaluate_acceptance` 判 `None != True`
→ 全红 → REJECTED。

**为什么不能空声明判据**：`evaluate_acceptance` 里是
`if not failed: ACCEPTED`。零条判据的盒子会被判成「全过」——
那就是「看起来成功，其实什么也没验」的谎。
"""
from __future__ import annotations

from boxes.wms_testing.definition import WMS_TESTING_BOX, get_definition
from boxes.wms_testing.runner import RUNNER, WMS_TESTING_HANDLERS

__all__ = ["WMS_TESTING_BOX", "get_definition", "RUNNER", "WMS_TESTING_HANDLERS"]
