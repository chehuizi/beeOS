"""
runtime.models - TaskRun durable object + 状态机 + Acceptance 事件 + event log

按 [beebox-design.md §3.4] 实现：
- Task Run 是 durable object —— 持久化对象（不依赖 instance 内存）
- 9 状态状态机（履约层）：triggered / running / RETRYING / PARTIALLY_COMPLETED /
  WAITING_EXTERNAL / COMPENSATING / REJECTED / COMPLETED / FAILED
- Acceptance 4 状态（独立阶段）：AWAITING_ACCEPTANCE / ACCEPTED / REJECTED /
  COMPENSATING_ACCEPTANCE
- 12 字段履约事实（originator / intent / contract_ref / release_id /
  beeline_id+version / runtime_id / instance_id / result / acceptance /
  evidence / cost / latency）

PoC 2 范围：
- 状态机支持 triggered / running / COMPLETED / FAILED（其余状态下一轮）
- Acceptance 完整支持 4 状态转移
- 其余字段结构就位
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


# ============================================================
# 状态机枚举
# ============================================================


class TaskRunStatus(str, Enum):
    """Task Run 9 状态（履约层）

    PoC 2 实现：
    - triggered / running / COMPLETED / FAILED
    下一轮：
    - RETRYING / PARTIALLY_COMPLETED / WAITING_EXTERNAL / COMPENSATING / REJECTED
    """

    TRIGGERED = "triggered"
    RUNNING = "running"
    RETRYING = "retrying"  # placeholder，本 PoC 不实现转移
    PARTIALLY_COMPLETED = "partially_completed"  # placeholder
    WAITING_EXTERNAL = "waiting_external"  # placeholder
    COMPENSATING = "compensating"  # placeholder
    REJECTED = "rejected"  # placeholder
    COMPLETED = "completed"
    FAILED = "failed"


# 本 PoC 支持的状态转移白名单
ALLOWED_TRANSITIONS: dict[TaskRunStatus, set[TaskRunStatus]] = {
    TaskRunStatus.TRIGGERED: {TaskRunStatus.RUNNING, TaskRunStatus.FAILED},
    TaskRunStatus.RUNNING: {
        TaskRunStatus.COMPLETED,
        TaskRunStatus.FAILED,
        TaskRunStatus.RETRYING,
        TaskRunStatus.WAITING_EXTERNAL,
    },
    TaskRunStatus.RETRYING: {TaskRunStatus.RUNNING, TaskRunStatus.FAILED},
    TaskRunStatus.WAITING_EXTERNAL: {TaskRunStatus.RUNNING, TaskRunStatus.FAILED},
    TaskRunStatus.COMPLETED: set(),  # 终态
    TaskRunStatus.FAILED: set(),  # 终态
}


class AcceptanceStatus(str, Enum):
    """Acceptance 4 状态（独立阶段）

    完整支持：
    - AWAITING_ACCEPTANCE：task run COMPLETED 后进入，等验收判定
    - ACCEPTED：业务履约成功
    - REJECTED：业务履约失败（contract 被违反）
    - COMPENSATING_ACCEPTANCE：验收触发补偿（执行反向 op 后回 AWAITING_ACCEPTANCE）
    """

    AWAITING_ACCEPTANCE = "awaiting_acceptance"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    COMPENSATING_ACCEPTANCE = "compensating_acceptance"


# ============================================================
# Event log（durable）
# ============================================================


class EventLogEntry(BaseModel):
    """task run event log 单条记录"""

    timestamp: datetime
    event: str  # task_triggered / op_start / op_finish / status_change / acceptance_*
    op_id: Optional[str] = None
    detail: Optional[dict[str, Any]] = None


class EventLog(BaseModel):
    """task run 持久化 event log"""

    entries: list[EventLogEntry] = Field(default_factory=list)

    def append(self, event: str, op_id: Optional[str] = None, detail: Optional[dict[str, Any]] = None) -> None:
        self.entries.append(
            EventLogEntry(
                timestamp=datetime.now(timezone.utc),
                event=event,
                op_id=op_id,
                detail=detail or {},
            )
        )


# ============================================================
# Operation 执行记录（durable）
# ============================================================


class OperationRecord(BaseModel):
    """单个 operation 在 task run 内的执行记录"""

    op_id: str
    status: Literal["pending", "running", "completed", "failed", "skipped"] = "pending"
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    input: Optional[dict[str, Any]] = None
    output: Optional[dict[str, Any]] = None
    error: Optional[str] = None


# ============================================================
# 12 字段履约事实
# ============================================================


class TaskRunIdentity(BaseModel):
    """task run 创建瞬间固化的 identity（不可变）"""

    task_run_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    originator: str  # 请求方
    intent: str  # task type
    contract_ref: str  # 引用 result_schema id
    release_id: str  # 绑定的 release
    beeline_id: str
    beeline_version: int
    runtime_id: str
    instance_id: str

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None


class AcceptanceEvaluation(BaseModel):
    """Acceptance 判定结果（task_run.acceptance 字段）"""

    status: AcceptanceStatus
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    passed_rules: list[str] = Field(default_factory=list)
    failed_rules: list[dict[str, Any]] = Field(default_factory=list)
    exception_action: Optional[str] = None  # no_deliver / rollback / escalate
    queen_decision: Optional[str] = None  # queen 决策（escalate 时）


class Evidence(BaseModel):
    """task run 执行的证据包"""

    event_log: EventLog
    operations: dict[str, OperationRecord]  # 按 op_id 索引
    total_cost: float = 0.0
    total_latency_ms: int = 0


# ============================================================
# TaskRun 顶层（durable object）
# ============================================================


class TaskRun(BaseModel):
    """Task Run durable object

    12 字段履约事实：
    - identity（originator / intent / contract_ref / release_id /
      beeline_id+version / runtime_id / instance_id）
    - status（9 状态机）
    - current_op（当前执行的 op）
    - inputs / outputs（按 op_id 索引的 operation 记录）
    - event_log（持久化事件流）
    - result（业务结果数据）
    - acceptance（验收判定）
    - evidence（证据包：event_log + operations + cost + latency）
    """

    identity: TaskRunIdentity
    status: TaskRunStatus = TaskRunStatus.TRIGGERED
    current_op: Optional[str] = None
    operations: dict[str, OperationRecord] = Field(default_factory=dict)
    event_log: EventLog = Field(default_factory=EventLog)
    result: Optional[dict[str, Any]] = None
    acceptance: Optional[AcceptanceEvaluation] = None
    evidence: Evidence = Field(default_factory=lambda: Evidence(
        event_log=EventLog(),
        operations={},
    ))

    def transition_to(self, new_status: TaskRunStatus, reason: Optional[str] = None) -> None:
        """状态机转移（白名单校验）"""
        allowed = ALLOWED_TRANSITIONS.get(self.status, set())
        if new_status not in allowed:
            raise ValueError(
                f"illegal task run transition: {self.status.value} -> {new_status.value}"
            )
        old_status = self.status
        self.status = new_status
        # 进入 running 时记录 started_at
        if new_status == TaskRunStatus.RUNNING and self.identity.started_at is None:
            self.identity.started_at = datetime.now(timezone.utc)
        # 进入终态时记录 finished_at
        if new_status in (TaskRunStatus.COMPLETED, TaskRunStatus.FAILED):
            self.identity.finished_at = datetime.now(timezone.utc)
        self.event_log.append(
            event="status_change",
            detail={"from": old_status.value, "to": new_status.value, "reason": reason},
        )

    def is_terminal(self) -> bool:
        return self.status in (TaskRunStatus.COMPLETED, TaskRunStatus.FAILED)


def create_task_run(
    originator: str,
    intent: str,
    contract_ref: str,
    release_id: str,
    beeline_id: str,
    beeline_version: int,
    runtime_id: str,
    instance_id: str,
) -> TaskRun:
    """工厂函数：创建一个 task run（status=triggered）"""
    identity = TaskRunIdentity(
        originator=originator,
        intent=intent,
        contract_ref=contract_ref,
        release_id=release_id,
        beeline_id=beeline_id,
        beeline_version=beeline_version,
        runtime_id=runtime_id,
        instance_id=instance_id,
    )
    return TaskRun(identity=identity)