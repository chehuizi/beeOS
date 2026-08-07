"""月结工作流 - M1 占位，V1 完整实现。"""

from pydantic import BaseModel

from beeos_core.logging import get_logger

logger = get_logger(__name__)


class MonthCloseWorkflow(BaseModel):
    """月结工作流（M1 占位）。"""

    period: str
    client_ids: list[str] = []

    async def run(self) -> dict:
        """执行月结（M1 stub）。"""
        logger.warning("month_close.not_implemented", period=self.period)
        return {
            "status": "not_implemented",
            "period": self.period,
            "message": "MonthCloseBox lands in V1. M1 demo only.",
        }
