"""Application settings, loaded from the environment / repo-root `.env`."""

from datetime import date
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.valuation import DynastyCurve, TierParams

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

    # --- Dynasty age/longevity curve -------------------------------------------------------
    # The whole dynasty adjustment, as five numbers (see app.valuation.curve). They live here
    # rather than in the curve module so calibrating the board is an env change and a restart,
    # not a code change: DYNASTY_PRIME_START=25 DYNASTY_DECLINE_PER_YEAR=0.07 and the board
    # re-ranks. Defaults are a MODERATE curve — a starting point to calibrate against, not a
    # conviction.
    #
    # The prime band multiplies 1.0 at both ends: these ages are the reference the rest of the
    # curve is priced relative to.
    dynasty_prime_start: int = 24
    dynasty_prime_end: int = 27
    # Each year UNDER prime_start adds this much (0.04 -> a 20-year-old is worth 1.16x).
    dynasty_youth_bonus_per_year: float = 0.04
    # Each year OVER prime_end subtracts this much (0.05 -> a 32-year-old is worth 0.75x).
    dynasty_decline_per_year: float = 0.05
    # The floor the decline can't go below. A 38-year-old still scoring 45 a night has real
    # win-now value in a startup, and a curve that took him to zero would be lying about it.
    dynasty_min_multiplier: float = 0.40

    # --- Draft tiers ------------------------------------------------------------------------
    # Auto-tiering by score-gap clustering (FEATURE_SPEC 6), tunable for the same reason the
    # curve is: how coarse the tiers should be is a judgement about our draft, not a constant.
    # `GET /valuation/tiers` prints what these come to on the live board.
    #
    # A break opens where the drop to the next player exceeds this multiple of the MEDIAN drop
    # across the tiered pool. Higher -> fewer, larger tiers.
    tier_gap_multiple: float = 2.0
    # No tier smaller than this, unless the gap that opened it is genuinely huge (a true
    # outlier at the top of the board is allowed to stand alone). A tier of one is a ranking.
    tier_min_size: int = 2
    # Hard cap. Past about fifteen, a tiered board is a list again.
    tier_max: int = 15
    # How many top-ranked players get tiered at all. Gaps among players nobody will draft are
    # noise, and they would drag the median that every break is measured against.
    tier_pool: int = 150

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

    def dynasty_curve(self) -> DynastyCurve:
        """The active age/longevity curve, built from the DYNASTY_* settings.

        Built per call rather than cached, so a setting changed in a test (or in a REPL) takes
        effect the way an env var would. Construction validates the parameters, so a nonsense
        curve fails loudly at the first request that would have used it instead of silently
        ranking the board by it.
        """
        return DynastyCurve(
            prime_start=self.dynasty_prime_start,
            prime_end=self.dynasty_prime_end,
            youth_bonus_per_year=self.dynasty_youth_bonus_per_year,
            decline_per_year=self.dynasty_decline_per_year,
            min_multiplier=self.dynasty_min_multiplier,
        )

    def tier_params(self) -> TierParams:
        """The active tiering parameters, built from the TIER_* settings.

        Per call, not cached, for the same reason as `dynasty_curve()`: a setting changed in a
        test or a REPL has to take effect the way an env var would, and construction validates
        the numbers so a nonsense set fails at the request that would have tiered by it.
        """
        return TierParams(
            gap_multiple=self.tier_gap_multiple,
            min_size=self.tier_min_size,
            max_tiers=self.tier_max,
            pool=self.tier_pool,
        )


@lru_cache
def get_settings() -> Settings:
    """Cached settings instance, so the `.env` is read once per process."""
    return Settings()
