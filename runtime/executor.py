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
from runtime.mock_runner import MockRunner
from runtime.models import OperationRecord, TaskRun, TaskRunStatus


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
        executor = BeelineExecutor(runner=MockRunner())
        task_run = executor.execute(task_run, beeline, input_data, contract)
    """

    def __init__(self, runner: Optional[MockRunner] = None) -> None:
        self.runner = runner or MockRunner()

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
                task_run.event_log.append(
                    event="op_finish",
                    op_id=current_op.op_id,
                    detail={"status": "failed", "error": str(e)},
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

        return task_run