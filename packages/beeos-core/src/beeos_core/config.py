"""配置管理 - 所有 beeOS 服务共享同一份配置 Schema。"""

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class BeeOSSettings(BaseSettings):
    """beeOS 全局配置。

    所有环境变量必须 BEEOOS_ 前缀，便于私有化部署时一次注入。
    """

    model_config = SettingsConfigDict(
        env_prefix="BEEOOS_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # === 部署元信息 ===
    instance_id: str = Field(default="dev", description="实例 ID（多实例区分）")
    env: str = Field(default="development", description="development / staging / production")
    log_level: str = Field(default="INFO", description="DEBUG / INFO / WARNING / ERROR")

    # === 数据库 ===
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "beeos"
    postgres_user: str = "beeos"
    postgres_password: SecretStr = SecretStr("change-me")

    # === Redis ===
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_password: SecretStr | None = None
    redis_db: int = 0

    # === Guardian (安全) ===
    master_key: SecretStr = Field(
        default=SecretStr("dev-only-do-not-use-in-prod"),
        description="凭证加密主密钥，部署时必须替换",
    )
    api_token_secret: SecretStr = Field(
        default=SecretStr("dev-only-secret"),
        description="JWT / API Token 签名密钥",
    )
    api_token_ttl_hours: int = 24

    # === LLM (DeepSeek + 通义 AB) ===
    llm_primary: str = "deepseek-chat"
    llm_primary_api_key: SecretStr | None = None
    llm_primary_base_url: str = "https://api.deepseek.com"
    llm_fallback: str = "qwen-plus"
    llm_fallback_api_key: SecretStr | None = None
    llm_fallback_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    llm_request_timeout_seconds: int = 60
    llm_max_retries: int = 3

    # === 租户遥测 (默认关闭) ===
    vendor_telemetry_enabled: bool = False

    # === Portal ===
    portal_url: str = "http://localhost:3000"
    cors_allowed_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])

    # === Box 运行时 ===
    box_runtime_dir: str = "/var/lib/beeos/boxes"
    box_default_timeout_seconds: int = 1800  # 30 分钟

    @property
    def postgres_dsn(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:"
            f"{self.postgres_password.get_secret_value()}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def redis_url(self) -> str:
        auth = (
            f":{self.redis_password.get_secret_value()}@"
            if self.redis_password
            else ""
        )
        return f"redis://{auth}{self.redis_host}:{self.redis_port}/{self.redis_db}"


@lru_cache
def get_settings() -> BeeOSSettings:
    """获取全局配置（缓存，同一进程内只实例化一次）。"""
    return BeeOSSettings()
