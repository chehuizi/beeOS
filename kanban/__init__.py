"""kanban - 履约盒子运行面板（CLI + Web）

按 [beebox-design.md §4.2] 实现：
- 只读 + 运维操作
- 显示 runtime / instance / task run / operation / 异常 / 指标
- 实时性 + 历史回溯

入口：
  CLI:  python -m kanban.cli
  Web:  python -m kanban.web    （简易 HTTP server）
"""

# 懒导入：避免 runpy "found in sys.modules" 警告
from runtime.store import TaskRunStore

__all__ = ["TaskRunStore"]