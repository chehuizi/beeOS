"""BOM 模型 + 缓存 - 内核的执行蓝图库。"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class BOMStep(BaseModel):
    """BOM 里的单个执行步骤。

    字段：
    - seq: 步骤序号
    - tool: 工具名（BeeBox 里注册的）
    - params: 参数模板，支持 $variable 占位符（从 task.params 替换）
    - depends_on: 依赖的前置步骤 seq（V1+ 启用并行执行时用）
    """

    seq: int
    tool: str
    params: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[int] = Field(default_factory=list)


class BOM(BaseModel):
    """物料清单（蓝图）- 定义任务的执行步骤。

    存放位置：boms/*.yaml（中央 Workshop）
    V1+ 加载路径：BOM 蓝图 → 缓存 → 实例化 → ExecutionPlan
    """

    bom_id: str
    workspace_id: str
    name: str = Field(description="任务目标描述，用于 (workspace_id, name) 索引")
    version: str = "v1"
    steps: list[BOMStep]
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    created_by: str = "system"

    def param_template(self, step: BOMStep, context: dict[str, Any]) -> dict[str, Any]:
        """把 step.params 里的 $variable 替换成 context 里的值。"""
        import json
        raw = json.dumps(step.params, ensure_ascii=False, default=str)
        for k, v in context.items():
            raw = raw.replace(f'"${k}"', json.dumps(v, ensure_ascii=False, default=str))
            raw = raw.replace(f"${{{k}}}", json.dumps(v, ensure_ascii=False, default=str))
        return json.loads(raw)


class BOMCache:
    """BOM 缓存：启动时从 boms/ 加载所有 .yaml 到内存。

    索引：(workspace_id, objective) -> BOM
    V1+ 加 LRU + 持久化。
    """

    def __init__(self, boms_dir: str | Path = "./boms") -> None:
        self.boms_dir = Path(boms_dir)
        self._boms: dict[str, BOM] = {}
        self._index: dict[tuple[str, str], str] = {}  # (workspace_id, objective) -> bom_id
        self._loaded = 0
        self._load()

    def _load(self) -> None:
        if not self.boms_dir.exists():
            return
        for yaml_file in sorted(self.boms_dir.glob("*.yaml")):
            try:
                data = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))
                if not data:
                    continue
                bom = BOM(**data)
                self._boms[bom.bom_id] = bom
                self._index[(bom.workspace_id, bom.name)] = bom.bom_id
                self._loaded += 1
            except Exception as e:
                # 不抛错，只警告（M0 demo 容错）
                print(f"[WARN] skip {yaml_file.name}: {e}")

    def get(self, workspace_id: str, objective: str) -> BOM | None:
        """按 (workspace_id, objective) 查 BOM。命中返回，未命中返回 None。"""
        bom_id = self._index.get((workspace_id, objective))
        if bom_id:
            return self._boms.get(bom_id)
        return None

    def put(self, bom: BOM) -> None:
        """缓存新 BOM（V1+ Bee Planner 规划完会调这个）。"""
        self._boms[bom.bom_id] = bom
        self._index[(bom.workspace_id, bom.name)] = bom.bom_id

    def list_all(self) -> list[BOM]:
        return list(self._boms.values())

    @property
    def loaded_count(self) -> int:
        return self._loaded
