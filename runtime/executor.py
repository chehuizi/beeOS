"""
runtime.executor - 进程内 beeline executor

按 [beebox-design.md §3.4.3 + §3.4.4] 实现：
- 起点 operation（input_from=external）
- 顺序 / 并发 / 分支推进
- when 条件评估
- 终态判断

PoC 2 范围：
- 顺序 + 分支（按 when 评估）
- 并发（fan-out）支持但不实现 fan-in（统一在终态或下一 op）
- 汇聚（fan-in）支持单输入情况
- RETRYING / WAITING_EXTERNAL / COMPENSATING / PARTIALLY_COMPLETED 状态下一轮

执行流程：
1. create_task_run → status=triggered
2. executor.execute(task_run, beeline, input) → 转移 status=running
3. 找 start op（input_from=external）
4. 循环：
   a. 执行 current_op（mock runner）
   b. 记录 op output
   c. 选 next op（按 when）
   d. 没 next → 退出循环
5. 转移 status=completed
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Optional

from core.beeline_models import Beeline, NextRef, Operation
from runtime.llm import LLMError, LLMParseError
from runtime.models import OperationRecord, TaskRun, TaskRunStatus
from runtime.runner import HandlerRunner


# 解析 when 条件为可执行函数（极简版）
def _evaluate_when(when: Optional[str], op_output: dict[str, Any]) -> bool:
    """评估 when 条件

    支持：
    - None → True（无条件）
    - "resolution_type == fulfilled" → 等值
    - "resolution_type in [fulfilled, partial_fulfilled]" → in
    - "eta_acceptable == true" → 布尔
    """
    if when is None:
        return True

    cond = when.strip()

    # in [...] 语法
    if " in [" in cond:
        field = cond.split(" in ")[0].strip()
        values_str = cond.split(" in [")[1].rstrip("]")
        values = [v.strip().strip('"').strip("'") for v in values_str.split(",")]
        return op_output.get(field) in values

    # 单等值
    if "==" in cond:
        field, value = cond.split("==", 1)
        field = field.strip()
        value = value.strip()
        # 处理 true/false
        if value == "true":
            value = True
        elif value == "false":
            value = False
        elif value.isdigit():
            value = int(value)
        return op_output.get(field) == value

    raise ValueError(f"unsupported when clause: {when!r}")


def _pick_next(op: Operation, op_output: dict[str, Any]) -> Optional[NextRef]:
    """从 op.next 中选一个 next

    规则：
    - 多个 next 带 when：取第一个 when 匹配的（互斥）
    - 多个 next：取第一个 when=None 的（默认）
    - 单个 next：返回它
    """
    if not op.next:
        return None

    # 第一遍：找带 when 匹配的
    for nxt in op.next:
        if nxt.when is not None:
            if _evaluate_when(nxt.when, op_output):
                return nxt

    # 第二遍：找无条件默认
    for nxt in op.next:
        if nxt.when is None:
            return nxt

    # 没匹配上：返回 None（断路）
    return None


class BeelineExecutor:
    """进程内 beeline executor

    用法：
        # runner 由盒子自己提供（boxes/*/runner.py 里的 RUNNER）
        executor = BeelineExecutor(runner=some_box.RUNNER)
        task_run = executor.execute(task_run, beeline, input_data)

    runner 是**必填**且必须由调用方（编排层）按 box_id 取。
    内核不兜底一张「什么都认识」的表：以前它兜底过，那张表里塞了
    三只盒子的业务实现，于是内核比任何一只盒子都懂业务，
    加盒子必须改内核。兜底掉的代价是盒子带不走自己的实现。
    """

    def __init__(self, runner: HandlerRunner) -> None:
        if runner is None:
            raise ValueError(
                "BeelineExecutor requires a runner from the box "
                "(boxes/*/runner.py RUNNER); the kernel no longer "
                "falls back to a shared handler table"
            )
        self.runner = runner

    def execute(
        self,
        task_run: TaskRun,
        beeline: Beeline,
        input_data: dict[str, Any],
    ) -> TaskRun:
        """执行 beeline（同步，进程内）

        Args:
            task_run: 已创建的 task run（status=triggered）
            beeline: 要执行的 beeline
            input_data: task 输入（按 task_schema 形状）

        Returns:
            更新后的 task run（status=running → completed/failed）

        Raises:
            ValueError: beeline 完整性错误
            NotImplementedError: op type 未注册
        """
        # triggered -> running
        task_run.transition_to(TaskRunStatus.RUNNING, reason="executor picked up")
        task_run.identity.started_at = datetime.now(timezone.utc)
        task_run.current_op = None

        # 找起点 op
        starts = beeline.get_start_operations()
        if not starts:
            task_run.transition_to(TaskRunStatus.FAILED, reason="no start operation")
            return task_run
        if len(starts) > 1:
            task_run.transition_to(TaskRunStatus.FAILED, reason=f"multiple start ops: {[o.op_id for o in starts]}")
            return task_run

        current_op: Operation = starts[0]
        # task input 作为起点 input
        op_input: dict[str, Any] = dict(input_data)

        # 沿 beeline 推进
        max_steps = len(beeline.operations) * 3  # 安全网（防意外的环）
        step_count = 0

        try:
            while current_op is not None:
                step_count += 1
                if step_count > max_steps:
                    task_run.transition_to(TaskRunStatus.FAILED, reason="step limit exceeded")
                    return task_run

                task_run.current_op = current_op.op_id
                task_run.event_log.append(event="op_start", op_id=current_op.op_id)

                # 记录 op 启动
                record = OperationRecord(
                    op_id=current_op.op_id,
                    status="running",
                    started_at=datetime.now(timezone.utc),
                    input=op_input,
                )
                task_run.operations[current_op.op_id] = record

                try:
                    op_output = self.runner.run(current_op.type, op_input)
                except Exception as e:
                    record.status = "failed"
                    record.finished_at = datetime.now(timezone.utc)
                    record.error = str(e)
                    # 中止归因：LLM 是基础设施设施，runtime 认得它；盒子据此统计
                    # 自己的调用失败率。判据不是业务知识，所以这层归类不算越界。
                    # **三态归因，别把两件事混成一个**：
                    # - llm_parse：模型答了但格式不对（提示词/约束的问题）
                    # - llm_call：网络/凭证/服务端挂了（基础设施的问题）
                    # - op_error：跟 LLM 无关，盒子的逻辑错了
                    # 混成一个的后果：盒子的 llm_call_failure_rate 指标
                    # 既反映不了网络稳定性，也反映不了模型服从度——
                    # 一个数同时坏在两个方向上。
                    if isinstance(e, LLMParseError):
                        record.error_kind = "llm_parse"
                    elif isinstance(e, LLMError):
                        record.error_kind = "llm_call"
                    else:
                        record.error_kind = "op_error"
                    task_run.event_log.append(
                        event="op_finish",
                        op_id=current_op.op_id,
                        detail={"status": "failed", "error": str(e), "error_kind": record.error_kind},
                    )
                    task_run.transition_to(TaskRunStatus.FAILED, reason=f"op '{current_op.op_id}' failed: {e}")
                    return task_run

                # op 成功
                record.status = "completed"
                record.finished_at = datetime.now(timezone.utc)
                record.output = op_output
                task_run.event_log.append(
                    event="op_finish",
                    op_id=current_op.op_id,
                    detail={"status": "completed"},
                )

                # 选 next
                next_ref = _pick_next(current_op, op_output)
                if next_ref is None:
                    # 终态
                    task_run.result = op_output  # 最终结果作为 task run result
                    task_run.current_op = None
                    break

                next_op = beeline.get_operation(next_ref.op_id)
                if next_op is None:
                    task_run.transition_to(TaskRunStatus.FAILED, reason=f"next op '{next_ref.op_id}' not found")
                    return task_run

                # 下个 op 的 input = 当前 op 的 output（按 input_from 链）
                op_input = dict(op_output)
                current_op = next_op

            # running -> completed
            if task_run.status == TaskRunStatus.RUNNING:
                task_run.transition_to(TaskRunStatus.COMPLETED, reason="beeline finished")


        except Exception as e:
            # ============================================================
            # **兜底：任何异常路径都必须落成一条 failed 记录**
            # ============================================================
            #
            # **为什么需要这段**（2026-10-30 调研实测）：
            # 上面的 try 只包着 `runner.run(op)`。但整条链上还有几处会抛：
            # - `_pick_next` 对不支持的 when 子句 raise ValueError
            # - `op_input = dict(op_output)` —— op 输出不是 dict 就 TypeError
            # - `transition_to` 非法转移
            # 这些异常冒到 kanban/trigger.py，**直接跳过 store.append** →
            # **这次履约在看板/store 里根本不存在**。
            #
            # 「失败但查无此事」是最贵的一种故障：连「它挂了」都看不到，
            # 事后无从判断是哪一环、为什么。
            #
            # **这不是降级**：状态照旧是 failed，结论照旧是红。
            # 变的只是「它现在能被查到了」。
            reason = f"履约异常中断（{type(e).__name__}）：{e}"
            rec = None
            if current_op is not None:
                rec = task_run.operations.get(current_op.op_id)
            if rec is not None and rec.status == "running":
                # 挂在还在跑的那个 op 头上 —— 归因要落到具体环节
                rec.status = "failed"
                rec.finished_at = datetime.now(timezone.utc)
                rec.error = reason
                rec.error_kind = "op_error"
                task_run.event_log.append(
                    event="op_finish",
                    op_id=rec.op_id,
                    detail={"status": "failed", "error": reason,
                            "error_kind": "op_error"},
                )
            try:
                task_run.transition_to(TaskRunStatus.FAILED, reason=reason)
            except Exception:
                # 连状态转移都非法（例：已经 terminal 了）→
                # 强行落状态 + 事件，绝不让记录蒸发
                task_run.status = TaskRunStatus.FAILED
                task_run.event_log.append(
                    event="status_change", detail={"reason": reason},
                )

        return task_run