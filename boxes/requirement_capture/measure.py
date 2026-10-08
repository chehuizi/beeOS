"""
boxes.requirement_capture.measure - 需求捕获盒的实测指标采集

definition.metrics 里写的是 target（目标）。目标是承诺，不是事实。
这一层负责从履约历史里把 actual 算出来，让看板显示的每个数字都有出处。

每个算法只吃两类通用信号，runtime 不需要知道任何业务语义：
- record.observed —— 本次履约实测到的判据原值（哪条判据、多少）
- record.error_kind —— 中止归因（是不是 LLM 调用挂的）

**已删除的指标**（曾经声明过，但没有任何信号源能算出它）：
manual_rewrite_rate「人工改动 requirement_type 的比例」
  —— 看板上没有「人工改写」这个动作，产出也整份不落盘，
  事后无从判断某条需求是被改过的。声明一个算不出来的指标，
  等于在看板上摆一个永远填不满的洞，所以从 definition 里拿掉。
  这条业务语义没丢：它记在 type_boundary 盲区里（见 definition.py 顶部），
  等真有了人工改写事件再声明回来。

窗口内的样本会被显式计数：actual 是 None 表示「还没测过」，
它和 0.0 在看板上长得不一样——0.0 是差，None 是没有。
"""

from __future__ import annotations

from typing import Any

import core.metrics as core_metrics
from core.metrics import Computer, Measurement


def _observed(records: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    """带某个判据实测值的履约记录（中止的 run 没有 observed，自然被排除）"""
    return [r for r in records if key in (r.get("observed") or {})]


def _extraction_precision(records: list[dict[str, Any]]) -> Measurement:
    """抽出的需求里没被判定为幻觉的比例"""
    judged = _observed(records, "no_hallucination")
    return core_metrics.ratio_pct(judged, lambda r: r["observed"]["no_hallucination"] is True)


def _extraction_recall(records: list[dict[str, Any]]) -> Measurement:
    """原文事实被抽取覆盖的比例（source_coverage 均值）"""
    judged = _observed(records, "source_coverage")
    return core_metrics.mean([r["observed"]["source_coverage"] for r in judged])


def _first_pass_capture_rate(records: list[dict[str, Any]]) -> Measurement:
    """首次抽取即通过全部 acceptance 的比例"""
    judged = [r for r in records if r.get("acceptance_status")]
    return core_metrics.ratio_pct(judged, lambda r: r["acceptance_status"] == "accepted")


def _llm_call_failure_rate(records: list[dict[str, Any]]) -> Measurement:
    """LLM 调用失败导致履约中止的比例

    分母是全部履约（含中止的）——分母只算跑完的那些，
    会把「挂了」从失败率里洗掉，那是自欺。
    """
    return core_metrics.ratio_pct(records, lambda r: r.get("error_kind") == "llm_call")


def _capture_latency(records: list[dict[str, Any]]) -> Measurement:
    """抽取端到端时长（秒）"""
    done = [r for r in records if r.get("status") == "completed" and r.get("duration_ms")]
    return core_metrics.mean([r["duration_ms"] / 1000.0 for r in done])


COMPUTERS: dict[str, Computer] = {
    "extraction_precision": _extraction_precision,
    "extraction_recall": _extraction_recall,
    "first_pass_capture_rate": _first_pass_capture_rate,
    "llm_call_failure_rate": _llm_call_failure_rate,
    "capture_latency": _capture_latency,
}