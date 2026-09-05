"""Kernel - 5 组件编排器。"""
from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any

from bee_kernel.bom import BOMCache
from bee_kernel.mes import ExecutionPlan, MESExecutor
from bee_kernel.result import Result
from bee_kernel.task import Task
from bee_kernel.workspace import Workspace, WorkspaceRegistry


class Kernel:
    """beeOS 内核主类。

    5 组件按顺序协同：
      ① Task Receiver  →  ② Workspace Router  →  ③ BOM Cache
                                                            ↓ (miss)
                                                       ④ Bee Planner (V1+)
                                                            ↓
                                                        ⑤ MES Executor → ⑥ Result
    """

    def __init__(
        self,
        boms_dir: str | Path = "./boms",
        workspaces_path: str | Path = "./workspaces.yaml",
    ) -> None:
        self.bom_cache = BOMCache(boms_dir)
        self.workspace_registry = WorkspaceRegistry(workspaces_path)
        self.mes = MESExecutor()
        self._executions: dict[str, ExecutionPlan] = {}  # M0 内存存执行历史

    # === ② Workspace Router ===

    def resolve_workspace(self, workspace_id: str) -> Workspace:
        ws = self.workspace_registry.get(workspace_id)
        if ws is None:
            raise KeyError(f"workspace {workspace_id!r} 不存在（已注册: {self._known_workspaces()})")
        return ws

    def _known_workspaces(self) -> list[str]:
        return [w.workspace_id for w in self.workspace_registry.list_all()]

    # === ③ BOM Cache + 入口 ===

    def resolve_bom(self, workspace_id: str, objective: str):
        """查 BOM 蓝图。M0 不命中就抛错（V1+ 调 Bee Planner）。"""
        bom = self.bom_cache.get(workspace_id, objective)
        if bom is None:
            known = [(ws, b.name) for ws in self._known_workspaces()
                     for b in self.bom_cache.list_all() if b.workspace_id == ws]
            raise KeyError(
                f"找不到 BOM: workspace={workspace_id!r} objective={objective!r}\n"
                f"已注册: {known}\n"
                f"提示: 在 boms/ 加 YAML 文件，或实现 Bee Planner 兜底"
            )
        return bom

    # === 主入口 ===

    def submit(self, task: Task) -> Result:
        """提交任务并执行（同步，M0 简化）。

        6 步：Receiver → Router → BOM Cache → Planner(V1) → MES → Result
        """
        t0 = time.perf_counter()

        # ① Task Receiver 已经在 Task 构造时做了 schema 校验（field_validator）
        # 这里可以加：鉴权、限流、审计

        # ② Workspace Router
        workspace = self.resolve_workspace(task.workspace_id)

        # ③ BOM Cache（miss 时 V1+ 调 ④ Planner；M0 直接报错）
        bom = self.resolve_bom(task.workspace_id, task.objective)

        # ⑤ MES 实例化 + 执行
        plan = self.mes.instantiate(task, bom)
        self._executions[plan.exec_id] = plan
        plan = self.mes.execute(plan, task, bom, workspace.bee_box_ref)

        # ⑥ 汇总 Result
        elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)
        result = Result(
            task_id=task.task_id,
            exec_id=plan.exec_id,
            bom_id=bom.bom_id,
            status=plan.status,
            deliverables=plan.step_results,
            step_count=len(plan.step_results),
            elapsed_ms=elapsed_ms,
            error=plan.error,
        )
        return result

    # === 查询 ===

    def get_execution(self, exec_id: str) -> ExecutionPlan | None:
        return self._executions.get(exec_id)

    def list_boms(self) -> list[dict[str, Any]]:
        return [
            {
                "bom_id": b.bom_id,
                "workspace_id": b.workspace_id,
                "name": b.name,
                "version": b.version,
                "steps": len(b.steps),
            }
            for b in self.bom_cache.list_all()
        ]

    def list_workspaces(self) -> list[dict[str, Any]]:
        return [
            {
                "workspace_id": w.workspace_id,
                "domain": w.domain,
                "name": w.name,
                "bee_box_ref": w.bee_box_ref,
            }
            for w in self.workspace_registry.list_all()
        ]
