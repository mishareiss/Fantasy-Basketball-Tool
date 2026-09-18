"""The odds model: American odds -> implied probability -> de-vig -> a fair per-game number.

Pure arithmetic, so this file needs no database and no fixtures. The property the whole
feature leans on is the first section's last test: an even, one-sided or absent price leaves
the value EXACTLY equal to the line, at every sigma. That is what makes "type in 9.5 assists
and nothing else" mean 9.5 assists.
"""

import pytest

from app.ranking.market import (
    EVEN_PROBABILITY,
    MIN_LINE_SCALE,
    devig,
    dispersion,
    fair_value,
    implied_probability,
)

# The shipped default (Settings.market_sigma_frac). Pinned here rather than read from
# settings, so a locally-calibrated .env can't move the numbers this file asserts.
SIGMA = 0.25


# --- American odds -> implied probability ---------------------------------------------------


def test_a_favourite_prices_above_an_even_chance():
    """-150 risks 150 to win 100: break even at 150/250."""
    assert implied_probability(-150) == pytest.approx(0.6)


def test_an_underdog_prices_below_an_even_chance():
    """+150 risks 100 to win 150: break even at 100/250."""
    assert implied_probability(150) == pytest.approx(0.4)


def test_the_standard_juice_is_a_hair_over_half():
    assert implied_probability(-110) == pytest.approx(110 / 210)


def test_no_price_is_no_probability_rather_than_a_default():
    assert implied_probability(None) is None
    # Zero isn't a price anyone writes; treated as absent, not as a division by 100.
    assert implied_probability(0) is None


# --- de-vig ---------------------------------------------------------------------------------


def test_a_two_sided_line_comes_back_summing_to_one():
    """The whole point: the raw pair sums to more than 1, and the excess is the book's margin."""
    over, under = -140, 115
    raw_total = implied_probability(over) + implied_probability(under)
    assert raw_total > 1.0  # the overround is really there

    fair_over = devig(over, under)
    fair_under = devig(under, over)
    assert fair_over + fair_under == pytest.approx(1.0)
    # Removed proportionally, so the fair price is below the vigged one on both sides.
    assert fair_over < implied_probability(over)


def test_the_standard_minus_110_pair_de_vigs_to_a_coin_flip():
    assert devig(-110, -110) == pytest.approx(0.5)


def test_a_juiced_but_symmetric_pair_is_still_a_coin_flip():
    """-200/-200 is a 9% hold and no opinion; de-vigging has to say so."""
    assert devig(-200, -200) == pytest.approx(0.5)


def test_a_favoured_over_de_vigs_above_a_half():
    assert devig(-200, 170) > 0.5


@pytest.mark.parametrize(
    "over, under",
    [(-135, None), (None, 120), (None, None), (-135, 0)],
    ids=["over only", "under only", "neither", "a zero on one side"],
)
def test_a_line_we_cannot_de_vig_falls_back_to_even(over, under):
    """One price can't be de-vigged: there's nothing to measure the margin against.

    Reading -135 alone as a 57% chance would take the book's whole margin as an opinion about
    the player, which is a worse answer than "we have a line and no usable price".
    """
    assert devig(over, under) == EVEN_PROBABILITY


# --- the dispersion dial --------------------------------------------------------------------


def test_sigma_scales_with_the_line_so_one_fraction_fits_every_stat():
    assert dispersion(28.0, SIGMA) == pytest.approx(7.0)
    assert dispersion(4.0, SIGMA) == pytest.approx(1.0)


def test_a_small_line_still_gets_room_to_move():
    """A 0.4-steals line at frac * line would have a sigma no price could shift."""
    assert dispersion(0.4, SIGMA) == pytest.approx(SIGMA * MIN_LINE_SCALE)


# --- line + price -> a fair value ------------------------------------------------------------


@pytest.mark.parametrize(
    "over, under",
    [(None, None), (-110, -110), (-135, None), (None, 120), (-200, -200)],
    ids=["no price", "even", "over only", "under only", "juiced but symmetric"],
)
def test_an_unshaded_price_leaves_the_line_exactly_where_the_book_put_it(over, under):
    """The property the entry UI leans on: a bare line stores and prices as itself."""
    assert fair_value(9.5, over, under, sigma_fraction=SIGMA) == pytest.approx(9.5)


@pytest.mark.parametrize("sigma", [0.0, 0.05, 0.25, 0.9])
def test_and_it_does_so_at_every_setting_of_the_dial(sigma):
    """PHI_INV(0.5) is 0, so sigma multiplies nothing. Calibrating can't corrupt a bare line."""
    assert fair_value(27.5, sigma_fraction=sigma) == pytest.approx(27.5)


def test_a_favoured_over_lifts_the_value_above_the_line():
    """-150 over / +150 under: the market thinks the truth is above the midpoint."""
    assert fair_value(9.5, -150, 150, sigma_fraction=SIGMA) > 9.5


def test_a_favoured_under_drops_the_value_below_the_line():
    assert fair_value(9.5, 150, -150, sigma_fraction=SIGMA) < 9.5


def test_the_two_shade_by_the_same_amount_in_opposite_directions():
    """Symmetric prices are symmetric opinions; the normal model has to keep them so."""
    up = fair_value(9.5, -150, 150, sigma_fraction=SIGMA) - 9.5
    down = 9.5 - fair_value(9.5, 150, -150, sigma_fraction=SIGMA)
    assert up == pytest.approx(down)


def test_the_dial_sets_how_far_a_shaded_price_moves_the_number():
    """The only thing sigma does — and it does it monotonically, which is what makes it tunable."""
    line = 27.5
    shifts = [
        fair_value(line, -200, 170, sigma_fraction=sigma) - line for sigma in (0.05, 0.25, 0.5)
    ]
    assert shifts == sorted(shifts)
    assert shifts[0] > 0
    # Twice the dispersion, twice the shift: the shift is linear in sigma.
    doubled = fair_value(line, -200, 170, sigma_fraction=0.5) - line
    assert doubled == pytest.approx(2 * (fair_value(line, -200, 170, sigma_fraction=0.25) - line))


def test_a_wildly_shaded_small_line_is_clamped_at_zero_rather_than_going_negative():
    """No counting stat is negative, however the price reads."""
    assert fair_value(0.5, 5000, -20000, sigma_fraction=2.0) == 0.0


def test_an_extreme_price_does_not_blow_up_the_quantile():
    """A de-vigged probability can land on 0 or 1, where PHI_INV is undefined.

    Clamped rather than raised: the answer is "as far that way as this model goes".
    """
    assert fair_value(20.0, -100000, 100000, sigma_fraction=SIGMA) > 20.0
    assert fair_value(20.0, 100000, -100000, sigma_fraction=SIGMA) < 20.0


def test_a_zero_line_stays_zero_when_nobody_is_shading_it():
    assert fair_value(0.0, sigma_fraction=SIGMA) == 0.0
