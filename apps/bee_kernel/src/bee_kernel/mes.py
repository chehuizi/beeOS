"""MES Executor - 按 BOM 调度 BeeBox 工具。"""
from __future__ import annotations

import importlib
import uuid
from datetime import datetime, timezone
from typing import Any, Protocol

from pydantic import BaseModel, Field

from bee_kernel.bom import BOM, BOMStep
from bee_kernel.task import Task


class BeeBoxProtocol(Protocol):
    """BeeBox runtime 接口契约（kernel 通过此调用 box）。

    实现类只要满足：
    - list_tools() -> list[str]  列出所有可用工具
    - run_tool(name, params) -> dict  调一个工具
    """

    def list_tools(self) -> list[str]: ...
    def run_tool(self, name: str, params: dict[str, Any]) -> dict[str, Any]: ...


class ExecutionPlan(BaseModel):
    """BOM + Task 实例化后的执行计划（带运行时状态）。"""

    exec_id: str
    task_id: str
    bom_id: str
    status: str = "Queued"  # Queued / Planning / Ready / Running / Done / Failed / AwaitingHuman
    current_step: int = 0
    step_results: list[dict[str, Any]] = Field(default_factory=list)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None


class MESExecutor:
    """按 BOM 顺序调度 BeeBox 工具。

    M0 实现：同步 + 同进程 + 简单错误处理。
    V1+：异步 + 重试 + AwaitingHuman + 并行（depends_on）。
    """

    def __init__(self) -> None:
        self._bee_box_cache: dict[str, BeeBoxProtocol] = {}

    def instantiate(self, task: Task, bom: BOM) -> ExecutionPlan:
        """BOM + Task → ExecutionPlan（准备执行计划）。"""
        return ExecutionPlan(
            exec_id=f"exec-{uuid.uuid4().hex[:12]}",
            task_id=task.task_id,
            bom_id=bom.bom_id,
        )

    def _load_bee_box(self, ref: str) -> BeeBoxProtocol:
        """根据 "module.path:ClassName" 加载并实例化 BeeBox runtime。"""
        if ref in self._bee_box_cache:
            return self._bee_box_cache[ref]
        module_name, _, class_name = ref.partition(":")
        if not module_name or not class_name:
            raise ValueError(f"bee_box_ref 格式错误（应是 'module.path:ClassName'）: {ref!r}")
        module = importlib.import_module(module_name)
        cls = getattr(module, class_name)
        instance = cls()
        self._bee_box_cache[ref] = instance
        return instance

    def execute(self, plan: ExecutionPlan, task: Task, bom: BOM, bee_box_ref: str) -> ExecutionPlan:
        """同步执行 BOM 的每个步骤。"""
        plan.status = "Running"
        plan.started_at = datetime.now(timezone.utc)
        bee_box = self._load_bee_box(bee_box_ref)

        try:
            for step in bom.steps:
                # 解析参数：$variable 从 task.params 替换
                params = bom.param_template(step, task.params)
                # 调 BeeBox 工具
                result = bee_box.run_tool(step.tool, params)
                # 记录步骤结果
                plan.step_results.append({
                    "seq": step.seq,
                    "tool": step.tool,
                    "params": params,
                    "output": result,
                })
                plan.current_step = step.seq
            plan.status = "Done"
        except Exception as e:
            plan.status = "Failed"
            plan.error = f"{type(e).__name__}: {e}"
        finally:
            plan.finished_at = datetime.now(timezone.utc)
        return plan
