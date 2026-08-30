"""Bee Orchestrator - runtime 引擎核心（M0 状态机版）。

设计原则（bee = runtime）：
- 加载 beeBox 的 manifest + workflow 声明
- 跑状态机：Queued → Running → {Done | Failed}
- 每步调 box.run_step()，收集 trace，写本地审计
- 任何异常 → Failed + 审计

V1+ 在 box.run_step() 之上包 LLM ReAct 循环（M0 写死按 WORKFLOW 顺序跑）。
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from beeos_core.logging import get_logger

from bee.audit import LocalAuditLog
from bee.registry import get_manifest, get_workflow, list_supported, run_step
from bee.state import JobStateMachine, JobStatus

logger = get_logger(__name__)


class BeeConfig(BaseModel):
    """Bee 配置。"""

    max_steps: int = Field(default=100, description="单 Bee 最多执行步骤数（防死循环）")
    max_execution_seconds: int = Field(default=1800, description="单 Bee 最大执行时长（30 分钟）")
    audit_path: str = Field(default="./logs/audit.jsonl", description="审计日志路径")


class BeeResult(BaseModel):
    """Bee 执行结果。"""

    status: JobStatus
    box_type: str
    period: str
    started_at: str
    finished_at: str
    elapsed_ms: float
    steps: list[dict]
    error: str | None = None


class Bee:
    """Bee 执行单元（M0 写死 workflow，V1+ ReAct 循环）。"""

    def __init__(self, config: BeeConfig | None = None) -> None:
        self.config = config or BeeConfig()
        self.audit = LocalAuditLog(self.config.audit_path)
        logger.info("bee.init", config=self.config.model_dump())

    async def run(
        self,
        box_type: str,
        context: dict[str, Any] | None = None,
    ) -> BeeResult:
        """执行 Box 任务。

        Args:
            box_type: Box 类型（"month_close" 等）
            context: 任务参数（period, client_ids, approver, ...）

        Returns:
            BeeResult，含 status / steps 完整 trace
        """
        context = context or {}
        if box_type not in list_supported():
            msg = f"Unknown box_type: {box_type}. Supported: {list_supported()}"
            logger.error("bee.unknown_box_type", box_type=box_type)
            return BeeResult(
                status=JobStatus.FAILED,
                box_type=box_type,
                period=context.get("period", "unknown"),
                started_at=datetime.now(timezone.utc).isoformat(),
                finished_at=datetime.now(timezone.utc).isoformat(),
                elapsed_ms=0.0,
                steps=[],
                error=msg,
            )

        # 加载 Box 声明
        manifest = get_manifest(box_type)
        workflow = get_workflow(box_type)
        period = context.get("period", "unknown")

        # 状态机 + 计时
        sm = JobStateMachine(JobStatus.QUEUED)
        started_at = datetime.now(timezone.utc)
        t0 = time.perf_counter()
        self.audit.write(
            actor="bee", action="job.start",
            resource=f"box:{box_type}",
            payload={"from": "Queued", "to": "Running", "context": context},
        )
        sm.transition(JobStatus.RUNNING)

        steps_trace: list[dict] = []
        prev_outputs: dict = {}
        final_status = JobStatus.DONE
        error_msg: str | None = None

        # 跑 workflow
        try:
            for i, step in enumerate(workflow, 1):
                if i > self.config.max_steps:
                    raise RuntimeError(f"Exceeded max_steps={self.config.max_steps}")
                if (time.perf_counter() - t0) > self.config.max_execution_seconds:
                    raise RuntimeError(f"Exceeded max_execution_seconds={self.config.max_execution_seconds}")

                step_t0 = time.perf_counter()
                output = run_step(box_type, step["name"], context, prev_outputs)
                elapsed_ms = round((time.perf_counter() - step_t0) * 1000, 2)
                prev_outputs[step["name"]] = output
                steps_trace.append({
                    "step": step["name"],
                    "tool": step["tool"],
                    "input": {"period": period},
                    "output": output if isinstance(output, dict) else {"value": output},
                    "elapsed_ms": elapsed_ms,
                })
                self.audit.write(
                    actor="bee", action="step.done",
                    resource=f"box:{box_type}/step:{step['name']}",
                    payload={"step": step["name"], "elapsed_ms": elapsed_ms},
                )
                logger.info(
                    "bee.step", box=box_type, step=step["name"],
                    elapsed_ms=elapsed_ms, progress=f"{i}/{len(workflow)}",
                )

            sm.transition(JobStatus.DONE)
            self.audit.write(
                actor="bee", action="job.complete",
                resource=f"box:{box_type}",
                payload={"status": "Done", "total_steps": len(steps_trace)},
            )
        except Exception as e:
            final_status = JobStatus.FAILED
            error_msg = f"{type(e).__name__}: {e}"
            sm.transition(JobStatus.FAILED)
            self.audit.write(
                actor="bee", action="job.exception",
                resource=f"box:{box_type}",
                payload={"error": error_msg, "type": type(e).__name__},
            )
            logger.exception("bee.exception", box=box_type)

        finished_at = datetime.now(timezone.utc)
        total_ms = round((time.perf_counter() - t0) * 1000, 2)

        return BeeResult(
            status=final_status,
            box_type=box_type,
            period=period,
            started_at=started_at.isoformat(),
            finished_at=finished_at.isoformat(),
            elapsed_ms=total_ms,
            steps=steps_trace,
            error=error_msg,
        )
