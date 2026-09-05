"""Task 模型 - 内核的输入单元（工单）。"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field, field_validator


def _new_task_id() -> str:
    return f"task-{uuid.uuid4().hex[:12]}"


class Task(BaseModel):
    """工单 - 提交到内核的任务。

    字段语义：
    - workspace_id: 目标车间（路由目标）
    - objective: 任务目标描述（用于查 BOM 蓝图）
    - params: 任务参数（注入到 BOM step 的 params 模板）
    - priority: 0-9，0 最高
    """

    task_id: str = Field(default_factory=_new_task_id, description="任务 ID（自动生成）")
    workspace_id: str = Field(..., min_length=1, description="目标车间 ID")
    objective: str = Field(..., min_length=1, description="任务目标描述")
    params: dict[str, Any] = Field(default_factory=dict, description="任务参数（注入 BOM step）")
    priority: int = Field(default=5, ge=0, le=9, description="优先级 0-9，0 最高")
    submitted_by: str = Field(default="anonymous", description="提交者")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("objective")
    @classmethod
    def _strip_objective(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("objective 不能为空")
        return v

    @field_validator("workspace_id")
    @classmethod
    def _strip_workspace(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("workspace_id 不能为空")
        return v
