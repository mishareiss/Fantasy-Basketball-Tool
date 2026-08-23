"""The /sync/league endpoint. ESPN itself is stubbed; the live pull is covered separately."""

import pytest
from fastapi.testclient import TestClient

from app.api import sync as sync_route
from app.db.session import get_db
from app.espn.client import ESPNCredentialsError, ESPNRequestError
from app.espn.players import parse_player_pool
from app.espn.sync import SyncSummary, sync_players, sync_scoring_settings
from app.main import app
from app.scoring import parse_league_settings


@pytest.fixture
def api(db):
    app.dependency_overrides[get_db] = lambda: db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_returns_a_summary_of_what_it_stored(
    api, db, monkeypatch, msettings_payload, player_pool_payload
):
    def fake_sync(session):
        summary = SyncSummary(league_id=42, season=2027)
        sync_scoring_settings(
            session,
            espn_league_id=42,
            season=2027,
            parsed=parse_league_settings(msettings_payload),
            summary=summary,
        )
        sync_players(session, parse_player_pool(player_pool_payload), summary)
        session.commit()
        return summary

    monkeypatch.setattr(sync_route, "sync_league", fake_sync)

    response = api.post("/sync/league")

    assert response.status_code == 200
    body = response.json()
    assert body["scoring_rules"] == 17
    assert body["points_by_stat"]["PTS"] == 3.0
    assert body["players_created"] == len(player_pool_payload)


def test_missing_cookies_are_a_503_with_a_useful_message(api, monkeypatch):
    def fake_sync(session):
        raise ESPNCredentialsError("Missing ESPN credentials: ESPN_S2")

    monkeypatch.setattr(sync_route, "sync_league", fake_sync)

    response = api.post("/sync/league")

    assert response.status_code == 503
    assert "ESPN_S2" in response.json()["detail"]


def test_an_espn_failure_is_a_502(api, monkeypatch):
    def fake_sync(session):
        raise ESPNRequestError("ESPN returned HTTP 500 for view 'mSettings'")

    monkeypatch.setattr(sync_route, "sync_league", fake_sync)

    response = api.post("/sync/league")

    assert response.status_code == 502
