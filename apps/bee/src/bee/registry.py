"""BeeBox 注册表 - bee_type 字符串 → Box 模块映射。

M0 实现：硬编码 + 静态 import（V1+ 用 entry_points / importlib.metadata 自动发现）。
每个 Box 模块必须暴露：
  - MANIFEST (dict)    声明 schemas/tools/version
  - WORKFLOW (list)    步骤顺序
  - run_step(name, ctx, prev)  单步执行
"""

from __future__ import annotations

import importlib
from types import ModuleType


# bee_type → Python 模块路径
_BOX_MODULES: dict[str, str] = {
    "month_close": "month_close",
}


def _load_box(box_type: str) -> ModuleType:
    """动态加载 Box 模块。"""
    module_name = _BOX_MODULES.get(box_type)
    if module_name is None:
        raise ValueError(
            f"Unknown bee_type: {box_type}. Registered: {list(_BOX_MODULES.keys())}"
        )
    return importlib.import_module(module_name)


def list_supported() -> list[str]:
    """列出所有已注册的 bee_type。"""
    return list(_BOX_MODULES.keys())


def get_manifest(box_type: str) -> dict:
    """取 Box 的 MANIFEST 字典。"""
    box = _load_box(box_type)
    return box.MANIFEST  # type: ignore[attr-defined]


def get_workflow(box_type: str) -> list[dict]:
    """取 Box 的 WORKFLOW 步骤列表。"""
    box = _load_box(box_type)
    return box.WORKFLOW  # type: ignore[attr-defined]


def run_step(box_type: str, step_name: str, context: dict, prev_outputs: dict) -> dict:
    """执行 Box 单步。"""
    box = _load_box(box_type)
    return box.run_step(step_name, context, prev_outputs)  # type: ignore[attr-defined]
