"""Result 模型 - 内核的输出单元。"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class Result(BaseModel):
    """任务执行结果（最终交付物）。"""

    task_id: str
    exec_id: str
    bom_id: str | None = None
    status: str  # Done / Failed / AwaitingHuman
    deliverables: list[dict[str, Any]] = Field(default_factory=dict)
    step_count: int = 0
    elapsed_ms: float = 0.0
    error: str | None = None
    finished_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
