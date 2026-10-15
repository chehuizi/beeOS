"""boxes.wms_ops - WMS Ops Box

WMS 运维盒——管**上线之后**的事：变更审批、发布执行、故障处置、回滚能力、以及把线上真实状态回写进系统档案。

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

from boxes.wms_ops.definition import WMS_OPS_BOX, get_definition
from boxes.wms_ops.runner import RUNNER, WMS_OPS_HANDLERS

__all__ = ["WMS_OPS_BOX", "get_definition", "RUNNER", "WMS_OPS_HANDLERS"]
