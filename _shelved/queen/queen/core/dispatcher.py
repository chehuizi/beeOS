"""任务派发 - Queued → Running → Done/Failed 状态机驱动。

由 FastAPI 路由在 POST /api/v0/jobs 后用 asyncio.create_task 调起。
"""

from datetime import datetime, timezone

from sqlalchemy import select

from beeos_core.db import session_scope
from beeos_core.logging import get_logger
from beeos_core.models import Job
from bee.runtime import Bee

from queen.core.audit import write_audit
from queen.core.state_machine import JobStatus, can_transition

logger = get_logger(__name__)


async def dispatch_job(job_id: str) -> None:
    """派发一个 Queued 任务到 Bee。

    流程：load job → 状态机 Queued→Running → 调 Bee.run() → 写 result
    → 状态机 Running→Done 或 Failed。每步写审计。
    """
    logger.info("dispatcher.start", job_id=job_id)
    try:
        # === 1. 加载 + 状态机 Queued → Running ===
        async with session_scope() as session:
            stmt = select(Job).where(Job.job_id == job_id)
            job = (await session.execute(stmt)).scalar_one_or_none()
            if job is None:
                logger.error("dispatcher.job_not_found", job_id=job_id)
                return
            if not can_transition(JobStatus(job.status), JobStatus.RUNNING):
                logger.error(
                    "dispatcher.invalid_transition", from_=job.status, to="Running"
                )
                return

            job.status = JobStatus.RUNNING.value
            job.started_at = datetime.now(timezone.utc)
            job.progress = 0.1
            params = dict(job.params)  # 复制出来，避免 session 关闭后失效

        await write_audit(
            actor="queen",
            action="job.start",
            resource=f"job:{job_id}",
            payload={"from": "Queued", "to": "Running"},
        )

        # === 2. 调 Bee（同步等待）===
        bee = Bee()
        result = await bee.run(
            task="month_close",
            context={"bee_type": "month_close", **params},
        )

        # === 3. 写 result + 状态机 Running → Done/Failed ===
        async with session_scope() as session:
            stmt = select(Job).where(Job.job_id == job_id)
            job = (await session.execute(stmt)).scalar_one_or_none()
            if job is None:
                logger.error("dispatcher.job_vanished", job_id=job_id)
                return

            if isinstance(result, dict) and result.get("status") == "done":
                job.status = JobStatus.DONE.value
                job.progress = 1.0
                job.current_step = "completed"
                job.result = result
            else:
                job.status = JobStatus.FAILED.value
                job.error = {
                    "message": result.get("error", "unknown") if isinstance(result, dict) else "unknown"
                }

            job.finished_at = datetime.now(timezone.utc)

        await write_audit(
            actor="queen",
            action="job.complete",
            resource=f"job:{job_id}",
            payload={"status": job.status},
        )
        logger.info("dispatcher.done", job_id=job_id, status=job.status)

    except Exception as e:
        # 兜底：任何未捕获异常都置 Failed + 写审计
        logger.exception("dispatcher.exception", job_id=job_id)
        try:
            async with session_scope() as session:
                stmt = select(Job).where(Job.job_id == job_id)
                job = (await session.execute(stmt)).scalar_one_or_none()
                if job is not None:
                    job.status = JobStatus.FAILED.value
                    job.error = {"message": f"{type(e).__name__}: {e}"}
                    job.finished_at = datetime.now(timezone.utc)
            await write_audit(
                actor="queen",
                action="job.exception",
                resource=f"job:{job_id}",
                payload={"error": str(e), "type": type(e).__name__},
            )
        except Exception:
            logger.exception("dispatcher.exception_cleanup_failed", job_id=job_id)
