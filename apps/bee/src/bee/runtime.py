"""Bee 核心 - ReAct 循环桩（M1 仅占位，V1 完整实现）。"""

from pydantic import BaseModel, Field

from beeos_core.logging import get_logger

logger = get_logger(__name__)


class BeeConfig(BaseModel):
    """Bee 配置。"""

    max_token_budget: int = Field(default=200_000, description="单 Bee 最大 Token 预算")
    max_tool_calls: int = Field(default=50, description="单 Bee 最大工具调用次数")
    max_execution_seconds: int = Field(default=1800, description="单 Bee 最大执行时长")
    llm_request_timeout_seconds: int = Field(default=60)


class Bee:
    """Bee 执行单元 - MVP 占位实现。"""

    def __init__(self, config: BeeConfig | None = None) -> None:
        self.config = config or BeeConfig()
        logger.info("bee.init", config=self.config.model_dump())

    async def run(self, task: str, context: dict) -> dict:
        """执行任务（M1 占位）。"""
        logger.warning("bee.run.not_implemented", task=task)
        return {
            "status": "not_implemented",
            "message": "Bee runtime is M1 stub. Real ReAct loop lands in V1.",
        }
