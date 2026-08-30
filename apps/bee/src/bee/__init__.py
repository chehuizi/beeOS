"""Bee — runtime 引擎（M0 独立运行版）。

按 bee = runtime 原则：
- orchestrator.py 加载 beeBox 跑 workflow（runtime 核心）
- state.py 5 状态机
- audit.py 本地 JSONL 审计
- registry.py 加载 Box 模块

V1+ 在 orchestrator 之上加 LLM ReAct 循环（manifest 给建议，LLM 决定）。
"""

from bee.orchestrator import Bee, BeeConfig, BeeResult
from bee.registry import list_supported
from bee.state import JobStateMachine, JobStatus

__all__ = [
    "Bee",
    "BeeConfig",
    "BeeResult",
    "JobStateMachine",
    "JobStatus",
    "list_supported",
]

__version__ = "0.1.0"
