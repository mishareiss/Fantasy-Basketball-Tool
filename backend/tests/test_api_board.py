"""GET /players/board — the first data-driven draft board."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.ages import sync_ages
from app.db.models import AdpEntry, Projection
from app.db.session import get_db
from app.espn.ownership import parse_ownership
from app.espn.players import parse_player_pool
from app.espn.statsplits import parse_projections
from app.espn.sync import (
    SyncSummary,
    sync_adp,
    sync_players,
    sync_projections,
    sync_scoring_settings,
)
from app.ingest import run_import
from app.main import app
from app.scoring import ScoringEngine, parse_league_settings
from tests.conftest import AGE_AS_OF, SEASON

LEAGUE_ID = 999999


@pytest.fixture
def api(db):
    app.dependency_overrides[get_db] = lambda: db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def synced(db, msettings_payload, player_pool_payload) -> SyncSummary:
    """A database in the state a real sync leaves it in."""
    summary = SyncSummary(league_id=LEAGUE_ID, season=SEASON)
    settings_row = sync_scoring_settings(
        db,
        espn_league_id=LEAGUE_ID,
        season=SEASON,
        parsed=parse_league_settings(msettings_payload),
        summary=summary,
    )
    sync_players(db, parse_player_pool(player_pool_payload), summary)
    sync_projections(
        db,
        parse_projections(player_pool_payload, SEASON),
        ScoringEngine(settings_row.scoring_rules),
        summary,
    )
    sync_adp(db, parse_ownership(player_pool_payload), summary, season=SEASON)
    db.commit()
    return summary


def test_ranks_by_projected_points_per_game(api, synced):
    body = api.get("/players/board").json()

    assert body["total_ranked"] == synced.projections_seen
    assert body["season"] == synced.projection_season

    per_game = [row["fantasy_points_per_game"] for row in body["players"]]
    assert per_game == sorted(per_game, reverse=True)
    assert [row["rank"] for row in body["players"]] == list(range(1, len(body["players"]) + 1))


def test_rows_carry_the_player_and_the_market(api, synced):
    top = api.get("/players/board?limit=1").json()["players"][0]

    assert top["name"]
    assert top["nba_team"]
    assert top["positions"]
    assert top["fantasy_points_total"] > 0
    assert top["projected_games"] > 0
    assert top["per_game_basis"] == "projected_games"
    # ESPN's redraft ADP, alongside our number — the whole point of the board.
    assert top["adp"] is not None


def test_limit_defaults_to_fifty_and_is_respected(api, synced):
    assert len(api.get("/players/board").json()["players"]) == min(50, synced.projections_seen)
    assert len(api.get("/players/board?limit=5").json()["players"]) == 5


def test_position_filter_narrows_the_board(api, synced):
    body = api.get("/players/board?position=c").json()

    assert body["position"] == "C"
    assert body["players"], "fixture should contain projected centers"
    assert all("C" in row["positions"] for row in body["players"])
    assert body["total_ranked"] < api.get("/players/board").json()["total_ranked"]


def test_only_players_with_a_projection_appear(api, db, synced):
    body = api.get("/players/board?limit=1000").json()

    assert {row["espn_player_id"] for row in body["players"]} == set(
        db.scalars(select(Projection.player_id))
    )


def test_a_player_without_adp_still_makes_the_board(api, db, synced):
    """A player we can price but ESPN has no market for is exactly who we want to find."""
    top_id = api.get("/players/board?limit=1").json()["players"][0]["espn_player_id"]
    db.delete(db.scalar(select(AdpEntry).where(AdpEntry.player_id == top_id)))
    db.commit()

    top = api.get("/players/board?limit=1").json()["players"][0]

    assert top["espn_player_id"] == top_id
    assert top["adp"] is None


def test_an_unsynced_database_says_so_instead_of_returning_nothing(api, db):
    response = api.get("/players/board")

    assert response.status_code == 404
    assert "sync" in response.json()["detail"].lower()


def test_an_unknown_position_returns_an_empty_board_not_an_error(api, synced):
    body = api.get("/players/board?position=QB").json()

    assert body["total_ranked"] == 0
    assert body["players"] == []


def test_the_board_shows_age_beside_value(api, db, synced, nba_players, fetch_recorded_birthdate):
    """Value and age on one line — the two numbers a dynasty startup is decided on."""
    sync_ages(
        db,
        as_of=AGE_AS_OF,
        nba_players=nba_players,
        fetch=fetch_recorded_birthdate,
        delay=0,
        sleep=lambda _: None,
    )

    body = api.get("/players/board?limit=1000").json()

    assert body["age_as_of"] == AGE_AS_OF.isoformat(), "an age means nothing without its date"
    aged = [row for row in body["players"] if row["age"] is not None]
    assert len(aged) > 40
    assert all(18 <= row["age"] <= 45 for row in aged)
    assert next(row["age"] for row in body["players"] if row["name"] == "LeBron James") == 41


def test_a_player_with_no_age_still_makes_the_board(api, synced):
    """Before any age sync every age is null, and the board is still the board."""
    body = api.get("/players/board").json()

    assert body["players"]
    assert all(row["age"] is None for row in body["players"])


def test_two_seasons_of_adp_do_not_duplicate_a_board_row(api, db, synced):
    """The reason the ADP join pins a season: without it, this player joins twice."""
    before = api.get("/players/board?limit=1000").json()
    sga = db.scalar(select(AdpEntry).where(AdpEntry.player_id == 4278073))
    db.add(AdpEntry(player_id=4278073, source=sga.source, season=SEASON - 1, adp=sga.adp + 30))
    db.commit()

    body = api.get("/players/board?limit=1000").json()

    assert body["total_ranked"] == before["total_ranked"]
    ids = [row["espn_player_id"] for row in body["players"]]
    assert len(ids) == len(set(ids))


def test_the_board_shows_the_newest_adp_season_by_default(api, db, synced):
    old_adp = 130.0
    db.add(AdpEntry(player_id=4278073, source="espn", season=SEASON - 1, adp=old_adp))
    db.commit()

    body = api.get("/players/board?limit=1000").json()
    row = next(row for row in body["players"] if row["espn_player_id"] == 4278073)

    assert body["adp_season"] == SEASON
    assert row["adp"] != old_adp

    # ...and last year's read is still there to ask for, which is the dynasty trend.
    older = api.get(f"/players/board?limit=1000&adp_season={SEASON - 1}").json()
    assert older["adp_season"] == SEASON - 1
    assert (
        next(row for row in older["players"] if row["espn_player_id"] == 4278073)["adp"] == old_adp
    )


def test_adp_source_picks_whose_market_is_displayed(api, db, synced):
    """Rank by ESPN's projection, read someone else's ADP — the whole point of the param."""
    db.add(AdpEntry(player_id=4278073, source="hashtag", season=SEASON, adp=2.0))
    db.commit()

    body = api.get("/players/board?limit=1000&adp_source=hashtag").json()

    assert body["source"] == "espn"  # still ranked by ESPN's projection
    assert body["adp_source"] == "hashtag"
    assert body["adp_season"] == SEASON
    rows = {row["espn_player_id"]: row for row in body["players"]}
    assert rows[4278073]["adp"] == 2.0
    # Nobody else was imported from that source, so their ADP column is empty, not ESPN's.
    assert rows[3112335]["adp"] is None
    assert body["total_ranked"] == api.get("/players/board?limit=1000").json()["total_ranked"]


def test_an_adp_source_we_hold_nothing_for_empties_the_column_without_breaking_the_board(
    api, synced
):
    body = api.get("/players/board?limit=1000&adp_source=nobody").json()

    assert body["adp_season"] is None
    assert body["total_ranked"] == api.get("/players/board?limit=1000").json()["total_ranked"]
    assert all(row["adp"] is None for row in body["players"])


def test_the_adp_worklist_is_empty_when_every_board_player_has_a_market(api, synced):
    """`need=adp` asks the complement of the importer's own review list, from our side."""
    body = api.get("/players/unresolved?need=adp").json()

    assert (body["source"], body["season"]) == ("espn", SEASON)
    assert body["total"] == 0


