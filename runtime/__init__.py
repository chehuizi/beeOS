"""runtime - 进程内 executor + TaskRun + Acceptance

按 [beebox-design.md §3.4] 实现业务履约的运行时。

模块：
- models：TaskRun durable object + 状态机 + Acceptance 状态 + event log
- executor：beeline 进程内执行器（顺序 / 分支 / 终点）
- mock_runner：operation mock 实现（业务语义真的，基础设施假的）
- acceptance：Acceptance 阶段评估器（4 状态转移）
- queen：Queen 决策 hook（escalation decision）

PoC 2 范围：
- TaskRun 状态：triggered / running / COMPLETED / FAILED
- Acceptance 4 状态完整
- 顺序 + 分支执行
- Mock operation（无真实 bee / external_system 调用）
"""

from runtime.models import (
    ALLOWED_TRANSITIONS,
    AcceptanceEvaluation,
    AcceptanceStatus,
    EventLog,
    EventLogEntry,
    OperationRecord,
    TaskRun,
    TaskRunIdentity,
    TaskRunStatus,
    create_task_run,
)
from runtime.executor import BeelineExecutor
from runtime.mock_runner import MOCK_HANDLERS, MockRunner
from runtime.acceptance import evaluate_acceptance
from runtime.queen import default_queen_escalation_handler

__all__ = [
    # models
    "ALLOWED_TRANSITIONS",
    "AcceptanceEvaluation",
    "AcceptanceStatus",
    "EventLog",
    "EventLogEntry",
    "OperationRecord",
    "TaskRun",
    "TaskRunIdentity",
    "TaskRunStatus",
    "create_task_run",
    # executor
    "BeelineExecutor",
    # mock_runner
    "MOCK_HANDLERS",
    "MockRunner",
    # acceptance
    "evaluate_acceptance",
    # queen
    "default_queen_escalation_handler",
]