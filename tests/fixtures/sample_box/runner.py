"""tests.fixtures.sample_box.runner - 夹具盒的 op 实现

**执行是假的，形状是真的**：不调任何外部系统，返回确定性结果，
但每个 op 的输出字段都按 `schemas.py` 声明的形状来——
运行时只认形状，不认业务。
"""

from __future__ import annotations

from typing import Any

from runtime.runner import HandlerRunner


def _sample_classify(input_data: dict[str, Any]) -> dict[str, Any]:
    return {
        "sample_id": input_data.get("sample_id", "unknown"),
        "kind": input_data.get("kind", "generic"),
        "amount": input_data.get("amount", 0),
    }


def _sample_resolve(input_data: dict[str, Any]) -> dict[str, Any]:
    return {
        "sample_id": input_data.get("sample_id", "unknown"),
        "sample_state": "resolved",
        "notified": False,
        "steps": ["resolve"],
    }


def _sample_notify(input_data: dict[str, Any]) -> dict[str, Any]:
    return {
        "sample_id": input_data.get("sample_id", "unknown"),
        "sample_state": input_data.get("sample_state", "resolved"),
        "notified": True,
        "steps": list(input_data.get("steps", [])) + ["notify"],
    }


def _sample_escalate(input_data: dict[str, Any]) -> dict[str, Any]:
    return {
        "sample_id": input_data.get("sample_id", "unknown"),
        "sample_state": "escalated",
        "notified": False,
        "steps": list(input_data.get("steps", [])) + ["escalate"],
    }


SAMPLE_HANDLERS: dict[str, Any] = {
    "sample_classify": _sample_classify,
    "sample_resolve": _sample_resolve,
    "sample_notify": _sample_notify,
    "sample_escalate": _sample_escalate,
}

RUNNER = HandlerRunner(SAMPLE_HANDLERS, owner="sample_box")
