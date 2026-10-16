"""Live ESPN checks. Skipped without cookies, so CI and cold checkouts stay green.

Run them deliberately with `uv run pytest -m live`. They're the canary for ESPN changing an
endpoint or our cookies expiring — the two failure modes the recorded fixtures can't see.
"""

from datetime import date

import pytest

from app.config import Settings
from app.espn.client import ESPNClient, credentials_available
from app.espn.league import parse_pro_schedule, parse_rosters, parse_teams, resolve_my_team
from app.espn.ownership import parse_ownership
from app.espn.statsplits import parse_projections
from app.scoring import ScoringEngine, parse_league_settings
from app.scoring.projections import score_projection
from app.team.calendar import eastern_date

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not credentials_available(Settings()),
        reason="ESPN cookies not set in .env; skipping live pull",
    ),
]


@pytest.fixture(scope="module")
def client(live_settings) -> ESPNClient:
    """Built from the real `.env` (`live_settings`), not the suite's pinned fake league."""
    return ESPNClient.from_settings(live_settings)


@pytest.fixture(scope="module")
def player_pool(client) -> list[dict]:
    """One live pull, shared by the tests below — ESPN has no published rate limit to test."""
    return client.fetch_player_pool_pages()


def test_msettings_still_exposes_scoring_items(client):
    parsed = parse_league_settings(client.fetch_settings_view())

    assert parsed.scoring_type == "H2H_POINTS"
    assert parsed.coefficients, "ESPN returned no scoring items"
    assert parsed.roster_slots


def test_player_pool_is_a_plausible_size(player_pool):
    assert len(player_pool) > 400, "ESPN returned suspiciously few players"


def test_projections_still_come_back_in_the_player_payload(client, player_pool):
    """The whole board depends on ESPN shipping a projected split inside this one payload."""
    splits = parse_projections(player_pool, client.season)

    assert len(splits) > 100, "ESPN published far fewer projections than expected"
    assert all(split.stats for split in splits)
    assert all(split.projected_games and split.projected_games > 0 for split in splits)
    # Season totals, not per-game figures. `.get` because ESPN does publish a handful of
    # near-empty projections (a games count and nothing else) for deep-bench players.
    assert max(split.stats.get("PTS", 0.0) for split in splits) > 1000


def test_our_scoring_reproduces_espns_own_projected_points(client, player_pool):
    """`appliedTotal` is ESPN applying *our* coefficients server-side — a free correctness check.

    A mismatch means the stored formula or the stat-id map has drifted, and the board is
    ranking on the wrong numbers.
    """
    engine = ScoringEngine(parse_league_settings(client.fetch_settings_view()).coefficients)
    splits = parse_projections(player_pool, client.season)

    for split in splits:
        scored = score_projection(
            engine,
            split.stats,
            per_game_stats=split.average_stats,
            projected_games=split.projected_games,
        )
        assert scored.fantasy_points_total == pytest.approx(split.espn_applied_total, abs=0.01)


def test_adp_still_comes_back_in_the_player_payload(player_pool):
    records = parse_ownership(player_pool)

    assert len(records) == len(player_pool)
    assert all(record.adp is not None for record in records)
    # Somebody has to be an early pick, or ESPN has stopped publishing real ADP.
    assert min(record.adp for record in records) < 10


def test_espn_api_league_loads_with_our_cookies(client):
    """The `espn-api` path, used by the draft/box-score features that come later."""
    league = client.league

    assert league.settings.name
    assert len(league.teams) > 0


def test_the_in_season_views_resolve_our_team_and_date_the_schedule(client):
    """Schedule, mTeam and mRoster, live, against the real league from `.env`."""
    live = client

    teams = parse_teams(live.fetch_teams())
    resolution = resolve_my_team(teams, swid=live.credentials.swid, override=None)
    assert len(teams) == 9
    assert resolution.team_id == 8, resolution.warning

    games = parse_pro_schedule(live.fetch_pro_schedule())
    opening = [game for game in games if game.scoring_period_id == 1]
    assert opening, "ESPN's schedule has no period-1 games"
    assert {eastern_date(game.starts_at) for game in opening} == {date(2026, 10, 20)}

    rosters = parse_rosters(live.fetch_rosters())
    assert {record.team_id for record in rosters} == {team.team_id for team in teams}
