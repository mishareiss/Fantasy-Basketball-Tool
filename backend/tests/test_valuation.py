"""The age/longevity curve and the two value horizons — pure functions, no database.

These are the numbers the whole dynasty board rests on, so they are tested as arithmetic
rather than through the API: if the curve is wrong here, every ranking above it is wrong in a
way that looks plausible.
"""

import pytest

from app.config import get_settings
from app.valuation import (
    BAND_DECLINE,
    BAND_FLOOR,
    BAND_PRIME,
    BAND_UNKNOWN,
    BAND_YOUTH,
    SAMPLE_MAX_AGE,
    SAMPLE_MIN_AGE,
    DynastyCurve,
    age_multiplier,
    current_year_value,
    dynasty_value,
    sample_table,
    value_player,
)
from tests.conftest import DYNASTY_CURVE

# The curve the suite pins (tests/conftest.py), spelled out in the curve's own field names.
DEFAULTS = {name.removeprefix("dynasty_"): value for name, value in sorted(DYNASTY_CURVE.items())}


@pytest.fixture
def curve() -> DynastyCurve:
    """The default curve, spelled out here so a settings change can't move these tests."""
    return DynastyCurve(**DEFAULTS)


# --- the curve --------------------------------------------------------------------------


def test_the_prime_band_multiplies_by_one_at_both_ends(curve):
    """The prime band is the reference the rest of the curve is priced against."""
    assert curve.multiplier(24) == 1.0
    assert curve.multiplier(27) == 1.0
    assert all(curve.multiplier(age) == 1.0 for age in range(24, 28))


def test_a_young_player_is_worth_more_than_his_projection(curve):
    assert curve.multiplier(20) == pytest.approx(1.16)
    assert curve.multiplier(23) > 1.0


def test_an_aging_player_is_worth_less_than_his_projection(curve):
    assert curve.multiplier(32) == pytest.approx(0.75)
    assert curve.multiplier(28) < 1.0


def test_the_floor_holds_at_an_extreme_age(curve):
    """Without the floor a 45-year-old prices negative, which is not a discount but a bug."""
    assert curve.multiplier(45) == curve.min_multiplier
    assert curve.multiplier(60) == curve.min_multiplier
    assert curve.multiplier(45) == curve.multiplier(60)


def test_no_age_means_no_adjustment(curve):
    """A missing birthdate is missing information, not evidence of anything."""
    assert curve.multiplier(None) == 1.0
    assert curve.band(None) == BAND_UNKNOWN


def test_the_uplift_rises_monotonically_below_prime(curve):
    below = [curve.multiplier(age) for age in range(18, curve.prime_start + 1)]
    assert below == sorted(below, reverse=True), "younger must never be worth less"
    assert len(set(below)) == len(below), "every year below prime is a distinct step"


def test_the_discount_falls_monotonically_above_prime(curve):
    above = [curve.multiplier(age) for age in range(curve.prime_end, 46)]
    assert above == sorted(above, reverse=True), "older must never be worth more"
    assert above[0] == 1.0 and above[-1] == curve.min_multiplier


def test_the_band_says_which_segment_produced_the_number(curve):
    assert curve.band(20) == BAND_YOUTH
    assert curve.band(25) == BAND_PRIME
    assert curve.band(30) == BAND_DECLINE
    assert curve.band(44) == BAND_FLOOR


def test_a_steeper_setting_bites_harder():
    """The point of the parameters: change one number, the whole curve moves."""
    moderate = DynastyCurve(**DEFAULTS)
    steep = DynastyCurve(**{**DEFAULTS, "decline_per_year": 0.10})
    generous = DynastyCurve(**{**DEFAULTS, "youth_bonus_per_year": 0.08})

    assert steep.multiplier(32) < moderate.multiplier(32)
    assert generous.multiplier(20) > moderate.multiplier(20)
    # ...and the band nobody touched is untouched.
    assert steep.multiplier(25) == generous.multiplier(25) == 1.0


