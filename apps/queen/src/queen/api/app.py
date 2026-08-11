"""Queen FastAPI 应用 - Job API + 异步 dispatch + DB schema 启动建表。

路由：
  GET  /health                   健康检查
  GET  /ready                    就绪检查（DB + Redis 连通）
  POST /api/v0/jobs              Portal 提交任务（创建 + 异步 dispatch）
  GET  /api/v0/jobs              Portal 任务列表
  GET  /api/v0/audit             Portal 审计日志

Portal fetch 路径无 /queen 前缀，与 job-system.md §4.1 不一致，以 Portal HTML 为准。
"""

import asyncio
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from sqlalchemy import select

from beeos_core.config import get_settings
from beeos_core.db import close_db, get_engine, session_scope
from beeos_core.guardian import is_high_risk
from beeos_core.logging import configure_logging, get_logger
from beeos_core.models import AuditLog, Base, Job, Pollen

from queen.api.schemas import AuditEntry, JobResponse, JobSubmitRequest
from queen.core.audit import write_audit
from queen.core.dispatcher import dispatch_job
from queen.core.state_machine import JobStatus

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    """应用生命周期：启动时建表 + 配置日志；退出时关 DB。"""
    configure_logging()
    logger.info("queen.startup", env=get_settings().env)
    # M1: 启动时自动 create_all（V1 改 Alembic）
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("queen.schema_ready")
    yield
    await close_db()
    logger.info("queen.shutdown")


app = FastAPI(
    title="beeOS Queen",
    description="beeOS 调度服务 - 任务调度大脑",
    version="0.1.0",
    lifespan=lifespan,
)


# === 健康检查 ===


@app.get("/health")
async def health() -> dict:
    """健康检查端点。"""
    return {"status": "ok", "service": "queen", "version": "0.1.0"}


@app.get("/ready")
async def ready() -> dict:
    """就绪检查：DB + Redis 连通性。"""
    settings = get_settings()
    checks: dict[str, str] = {}

    # PG
    try:
        async with session_scope() as session:
            await session.execute(select(1))
        checks["postgres"] = "ok"
    except Exception as e:
        checks["postgres"] = f"fail: {e}"

    # Redis（M1 暂不强依赖，best-effort）
    try:
        import redis.asyncio as aioredis

        r = aioredis.from_url(settings.redis_url)
        await r.ping()
        await r.aclose()
        checks["redis"] = "ok"
    except Exception as e:
        checks["redis"] = f"fail: {e}"

    status = "ready" if all(v == "ok" for v in checks.values()) else "degraded"
    return {"status": status, "service": "queen", "checks": checks}


# === Job API（Portal 实际 fetch 的路径）===


@app.post("/api/v0/jobs", response_model=JobResponse, status_code=201)
async def create_job(req: JobSubmitRequest) -> JobResponse:
    """Portal new.html POST 提交。"""
    logger.info("queen.create_job", bee_type=req.bee_type, period=req.period)

    # Prompt 注入检测（notes 字段）
    if req.notes and is_high_risk(req.notes):
        raise HTTPException(400, "notes contains high-risk content (prompt injection)")

    job_id = uuid4()
    context_id = uuid4()

    # 构造 params（M1 Bee 期望的格式）
    params = {
        "period": req.period,
        "client_ids": req.clients,
        "approver": "manager@example.com",  # M1 写死
    }

    async with session_scope() as session:
        job = Job(
            job_id=job_id,
            bee_type=req.bee_type,
            status=JobStatus.QUEUED.value,
            params=params,
            context_id=context_id,
        )
        session.add(job)

        pollen = Pollen(
            context_id=context_id,
            job_id=job_id,
            payload={"submit": {"urgency": req.urgency, "notes": req.notes}},
        )
        session.add(pollen)
        await session.flush()
        created_at = job.created_at

    await write_audit(
        actor="portal:anonymous",  # M1 暂不鉴权
        action="job.create",
        resource=f"job:{job_id}",
        payload={"bee_type": req.bee_type, "period": req.period, "urgency": req.urgency},
    )

    # 异步 dispatch（fire-and-forget；Portal 轮询 /api/v0/jobs 看状态）
    asyncio.create_task(dispatch_job(str(job_id)))

    return JobResponse(
        job_id=job_id,
        short_id=f"job-{job_id.hex[:8]}",
        bee_type=req.bee_type,
        status=JobStatus.QUEUED.value,
        created_at=created_at,
    )


@app.get("/api/v0/jobs", response_model=list[JobResponse])
async def list_jobs(limit: int = 50) -> list[JobResponse]:
    """Portal jobs.html GET 列表。"""
    async with session_scope() as session:
        stmt = select(Job).order_by(Job.created_at.desc()).limit(limit)
        rows = (await session.execute(stmt)).scalars().all()
        return [
            JobResponse(
                job_id=r.job_id,
                short_id=f"job-{r.job_id.hex[:8]}",
                bee_type=r.bee_type,
                status=r.status,
                progress=r.progress or 0.0,
                current_step=r.current_step,
                created_at=r.created_at,
                started_at=r.started_at,
                finished_at=r.finished_at,
                result=r.result,
                error=r.error,
            )
            for r in rows
        ]


@app.get("/api/v0/audit", response_model=list[AuditEntry])
async def list_audit(limit: int = 100) -> list[AuditEntry]:
    """Portal audit.html GET 列表。"""
    async with session_scope() as session:
        stmt = select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)
        rows = (await session.execute(stmt)).scalars().all()
        return [
            AuditEntry(
                id=r.id, ts=r.ts, actor=r.actor, action=r.action, resource=r.resource
            )
            for r in rows
        ]
