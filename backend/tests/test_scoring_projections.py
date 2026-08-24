"""Pricing a projected stat line under our custom formula.

The strongest check here is against ESPN itself: `appliedTotal` is ESPN's own fantasy points
for a split, computed server-side with *our* league's coefficients. If our engine agrees with
it to the cent across the whole fixture, our scoring load is right.
"""

import pytest

from app.espn.statsplits import parse_projections
from app.scoring import ScoringEngine, parse_league_settings
from app.scoring.projections import score_projection


@pytest.fixture
def engine(msettings_payload) -> ScoringEngine:
    return ScoringEngine(parse_league_settings(msettings_payload).coefficients)


def test_totals_come_from_the_stored_coefficients(engine):
    scored = score_projection(
        engine, {"PTS": 100.0, "REB": 50.0, "BLK": 10.0}, projected_games=10.0
    )

    assert scored.fantasy_points_total == 100 * 3 + 50 * 4 + 10 * 7


def test_per_game_divides_the_total_by_projected_games(engine):
    scored = score_projection(engine, {"PTS": 100.0}, projected_games=10.0)

    assert scored.fantasy_points_per_game == 30.0
    assert scored.per_game_basis == "projected_games"


def test_falls_back_to_scoring_the_per_game_stats_without_a_games_count(engine):
    scored = score_projection(engine, {"PTS": 100.0}, per_game_stats={"PTS": 10.0})

    assert scored.fantasy_points_per_game == 30.0
    assert scored.per_game_basis == "per_game_stats"


def test_zero_games_is_never_treated_as_a_divisor(engine):
    scored = score_projection(
        engine, {"PTS": 100.0}, per_game_stats={"PTS": 10.0}, projected_games=0
    )

    assert scored.per_game_basis == "per_game_stats"


def test_no_games_and_no_per_game_stats_is_flagged_not_guessed(engine):
    """An invented 82-game season would quietly rank someone who may never play."""
    scored = score_projection(engine, {"PTS": 100.0})

    assert scored.fantasy_points_total == 300.0
    assert scored.fantasy_points_per_game == 0.0
    assert scored.per_game_basis == "unavailable"


def test_our_scoring_matches_espns_own_applied_total(engine, player_pool_payload):
    splits = parse_projections(player_pool_payload, 2027)
    assert splits, "fixture has no projections to check against"

    for split in splits:
        scored = score_projection(
            engine,
            split.stats,
            per_game_stats=split.average_stats,
            projected_games=split.projected_games,
        )
        assert scored.fantasy_points_total == pytest.approx(split.espn_applied_total, abs=0.01)
        assert scored.fantasy_points_per_game == pytest.approx(split.espn_applied_average, abs=0.01)
