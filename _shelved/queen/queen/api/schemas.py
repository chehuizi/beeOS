"""Queen API Pydantic schemas。

Portal 实际 fetch 的路径是 /api/v0/jobs（无 /queen 前缀，与 job-system.md
不一致；以 Portal HTML 为准）。
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class JobSubmitRequest(BaseModel):
    """Portal new.html POST 提交契约。

    对应 apps/portal/jobs/new.html 的 submit() payload。
    """

    bee_type: str = Field(..., description="M1: month_close")
    period: str = Field(..., pattern=r"^\d{4}-\d{2}$", description="YYYY-MM")
    clients: list[str] = Field(default_factory=list, description="客户编号列表，空=全部")
    urgency: str = Field(default="normal", pattern=r"^(normal|urgent)$")
    notes: str = Field(default="", max_length=500)


class JobResponse(BaseModel):
    """GET /api/v0/jobs 列表 / POST 响应。

    Portal jobs.html 实际只读 job_id/bee_type/status/started_at/progress 5 个字段。
    其他字段返回是给 V1+ 用。
    """

    job_id: UUID
    short_id: str
    bee_type: str
    status: str
    priority: int = 1
    progress: float = 0.0
    current_step: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None


class AuditEntry(BaseModel):
    """GET /api/v0/audit 列表项。

    Portal audit.html 期望字段。
    """

    id: int
    ts: datetime
    actor: str
    action: str
    resource: str | None = None
