"""Queen FastAPI 应用 - 雏形（M1 仅暴露 /health）。"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from beeos_core.config import get_settings
from beeos_core.db import close_db
from beeos_core.logging import configure_logging, get_logger

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    """应用生命周期。"""
    configure_logging()
    logger.info("queen.startup", env=get_settings().env)
    yield
    await close_db()
    logger.info("queen.shutdown")


app = FastAPI(
    title="beeOS Queen",
    description="beeOS 调度服务 - 任务调度大脑",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health() -> dict:
    """健康检查端点。"""
    return {"status": "ok", "service": "queen", "version": "0.1.0"}


@app.get("/ready")
async def ready() -> dict:
    """就绪检查（DB / Redis 可达）。"""
    # TODO(M1): 检查 DB / Redis 连通性
    return {"status": "ready", "service": "queen"}


# === M1 之后接入的路由（占位）===
# @app.post("/api/v0/queen/jobs", response_model=JobResponse)
# async def create_job(...): ...
# @app.get("/api/v0/queen/jobs/{job_id}", response_model=JobResponse)
# async def get_job(...): ...
