"""Application settings, loaded from the environment / repo-root `.env`."""

from datetime import date
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/config.py -> backend/app -> backend -> repo root
REPO_ROOT = Path(__file__).resolve().parents[2]

# When an NBA season starts, near enough. ESPN labels a season by the year it *ends*
# (ESPN_SEASON=2027 is the 2026-27 season), so season 2027 opens on 2026-10-01.
SEASON_START = (10, 1)


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

    # The date ages are computed as of. Ages must be reproducible: computing them from
    # `date.today()` would silently change a player's stored age (and therefore his dynasty
    # value) between two runs a month apart, and would be wrong on draft day for anyone with a
    # birthday in between. Defaults to the start of the configured ESPN season; override with
    # AGE_AS_OF=2026-10-01 to pin it to anything else, e.g. the actual draft date.
    age_as_of: date | None = None

    # Future projection / odds sources
    balldontlie_api_key: str | None = None
    the_odds_api_key: str | None = None

    # CORS: origins allowed to call this API (the Next.js dev server by default)
    cors_origins: list[str] = ["http://localhost:3000"]

    def resolved_age_as_of(self) -> date:
        """The date every stored age is computed at — explicit setting, or season start.

        Falls back to the current calendar year's season only when ESPN_SEASON is unset, which
        in practice means a checkout with no league configured.
        """
        if self.age_as_of is not None:
            return self.age_as_of
        season = self.espn_season or (date.today().year + 1)
        return date(season - 1, *SEASON_START)


@lru_cache
def get_settings() -> Settings:
    """Cached settings instance, so the `.env` is read once per process."""
    return Settings()
