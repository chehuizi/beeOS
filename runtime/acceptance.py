"""
runtime.acceptance - Acceptance 阶段评估器

按 [beebox-design.md §3.4.6] 实现：
- task run COMPLETED → AWAITING_ACCEPTANCE
- 按 definition.result.acceptance 逐条判定 result 数据
- 全通过 → ACCEPTED
- 任一失败 → 按 contract.exceptions 决定：no_deliver / rollback / escalate
- rollback → COMPENSATING_ACCEPTANCE（执行反向 op 后回 AWAITING_ACCEPTANCE）
- escalate → 触发 queen 接管

本 PoC：
- 实现 ACCEPTED / REJECTED / COMPENSATING_ACCEPTANCE 三种判定
- escalate 走 queen hook（runtime/queen.py）
- rollback 暂不实现反向 op（标记 COMPENSATING_ACCEPTANCE，回 AWAITING_ACCEPTANCE）
"""

from __future__ import annotations

from typing import Any

from core.models import AcceptanceRule, ExceptionRule, ResultDef
from runtime.models import (
    AcceptanceEvaluation,
    AcceptanceStatus,
    RejectionClass,
    TaskRun,
)


# ============================================================
# 单条 acceptance rule 评估
# ============================================================


def _evaluate_rule(rule: AcceptanceRule, result: dict[str, Any]) -> bool:
    """评估单条 acceptance rule

    Args:
        rule: AcceptanceRule（metric + op + value）
        result: task run 业务结果 dict

    Returns:
        True = pass, False = fail
    """
    actual = result.get(rule.metric)

    if rule.op == "eq":
        return actual == rule.value
    if rule.op == "gte":
        return actual is not None and actual >= rule.value
    if rule.op == "lte":
        return actual is not None and actual <= rule.value
    if rule.op == "in":
        return actual in rule.value
    if rule.op == "match":
        # 简单 substring 匹配
        return actual is not None and rule.value in str(actual)

    raise ValueError(f"unknown op: {rule.op}")


def _find_exception_rule(
    exceptions: list[ExceptionRule],
    result: dict[str, Any],
) -> ExceptionRule | None:
    """按 result 数据找触发的 exception 规则（第一条匹配）"""
    for rule in exceptions:
        # PoC 简化：condition 是 "field op value" 形式（如 "refund_amount > 500"）
        # 更复杂的用真实 expression engine
        if _evaluate_condition(rule.condition, result):
            return rule
    return None


def _evaluate_condition(condition: str, result: dict[str, Any]) -> bool:
    """极简 condition 评估（PoC 用）——支持：
    - "refund_amount > 500"
    - "customer_tier == enterprise AND severity == critical"
    """
    cond = condition.strip()
    # AND 分割
    if " AND " in cond:
        return all(_evaluate_condition(p, result) for p in cond.split(" AND "))
    # 单条件：field op value
    for op, op_fn in (
        (">=", lambda a, b: a >= b),
        ("<=", lambda a, b: a <= b),
        ("==", lambda a, b: a == b),
        ("!=", lambda a, b: a != b),
        (">", lambda a, b: a > b),
        ("<", lambda a, b: a < b),
    ):
        if op in cond:
            field, value = cond.split(op, 1)
            field = field.strip()
            value = value.strip()
            actual = result.get(field)
            # 尝试转换 value
            try:
                if value in ("true", "false"):
                    value = value == "true"
                else:
                    value = float(value) if "." in value else int(value)
            except (ValueError, TypeError):
                value = value.strip('"').strip("'")
            if actual is None:
                return False
            return op_fn(actual, value)
    return False


