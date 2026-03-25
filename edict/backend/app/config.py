"""Edict 配置管理 — 从环境变量加载所有配置。"""

from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # ── Postgres ──
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "edict"
    postgres_user: str = "edict"
    postgres_password: str = "edict_secret_change_me"
    database_url_override: str | None = None  # 直接设置 DATABASE_URL 环境变量时用

    # ── Redis ──
    redis_url: str = "redis://localhost:6379/0"

    # ── Server ──
    backend_host: str = "0.0.0.0"
    backend_port: int = 8000
    port: int = 8000
    secret_key: str = "change-me-in-production"
    debug: bool = False

    # ── OpenClaw ──
    openclaw_gateway_url: str = "http://localhost:18789"
    openclaw_bin: str = "openclaw"
    openclaw_project_dir: str | None = None

    # ── Legacy 兼容 ──
    legacy_data_dir: str = "../data"
    legacy_tasks_file: str = "../data/tasks_source.json"

    # ── 调度参数 ──
    stall_threshold_sec: int = 180
    max_dispatch_retry: int = 3
    dispatch_timeout_sec: int = 300
    heartbeat_interval_sec: int = 30
    scheduler_scan_interval_seconds: int = 60

    # ── 消息通知 ──
    notification_enabled: bool = True
    default_dispatch_channel: str = "feishu"

    @property
    def database_url(self) -> str:
        if self.database_url_override:
            return self.database_url_override
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def database_url_sync(self) -> str:
        """同步 URL，供 Alembic 使用。"""
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "env_prefix": "",
        "alias_generator": None,
        "populate_by_name": True,
        "extra": "ignore",
    }


@lru_cache
def get_settings() -> Settings:
    return Settings()
