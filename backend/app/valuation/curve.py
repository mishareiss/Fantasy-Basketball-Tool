"""The age/longevity curve: one transparent multiplier, tunable without touching code.

This is deliberately ONE multiplier applied to the current projection (FEATURE_SPEC 4), not a
multi-year model. A dynasty startup is decided in an afternoon, and a number we can explain in
a sentence — "he's 21, so we pay 1.12x for him" — is worth more than a simulation we can't
argue with. A richer longevity model (minutes trajectory, injury history, contract years) can
replace the inside of `multiplier()` later; everything above it only knows it gets a float.

The shape is four straight lines:

    youth      age < prime_start   1.0 + (prime_start - age) * youth_bonus_per_year
    prime      prime_start..end    1.0
    decline    age > prime_end     1.0 - (age - prime_end) * decline_per_year
    floor      ...clamped at       min_multiplier

Every one of those numbers comes from `Settings` (see `Settings.dynasty_curve`), because the
whole point of the curve is that we calibrate it by moving numbers, not by editing this file.
"""

from dataclasses import dataclass

# The ages the transparency endpoint tabulates. Wide enough to show the youth uplift, the
# prime plateau, the decline, and the floor actually biting, which is the point of looking.
SAMPLE_MIN_AGE = 19
SAMPLE_MAX_AGE = 40

# Which segment produced a multiplier. Returned beside the number so the curve reads as a
# shape rather than a column of floats.
BAND_YOUTH = "youth"
BAND_PRIME = "prime"
BAND_DECLINE = "decline"
BAND_FLOOR = "floor"
BAND_UNKNOWN = "unknown"

# Multipliers are rounded here so the curve is exactly the number we tuned. Without it
# `1.0 + 4 * 0.04` is 1.1600000000000001, which then propagates into every value on the board
# and into the sample table we calibrate against.
PRECISION = 4


@dataclass(frozen=True)
class DynastyCurve:
    """The tunable parameters, validated once at construction.

    Frozen because a curve is a snapshot of the settings that built it: a request that ranks a
    board with one set of numbers should not be able to have them change underneath it.
    """

    prime_start: int
    prime_end: int
    youth_bonus_per_year: float
    decline_per_year: float
    min_multiplier: float

    def __post_init__(self) -> None:
        """Refuse a curve that can't mean anything, naming the setting that's wrong.

        These come from the environment, so the failure mode without this is a board that
        silently ranks by nonsense — an inverted prime band that makes every player 'young',
        or a negative decline rate that pays MORE for a 38-year-old.
        """
        if self.prime_start > self.prime_end:
            raise ValueError(
                f"DYNASTY_PRIME_START ({self.prime_start}) must be <= DYNASTY_PRIME_END "
                f"({self.prime_end})"
            )
        if self.youth_bonus_per_year < 0:
            raise ValueError(
                f"DYNASTY_YOUTH_BONUS_PER_YEAR ({self.youth_bonus_per_year}) must be >= 0"
            )
        if self.decline_per_year < 0:
            raise ValueError(f"DYNASTY_DECLINE_PER_YEAR ({self.decline_per_year}) must be >= 0")
        if not 0 < self.min_multiplier <= 1:
            raise ValueError(f"DYNASTY_MIN_MULTIPLIER ({self.min_multiplier}) must be > 0 and <= 1")

    def band(self, age: int | None) -> str:
        """Which segment of the curve an age lands in — 'floor' when the clamp is what bit."""
        if age is None:
            return BAND_UNKNOWN
        if age < self.prime_start:
            return BAND_YOUTH
        if age <= self.prime_end:
            return BAND_PRIME
        if self._raw_decline(age) <= self.min_multiplier:
            return BAND_FLOOR
        return BAND_DECLINE

    def multiplier(self, age: int | None) -> float:
        """What a player of this age's projection is worth in dynasty terms.

        `None` is 1.0 on purpose: we have no birthdate for him, and inventing a discount from
        nothing would quietly bury a rookie we simply haven't matched to nba.com yet. The
        caller is told the value is un-aged (`PlayerValue.age_adjusted`) rather than being left
        to assume the 1.0 was earned — see `app.valuation.engine`.
        """
        if age is None:
            return 1.0
        if age < self.prime_start:
            return round(1.0 + (self.prime_start - age) * self.youth_bonus_per_year, PRECISION)
        if age <= self.prime_end:
            return 1.0
        return round(max(self.min_multiplier, self._raw_decline(age)), PRECISION)

    def _raw_decline(self, age: int) -> float:
        """The decline line before the floor clamps it."""
        return 1.0 - (age - self.prime_end) * self.decline_per_year


@dataclass(frozen=True)
class CurvePoint:
    """One row of the sample table: an age, what it multiplies by, and why."""

    age: int
    multiplier: float
    band: str


def sample_table(
    curve: DynastyCurve, start: int = SAMPLE_MIN_AGE, end: int = SAMPLE_MAX_AGE
) -> list[CurvePoint]:
    """The curve as a table, for eyeballing its shape without reading the code."""
    return [
        CurvePoint(age=age, multiplier=curve.multiplier(age), band=curve.band(age))
        for age in range(start, end + 1)
    ]
