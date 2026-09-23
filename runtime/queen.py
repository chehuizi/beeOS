"""
runtime.queen - Queen 决策 hook（bounded autonomy）

按 [beebox-design.md §3.4.7] 实现 Queen 自治边界：
- Queen 可改：并发 / procedure 选择 / retry / route / escalate / pause
- Queen 不可改：contract / acceptance / release / policy

PoC 2 范围：
- 极简 queen hook（escalation decision only）
- 按 queen.rules.exception_handling.escalation_rules 决策
"""

from __future__ import annotations

from typing import Any

from runtime.models import TaskRun


def default_queen_escalation_handler(
    task_run: TaskRun,
    failed_rules: list[dict[str, Any]],
    queen_rules: dict[str, Any] | None = None,
) -> str:
    """Queen escalation 默认决策

    Args:
        task_run: 已 COMPLETED 的 task run
        failed_rules: acceptance 失败的规则列表
        queen_rules: beeBox definition.queen.rules（可选）

    Returns:
        queen 决策字符串（human_review / auto_resolve / observe_only）

    PoC 决策逻辑：
    - 有 refund 失败 → human_review
    - 其他失败 → auto_resolve（默认）
    """
    if not queen_rules:
        return "auto_resolve"

    escalation_rules = queen_rules.get("exception_handling", {}).get("escalation_rules", [])

    # 检查是否触发升级规则
    result = task_run.result or {}
    for rule in escalation_rules:
        trigger = rule.get("trigger", "")
        action = rule.get("action", "")

        if "refund_amount >" in trigger:
            threshold = float(trigger.split(">")[-1].strip())
            if (result.get("refund_amount") or 0) > threshold:
                return f"queen.{action}"

    return "auto_resolve"