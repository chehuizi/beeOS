"""5 状态机 - Bee 内存版（M0 不入 PG）。

对应原 state_machine.py 的简化版，逻辑一致：
  Queued → Running → {Done | Failed | AwaitingHuman}
  Failed → Queued（允许重试）

M0 没用 AwaitingHuman（V1 接入 ReAct + 人工审批时启用）。
"""

from __future__ import annotations

from enum import Enum
from typing import Self


class JobStatus(str, Enum):
    """任务状态。"""

    QUEUED = "Queued"
    RUNNING = "Running"
    AWAITING_HUMAN = "AwaitingHuman"
    DONE = "Done"
    FAILED = "Failed"


_ALLOWED: dict[JobStatus, set[JobStatus]] = {
    JobStatus.QUEUED: {JobStatus.RUNNING, JobStatus.FAILED},
    JobStatus.RUNNING: {JobStatus.DONE, JobStatus.FAILED, JobStatus.AWAITING_HUMAN},
    JobStatus.AWAITING_HUMAN: {JobStatus.RUNNING, JobStatus.FAILED},
    JobStatus.DONE: set(),
    JobStatus.FAILED: {JobStatus.QUEUED},  # 允许重试
}


class JobStateMachine:
    """任务状态机（M0 内存版）。"""

    def __init__(self, initial: JobStatus = JobStatus.QUEUED) -> None:
        self._status = initial
        self.history: list[tuple[JobStatus, JobStatus]] = []

    @property
    def status(self) -> JobStatus:
        return self._status

    def can_transition(self, to: JobStatus) -> bool:
        return to in _ALLOWED.get(self._status, set())

    def transition(self, to: JobStatus) -> Self:
        """状态转换。非法转换抛 ValueError。"""
        if not self.can_transition(to):
            raise ValueError(f"Illegal transition: {self._status.value} → {to.value}")
        self.history.append((self._status, to))
        self._status = to
        return self
