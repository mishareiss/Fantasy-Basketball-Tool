"""GET /rankings — which boards we hold, and what one of them says.

Paired with `POST /import/ranking`, which is the only way a set gets here: these tests import
through the real endpoint and then read the result back, so the two halves are tested against
each other rather than against a hand-built fixture that could drift from what the importer
actually writes.
"""

import pytest
from fastapi.testclient import TestClient

from app.db.session import get_db
from app.main import app
from tests.conftest import SEASON

SOURCE = "hashtag"
SET_NAME = "Dynasty Top 200"
HORIZON = "dynasty"


@pytest.fixture
def api(players):
    app.dependency_overrides[get_db] = lambda: players
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _import(api, csv, *, name: str | None = SET_NAME, horizon: str = HORIZON, **overrides) -> dict:
    body = {
        "source": SOURCE,
        "season": SEASON,
        "text": csv,
        "dry_run": False,
        **overrides,
    }
    options = {"horizon": horizon}
    if name is not None:
        options["name"] = name
    body.setdefault("options", options)
    response = api.post("/import/ranking", json=body)
    assert response.status_code == 200, response.text
    return response.json()


# --- the import endpoint's half -------------------------------------------------------------


def test_the_set_name_and_horizon_flow_through_the_options_body(api, ranking_csv):
    body = _import(api, ranking_csv, dry_run=True)

    assert body["options"] == {"horizon": HORIZON, "name": SET_NAME}
    assert f"set {SET_NAME!r} ({SOURCE}, {HORIZON}, season {SEASON})" in body["notes"][0]


def test_a_ranking_with_no_horizon_is_a_422_that_says_what_it_wanted(api, ranking_csv):
    """The one option this kind refuses to default: a rank-only list has no stats to adjust."""
    response = api.post(
        "/import/ranking",
        json={"source": SOURCE, "season": SEASON, "text": ranking_csv, "options": {"name": "x"}},
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "horizon" in detail and "dynasty" in detail and "redraft" in detail


def test_an_invalid_horizon_is_a_422_too(api, ranking_csv):
    response = api.post(
        "/import/ranking",
        json={
            "source": SOURCE,
            "season": SEASON,
            "text": ranking_csv,
            "options": {"name": SET_NAME, "horizon": "keeper"},
        },
    )

    assert response.status_code == 422
    assert "keeper" in response.json()["detail"]


def test_the_dry_run_preview_says_where_rank_came_from(api, ranking_csv, ranking_order_csv):
    with_column = _import(api, ranking_csv, dry_run=True)
    from_order = _import(api, ranking_order_csv, dry_run=True)

    assert "rank from column for 11 row(s), from file order for 0" in with_column["notes"][1]
    assert "rank from column for 0 row(s), from file order for 7" in from_order["notes"][1]


def test_an_option_the_kind_does_not_know_is_a_422(api, ranking_csv):
    response = api.post(
        "/import/ranking",
        json={
            "source": SOURCE,
            "season": SEASON,
            "text": ranking_csv,
            "options": {"basis": "season", "horizon": HORIZON},
        },
    )

    assert response.status_code == 422
    assert "unknown option" in response.json()["detail"]


def test_a_tier_label_survives_the_json_round_trip(api, ranking_csv):
    rows = _import(api, ranking_csv, dry_run=True)["rows"]

    fox = next(row for row in rows if row["source_name"] == "DeAaron Fox")
    assert fox["values"]["tier"] == "Elite"
    assert fox["values"]["rank"] == 19


# --- GET /rankings --------------------------------------------------------------------------


def test_listing_the_sets_we_hold(api, ranking_csv, ranking_order_csv):
    _import(api, ranking_csv)
    _import(api, ranking_order_csv, name="Our Board")

    sets = api.get("/rankings").json()

    assert {(entry["name"], entry["entry_count"]) for entry in sets} == {
        (SET_NAME, 11),
        ("Our Board", 7),
    }
    assert {entry["horizon"] for entry in sets} == {HORIZON}
    assert {entry["source"] for entry in sets} == {SOURCE}
    assert {entry["season"] for entry in sets} == {SEASON}
    assert all(entry["as_of"] for entry in sets)


def test_the_list_can_be_narrowed_by_source_and_season(api, ranking_csv):
    _import(api, ranking_csv)

    assert api.get("/rankings", params={"source": SOURCE}).json() != []
    assert api.get("/rankings", params={"source": "nobody"}).json() == []
    assert api.get("/rankings", params={"season": SEASON + 1}).json() == []


def test_the_two_horizons_of_one_name_are_two_listed_sets(api, ranking_csv):
    """End to end: the same name and season, imported twice, read back as two boards."""
    _import(api, ranking_csv, name="Top 200", horizon="dynasty")
    _import(api, ranking_csv, name="Top 200", horizon="redraft")

    sets = api.get("/rankings").json()

    assert sorted(entry["horizon"] for entry in sets) == ["dynasty", "redraft"]
    assert {entry["name"] for entry in sets} == {"Top 200"}
    assert len({entry["id"] for entry in sets}) == 2
    assert [
        entry["name"] for entry in api.get("/rankings", params={"horizon": "dynasty"}).json()
    ] == ["Top 200"]


def test_the_detail_view_says_which_horizon_the_board_is(api, ranking_csv):
    _import(api, ranking_csv, horizon="redraft")
    set_id = api.get("/rankings").json()[0]["id"]

    assert api.get(f"/rankings/{set_id}").json()["horizon"] == "redraft"


def test_an_empty_database_lists_nothing_rather_than_failing(api):
    assert api.get("/rankings").json() == []


# --- GET /rankings/{id} ---------------------------------------------------------------------


def test_the_set_comes_back_in_rank_order_with_the_player_joined_on(api, ranking_csv):
    _import(api, ranking_csv)
    set_id = api.get("/rankings").json()[0]["id"]

    body = api.get(f"/rankings/{set_id}").json()

    ranks = [entry["rank"] for entry in body["entries"]]
    assert ranks == sorted(ranks) == [1, 2, 3, 4, 5, 8, 12, 14, 19, 31, 55]
    # Team and positions are OUR player's, not the file's — the file said "PG/SG" and we say
    # what ESPN says. That is the whole reason the entry stores a player id and not a name.
    top = body["entries"][0]
    assert top["name"] == "Shai Gilgeous-Alexander"
    assert top["nba_team"] == "OKC"
    assert top["positions"] == ["PG"]
    assert (top["tier"], top["value"]) == ("1", 98.4)
    assert body["entry_count"] == 11


def test_limit_narrows_what_you_read_not_what_the_set_is(api, ranking_csv):
    _import(api, ranking_csv)
    set_id = api.get("/rankings").json()[0]["id"]

    body = api.get(f"/rankings/{set_id}", params={"limit": 3}).json()

    assert [entry["rank"] for entry in body["entries"]] == [1, 2, 3]
    assert body["entry_count"] == 11


def test_a_replaced_set_reads_back_as_the_new_version_only(api, ranking_csv):
    _import(api, ranking_csv)
    set_id = api.get("/rankings").json()[0]["id"]
    revised = "\n".join(line for line in ranking_csv.splitlines() if "Jokić" not in line)

    _import(api, revised)

    body = api.get(f"/rankings/{set_id}").json()
    names = [entry["name"] for entry in body["entries"]]
    assert "Nikola Jokic" not in names
    assert len(names) == len(set(names)) == 10
    assert body["entry_count"] == 10


def test_a_set_we_do_not_hold_is_a_404_that_says_where_to_look(api):
    response = api.get("/rankings/404")

    assert response.status_code == 404
    assert "GET /rankings" in response.json()["detail"]
