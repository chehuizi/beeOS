"""bee-kernel FastAPI server。"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from bee_kernel.kernel import Kernel
from bee_kernel.task import Task

app = FastAPI(title="beeOS Kernel", version="0.1.0", description="beeOS 内核 - Task / BOM / MES")

# 全局 kernel 实例（M0 简化：单实例）
_kernel: Kernel | None = None


def get_kernel() -> Kernel:
    global _kernel
    if _kernel is None:
        _kernel = Kernel()
    return _kernel


# === Schemas ===

class TaskSubmitRequest(BaseModel):
    workspace_id: str
    objective: str
    params: dict = Field(default_factory=dict)
    priority: int = Field(default=5, ge=0, le=9)
    submitted_by: str = Field(default="api")


# === Routes ===

@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "bee-kernel", "version": "0.1.0"}


@app.post("/api/v0/tasks")
async def submit_task(req: TaskSubmitRequest) -> dict:
    """提交任务。M0 同步执行，立即返回 result。"""
    kernel = get_kernel()
    task = Task(
        workspace_id=req.workspace_id,
        objective=req.objective,
        params=req.params,
        priority=req.priority,
        submitted_by=req.submitted_by,
    )
    try:
        result = kernel.submit(task)
        return result.model_dump()
    except KeyError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        raise HTTPException(500, f"{type(e).__name__}: {e}")


@app.get("/api/v0/tasks/{exec_id}")
async def get_task(exec_id: str) -> dict:
    """查执行历史。"""
    plan = get_kernel().get_execution(exec_id)
    if plan is None:
        raise HTTPException(404, f"exec {exec_id!r} not found")
    return plan.model_dump()


@app.get("/api/v0/boms")
async def list_boms() -> dict:
    """列出所有 BOM 蓝图。"""
    return {"boms": get_kernel().list_boms()}


@app.get("/api/v0/workspaces")
async def list_workspaces() -> dict:
    """列出所有 workspace。"""
    return {"workspaces": get_kernel().list_workspaces()}
