"""boxes.wms_ops - WMS Ops Box

WMS 运维盒——管**上线之后**的事：变更审批、发布执行、故障处置、回滚能力、
以及把线上真实状态回写进系统档案。

**成熟度：live**。这条腿是真的：
- 它**接上上游**：测试盒的验收报告（验过没有）+ 开发盒的制品（放哪一版）
- 它**读生产**（只读 GET）——前面几只盒子都在说「应该是什么样」，
  它说「现在实际是什么样」
- 5 条判据的答案没有一条来自自述：审批看上游验收报告、回滚看 git/制品库、
  处置看告警条数对不对得上、线上状态看生产实例答不答话

**没读到不许算读到了**：生产连不上 → `runtime_state_recorded` 判红，
理由写清是连不上还是没配地址——不是产出一份空档案说「已回写」。

**「用不上」和「通过」分开**：没有变更就没有审批率、没有告警就没有处置
记录——那些判据的取值是 "n/a"，看板单独列，不混进通过率。
"""
from __future__ import annotations

from boxes.wms_ops.definition import WMS_OPS_BOX, get_definition
from boxes.wms_ops.runner import RUNNER, WMS_OPS_HANDLERS
from boxes.wms_ops.beeline import WMS_OPS_BEELINE, get_beeline

__all__ = [
    "WMS_OPS_BOX", "get_definition", "RUNNER", "WMS_OPS_HANDLERS",
    "WMS_OPS_BEELINE", "get_beeline",
]