def _classify_failure(
    failed: list[dict[str, Any]],
    result: dict[str, Any],
) -> RejectionClass:
    """给 REJECTED 归因——区分"投料没东西" / "模型没覆盖" / "规则打架" / 其余结构问题

    判据（按优先级，先看是否根本没东西可建）：
    1. 所有失败判据的 actual 都是 None——评测器一个值都没取到，
       说明上游没产出这些量（典型：0 条需求 → 没有任何模型元素 → 没有覆盖率）
    2. requirement_count == 0——明确没需求
    3. rule_consistency 判定为 false——规则自相矛盾（优先级高于覆盖率，
       因为规则矛盾时先修规则，补覆盖率没有意义）
    4. 有覆盖率判据失败且取到了具体数值——没覆盖全（判据名含 coverage 即可，
       不同盒子叫法不同：requirement_coverage / source_coverage）
    5. 其余（引用 / 结构 / 指标）
    """
    # 1 + 2：什么都没取到 / 明确没需求
    if result.get("requirement_count") == 0:
        return RejectionClass.EMPTY_INPUT
    if failed and all(f.get("actual") is None for f in failed):
        return RejectionClass.EMPTY_INPUT

    metrics_failed = {f.get("metric") for f in failed}

    # 3：规则矛盾优先归因
    if "rule_consistency" in metrics_failed and result.get("rule_consistency") is False:
        return RejectionClass.CONFLICT

    # 4：覆盖率有值但不够。
    #    按名字找 coverage 判据，不写死某一个：建模盒叫 requirement_coverage，
    #    捕获盒叫 source_coverage。写死一个名字 = 别的盒子全部落进 STRUCTURAL，
    #    归因错了比不归因更坏——人会被引去改错的东西。
    for metric in sorted(metrics_failed):
        if metric and "coverage" in metric and result.get(metric) is not None:
            return RejectionClass.INSUFFICIENT_COVERAGE

    # 5：其余结构性判据
    return RejectionClass.STRUCTURAL


# ============================================================
# Acceptance 评估器
# ============================================================


def evaluate_acceptance(
    task_run: TaskRun,
    contract: ResultDef,
    queen_decision_fn=None,
) -> AcceptanceEvaluation:
    """评估 task run 的 acceptance

    Args:
        task_run: 已 COMPLETED 的 task run（必须 status=COMPLETED）
        contract: beeBox definition.result（含 acceptance + exceptions）
        queen_decision_fn: queen 决策函数（escalate 时调用，可选）

    Returns:
        AcceptanceEvaluation（含 status + 通过/失败规则 + 触发的 exception action）

    状态转移：
    - 全 pass → ACCEPTED
    - 有 fail + exception=rollback → COMPENSATING_ACCEPTANCE（PoC 不执行反向 op，直接回 AWAITING）
    - 有 fail + exception=escalate → REJECTED（queen 决策）
    - 有 fail + exception=no_deliver → REJECTED
    - 有 fail 无 exception → REJECTED（默认）
    """
    if task_run.status.value != "completed":
        raise ValueError(
            f"acceptance requires task_run.status=completed, got {task_run.status.value}"
        )

    result = task_run.result or {}
    passed: list[str] = []
    failed: list[dict[str, Any]] = []

    for rule in contract.acceptance:
        try:
            ok = _evaluate_rule(rule, result)
        except Exception as e:
            ok = False
            failed.append({"rule": rule.metric, "error": str(e)})
            continue

        if ok:
            passed.append(rule.metric)
        else:
            failed.append(
                {"metric": rule.metric, "op": rule.op, "expected": rule.value, "actual": result.get(rule.metric)}
            )

    # 全 pass
    if not failed:
        return AcceptanceEvaluation(
            status=AcceptanceStatus.ACCEPTED,
            passed_rules=passed,
            failed_rules=[],
        )

    # 有 fail：找 exception 规则
    exception = _find_exception_rule(contract.exceptions, result)

    rejection_class = _classify_failure(failed, result)

    # 没匹配到 exception：默认 REJECTED
    if exception is None:
        return AcceptanceEvaluation(
            status=AcceptanceStatus.REJECTED,
            passed_rules=passed,
            failed_rules=failed,
            rejection_class=rejection_class,
            exception_action=None,
        )

    if exception.action == "no_deliver":
        return AcceptanceEvaluation(
            status=AcceptanceStatus.REJECTED,
            passed_rules=passed,
            failed_rules=failed,
            rejection_class=rejection_class,
            exception_action="no_deliver",
        )

    if exception.action == "rollback":
        # PoC 简化：标记 COMPENSATING_ACCEPTANCE（实际生产要执行反向 op）
        return AcceptanceEvaluation(
            status=AcceptanceStatus.COMPENSATING_ACCEPTANCE,
            passed_rules=passed,
            failed_rules=failed,
            exception_action="rollback",
        )

    if exception.action == "escalate":
        # 触发 queen 决策
        queen_decision = "pending"
        if queen_decision_fn is not None:
            queen_decision = queen_decision_fn(task_run, failed)
        return AcceptanceEvaluation(
            status=AcceptanceStatus.REJECTED,  # queen 默认拒绝（人工接管）
            passed_rules=passed,
            failed_rules=failed,
            rejection_class=rejection_class,
            exception_action="escalate",
            queen_decision=queen_decision,
        )

    raise ValueError(f"unknown exception action: {exception.action}")