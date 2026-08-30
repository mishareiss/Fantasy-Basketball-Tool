"""Value engine: both value horizons per player, computed on read.

Every player gets two numbers (FEATURE_SPEC 4): **current-year** value — his projection under
our scoring, win-now and age-agnostic — and **dynasty** value — the same projection run through
a transparent age/longevity curve. The board carries both and ranks by whichever horizon is
selected.

The curve's input has been available since `app.ages`: `Player.age`, from nba.com birthdates,
computed at a fixed `Settings.age_as_of` so the same player is the same age on every run.

Nothing here touches the database and nothing is stored. Value is computed on read, from the
projection row and the player's age, with the curve parameters from `Settings.dynasty_curve()`.
A `Valuation` table (value history, for dynasty trends over time) waits until we need to look
at how a player's value moved, rather than what it is now.
"""

from app.valuation.curve import (
    BAND_DECLINE,
    BAND_FLOOR,
    BAND_PRIME,
    BAND_UNKNOWN,
    BAND_YOUTH,
    PRECISION,
    SAMPLE_MAX_AGE,
    SAMPLE_MIN_AGE,
    CurvePoint,
    DynastyCurve,
    sample_table,
)
from app.valuation.engine import (
    PlayerValue,
    age_multiplier,
    current_year_value,
    dynasty_value,
    value_player,
)

__all__ = [
    "BAND_DECLINE",
    "BAND_FLOOR",
    "BAND_PRIME",
    "BAND_UNKNOWN",
    "BAND_YOUTH",
    "PRECISION",
    "SAMPLE_MAX_AGE",
    "SAMPLE_MIN_AGE",
    "CurvePoint",
    "DynastyCurve",
    "PlayerValue",
    "age_multiplier",
    "current_year_value",
    "dynasty_value",
    "sample_table",
    "value_player",
]
