"""MonthCloseBox - 会计月结自动化 Box（M0 独立运行版）。

按 BeeBox 原则：
- Box 暴露数据结构（schema）+ 数据工具（adapters）+ 工作流声明（workflow）
- Box 不做决策，不跑 LLM，不连 Queen
- Box 可独立通过 `python -m month_close` 跑

Bee 加载 Box 时只读 MANIFEST + WORKFLOW + 调 run_step()。
"""

from month_close import adapters, schema, workflow
from month_close.workflow import MANIFEST, WORKFLOW, list_steps, run_step

__all__ = [
    "MANIFEST",
    "WORKFLOW",
    "adapters",
    "list_steps",
    "run_step",
    "schema",
    "workflow",
]

__version__ = "0.1.0"
