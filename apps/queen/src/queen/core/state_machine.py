"""任务状态机 - 见 [技术架构 §4.1]。

5 个状态：Queued / Running / AwaitingHuman / Done / Failed
"""

from enum import Enum


class JobStatus(str, Enum):
    """任务状态。"""

    QUEUED = "Queued"
    RUNNING = "Running"
    AWAITING_HUMAN = "AwaitingHuman"
    DONE = "Done"
    FAILED = "Failed"


# 合法状态转换
ALLOWED_TRANSITIONS: dict[JobStatus, set[JobStatus]] = {
    JobStatus.QUEUED: {JobStatus.RUNNING, JobStatus.FAILED},
    JobStatus.RUNNING: {
        JobStatus.AWAITING_HUMAN,
        JobStatus.DONE,
        JobStatus.FAILED,
    },
    JobStatus.AWAITING_HUMAN: {JobStatus.RUNNING, JobStatus.FAILED},
    JobStatus.DONE: set(),
    JobStatus.FAILED: {JobStatus.QUEUED},  # 允许重试
}


def can_transition(from_status: JobStatus, to_status: JobStatus) -> bool:
    """判断状态转换是否合法。"""
    return to_status in ALLOWED_TRANSITIONS.get(from_status, set())
