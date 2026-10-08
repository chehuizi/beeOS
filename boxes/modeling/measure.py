"""
boxes.modeling.measure - 业务建模盒的实测指标采集

和捕获盒一样：definition.metrics 里的 target 是承诺，
这一层负责把 actual 从履约历史里算出来。

**已删除的指标**（声明过，但当前没有信号源能算）：
- model_reuse_rate「新需求复用已有 model 元素的比例」
  —— 复用比例要逐个 model 元素比对，产出整份不落盘，事后算不出来。
    真要测就得先落「本次复用了哪些元素 id」，那是另一件事。
- cost_per_modeling_request「单次建模请求成本」
  —— 没有成本记账。runtime 不记 token，盒子里也没接。
    要真做得先在 runtime 侧把 token 用量落进 observed，那是基础设施改动，
    不是画一个指标名就能拿到的数字。
- human_escalation_rate「升级人工率」
  —— ExceptionRule 里声明了 escalate 动作，但状态机里没有 escalate 态，
    没有任何路径能产出这个事件。分子恒为 0 会显示成「人工从不介入」，
    比不显示更糟。
"""

from __future__ import annotations

from typing import Any

import core.metrics as core_metrics
from core.metrics import Computer, Measurement


def _observed(records: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    return [r for r in records if key in (r.get("observed") or {})]


def _first_pass_acceptance_rate(records: list[dict[str, Any]]) -> Measurement:
    """首次建模即通过 acceptance 的比例"""
    judged = [r for r in records if r.get("acceptance_status")]
    return core_metrics.ratio_pct(judged, lambda r: r["acceptance_status"] == "accepted")


def _requirement_coverage_quality(records: list[dict[str, Any]]) -> Measurement:
    """requirement_coverage 打满 100 的比例"""
    judged = _observed(records, "requirement_coverage")
    return core_metrics.ratio_pct(judged, lambda r: r["observed"]["requirement_coverage"] >= 100.0)


def _durations(records: list[dict[str, Any]]) -> list[float]:
    done = [r for r in records if r.get("status") == "completed" and r.get("duration_ms")]
    return [r["duration_ms"] / 1000.0 for r in done]


def _modeling_turnaround_time(records: list[dict[str, Any]]) -> Measurement:
    """从投料到产出模型的时长（秒，均值）"""
    return core_metrics.mean(_durations(records))


def _p95_modeling_turnaround_time(records: list[dict[str, Any]]) -> Measurement:
    """95 分位建模时长（秒）"""
    return core_metrics.percentile(_durations(records), 0.95)


COMPUTERS: dict[str, Computer] = {
    "first_pass_acceptance_rate": _first_pass_acceptance_rate,
    "requirement_coverage_quality": _requirement_coverage_quality,
    "modeling_turnaround_time": _modeling_turnaround_time,
    "p95_modeling_turnaround_time": _p95_modeling_turnaround_time,
}