"""beeOS Core - 跨服务共享代码。

Queen / Bee / BeeBox 都需要的基础能力：
- 配置管理 (pydantic-settings)
- 数据库连接 (sqlalchemy + asyncpg)
- 鉴权 (Guardian)
- 结构化日志 (structlog)
- 可观测性 (OpenTelemetry)
- 凭证加密 (cryptography)
"""

__version__ = "0.1.0"