def test_the_adp_worklist_names_the_board_players_an_import_missed(api, db, synced, adp_csv):
    run_import(db, kind="adp", source="hashtag", season=SEASON, text=adp_csv, dry_run=False)

    body = api.get("/players/unresolved?need=adp&source=hashtag").json()

    imported = set(db.scalars(select(AdpEntry.player_id).where(AdpEntry.source == "hashtag")))
    assert imported
    listed = {row["espn_player_id"] for row in body["players"]}
    assert not (listed & imported), "a player we imported is not missing an ADP"
    assert 4278073 in imported and 4278073 not in listed
    # Only players we can price are on the board, so only they are on its worklist.
    assert listed <= set(db.scalars(select(Projection.player_id)))
    # Most valuable first: that's the order to work down.
    values = [row["fantasy_points_per_game"] or 0.0 for row in body["players"]]
    assert values == sorted(values, reverse=True)


def test_the_adp_worklist_is_per_season(api, db, synced, adp_csv):
    run_import(db, kind="adp", source="hashtag", season=SEASON, text=adp_csv, dry_run=False)

    this_season = api.get("/players/unresolved?need=adp&source=hashtag").json()
    last_season = api.get(f"/players/unresolved?need=adp&source=hashtag&season={SEASON - 1}").json()

    assert last_season["season"] == SEASON - 1
    assert last_season["total"] > this_season["total"], "we imported nothing for last season"


def test_an_unknown_need_lists_the_ones_that_work(api, synced):
    response = api.get("/players/unresolved?need=height")

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "age" in detail and "adp" in detail
