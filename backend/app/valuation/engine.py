"""The two value horizons, computed from a projection and an age. Pure functions, no DB.

FEATURE_SPEC 4 asks for both horizons on every player, always, because a dynasty *startup* is
the one draft where both lenses matter at the same pick: the win-now number says who helps
this season, the dynasty number says who we still want in 2030, and the interesting players
are the ones the two disagree about.

- Current-year value **is** the projected fantasy points. It is age-agnostic by definition, so
  there is nothing to compute — but it is a named function anyway, so the board asks the value
  engine for both horizons rather than reaching past it for one of them.
- Dynasty value is that same number through `DynastyCurve` (`app.valuation.curve`).

Both take "fantasy points": pass the per-game number for the per-game horizon (what the board
ranks by) and the season total for the total variant. The arithmetic is identical, so there is
one pair of functions rather than four.
"""

from dataclasses import dataclass

from app.valuation.curve import DynastyCurve


def current_year_value(fantasy_points: float) -> float:
    """Win-now value: the projection itself, under our scoring, untouched by age."""
    return fantasy_points


def age_multiplier(age: int | None, curve: DynastyCurve) -> float:
    """The curve's factor for an age. `None` -> 1.0; see `DynastyCurve.multiplier`.

    The curve is passed in rather than read from a module-level default, so there is exactly
    one place the active parameters come from (`Settings.dynasty_curve()`) and no call site can
    quietly rank a board with a curve nobody configured.
    """
    return curve.multiplier(age)


def dynasty_value(fantasy_points: float, age: int | None, curve: DynastyCurve) -> float:
    """Long-term value: the current-year number, aged."""
    return current_year_value(fantasy_points) * age_multiplier(age, curve)


@dataclass(frozen=True)
class PlayerValue:
    """Both horizons for one player, plus whether the dynasty number actually knows his age."""

    current_year: float
    dynasty: float
    multiplier: float
    # False when we hold no birthdate for him. The dynasty number is then just the current-year
    # number wearing a dynasty label: honest, but not a read on his future. The board carries
    # this through so an un-aged 1.0 is never mistaken for a player the curve looked at and
    # decided was in his prime.
    age_adjusted: bool


def value_player(fantasy_points: float, age: int | None, curve: DynastyCurve) -> PlayerValue:
    """Both horizons at once — what the board asks for, one row at a time."""
    multiplier = age_multiplier(age, curve)
    return PlayerValue(
        current_year=current_year_value(fantasy_points),
        dynasty=current_year_value(fantasy_points) * multiplier,
        multiplier=multiplier,
        age_adjusted=age is not None,
    )
