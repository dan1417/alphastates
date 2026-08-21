"""Configuration settings for the indexer."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # PostgreSQL
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "indexer"
    postgres_password: str = "indexer"
    postgres_db: str = "bittensor"

    # Redis
    redis_host: str = "localhost"
    redis_port: int = 6379

    # Bittensor Blockchain
    rpc_url: str = "ws://185.189.45.20:9944"
    local_rpc_url: str = "ws://host.docker.internal:9944"
    dtao_start_block: int = 4920351
    batch_size: int = 10000
    max_workers: int = 50

    # Historical batch fetch: max seconds without any worker completing a block (stall detection)
    indexer_rpc_wait_timeout_seconds: float = 180.0
    # After a stall, restart the full batch this many times (each attempt uses a fresh process pool)
    indexer_rpc_fetch_retries: int = 5

    # Subtensor Connection Pool
    subtensor_pool_size: int = 50
    
    # Reset option
    reset_db: bool = False
    
    # Authentication
    jwt_secret_key: str = "CHANGE-THIS-TO-A-SECURE-RANDOM-KEY-IN-PRODUCTION"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 60 * 24 * 7  # 7 days
    anonymous_default_password: str = "anonymous_default_password_2024"
    # Only this user sees EVM and MevShield in pending mempool (comma-separated for multiple)
    mempool_admin_emails: str = "admin@gmail.com"

    @property
    def database_url(self) -> str:
        """Construct async PostgreSQL connection URL."""
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def redis_url(self) -> str:
        """Construct Redis connection URL."""
        return f"redis://{self.redis_host}:{self.redis_port}"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()

