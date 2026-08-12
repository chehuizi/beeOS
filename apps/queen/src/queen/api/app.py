"""Queen FastAPI 应用 - Job API + 异步 dispatch + DB schema 启动建表。

路由：
  GET  /health                   健康检查
  GET  /ready                    就绪检查（DB + Redis 连通）
  POST /api/v0/jobs              Portal 提交任务（创建 + 异步 dispatch）
  GET  /api/v0/jobs              Portal 任务列表
  GET  /api/v0/audit             Portal 审计日志
  GET  /api/v0/runtime           架构页用：4 unit 状态 + 最近 10 job
  GET  /api/v0/overview          仪表盘用：公司全景（管理者/员工/工位/仓库 + 最近活动）

Portal fetch 路径无 /queen 前缀，与 job-system.md §4.1 不一致，以 Portal HTML 为准。
"""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from sqlalchemy import func, select

from beeos_core.config import get_settings
from beeos_core.db import close_db, get_engine, session_scope
from beeos_core.guardian import is_high_risk
from beeos_core.logging import configure_logging, get_logger
from beeos_core.models import AuditLog, Base, BoxManifest, Credential, Job, Pollen, User

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


@app.get("/api/v0/runtime")
async def runtime_status() -> dict:
    """架构页用：4 unit 状态 + 最近 10 job 统计。

    返回：
      units: 4 个 systemd unit 状态（Queen 自己活 = active，PG/Redis 查连通，
            nginx 假设活——Queen 没在跑说明 nginx 也访问不到）
      recent_jobs: last 10 jobs 的 done / failed 计数 + 平均耗时
      version: Queen 版本
    """
    settings = get_settings()
    units: dict[str, str] = {}

    # nginx: Queen 能响应说明 nginx 反代通；标记 active
    units["nginx"] = "active"

    # postgresql: 真实 SELECT
    try:
        async with session_scope() as session:
            await session.execute(select(1))
        units["postgresql"] = "active"
    except Exception as e:
        units["postgresql"] = f"fail: {e}"

    # redis: 真实 PING
    try:
        import redis.asyncio as aioredis

        r = aioredis.from_url(settings.redis_url)
        await r.ping()
        await r.aclose()
        units["redis"] = "active"
    except Exception as e:
        units["redis"] = f"fail: {e}"

    # beeos-queen: 我们自己就是它，能响应就是 active
    units["beeos-queen"] = "active"

    # 最近 10 job 统计
    async with session_scope() as session:
        # 按 created_at 倒序取 10 条
        stmt = (
            select(Job.status, Job.started_at, Job.finished_at)
            .order_by(Job.created_at.desc())
            .limit(10)
        )
        rows = (await session.execute(stmt)).all()

        # 全局计数（所有时间）
        count_stmt = select(Job.status, func.count(Job.job_id)).group_by(Job.status)
        counts = dict((await session.execute(count_stmt)).all())

    done_count = sum(1 for r in rows if r.status == "Done")
    failed_count = sum(1 for r in rows if r.status == "Failed")
    durations_ms: list[float] = []
    for r in rows:
        if r.status == "Done" and r.started_at and r.finished_at:
            delta = (r.finished_at - r.started_at).total_seconds() * 1000
            durations_ms.append(delta)
    avg_duration_ms = round(sum(durations_ms) / len(durations_ms), 1) if durations_ms else None

    return {
        "version": app.version,
        "units": units,
        "recent_jobs": {
            "window": len(rows),
            "done": done_count,
            "failed": failed_count,
            "in_flight": len(rows) - done_count - failed_count,
            "avg_duration_ms": avg_duration_ms,
        },
        "lifetime_counts": counts,
    }


@app.get("/api/v0/overview")
async def overview() -> dict:
    """仪表盘公司全景：4 单位状态 + 今日工单 + Hive 容量 + 最近活动。

    公司比喻：
      queen       管理者
      bee_count   员工数
      box_count   工位数
      hive_stats  仓库
    """
    units: dict[str, str] = {"nginx": "active"}

    # PG
    try:
        async with session_scope() as session:
            await session.execute(select(1))
        units["postgresql"] = "active"
    except Exception as e:
        units["postgresql"] = f"fail: {e}"

    # Redis
    try:
        import redis.asyncio as aioredis

        r = aioredis.from_url(get_settings().redis_url)
        await r.ping()
        await r.aclose()
        units["redis"] = "active"
    except Exception as e:
        units["redis"] = f"fail: {e}"

    units["beeos-queen"] = "active"

    # Hive 统计 + 今日工单 + 最近活动
    # "今日" 用 ECS 本地时间（CST = UTC+8，server timezone），
    # 不用 UTC 否则亚州用户在 UTC 凌晨看到的"今天"会少 8 小时。
    today_start = datetime.now().replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    async with session_scope() as session:
        # Hive 行数
        jobs_total = await session.scalar(select(func.count(Job.job_id))) or 0
        audit_total = await session.scalar(select(func.count(AuditLog.id))) or 0
        users_total = await session.scalar(select(func.count(User.user_id))) or 0
        credentials_total = (
            await session.scalar(select(func.count(Credential.credential_id))) or 0
        )
        box_manifests_total = (
            await session.scalar(select(func.count(BoxManifest.box_id))) or 0
        )

        # 今日工单按状态分组
        today_stmt = (
            select(Job.status, func.count(Job.job_id))
            .where(Job.created_at >= today_start)
            .group_by(Job.status)
        )
        today_counts = dict((await session.execute(today_stmt)).all())

        # 最近 5 条工单
        recent_jobs_rows = (
            (
                await session.execute(
                    select(Job)
                    .order_by(Job.created_at.desc())
                    .limit(5)
                )
            )
            .scalars()
            .all()
        )

        # 最近 5 条审计
        recent_audit_rows = (
            (
                await session.execute(
                    select(AuditLog).order_by(AuditLog.id.desc()).limit(5)
                )
            )
            .scalars()
            .all()
        )

    return {
        "version": app.version,
        "company": {
            "queen": units["beeos-queen"],
            "units": units,
            "bee_count": 1,  # M1: 1 bee, 跟 queen 同进程
            "box_count": 1,  # M1: month_close
        },
        "today_jobs": {
            "Queued": today_counts.get("Queued", 0),
            "Running": today_counts.get("Running", 0),
            "Done": today_counts.get("Done", 0),
            "Failed": today_counts.get("Failed", 0),
        },
        "hive_stats": {
            "jobs": jobs_total,
            "audit_log": audit_total,
            "users": users_total,
            "credentials": credentials_total,
            "box_manifests": box_manifests_total,
        },
        "recent_activity": {
            "jobs": [
                {
                    "job_id": str(j.job_id),
                    "short_id": f"job-{j.job_id.hex[:8]}",
                    "bee_type": j.bee_type,
                    "status": j.status,
                    "created_at": j.created_at.isoformat(),
                }
                for j in recent_jobs_rows
            ],
            "audit": [
                {
                    "id": a.id,
                    "ts": a.ts.isoformat(),
                    "actor": a.actor,
                    "action": a.action,
                    "resource": a.resource,
                }
                for a in recent_audit_rows
            ],
        },
    }
