"""POST /import/{kind} — the paste path, and the two phases over HTTP."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.config import get_settings
from app.db.models import AdpEntry, PlayerAlias, Projection
from app.db.session import get_db
from app.main import app
from tests.conftest import SEASON

SOURCE = "hashtag"


@pytest.fixture
def api(players):
    app.dependency_overrides[get_db] = lambda: players
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _body(adp_csv, **overrides) -> dict:
    return {"source": SOURCE, "season": SEASON, "text": adp_csv, **overrides}


def test_a_dry_run_is_the_default_and_writes_nothing(api, players, adp_csv):
    body = api.post("/import/adp", json=_body(adp_csv)).json()

    assert body["dry_run"] is True
    assert body["matched"] == 11
    assert body["rows_created"] == 11
    assert players.scalar(select(func.count()).select_from(AdpEntry)) == 0


def test_the_preview_carries_the_columns_it_found(api, adp_csv):
    body = api.post("/import/adp", json=_body(adp_csv)).json()

    assert body["columns"]["name"] == "PLAYER"
    assert body["columns"]["adp"] == "Avg Pick"
    assert body["delimiter"] == ","


def test_the_preview_carries_the_worklist_with_candidates(api, adp_csv):
    rows = api.post("/import/adp", json=_body(adp_csv)).json()["rows"]

    unmatched = [row for row in rows if row["status"] == "unmatched"]
    assert {row["source_name"] for row in unmatched} == {"Nikola Topić", "Zaccharie Risacher"}
    fuzzy = next(row for row in rows if row["source_name"] == "Victor Wembanyma")
    assert fuzzy["player_name"] == "Victor Wembanyama"
    assert fuzzy["candidates"][0]["full_name"] == "Victor Wembanyama"


def test_committing_writes_rows_and_aliases(api, players, adp_csv):
    body = api.post("/import/adp", json=_body(adp_csv, dry_run=False)).json()

    assert body["dry_run"] is False
    assert body["rows_created"] == 11
    assert players.scalar(select(func.count()).select_from(AdpEntry)) == 11
    assert players.scalar(select(func.count()).select_from(PlayerAlias)) == 11


def test_committing_twice_over_http_creates_no_duplicates(api, players, adp_csv):
    api.post("/import/adp", json=_body(adp_csv, dry_run=False))
    second = api.post("/import/adp", json=_body(adp_csv, dry_run=False)).json()

    assert (second["rows_created"], second["rows_updated"]) == (0, 0)
    assert second["rows_unchanged"] == 11
    assert second["aliases_created"] == 0
    assert players.scalar(select(func.count()).select_from(AdpEntry)) == 11


def test_strict_holds_the_fuzzy_row(api, adp_csv):
    body = api.post("/import/adp", json=_body(adp_csv, strict=True)).json()

    assert body["review"] == 1
    assert body["matched"] == 10


def test_a_column_map_comes_through_the_body(api):
    body = api.post(
        "/import/adp",
        json=_body("Guy,Slot\nCooper Flagg,6\n", column_map={"name": "Guy", "adp": "Slot"}),
    ).json()

    assert body["matched"] == 1
    assert body["rows"][0]["player_name"] == "Cooper Flagg"


def test_an_unknown_kind_is_a_404_listing_what_exists(api, adp_csv):
    """`ranking` is designed and not built; asking for it says so, and says what is built."""
    response = api.post("/import/ranking", json=_body(adp_csv))

    assert response.status_code == 404
    detail = response.json()["detail"]
    assert "adp" in detail and "projection" in detail


def test_an_unreadable_table_is_a_422_that_says_why(api):
    response = api.post("/import/adp", json=_body("Rank,Team\n1,DEN\n"))

    assert response.status_code == 422
    assert "no player-name column" in response.json()["detail"]


def test_the_season_falls_back_to_the_configured_one(api, players, adp_csv):
    body = api.post("/import/adp", json=_body(adp_csv, season=None, dry_run=False)).json()

    assert body["season"] == get_settings().espn_season
    assert players.scalar(select(AdpEntry).where(AdpEntry.player_id == 4278073)).season == (
        get_settings().espn_season
    )


def test_no_season_anywhere_is_refused_rather_than_stored(api, adp_csv, monkeypatch):
    monkeypatch.setattr(get_settings(), "espn_season", None)

    response = api.post("/import/adp", json=_body(adp_csv, season=None))

    assert response.status_code == 400
    assert "season" in response.json()["detail"]


def test_the_kinds_endpoint_lists_what_is_built_and_what_is_planned(api):
    kinds = api.get("/import/kinds").json()

    built = {kind["kind"]: kind for kind in kinds if kind["implemented"]}
    assert sorted(built) == ["adp", "projection"]
    assert built["adp"]["required"] == ["adp"]
    assert "avg pick" in built["adp"]["value_columns"]["adp"]
    assert built["projection"]["required"] == ["PTS"]
    assert "3ptm" in built["projection"]["value_columns"]["3PM"]
    assert {kind["kind"] for kind in kinds if not kind["implemented"]} == {
        "ranking",
        "market_line",
    }


def test_a_projection_paste_previews_and_commits_over_http(priced, projection_csv):
    """The same two phases, for the kind that needs our scoring rules loaded to price a row."""
    app.dependency_overrides[get_db] = lambda: priced
    try:
        client = TestClient(app)
        body = {
            "source": SOURCE,
            "season": SEASON,
            "text": projection_csv,
            "options": {"basis": "per_game"},
        }

        preview = client.post("/import/projection", json=body).json()
        assert preview["dry_run"] is True
        assert preview["options"] == {"basis": "per_game"}
        assert preview["columns"]["PTS"] == "PTS" and preview["columns"]["REB"] == "TREB"
        assert preview["rows_created"] == preview["matched"] == 12
        assert priced.scalar(select(func.count()).select_from(Projection)) == 0

        committed = client.post("/import/projection", json={**body, "dry_run": False}).json()
        assert committed["rows_created"] == 12
        assert priced.scalar(select(func.count()).select_from(Projection)) == 12
    finally:
        app.dependency_overrides.clear()


def test_an_unusable_basis_is_a_422_and_writes_nothing(priced, projection_csv):
    app.dependency_overrides[get_db] = lambda: priced
    try:
        response = TestClient(app).post(
            "/import/projection",
            json={
                "source": SOURCE,
                "season": SEASON,
                "text": projection_csv,
                "dry_run": False,
                "options": {"basis": "per_minute"},
            },
        )

        assert response.status_code == 422
        assert "basis" in response.json()["detail"]
        assert priced.scalar(select(func.count()).select_from(Projection)) == 0
    finally:
        app.dependency_overrides.clear()


def test_a_projection_import_without_our_scoring_rules_is_a_409_not_a_500(players, projection_csv):
    """Nothing is broken and nothing was written — the league just hasn't been synced."""
    app.dependency_overrides[get_db] = lambda: players
    try:
        response = TestClient(app).post(
            "/import/projection",
            json={
                "source": SOURCE,
                "season": SEASON,
                "text": projection_csv,
                "dry_run": False,
            },
        )

        assert response.status_code == 409
        assert "sync" in response.json()["detail"]
        assert players.scalar(select(func.count()).select_from(Projection)) == 0
    finally:
        app.dependency_overrides.clear()
