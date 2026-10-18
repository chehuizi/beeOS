"""
boxes.wms_system.measure - WMS System Box 的实测指标采集

definition.metrics 里写的是 target（目标）。目标是承诺，不是事实。
这一层负责从履约历史里把 actual 算出来，让看板显示的每个数字都有出处。

## 每个指标都从 acceptance 的实测值推出

`record.observed` 只搬运 definition 自己声明的判据名
（见 kanban/trigger.py 的 `observed = {...}`）——
不是任意字段都能进指标。所以这 4 个算法只吃两样东西：

- record.observed[<判据名>]  —— 这次真的探到的数
- record.acceptance_status   —— 这次判定的结论
- record.duration_ms         —— 这次履约的耗时

**不能读 result.aspects**：产出整份落盘了（23cc4a3 起），
但 observed 里没有它。想按「unverifiable 维度数」做指标，
得先把那个数变成一条判据——那才是它该待的地方。

## 没声明的指标

`unverifiable_aspect_count` 这类「缺口有多少」的数**故意不声明**。
它现在恒等于 2（部署版本 + 外部依赖），是个常数；
常数当指标，等于在看板上放一个永远不动的数字冒充监控。
它已经作为 `unverifiable_aspect_count` 落在每次产出里，
要看就去读产出——那是事实，不是趋势。
"""

from __future__ import annotations

from typing import Any

import core.metrics as core_metrics
from core.metrics import Computer, Measurement


def _observed(records: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    """带某个判据实测值的履约记录（中止的 run 没有 observed，自然被排除）"""
    return [r for r in records if key in (r.get("observed") or {})]


def _health_endpoint_up_rate(records: list[dict[str, Any]]) -> Measurement:
    """健康端点探通的履约占比"""
    probed = _observed(records, "health_endpoint_reachable")
    return core_metrics.ratio_pct(
        probed, lambda r: r["observed"]["health_endpoint_reachable"] is True
    )


def _readonly_probe_answer_rate(records: list[dict[str, Any]]) -> Measurement:
    """基线只读端点的平均应答率"""
    probed = _observed(records, "readonly_probe_answer_rate")
    return core_metrics.mean([r["observed"]["readonly_probe_answer_rate"] for r in probed])


def _first_pass_profile_rate(records: list[dict[str, Any]]) -> Measurement:
    """首次观测即通过全部 acceptance 的比例"""
    judged = [r for r in records if r.get("acceptance_status")]
    return core_metrics.ratio_pct(
        judged, lambda r: r["acceptance_status"] == "accepted"
    )


def _system_probe_latency(records: list[dict[str, Any]]) -> Measurement:
    """一次系统观测的端到端时长（秒）"""
    done = [r for r in records if r.get("status") == "completed" and r.get("duration_ms")]
    return core_metrics.mean([r["duration_ms"] / 1000.0 for r in done])


COMPUTERS: dict[str, Computer] = {
    "health_endpoint_up_rate": _health_endpoint_up_rate,
    "readonly_probe_answer_rate": _readonly_probe_answer_rate,
    "first_pass_profile_rate": _first_pass_profile_rate,
    "system_probe_latency": _system_probe_latency,
}
