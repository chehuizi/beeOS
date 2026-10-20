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

import re
from typing import Any

from core.models import AcceptanceRule, ExceptionRule, ResultDef, is_not_applicable
from runtime.models import (
    AcceptanceEvaluation,
    AcceptanceStatus,
    RejectionClass,
    TaskRun,
)


# ============================================================
# 单条 acceptance rule 评估
# ============================================================


def _evaluate_rule(rule: AcceptanceRule, result: dict[str, Any]) -> bool | None:
    """评估单条 acceptance rule

    Args:
        rule: AcceptanceRule（metric + op + value）
        result: task run 业务结果 dict

    Returns:
        True = pass, False = fail, None = **不适用**

    None 不是「缺值」也不是「失败」，是「这条判据对这次产出用不上」。
    典型：票上没印单价，却要验「数量 × 单价 == 金额」——无从检验，
    不该说它过，也不该说它错。混进 pass 是撒谎，混进 fail 同样是撒谎。
    """
    actual = result.get(rule.metric)
    if is_not_applicable(actual):
        return None

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


def condition_parts(condition: str) -> list[str]:
    """把一个 condition 按 AND 切成若干段

    AND 大小写都认：写成小写 `and` 是很自然的手滑，而它的后果是
    整条规则静默失效——**不报错、不提示**，比抛异常更难发现。

    切分要求 AND 两侧有空白，避免把字段名里的字母误当连接词
    （`brand` 里也有 `and`）。
    """
    return [
        part.strip()
        for part in re.split(r"\s+AND\s+", condition.strip(), flags=re.IGNORECASE)
        if part.strip()
    ]


def condition_fields(condition: str) -> list[str]:
    """这个条件实际会去读哪些字段

    **必须和 `_evaluate_condition` 用同一套解析。**

    这不是洁癖，是一个已经犯过的错：守卫原来用
    `re.split(r"(>=|<=|==|!=|>|<)", cond, maxsplit=1)` 取**位置最靠前**的
    比较符，被验的代码按 `(">=", "<=", "==", "!=", ">", "<")` 的**优先级顺序**
    找第一个出现的。条件里有两个比较符时两边选到不同的字段——
    守卫验的是它自己那套解析，于是放行了一条实际永远不触发的规则。

    守卫用被验者自己的解析，才能保证「验的就是那个」。
    """
    fields: list[str] = []
    for part in condition_parts(condition):
        match = re.search(r"(>=|<=|==|!=|>|<)", part)
        if match is None:
            continue  # 裸标识符：不读任何字段
        fields.append(part[: match.start()].strip())
    return fields


def _evaluate_condition(condition: str, result: dict[str, Any]) -> bool:
    """极简 condition 评估（PoC 用）——支持：
    - "refund_amount > 500"
    - "customer_tier == enterprise AND severity == critical"

    解析规则集中在 `condition_parts` / `condition_fields`，
    守卫读的是同一份——见 condition_fields 的说明。
    """
    parts = condition_parts(condition)
    if not parts:
        return False
    if len(parts) > 1:
        return all(_evaluate_condition(part, result) for part in parts)

    cond = parts[0]
    matches = list(re.finditer(r"(>=|<=|==|!=|>|<)", cond))
    if not matches:
        # 裸标识符：一个 op 都匹配不上 → 永不触发。
        # 这里静默返 False 是刻意的（见 _evaluate_rule 的注释），
        # 所以「有没有 op」必须由守卫提前拦住。
        return False
    if len(matches) > 1:
        # 一段里塞了两个比较符（多半是漏了 AND）。
        # 硬跑下去会在比较时抛 TypeError——那会把整个 acceptance 评估
        # 掀翻，看板上这只盒子直接 500，比规则不触发还糟。
        # 守卫会提前拦住这种写法，这里只是不让它炸。
        return False
    match = matches[0]

    op = match.group(1)
    field = cond[: match.start()].strip()
    value = cond[match.end():].strip()

    op_fns = {
        ">=": lambda a, b: a >= b,
        "<=": lambda a, b: a <= b,
        "==": lambda a, b: a == b,
        "!=": lambda a, b: a != b,
        ">": lambda a, b: a > b,
        "<": lambda a, b: a < b,
    }
    # 尝试转换 value
    try:
        if value in ("true", "false"):
            value = value == "true"
        else:
            value = float(value) if "." in value else int(value)
    except (ValueError, TypeError):
        value = value.strip('"').strip("'")
    actual = result.get(field)
    if actual is None:
        return False
    try:
        return op_fns[op](actual, value)
    except TypeError:
        # 类型对不上（比如拿 int 和一个没转成数字的 str 比）。
        # 同样是「这条规则这次不适用」，不是「整个评估崩掉」。
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
    - 无 fail（n/a 不算 fail）→ ACCEPTED
    - 有 fail + exception=rollback → COMPENSATING_ACCEPTANCE（PoC 不执行反向 op，直接回 AWAITING）
    - 有 fail + exception=escalate → REJECTED（queen 决策）
    - 有 fail + exception=no_deliver → REJECTED
    - 有 fail 无 exception → REJECTED（默认）

    n/a（判据对本次产出用不上）既不挡 ACCEPTED，也不进 passed_rules——
    它进 not_applicable_rules，由看板单独列出来。说成「通过」是撒谎。
    """
    if task_run.status.value != "completed":
        raise ValueError(
            f"acceptance requires task_run.status=completed, got {task_run.status.value}"
        )

    result = task_run.result or {}
    passed: list[str] = []
    failed: list[dict[str, Any]] = []
    not_applicable: list[str] = []

    for rule in contract.acceptance:
        error: str | None = None
        try:
            ok = _evaluate_rule(rule, result)
        except Exception as e:
            ok = False
            error = str(e)

        # 三态：None = 用不上，不进 failed 也不进 passed
        if ok is None:
            not_applicable.append(rule.metric)
            continue
        if ok:
            passed.append(rule.metric)
        else:
            entry = {
                "metric": rule.metric,
                "op": rule.op,
                "expected": rule.value,
                "actual": result.get(rule.metric),
            }
            if error is not None:
                entry["error"] = error
            failed.append(entry)

    # 全 pass（n/a 不算失败）
    if not failed:
        return AcceptanceEvaluation(
            status=AcceptanceStatus.ACCEPTED,
            passed_rules=passed,
            failed_rules=[],
            not_applicable_rules=not_applicable,
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
            not_applicable_rules=not_applicable,
            rejection_class=rejection_class,
            exception_action=None,
        )

    if exception.action == "no_deliver":
        return AcceptanceEvaluation(
            status=AcceptanceStatus.REJECTED,
            passed_rules=passed,
            failed_rules=failed,
            not_applicable_rules=not_applicable,
            rejection_class=rejection_class,
            exception_action="no_deliver",
        )

    if exception.action == "rollback":
        # PoC 简化：标记 COMPENSATING_ACCEPTANCE（实际生产要执行反向 op）
        return AcceptanceEvaluation(
            status=AcceptanceStatus.COMPENSATING_ACCEPTANCE,
            passed_rules=passed,
            failed_rules=failed,
            not_applicable_rules=not_applicable,
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
            not_applicable_rules=not_applicable,
            rejection_class=rejection_class,
            exception_action="escalate",
            queen_decision=queen_decision,
        )

    raise ValueError(f"unknown exception action: {exception.action}")