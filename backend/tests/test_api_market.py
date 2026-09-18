"""The market CRUD: GET /market/lines, PUT /market/lines, DELETE /market/lines[/{id}].

The importer's own suite (`test_ingest_market_line`) already proves that lines are stored per
(player, stat) and priced into one `Projection`. What is asserted here is the half a pasted
file has no verb for: reading a stored SET back, moving one number in it, and — the case the
whole module exists for — taking a line away.

That last one is the interesting test in this file. `derive_market_projections` used to be
upsert-only, which is exactly right for an import (a file only ever adds or moves lines) and
exactly wrong for a delete: the derived projection is the only thing the board can see, so a
player whose last line is gone would otherwise stay on `GET /sources` and
`GET /board/consensus` ranked by a number with nothing under it. He has to LEAVE.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import MarketLine, Projection
from app.db.session import get_db
from app.ingest.market_line import MARKET_PROJECTION_KIND, MARKET_SOURCE
from app.main import app
from app.ranking.market import fair_value
from app.scoring import load_scoring_engine_for_season
from app.scoring.stats import STAT_NAME_TO_ID
from tests.conftest import LEAGUE_ID, SEASON

JOKIC = 3112335
SGA = 4278073
WEMBY = 5104157

MARKET = "projection:market"

# The shipped default, pinned so a locally-calibrated .env can't move these numbers.
SIGMA = 0.25


@pytest.fixture
def api(db):
    app.dependency_overrides[get_db] = lambda: db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _put(api, player_id, stat, line, over=None, under=None, *, source=MARKET_SOURCE):
    return api.put(
        "/market/lines",
        json={
            "source": source,
            "season": SEASON,
            "player_id": player_id,
            "stat": stat,
            "line": line,
            "over_odds": over,
            "under_odds": under,
        },
    )


def _players(api, *, source=MARKET_SOURCE):
    body = api.get(f"/market/lines?source={source}&season={SEASON}").json()
    return {player["espn_player_id"]: player for player in body["players"]}


def _market_projection(db, player_id, *, source=MARKET_SOURCE):
    return db.scalar(
        select(Projection).where(
            Projection.player_id == player_id,
            Projection.source == source,
            Projection.kind == MARKET_PROJECTION_KIND,
            Projection.season == SEASON,
        )
    )


def _points_for(db, stat):
    """What our league pays per unit of a stat — so the expected values aren't magic numbers."""
    engine = load_scoring_engine_for_season(db, SEASON, espn_league_id=LEAGUE_ID)
    return engine.as_points_map()[stat]


# --- GET /market/lines ----------------------------------------------------------------------


def test_no_lines_yet_is_a_clean_200_rather_than_a_404(api, priced):
    """The starting state of every book, and a perfectly reasonable question to ask."""
    response = api.get("/market/lines")

    assert response.status_code == 200
    body = response.json()
    assert body["source"] == MARKET_SOURCE
    assert body["season"] == SEASON
    assert (body["total_players"], body["total_lines"], body["players"]) == (0, 0, [])


def test_a_multi_stat_player_is_one_entry_carrying_his_lines_and_his_derived_value(
    api, priced, make_market_lines
):
    make_market_lines({JOKIC: {"PTS": 27.5, "REB": 12.5, "AST": (9.5, -150, 120)}})

    body = api.get("/market/lines").json()

    assert body["total_players"] == 1
    assert body["total_lines"] == 3
    entry = body["players"][0]
    assert entry["espn_player_id"] == JOKIC
    assert entry["name"] == "Nikola Jokic"
    assert {line["stat"] for line in entry["lines"]} == {"PTS", "REB", "AST"}
    assert entry["stats_priced"] == 3
    # The derived row is the one the consensus board reads — the same number, not a re-derivation.
    assert (
        entry["fantasy_points_per_game"]
        == _market_projection(priced, JOKIC).fantasy_points_per_game
    )
    assists = next(line for line in entry["lines"] if line["stat"] == "AST")
    assert (assists["line"], assists["over_odds"], assists["under_odds"]) == (9.5, -150, 120)


def test_the_stats_offered_are_the_ones_our_league_actually_pays_for(api, priced):
    body = api.get("/market/lines").json()

    names = [stat["name"] for stat in body["stats"]]
    assert "PTS" in names and "AST" in names
    # A rate has no coefficient that can be multiplied by a per-game line, so it is never on
    # offer — the same rule `resolve_stat` enforces on a pasted cell.
    assert not {"FG%", "FT%"} & set(names)
    assert all(stat["points"] != 0 for stat in body["stats"])


def test_the_stat_list_is_empty_rather_than_a_500_before_a_league_sync(api, players):
    """Nothing can be priced until a sync stores our coefficients; the lines still list."""
    body = api.get("/market/lines").json()

    assert body["stats"] == []
    assert body["players"] == []


def test_a_second_book_is_listed_separately(api, priced, make_market_lines):
    make_market_lines({JOKIC: {"PTS": 27.5}})
    make_market_lines({WEMBY: {"BLK": 3.5}}, source="draftkings")

    assert set(_players(api)) == {JOKIC}
    assert set(_players(api, source="draftkings")) == {WEMBY}


