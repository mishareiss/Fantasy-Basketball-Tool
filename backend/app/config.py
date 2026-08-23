"""Application settings, loaded from the environment / repo-root `.env`."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/config.py -> backend/app -> backend -> repo root
REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Every configurable value comes from here; nothing is hardcoded at call sites."""

    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database
    database_url: str = "postgresql+psycopg://fbb:fbb@localhost:5432/fbb"

    # ESPN league access (cookies belong to our own account, for our own league)
    espn_s2: str | None = None
    swid: str | None = None
    espn_league_id: int | None = None
    espn_season: int | None = None

    # Future projection / odds sources
    balldontlie_api_key: str | None = None
    the_odds_api_key: str | None = None

    # CORS: origins allowed to call this API (the Next.js dev server by default)
    cors_origins: list[str] = ["http://localhost:3000"]


@lru_cache
def get_settings() -> Settings:
    """Cached settings instance, so the `.env` is read once per process."""
    return Settings()
