"""Pulling the projected full-season split out of ESPN's player payload."""

from app.espn.statsplits import (
    parse_projection_entry,
    parse_projections,
    select_projected_split,
)

# The split ids that matter, spelled out: source 1 = projection, split type 0 = full season.
PROJECTED_SEASON = {"statSourceId": 1, "statSplitTypeId": 0}
ACTUAL_SEASON = {"statSourceId": 0, "statSplitTypeId": 0}
PROJECTED_LAST_7 = {"statSourceId": 1, "statSplitTypeId": 1}


def _entry(*splits: dict, player_id: int = 1) -> dict:
    player = {"id": player_id, "fullName": "Test Player", "stats": list(splits)}
    return {"id": player_id, "player": player}


def _split(base: dict, season: int, stats: dict, **extra) -> dict:
    return {**base, "seasonId": season, "stats": stats, **extra}


def test_picks_the_projected_full_season_split_over_the_others():
    entry = _entry(
        _split(ACTUAL_SEASON, 2027, {"0": 100.0}),
        _split(PROJECTED_LAST_7, 2027, {"0": 200.0}),
        _split(PROJECTED_SEASON, 2027, {"0": 300.0, "42": 60.0}),
    )

    parsed = parse_projection_entry(entry, 2027)

    assert parsed is not None
    assert parsed.season == 2027
    assert parsed.stats["PTS"] == 300.0
    assert parsed.projected_games == 60.0


def test_falls_back_to_the_newest_projection_when_the_season_has_none():
    """Out of season, ESPN's newest projection is still last season's — better than no board."""
    entry = _entry(
        _split(PROJECTED_SEASON, 2025, {"0": 100.0}),
        _split(PROJECTED_SEASON, 2026, {"0": 200.0}),
        _split(ACTUAL_SEASON, 2027, {"0": 0.0}),
    )

    parsed = parse_projection_entry(entry, 2027)

    assert parsed is not None
    # The row records the season it is really for, so the stand-in is never mistaken for 2027.
    assert parsed.season == 2026
    assert parsed.stats["PTS"] == 200.0


def test_a_player_with_no_projection_yields_nothing():
    entry = _entry(_split(ACTUAL_SEASON, 2027, {"0": 100.0}))

    assert select_projected_split(entry["player"], 2027) is None
    assert parse_projection_entry(entry, 2027) is None


def test_derived_rate_stats_never_reach_the_stat_line():
    """FG% and PPG share the `stats` map with counting totals; multiplying a rate would be wrong."""
    entry = _entry(
        _split(PROJECTED_SEASON, 2027, {"0": 300.0, "19": 0.52, "29": 25.0, "35": 2.6, "44": 0.3})
    )

    parsed = parse_projection_entry(entry, 2027)

    assert parsed is not None
    assert set(parsed.stats) == {"PTS"}


def test_zero_projected_games_is_unknown_not_zero():
    entry = _entry(_split(PROJECTED_SEASON, 2027, {"0": 0.0, "42": 0.0}))

    parsed = parse_projection_entry(entry, 2027)

    assert parsed is not None
    assert parsed.projected_games is None


def test_nulls_are_dropped_rather_than_read_as_zero():
    entry = _entry(_split(PROJECTED_SEASON, 2027, {"0": 300.0, "1": None, "2": "n/a"}))

    parsed = parse_projection_entry(entry, 2027)

    assert parsed is not None
    assert "BLK" not in parsed.stats and "STL" not in parsed.stats


def test_keeps_espns_own_fantasy_points_for_the_split():
    entry = _entry(
        _split(PROJECTED_SEASON, 2027, {"0": 300.0}, appliedTotal=900.0, appliedAverage=15.0)
    )

    parsed = parse_projection_entry(entry, 2027)

    assert parsed is not None
    assert parsed.espn_applied_total == 900.0
    assert parsed.espn_applied_average == 15.0


def test_parses_the_recorded_pool(player_pool_payload):
    splits = parse_projections(player_pool_payload, 2027)

    # The fixture deliberately spans owned stars and the unowned tail, so both paths are covered.
    assert 0 < len(splits) < len(player_pool_payload)
    assert all(split.stats for split in splits)
    assert all(split.projected_games and split.projected_games > 0 for split in splits)


def test_reads_a_real_players_projection(player_pool_payload):
    by_id = {split.espn_player_id: split for split in parse_projections(player_pool_payload, 2027)}
    sga = by_id[4278073]

    assert sga.stats["PTS"] > 1000  # a season total, not a per-game figure
    assert sga.average_stats["PTS"] == sga.stats["PTS"] / sga.projected_games


def test_deduplicates_across_pages(player_pool_payload):
    doubled = player_pool_payload + player_pool_payload

    assert len(parse_projections(doubled, 2027)) == len(
        parse_projections(player_pool_payload, 2027)
    )


def test_skips_entries_without_a_usable_player():
    assert parse_projection_entry({"id": 1}, 2027) is None
    assert parse_projection_entry({"player": {"fullName": "No Id"}}, 2027) is None
