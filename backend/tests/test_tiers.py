"""The tiering engine: where a ranked list breaks into a draft plan, and why there.

Every fixture here is hand-built with the gaps visible in the literal, because the whole claim
of score-gap clustering is that you can look at a column of numbers and point at the breaks.
A test that needed a comment to explain where its tiers should fall would be testing the wrong
thing.
"""

import pytest

from app.config import Settings
from app.valuation import TierParams, assign_tiers, tier_structure

# Ordinary gaps of 1, with three obvious cliffs in it: 7, 8 and 16. The median gap is 1, so at
# the default 2.0x multiple the bar is 2 and exactly those three open tiers.
BOARD = [100, 99, 98, 97, 90, 89, 88, 80, 79, 78, 77, 76, 60, 59, 58]


def params(**overrides) -> TierParams:
    """The default parameters, with whichever one a test is about moved."""
    return TierParams(
        **{"gap_multiple": 2.0, "min_size": 2, "max_tiers": 15, "pool": 150, **overrides}
    )


def sizes(values, tier_params) -> list[int]:
    return [tier.size for tier in tier_structure(values, tier_params).tiers]


def test_a_new_tier_opens_at_every_unusually_large_drop():
    tiering = tier_structure(BOARD, params())

    assert tiering.typical_gap == 1.0
    assert tiering.threshold == 2.0
    assert [tier.size for tier in tiering.tiers] == [4, 3, 5, 3]
    # The three cliffs, and nothing else.
    assert [tier.gap for tier in tiering.breaks] == [7, 8, 16]
    assert [tier.start_index for tier in tiering.breaks] == [4, 7, 12]


def test_each_tier_reports_the_band_it_covers():
    tiers = tier_structure(BOARD, params()).tiers

    assert [(tier.value_high, tier.value_low) for tier in tiers] == [
        (100, 97),
        (90, 88),
        (80, 76),
        (60, 58),
    ]
    assert tiers[0].gap is None, "nothing opens the top tier but the top of the board"
    assert tiers[0].gap_ratio is None


def test_a_break_records_how_significant_the_gap_that_opened_it_was():
    """The number that answers 'why is this a break and the one above it isn't'."""
    tiering = tier_structure(BOARD, params())

    assert [tier.gap_ratio for tier in tiering.breaks] == [7.0, 8.0, 16.0]
    assert all(tier.gap > tiering.threshold for tier in tiering.breaks)


def test_assign_tiers_gives_one_tier_number_per_player():
    assert assign_tiers(BOARD, params()) == [1] * 4 + [2] * 3 + [3] * 5 + [4] * 3


def test_a_tier_number_never_decreases_as_value_decreases():
    """The invariant the board draws dividers from: tiers go down the page, never back up."""
    assigned = assign_tiers(BOARD, params())

    assert assigned == sorted(assigned)


def test_the_median_is_the_yardstick_so_the_cliffs_up_top_do_not_hide_the_ones_below():
    """The reason for the median. The mean of these gaps is 3.1 — a 2x bar of 6.2, which
    misses the 5-point break entirely and merges two real tiers into one."""
    values = [100, 70, 69, 68, 63, 62, 61]

    tiering = tier_structure(values, params())

    assert tiering.typical_gap == 1.0
    assert [tier.size for tier in tiering.tiers] == [1, 3, 3]


def test_a_higher_multiple_gives_fewer_larger_tiers():
    coarse = sizes(BOARD, params(gap_multiple=8.0))

    assert coarse == [12, 3], "only the 16-point cliff clears a bar of 8"
    assert len(coarse) < len(sizes(BOARD, params()))


def test_a_lower_multiple_gives_more_smaller_tiers():
    fine = sizes(BOARD, params(gap_multiple=0.5))

    assert len(fine) > len(sizes(BOARD, params()))
    assert sum(fine) == len(BOARD)


def test_the_minimum_size_drops_a_break_that_would_strand_a_player_alone():
    """A gap over the bar but not enormous: a tier of one is a ranking, not a tier."""
    values = [100, 97, 96, 95, 94, 93, 92]

    assert sizes(values, params(min_size=2)) == [7]
    # ...and the break is genuinely there, which min_size=1 shows.
    assert sizes(values, params(min_size=1)) == [1, 6]


