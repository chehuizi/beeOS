"""MonthCloseBox - 会计月结自动化 beeBox（M0 独立运行版）。

按 beeBox = workload 原则：
- Box 暴露 schema（数据契约）+ adapters（数据工具）+ workflow（步骤声明）
- Box 不做决策，不跑算法，不连 Queen
- Box 可独立通过 `python -m month_close` 跑

bee 加载 Box 时只读 MANIFEST + WORKFLOW + 调 run_step()。
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
