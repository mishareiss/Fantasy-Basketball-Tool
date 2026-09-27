"""`GET /players/{id}/detail` — one player's whole evidence, and nothing invented.

The board prints one number per player. This is the endpoint that answers what that number is
made of: the season he actually played, priced under OUR scoring, and what the market says
next to the props that opinion came from.

Every test here is really the same assertion from a different angle — ABSENT IS ABSENT. A
rookie has no last season, an unpriced player has no market, a stat nobody posted a number for
is missing from the map rather than present as zero. There is nowhere on this endpoint where a
0 stands in for "we don't know", because the whole point of showing somebody a box score is
that they can believe it.
"""

import pytest
from fastapi.testclient import TestClient

from app.db.models import MarketLine, Player
from app.db.session import get_db
from app.ingest.market_line import MARKET_SOURCE
from app.main import app
from tests.conftest import SEASON

# Jokic has both a projection and a completed season in the fixtures. Cazalon is the other
# end of the pool: a player we carry an identity for whom ESPN projects nothing and who has
# never completed a season we hold — the "absent is absent" case in both halves at once.
JOKIC = 3112335
UNPLAYED = 4871137


@pytest.fixture
def api(db):
    app.dependency_overrides[get_db] = lambda: db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_a_player_with_a_season_and_lines_returns_both_halves(
    api, db, with_actuals, make_market_lines
):
    make_market_lines({JOKIC: {"PTS": 27.5, "REB": (12.5, -130, 105), "AST": 9.5}})

    body = api.get(f"/players/{JOKIC}/detail").json()

    assert body["espn_player_id"] == JOKIC
    assert body["name"]
    assert body["positions"]

    last = body["last_season"]
    assert last["season"] < 2027  # a season that was played, not the one being drafted
    assert last["games"] > 0
    assert last["fantasy_ppg"] == pytest.approx(last["fantasy_total"] / last["games"])
    # A real box score: the stats a points league scores, per game, keyed by name.
    assert last["per_game"]["PTS"] > 20
    assert {"REB", "AST", "MIN", "FGM", "FGA", "FTM", "FTA", "3PM", "GP"} <= set(last["per_game"])

    market = body["market"]
    assert market["fantasy_ppg"] > 0
    assert market["per_game"]["PTS"] == 27.5

    # The raw props, by stat id, with the prices as entered — null for a side nobody priced.
    lines = {row["stat"]: row for row in body["market_lines"]}
    assert lines["REB"]["line"] == 12.5
    assert lines["REB"]["over_odds"] == -130
    assert lines["REB"]["under_odds"] == 105
    assert lines["AST"]["over_odds"] is None


def test_a_player_with_neither_returns_nulls_and_an_empty_list(api, synced):
    body = api.get(f"/players/{UNPLAYED}/detail").json()

    assert body["espn_player_id"] == UNPLAYED
    assert body["last_season"] is None
    assert body["market"] is None
    assert body["market_lines"] == []


def test_a_rookie_keeps_his_identity_while_the_players_around_him_have_seasons(api, with_actuals):
    """The interesting mixed case: the same response shape, one half genuinely empty."""
    jokic = api.get(f"/players/{JOKIC}/detail").json()
    rookie = api.get(f"/players/{UNPLAYED}/detail").json()

    assert jokic["last_season"] is not None
    assert rookie["last_season"] is None
    assert rookie["name"] and rookie["positions"]


def test_a_stat_nobody_priced_is_absent_from_the_market_map_rather_than_zero(
    api, synced, make_market_lines
):
    make_market_lines({JOKIC: {"PTS": 27.5}})

    body = api.get(f"/players/{JOKIC}/detail").json()

    assert body["market"]["per_game"] == {"PTS": 27.5}
    assert "REB" not in body["market"]["per_game"]
    # Partial by construction, and the lines say exactly how partial.
    assert [row["stat"] for row in body["market_lines"]] == ["PTS"]


def test_an_unknown_player_is_a_404_that_says_which_id_it_wanted(api, synced):
    response = api.get("/players/424242/detail")

    assert response.status_code == 404
    assert "424242" in response.json()["detail"]


def test_a_player_we_can_name_but_have_no_numbers_for_is_a_200(api, db):
    """Not a 404: "we hold him and know nothing about him" is a real and reportable state."""
    db.add(Player(espn_player_id=999999, full_name="Nobody At All", positions=["SF"]))
    db.commit()

    body = api.get("/players/999999/detail").json()

    assert body == {
        "espn_player_id": 999999,
        "name": "Nobody At All",
        "nba_team": None,
        "positions": ["SF"],
        "age": None,
        "last_season": None,
        "market": None,
        "market_lines": [],
    }


def test_the_market_lines_are_only_the_season_the_projection_was_derived_from(
    api, db, synced, make_market_lines
):
    """One season's props, never two — a set mixing last year's numbers with this year's is a
    stat line nobody can read.

    Last season's line goes in directly rather than through the importer: deriving a projection
    for it would need that season's scoring coefficients, and the claim being tested is about
    which LINES come back, not about pricing a season nobody is drafting.
    """
    make_market_lines({JOKIC: {"PTS": 27.5, "AST": 9.5}})
    db.add(
        MarketLine(player_id=JOKIC, source=MARKET_SOURCE, season=SEASON - 1, stat_id=0, line=25.0)
    )
    db.commit()

    body = api.get(f"/players/{JOKIC}/detail").json()

    assert body["market"]["per_game"] == {"PTS": 27.5, "AST": 9.5}
    assert sorted(row["stat"] for row in body["market_lines"]) == ["AST", "PTS"]
    assert all(row["line"] != 25.0 for row in body["market_lines"])