def test_a_genuinely_huge_gap_stands_a_player_alone_anyway():
    """The exception the guard needs: a real outlier at the top IS his own tier."""
    values = [100, 90, 89, 88, 87, 86]

    tiering = tier_structure(values, params(min_size=2))

    assert [tier.size for tier in tiering.tiers] == [1, 5]
    assert tiering.tiers[1].gap == 10, "ten times the typical drop of one"


def test_the_maximum_merges_the_least_significant_breaks_first():
    """A cap has to keep the biggest cliffs, or it is just truncation."""
    assert sizes(BOARD, params(max_tiers=3)) == [7, 5, 3], "the 7-point break goes first"
    assert sizes(BOARD, params(max_tiers=2)) == [12, 3], "then the 8-point one"
    assert sizes(BOARD, params(max_tiers=1)) == [len(BOARD)]


def test_only_the_top_of_the_board_is_tiered():
    """Gaps among players nobody will draft are noise — and would move the median."""
    tiering = tier_structure(BOARD, params(pool=8))

    assert tiering.pool_size == 8
    assert [tier.size for tier in tiering.tiers] == [4, 3, 1]
    assigned = assign_tiers(BOARD, params(pool=8))
    assert assigned[:8] == [1, 1, 1, 1, 2, 2, 2, 3]
    assert assigned[8:] == [None] * (len(BOARD) - 8), "untiered, not tier 99"


def test_a_pool_bigger_than_the_board_tiers_everybody():
    tiering = tier_structure(BOARD, params(pool=1000))

    assert tiering.pool_size == len(BOARD)
    assert all(tier is not None for tier in tiering.assignments)


def test_a_board_with_no_unusual_gaps_is_one_tier():
    assert sizes([10, 9, 8, 7, 6, 5], params()) == [6]


def test_a_board_of_identical_values_has_nothing_to_break_on():
    tiering = tier_structure([5.0] * 6, params())

    assert tiering.typical_gap == 0.0
    assert [tier.size for tier in tiering.tiers] == [6]


def test_mostly_tied_values_fall_back_to_the_mean_rather_than_a_zero_yardstick():
    """A median of zero would make every non-zero drop a break. The mean still knows how far
    apart the players who *do* differ are."""
    tiering = tier_structure([10, 10, 10, 10, 10, 5], params(min_size=1))

    assert tiering.typical_gap == 1.0, "the mean of four zeros and a five"
    assert [tier.size for tier in tiering.tiers] == [5, 1]


def test_an_empty_board_tiers_to_nothing():
    tiering = tier_structure([], params())

    assert tiering.tiers == []
    assert tiering.assignments == []
    assert tiering.pool_size == 0


def test_a_single_player_is_a_single_tier():
    assert sizes([42.0], params()) == [1]


def test_values_have_to_arrive_ranked():
    """Tiering an unordered list would produce tiers no board could draw."""
    with pytest.raises(ValueError, match="descending"):
        tier_structure([10, 20, 5], params())


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"gap_multiple": 0}, "TIER_GAP_MULTIPLE"),
        ({"gap_multiple": -1.0}, "TIER_GAP_MULTIPLE"),
        ({"min_size": 0}, "TIER_MIN_SIZE"),
        ({"max_tiers": 0}, "TIER_MAX"),
        ({"pool": 0}, "TIER_POOL"),
    ],
)
def test_nonsense_parameters_name_the_env_var_that_is_wrong(overrides, message):
    with pytest.raises(ValueError, match=message):
        params(**overrides)


def test_the_settings_helper_hands_over_the_active_parameters():
    settings = Settings(tier_gap_multiple=3.5, tier_min_size=4, tier_max=6, tier_pool=200)

    assert settings.tier_params() == TierParams(gap_multiple=3.5, min_size=4, max_tiers=6, pool=200)


def test_the_defaults_are_the_ones_the_readme_documents():
    assert Settings().tier_params() == TierParams(
        gap_multiple=2.0, min_size=2, max_tiers=15, pool=150
    )
