"""Queen 入口 - 启动 FastAPI 服务。"""

import uvicorn

from beeos_core.config import get_settings
from beeos_core.logging import configure_logging


def main() -> None:
    """启动 Queen HTTP 服务。"""
    configure_logging()
    settings = get_settings()
    uvicorn.run(
        "queen.api.app:app",
        host="0.0.0.0",
        port=8080,
        reload=settings.env == "development",
        log_config=None,  # 使用 structlog
    )


if __name__ == "__main__":
    main()
