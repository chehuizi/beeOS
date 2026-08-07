"""Queen 调度服务 - beeOS 任务调度大脑。

对应 [技术架构 §4.1 Queen]。
- 接收工单 → 拆解任务 → 派发 Bee → 监控执行 → 收敛结果
- 任务状态机：Draft → Queued → Running → AwaitingHuman → Done / Failed
"""

__version__ = "0.1.0"
