"""Workspace 注册表 - 内核的"车间"管理。

每个 Workspace 关联一个 BeeBox runtime（通过 bee_box_ref 字符串定位）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class Workspace(BaseModel):
    """车间 - 一个 BeeBox 跑任务的容器。

    字段：
    - workspace_id: 唯一标识
    - domain: 业务/研发/产品/质量（WMS 双层架构第一层：按领域隔离）
    - name: 显示名
    - bee_box_ref: 关联的 BeeBox，格式 "module.path:ClassName"
        例："bee_kernel.demo_box:WMSDemoBox"
    """

    workspace_id: str
    domain: str = Field(description="业务 / 研发 / 产品 / 质量")
    name: str
    bee_box_ref: str = Field(description="关联 BeeBox: 'module.path:ClassName'")


class WorkspaceRegistry:
    """从 YAML 加载 Workspace 表。

    M0 状态：启动时一次性加载到内存。
    V1+：动态注册 + 多租户。
    """

    def __init__(self, registry_path: str | Path = "./workspaces.yaml") -> None:
        self._path = Path(registry_path)
        self._workspaces: dict[str, Workspace] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        data = yaml.safe_load(self._path.read_text(encoding="utf-8"))
        if not data:
            return
        for ws_data in data.get("workspaces", []):
            ws = Workspace(**ws_data)
            self._workspaces[ws.workspace_id] = ws

    def get(self, workspace_id: str) -> Workspace | None:
        return self._workspaces.get(workspace_id)

    def list_all(self) -> list[Workspace]:
        return list(self._workspaces.values())
