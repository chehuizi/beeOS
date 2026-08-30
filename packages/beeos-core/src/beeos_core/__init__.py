"""beeOS Core（M0 精简版）。

M0 只暴露 2 个模块：
- `beeos_core.config` — pydantic-settings（BEEOOS_ 前缀）
- `beeos_core.logging` — structlog 包装

DB / 鉴权 / Guardian 已 shelve 到 `_shelved/beeos_core/`，V1 恢复。
"""

__version__ = "0.1.0"
