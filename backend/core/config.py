"""Environment-based configuration using pydantic-settings."""

from typing import Optional

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+asyncpg://gitdeploy:gitdeploy@localhost:5432/gitdeploy"
    SYNC_DATABASE_URL: str = "postgresql://gitdeploy:gitdeploy@localhost:5432/gitdeploy"
    REDIS_URL: str = "redis://localhost:6379"
    GITHUB_CLONE_DIR: str = "/tmp/gitdeploy/repos"
    MAX_JOB_TIMEOUT_SECONDS: int = 1800
    MAX_DB_PROVISION_TIMEOUT_SECONDS: int = 60
    MAX_REPO_SIZE_MB: int = 500
    ALLOWED_ORIGINS: list[str] = ["http://localhost:5173"]
    SECRET_KEY: str = "YLSdu_26pOrLhe9qtZrOPkiMHVJCjLqr_X-mVxWma_g="
    DB_DETECTION_CONFIDENCE_THRESHOLD: float = 0.7
    DB_AI_FALLBACK_CONFIDENCE_THRESHOLD: float = 0.5
    CELERY_BROKER_URL: Optional[str] = None
    DEFAULT_ADMIN_USER: str = "admin"
    DEFAULT_ADMIN_PASSWORD: str = "admin"  # CHANGE THIS — set via DEFAULT_ADMIN_PASSWORD env var
    JWT_EXPIRY_HOURS: int = 24

    @property
    def effective_celery_broker(self) -> str:
        return self.CELERY_BROKER_URL or self.REDIS_URL

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
