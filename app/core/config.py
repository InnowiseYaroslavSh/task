from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-backed application settings."""

    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    database_url: str = "sqlite+aiosqlite:///./accelerator.db"
    jwt_secret: str = Field(default="dev-only-change-me", min_length=16)
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 15
    refresh_token_days: int = 30
    auto_create_tables: bool = False
    environment: str = "development"
    rate_limit_per_minute: int = 60


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