def test_players_are_listed_most_valuable_first(api, priced, make_market_lines):
    make_market_lines({JOKIC: {"PTS": 27.5}, WEMBY: {"PTS": 9.5}})

    body = api.get("/market/lines").json()

    assert [player["espn_player_id"] for player in body["players"]] == [JOKIC, WEMBY]


# --- PUT /market/lines ----------------------------------------------------------------------


def test_putting_a_line_creates_it_and_prices_the_player(api, priced):
    body = _put(api, JOKIC, "Points", 27.5).json()

    assert body["created"] is True
    assert (body["line"]["stat"], body["line"]["line"]) == ("PTS", 27.5)
    assert body["player"]["stats_priced"] == 1
    # An unpriced line derives itself, so the value is exactly the line times its coefficient.
    assert body["player"]["fantasy_points_per_game"] == pytest.approx(
        27.5 * _points_for(priced, "PTS")
    )
    assert _market_projection(priced, JOKIC) is not None


def test_the_new_value_reflects_the_odds_and_not_just_the_line(api, priced):
    even = _put(api, JOKIC, "AST", 9.5).json()["player"]["fantasy_points_per_game"]

    shaded = _put(api, JOKIC, "AST", 9.5, -400, 300).json()["player"]["fantasy_points_per_game"]

    assert shaded > even
    assert shaded == pytest.approx(
        round(fair_value(9.5, -400, 300, sigma_fraction=SIGMA), 4) * _points_for(priced, "AST")
    )


def test_putting_the_same_stat_twice_moves_the_number_rather_than_adding_a_second_line(api, priced):
    _put(api, JOKIC, "AST", 9.5)

    body = _put(api, JOKIC, "assists", 10.5).json()

    assert body["created"] is False
    assert body["player"]["stats_priced"] == 1
    assert body["line"]["line"] == 10.5
    assert priced.scalar(select(MarketLine.line).where(MarketLine.player_id == JOKIC)) == 10.5


def test_editing_one_stat_leaves_the_others_and_re_derives_the_whole_set(api, priced):
    _put(api, JOKIC, "PTS", 27.5)
    _put(api, JOKIC, "AST", 9.5)

    body = _put(api, JOKIC, "AST", 11.5).json()

    assert body["player"]["stats_priced"] == 2
    assert body["player"]["fantasy_points_per_game"] == pytest.approx(
        27.5 * _points_for(priced, "PTS") + 11.5 * _points_for(priced, "AST")
    )


@pytest.mark.parametrize("stat", ["PRA", "pts+reb", "FG%", "Rebounds+Assists"])
def test_a_stat_we_cannot_price_is_a_422_rather_than_a_500(api, priced, stat):
    response = _put(api, JOKIC, stat, 34.5)

    assert response.status_code == 422
    assert "not a counting stat we can price" in response.json()["detail"]


def test_a_player_we_do_not_carry_is_a_404(api, priced):
    assert _put(api, 99999999, "PTS", 27.5).status_code == 404


def test_nothing_can_be_priced_before_a_league_sync(api, players):
    response = _put(api, JOKIC, "PTS", 27.5)

    assert response.status_code == 409
    assert "run POST /sync/league" in response.json()["detail"]
    assert players.scalar(select(MarketLine.id)) is None


# --- DELETE /market/lines/{id} --------------------------------------------------------------


def test_deleting_a_line_removes_it_and_re_prices_what_is_left(api, priced, make_market_lines):
    make_market_lines({JOKIC: {"PTS": 27.5, "AST": 9.5}})
    assists = next(line for line in _players(api)[JOKIC]["lines"] if line["stat"] == "AST")

    body = api.delete(f"/market/lines/{assists['id']}").json()

    assert body["deleted"] == 1
    assert body["player_removed"] is False
    assert [line["stat"] for line in body["player"]["lines"]] == ["PTS"]
    assert body["player"]["fantasy_points_per_game"] == pytest.approx(
        27.5 * _points_for(priced, "PTS")
    )
    assert _market_projection(priced, JOKIC).fantasy_points_per_game == pytest.approx(
        27.5 * _points_for(priced, "PTS")
    )


def test_deleting_the_last_line_removes_the_derived_projection_entirely(
    api, priced, make_market_lines
):
    """The landmine. An upsert-only derivation would leave a phantom here — or a zero."""
    make_market_lines({JOKIC: {"PTS": 27.5}})
    only = _players(api)[JOKIC]["lines"][0]

    body = api.delete(f"/market/lines/{only['id']}").json()

    assert body["player_removed"] is True
    assert body["player"]["lines"] == []
    # Null, not 0.0: "the market no longer says anything about him" is not "the market rates
    # him at nothing".
    assert body["player"]["fantasy_points_per_game"] is None
    assert _market_projection(priced, JOKIC) is None
    assert _players(api) == {}


