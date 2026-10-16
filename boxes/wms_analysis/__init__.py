"""boxes.wms_analysis - WMS Analysis Box

WMS 线上效果分析盒——**上线之后回头验需求**：拿真实运行数据，跟当初那条
需求声明的预期值逐项比。落差直接变成下一轮需求的种子。

**成熟度：skeleton（骨架）**。判据已声明、腿还没实现——所以这只盒子
现在跑出来一定是红的。看板上标着「未实现」，且不计入验收率。

骨架盒的做法是**反过来**的：真声明判据（它将来要验的东西），
产出**不带**这些 metric → `evaluate_acceptance` 判 `None != True`
→ 全红 → REJECTED。

**为什么不能空声明判据**：`evaluate_acceptance` 里是
`if not failed: ACCEPTED`。零条判据的盒子会被判成「全过」——
那就是「看起来成功，其实什么也没验」的谎。

**它跟别的 WMS 盒不一样的地方**：闭环口。ops ─交接→ 本盒 ─反馈→ 需求盒。
反馈边声明在 `placement.feedback_into`（不是 `feeds_into`），
因为需求盒不会去验分析结论——分析结论是拿去**开新一轮需求**的。
分开声明还有个副作用是好的：反馈边不进拓扑排序，
所以「需求 → … → 运维 → 分析 → 需求」这个环不会让主干排序成环。
"""
from __future__ import annotations

from boxes.wms_analysis.definition import WMS_ANALYSIS_BOX, get_definition
from boxes.wms_analysis.runner import RUNNER, WMS_ANALYSIS_HANDLERS

__all__ = ["WMS_ANALYSIS_BOX", "get_definition", "RUNNER", "WMS_ANALYSIS_HANDLERS"]
