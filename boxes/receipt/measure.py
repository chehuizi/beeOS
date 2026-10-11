"""
boxes.receipt.measure - 小票识别盒的实测指标采集

三件事都不做假：
- 没跑过 → actual 是 None（不是 0），看板上「未测」和「0%」长得不一样
- LLM 挂掉导致履约中止的 run **计入分母**（漏掉它会把失败率洗成 0）
- 只有跑完并判定的 run 才有判据类样本
"""

from __future__ import annotations

from typing import Any

import core.metrics as core_metrics
from core.metrics import Computer, Measurement
from core.models import is_not_applicable


def _finished(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """跑完并进入验收判定的 run（中止的没有判据值）"""
    return [r for r in records if r.get("acceptance_status")]


def _rate(records: list[dict[str, Any]], metric: str) -> Measurement:
    """判据为 True 的比例

    **n/a 的 run 分子分母都不算。** 它不是「一致」也不是「不一致」，
    是「这张票压根没用上这条判据」——把它算进分母，等于把「用不上」
    记成「做错了」；把它算进分子，等于把「没验」记成「验过了」。
    两种都是假信号。真实一致率只由「验了而且过了」的 run 贡献。
    """
    judged = [
        r for r in _finished(records)
        if metric in (r.get("observed") or {})
        and not is_not_applicable(r["observed"].get(metric))
    ]
    return core_metrics.ratio_pct(
        judged, lambda r: r["observed"][metric] is True
    )


def _first_pass_receipt_rate(records: list[dict[str, Any]]) -> Measurement:
    """首次识别即过全部判据的比例"""
    judged = [r for r in records if r.get("acceptance_status")]
    return core_metrics.ratio_pct(judged, lambda r: r["acceptance_status"] == "accepted")


def _arithmetic_consistency_rate(records: list[dict[str, Any]]) -> Measurement:
    """算术自洽率（整体口径）"""
    return _rate(records, "totals_match")


def _field_completeness_rate(records: list[dict[str, Any]]) -> Measurement:
    return _rate(records, "field_completeness")


def _receipt_latency(records: list[dict[str, Any]]) -> Measurement:
    done = [r for r in records if r.get("status") == "completed" and r.get("duration_ms")]
    return core_metrics.mean([r["duration_ms"] / 1000.0 for r in done])


COMPUTERS: dict[str, Computer] = {
    "first_pass_receipt_rate": _first_pass_receipt_rate,
    "arithmetic_consistency_rate": _arithmetic_consistency_rate,
    "field_completeness_rate": _field_completeness_rate,
    "receipt_latency": _receipt_latency,
}