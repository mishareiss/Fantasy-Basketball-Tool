"""Our custom scoring formula: parsed from the real league settings, then applied."""

import pytest

from app.scoring import (
    ESPNSettingsError,
    ScoringCoefficient,
    parse_league_settings,
    score_stat_line,
    unscored_keys,
)
from app.scoring.engine import normalise_stat_line
from app.scoring.stats import STAT_ID_TO_NAME

# The scoring items our league actually uses, as pulled from ESPN. If ESPN's stat-id mapping
# ever shifts under us, this is the test that notices.
EXPECTED_POINTS = {
    "PTS": 3.0,
    "BLK": 7.0,
    "STL": 7.0,
    "AST": 4.0,
    "OREB": 0.5,
    "REB": 4.0,
    "EJ": -10.0,
    "FF": -2.0,
    "PF": -0.2,
    "TF": -0.7,
    "TO": -2.0,
    "FTM": 1.0,
    "3PM": 0.5,
    "FTMI": -0.5,
    "DD": 10.0,
    "TD": 30.0,
    "QD": 1000.0,
}


def test_stat_id_map_covers_the_stats_we_score():
    for stat_id, name in ((0, "PTS"), (1, "BLK"), (2, "STL"), (3, "AST"), (6, "REB"), (17, "3PM")):
        assert STAT_ID_TO_NAME[stat_id] == name


def test_parses_our_league_scoring_rules(msettings_payload):
    parsed = parse_league_settings(msettings_payload)

    assert parsed.scoring_type == "H2H_POINTS"
    assert parsed.as_points_map() == EXPECTED_POINTS
    assert len(parsed.coefficients) == len(EXPECTED_POINTS)
    # Sorted by stat id, so the order is stable run to run.
    stat_ids = [c.stat_id for c in parsed.coefficients]
    assert stat_ids == sorted(stat_ids)


def test_parses_roster_slots_dropping_unused_ones(msettings_payload):
    parsed = parse_league_settings(msettings_payload)

    assert parsed.roster_slots == {
        "PG": 1,
        "SG": 1,
        "SF": 1,
        "PF": 1,
        "C": 1,
        "UT": 2,
        "BE": 10,
        "IR": 3,
    }
    assert "G" not in parsed.roster_slots  # ESPN reports it as zero for our league


def test_rejects_a_payload_with_no_scoring_items():
    with pytest.raises(ESPNSettingsError):
        parse_league_settings({"settings": {"scoringSettings": {"scoringItems": []}}})


def test_scores_a_stat_line_under_our_formula(msettings_payload):
    """A 30/8/6 line with the peripherals, scored by hand against the stored coefficients."""
    coefficients = parse_league_settings(msettings_payload).coefficients

    stat_line = {
        "PTS": 30,
        "REB": 8,
        "OREB": 2,
        "AST": 6,
        "STL": 1.5,
        "BLK": 0.5,
        "TO": 3,
        "3PM": 3,
        "FTM": 7,
        "FTA": 8,
        "PF": 2,
        "DD": 1,
    }

    expected = (
        30 * 3.0  # PTS      90.0
        + 8 * 4.0  # REB      32.0
        + 2 * 0.5  # OREB      1.0
        + 6 * 4.0  # AST      24.0
        + 1.5 * 7.0  # STL     10.5
        + 0.5 * 7.0  # BLK      3.5
        + 3 * -2.0  # TO       -6.0
        + 3 * 0.5  # 3PM       1.5
        + 7 * 1.0  # FTM       7.0
        + 2 * -0.2  # PF      -0.4
        + 1 * 10.0  # DD      10.0
    )

    assert score_stat_line(stat_line, coefficients) == pytest.approx(expected)
    assert score_stat_line(stat_line, coefficients) == pytest.approx(173.1)


def test_ignores_stats_our_league_does_not_score(msettings_payload):
    """FTA is in the line but unscored, so it must not move the total."""
    coefficients = parse_league_settings(msettings_payload).coefficients

    with_fta = score_stat_line({"PTS": 10, "FTA": 9}, coefficients)
    without_fta = score_stat_line({"PTS": 10}, coefficients)

    assert with_fta == without_fta == pytest.approx(30.0)
    assert unscored_keys({"PTS": 10, "FTA": 9}, coefficients) == ["FTA"]


def test_accepts_stat_lines_keyed_by_espn_stat_id(msettings_payload):
    """ESPN's raw splits come back keyed by id; both shapes must score identically."""
    coefficients = parse_league_settings(msettings_payload).coefficients

    by_name = score_stat_line({"PTS": 20, "AST": 5, "BLK": 2}, coefficients)
    by_int_id = score_stat_line({0: 20, 3: 5, 1: 2}, coefficients)
    by_str_id = score_stat_line({"0": 20, "3": 5, "1": 2}, coefficients)

    assert by_name == by_int_id == by_str_id == pytest.approx(20 * 3 + 5 * 4 + 2 * 7)


def test_missing_and_none_stats_contribute_nothing():
    coefficients = [
        ScoringCoefficient(0, "PTS", 3.0),
        ScoringCoefficient(1, "BLK", 7.0),
        ScoringCoefficient(11, "TO", -2.0),
    ]

    assert score_stat_line({"PTS": 10}, coefficients) == pytest.approx(30.0)
    assert score_stat_line({"PTS": 10, "BLK": None}, coefficients) == pytest.approx(30.0)
    assert score_stat_line({}, coefficients) == 0.0


def test_normalise_stat_line_renames_ids_to_stat_names():
    assert normalise_stat_line({0: 20, "3": 5, "PTS": None, "REB": 8}) == {
        "PTS": 20.0,
        "AST": 5.0,
        "REB": 8.0,
    }
