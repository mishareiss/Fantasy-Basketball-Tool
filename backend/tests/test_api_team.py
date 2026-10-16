"""The in-season endpoints: the league sync's new fields, the lineup pull, and team status.
ESPN is the recorded fixtures behind `FakeESPNClient`."""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.api import sync as sync_route
from app.api import team as team_route
from app.db.session import get_db
from app.espn.sync import sync_league
from app.main import app
from tests.conftest import MY_TEAM_ID, PRESEASON_NOW


@pytest.fixture
def client_stub(fake_espn):
    return fake_espn()


@pytest.fixture
def api(db, client_stub, monkeypatch):
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[sync_route.get_espn_client] = lambda: client_stub
    monkeypatch.setattr(
        sync_route,
        "sync_league",
        lambda session: sync_league(session, client_stub, now=PRESEASON_NOW),
    )
    monkeypatch.setattr(team_route, "_now", lambda: PRESEASON_NOW)
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_the_league_sync_response_carries_the_in_season_fields(api):
    body = api.post("/sync/league").json()

    assert body["games_seen"] == body["games_created"] > 0
    assert body["schedule_date_mismatches"] == 0
    assert body["schedule_mismatched_games"] == []
    assert body["teams_seen"] == 9
    assert body["my_team_id"] == MY_TEAM_ID
    assert body["my_team_warning"] is None
    assert body["my_team_matchup_acquisition_totals"] == {}
    assert body["roster_entries"] > 0
    assert body["roster_scoring_period"] == 1
    assert body["transactions_created"] > 0
    assert body["waiver_entry_keys"] is None
    assert body["actuals_available"] is False
    assert set(body["current_actuals"]) == {
        "actual_current",
        "actual_last_7",
        "actual_last_15",
        "actual_last_30",
    }
    assert body["current_actuals"]["actual_current"] == {
        "seen": 0,
        "created": 0,
        "updated": 0,
        "unchanged": 0,
        "removed": 0,
    }


def test_the_lineup_pull_needs_a_team_first(api):
    response = api.post("/sync/lineup")
    assert response.status_code == 409
    assert "make sync" in response.json()["detail"]


def test_the_lineup_pull_stores_our_team(api, client_stub):
    api.post("/sync/league")

    response = api.post("/sync/lineup", params={"first_sp": 1, "last_sp": 8})

    assert response.status_code == 200
    body = response.json()
    assert body["team_id"] == MY_TEAM_ID
    assert (body["first_sp"], body["last_sp"], body["days"]) == (1, 8, 8)
    assert body["assignments"] == 8 * 21
    assert ("mRoster", (8, MY_TEAM_ID)) in client_stub.calls


def test_a_backwards_lineup_range_is_a_422(api):
    api.post("/sync/league")
    assert api.post("/sync/lineup", params={"first_sp": 9, "last_sp": 3}).status_code == 422


def test_team_status_before_any_sync(api):
    body = api.get("/team/status").json()

    assert body["season"] == 2027
    assert body["my_team"] is None
    assert body["today_period"] is None  # Oct 15: before tip-off
    assert body["today_date"] == "2026-10-15"
    assert body["current_matchup"] == {
        "id": 1,
        "label": "Matchup 1",
        "start": "2026-10-20",
        "end": "2026-10-25",
    }
    assert body["games_synced"] == 0
    assert body["roster_size"] == 0
    assert body["lineup_days_stored"] == 0
    assert body["actuals_available"] is False
    assert body["acquisition_limit"] is None


def test_team_status_after_a_sync_and_a_lineup_pull(api):
    api.post("/sync/league")
    api.post("/sync/lineup", params={"first_sp": 1, "last_sp": 5})

    body = api.get("/team/status").json()

    assert body["my_team"] == {"id": MY_TEAM_ID, "name": "Team 8", "abbrev": "T8"}
    assert body["games_synced"] > 0
    assert body["roster_size"] == 21
    assert body["lineup_days_stored"] == 5
    assert body["acquisition_limit"] == 7


def test_team_status_mid_season(api, monkeypatch):
    # Sat Feb 20 2027, 9pm Pacific: the two-week All-Star matchup.
    monkeypatch.setattr(team_route, "_now", lambda: datetime(2027, 2, 21, 5, 0, tzinfo=UTC))
    body = api.get("/team/status").json()
    assert body["today_period"] == 124
    assert body["today_date"] == "2027-02-20"
    assert body["current_matchup"]["id"] == 18
    assert (body["current_matchup"]["start"], body["current_matchup"]["end"]) == (
        "2027-02-15",
        "2027-02-28",
    )