def test_moving_the_prime_band_moves_who_counts_as_young():
    later = DynastyCurve(**{**DEFAULTS, "prime_start": 27, "prime_end": 30})

    assert later.multiplier(26) > 1.0, "26 is now below prime"
    assert later.multiplier(29) == 1.0, "29 is now in it"
    assert DynastyCurve(**DEFAULTS).multiplier(26) == 1.0


@pytest.mark.parametrize(
    "broken",
    [
        {"prime_start": 30, "prime_end": 27},
        {"youth_bonus_per_year": -0.01},
        {"decline_per_year": -0.05},
        {"min_multiplier": 0.0},
        {"min_multiplier": 1.5},
    ],
)
def test_a_nonsense_curve_is_refused_at_construction(broken):
    """A bad env var must fail loudly, not silently rank the board by nonsense."""
    with pytest.raises(ValueError):
        DynastyCurve(**{**DEFAULTS, **broken})


def test_the_sample_table_covers_the_advertised_range(curve):
    table = sample_table(curve)

    assert [point.age for point in table] == list(range(SAMPLE_MIN_AGE, SAMPLE_MAX_AGE + 1))
    assert {point.band for point in table} == {BAND_YOUTH, BAND_PRIME, BAND_DECLINE, BAND_FLOOR}
    multipliers = [point.multiplier for point in table]
    assert multipliers == sorted(multipliers, reverse=True)


# --- the value engine -------------------------------------------------------------------


def test_current_year_value_is_the_projection_itself(curve):
    """Win-now value is age-agnostic by definition; anything else here is a bug."""
    assert current_year_value(48.25) == 48.25
    assert value_player(48.25, 21, curve).current_year == 48.25
    assert value_player(48.25, 38, curve).current_year == 48.25


def test_dynasty_value_is_the_projection_through_the_curve(curve):
    assert dynasty_value(50.0, 20, curve) == pytest.approx(50.0 * 1.16)
    assert dynasty_value(50.0, 25, curve) == 50.0
    assert dynasty_value(50.0, 32, curve) == pytest.approx(50.0 * 0.75)


def test_a_player_with_no_age_keeps_his_value_and_is_flagged_un_aged(curve):
    value = value_player(40.0, None, curve)

    assert value.multiplier == 1.0
    assert value.dynasty == value.current_year == 40.0
    assert value.age_adjusted is False


def test_a_player_with_an_age_is_flagged_aged_even_when_the_multiplier_is_one(curve):
    """1.0 from the prime band and 1.0 from a missing birthdate must be tellable apart."""
    value = value_player(40.0, 25, curve)

    assert value.multiplier == 1.0 and value.dynasty == 40.0
    assert value.age_adjusted is True


def test_youth_beats_equal_production_and_age(curve):
    """The whole reason the horizon exists: same projection, different futures."""
    assert dynasty_value(45.0, 21, curve) > dynasty_value(45.0, 33, curve)
    assert current_year_value(45.0) == current_year_value(45.0)


def test_age_multiplier_reads_the_curve_it_is_handed(curve):
    assert age_multiplier(20, curve) == curve.multiplier(20)
    assert age_multiplier(None, curve) == 1.0


def test_the_total_variant_is_the_same_arithmetic(curve):
    """Per-game is what the board ranks; a season total goes through the same functions."""
    assert dynasty_value(3400.0, 20, curve) == pytest.approx(3400.0 * 1.16)


# --- settings ---------------------------------------------------------------------------


def test_settings_build_the_curve_from_the_dynasty_env_vars():
    settings = get_settings()
    original = settings.dynasty_decline_per_year
    try:
        assert settings.dynasty_curve() == DynastyCurve(**DEFAULTS), (
            "defaults are the moderate curve"
        )

        settings.dynasty_decline_per_year = 0.12
        assert settings.dynasty_curve().multiplier(32) == pytest.approx(0.40)
    finally:
        settings.dynasty_decline_per_year = original

    assert settings.dynasty_curve() == DynastyCurve(**DEFAULTS)
