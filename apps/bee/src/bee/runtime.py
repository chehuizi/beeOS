"""Bee 核心 - 派发到 Box。ReAct 循环 V1 实现，M1 仅直接调 Box.run()。"""

from pydantic import BaseModel, Field

from beeos_core.logging import get_logger

from bee.registry import get_box_class, list_supported

logger = get_logger(__name__)


class BeeConfig(BaseModel):
    """Bee 配置。"""

    max_token_budget: int = Field(default=200_000, description="单 Bee 最大 Token 预算")
    max_tool_calls: int = Field(default=50, description="单 Bee 最大工具调用次数")
    max_execution_seconds: int = Field(default=1800, description="单 Bee 最大执行时长")
    llm_request_timeout_seconds: int = Field(default=60)


class Bee:
    """Bee 执行单元 - 派发到 Box（M1 简化：同步调 Box.run()）。

    V1+ 在 Box.run() 之上包 LLM 推理循环（ReAct）。
    """

    def __init__(self, config: BeeConfig | None = None) -> None:
        self.config = config or BeeConfig()
        logger.info("bee.init", config=self.config.model_dump())

    async def run(self, task: str, context: dict) -> dict:
        """执行任务。

        Args:
            task: 任务标识（M1 仅用 bee_type，从 context 拿）
            context: {"bee_type": "month_close", "period": "2026-07",
                     "client_ids": [...], "approver": "..."}
                     （context 里除 bee_type 外的 key 都作为 Box 构造参数透传）

        Returns:
            Box.run() 的返回值（M1 是 dict，含 status / period / steps / result）。
            异常时返回 {"status": "failed", "error": "..."}。
        """
        bee_type = context.get("bee_type", task)
        logger.info("bee.run.start", bee_type=bee_type, task=task)

        box_cls = get_box_class(bee_type)
        if box_cls is None:
            logger.error("bee.run.unknown_bee_type", bee_type=bee_type, supported=list_supported())
            return {
                "status": "failed",
                "error": f"Unknown bee_type: {bee_type}. Supported: {list_supported()}",
            }

        # 构造 Box 实例：context 除 bee_type 外的 key 全作为 kwargs 透传
        box_kwargs = {k: v for k, v in context.items() if k != "bee_type"}
        try:
            box = box_cls(**box_kwargs)
            result = await box.run()
            logger.info(
                "bee.run.done",
                bee_type=bee_type,
                status=result.get("status") if isinstance(result, dict) else "unknown",
            )
            return result
        except Exception as e:
            logger.exception("bee.run.exception", bee_type=bee_type)
            return {
                "status": "failed",
                "error": f"{type(e).__name__}: {e}",
            }
