"""Bee-Box 注册表。bee_type 字符串 → Box 类映射。

未来加新 Box 在这里注册一行即可。
"""

from typing import Type

from month_close.workflow import MonthCloseWorkflow

# 格式: "bee_type 字符串" → Box 类
# Box 类必须接受 kwargs (period, client_ids, approver, ...) 并暴露 async run()
BEE_REGISTRY: dict[str, Type] = {
    "month_close": MonthCloseWorkflow,
}


def get_box_class(bee_type: str) -> Type | None:
    """根据 bee_type 取 Box 类。找不到返回 None。"""
    return BEE_REGISTRY.get(bee_type)


def list_supported() -> list[str]:
    """列出所有已注册的 bee_type（用于 /health 报告和错误信息）。"""
    return list(BEE_REGISTRY.keys())