def test_a_player_whose_last_line_went_leaves_the_market_source_and_the_consensus_board(
    api, db, synced, make_market_lines
):
    """The same fact, seen from the board — which is the only place it actually matters."""
    make_market_lines({JOKIC: {"PTS": 27.5}, WEMBY: {"BLK": 3.5}})
    before = next(
        source
        for source in api.get("/sources?horizon=dynasty").json()["sources"]
        if source["id"] == MARKET
    )
    assert before["player_count"] == 2

    jokic = _players(api)[JOKIC]["lines"][0]
    api.delete(f"/market/lines/{jokic['id']}")

    after = next(
        source
        for source in api.get("/sources?horizon=dynasty").json()["sources"]
        if source["id"] == MARKET
    )
    assert after["player_count"] == 1
    consensus = api.get(f"/board/consensus?sources={MARKET}&horizon=dynasty&limit=1000").json()
    ranked = {row["espn_player_id"] for row in consensus["players"] if MARKET in row["cells"]}
    assert ranked == {WEMBY}


def test_removing_the_only_player_leaves_the_market_off_the_source_list_altogether(
    api, db, synced, make_market_lines
):
    make_market_lines({JOKIC: {"PTS": 27.5}})
    only = _players(api)[JOKIC]["lines"][0]

    api.delete(f"/market/lines/{only['id']}")

    ids = {source["id"] for source in api.get("/sources?horizon=dynasty").json()["sources"]}
    assert MARKET not in ids


def test_deleting_a_line_that_is_already_gone_is_a_404(api, priced, make_market_lines):
    make_market_lines({JOKIC: {"PTS": 27.5}})
    only = _players(api)[JOKIC]["lines"][0]
    api.delete(f"/market/lines/{only['id']}")

    response = api.delete(f"/market/lines/{only['id']}")

    assert response.status_code == 404
    assert "No market line" in response.json()["detail"]


def test_one_players_delete_leaves_every_other_players_value_untouched(
    api, priced, make_market_lines
):
    make_market_lines({JOKIC: {"PTS": 27.5, "AST": 9.5}, WEMBY: {"BLK": 3.5}})
    before = _market_projection(priced, WEMBY).fantasy_points_per_game
    assists = next(line for line in _players(api)[JOKIC]["lines"] if line["stat"] == "AST")

    api.delete(f"/market/lines/{assists['id']}")

    assert _market_projection(priced, WEMBY).fantasy_points_per_game == before


# --- DELETE /market/lines?player_id= --------------------------------------------------------


def test_clearing_a_players_whole_set_drops_him_in_one_call(api, priced, make_market_lines):
    make_market_lines({JOKIC: {"PTS": 27.5, "REB": 12.5, "AST": 9.5}, WEMBY: {"BLK": 3.5}})

    body = api.delete(
        f"/market/lines?source={MARKET_SOURCE}&season={SEASON}&player_id={JOKIC}"
    ).json()

    assert (body["deleted"], body["player_removed"]) == (3, True)
    assert _market_projection(priced, JOKIC) is None
    assert set(_players(api)) == {WEMBY}
    assert priced.scalar(select(MarketLine.id).where(MarketLine.player_id == JOKIC)) is None


def test_clearing_a_player_with_no_lines_is_a_no_op_rather_than_an_error(api, priced):
    body = api.delete(f"/market/lines?player_id={JOKIC}&season={SEASON}").json()

    assert (body["deleted"], body["player_removed"]) == (0, True)


# --- the guard ------------------------------------------------------------------------------


def test_the_espn_board_is_untouched_by_every_one_of_these_calls(api, db, synced):
    """Acceptance criterion 4, on the whole response body: adding, editing and deleting a
    market line changes `GET /players/board` not at all — it is a different source."""
    before = api.get("/players/board?limit=1000&horizon=dynasty").json()

    _put(api, JOKIC, "PTS", 27.5)
    _put(api, JOKIC, "PTS", 29.5, -150, 120)
    _put(api, WEMBY, "BLK", 3.5)
    line_id = _players(api)[JOKIC]["lines"][0]["id"]
    api.delete(f"/market/lines/{line_id}")

    assert api.get("/players/board?limit=1000&horizon=dynasty").json() == before
    # Not a vacuous comparison: the other player's market projection really is stored.
    assert _market_projection(db, WEMBY) is not None


def test_a_line_on_games_played_sets_the_games_count_without_being_scored(api, priced):
    """GP is the one stat that is not scored but is still worth entering — it sets the total."""
    _put(api, JOKIC, "PTS", 27.5)

    body = _put(api, JOKIC, "GP", 65).json()

    assert body["player"]["projected_games"] == 65.0
    assert body["player"]["fantasy_points_per_game"] == pytest.approx(
        27.5 * _points_for(priced, "PTS")
    )
    assert body["player"]["fantasy_points_total"] == pytest.approx(
        round(27.5 * _points_for(priced, "PTS") * 65, 4)
    )
    assert STAT_NAME_TO_ID["GP"] in {line["stat_id"] for line in body["player"]["lines"]}
