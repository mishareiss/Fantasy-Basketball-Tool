"""GET /sources and GET /board/consensus — the multi-source board.

The single-source value board (`GET /players/board`) has its own suite and is deliberately
untouched by all of this; the last test here says so out loud.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import AdpEntry, Player, Projection
from app.db.models.ranking import HORIZON_DYNASTY as TAG_DYNASTY
from app.db.models.ranking import HORIZON_REDRAFT as TAG_REDRAFT
from app.db.session import get_db
from app.main import app
from tests.conftest import AGE_AS_OF, SEASON

PROJECTION = "projection:espn"
ADP = "adp:espn"

# Two of the fixture pool, by ESPN id.
JOKIC = 3112335
WEMBY = 5104157


@pytest.fixture
def api(db):
    app.dependency_overrides[get_db] = lambda: db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def top_ids(db):
    """The best-projected players, so a hand-built ranking set is about people on the board."""
    return list(
        db.scalars(select(Projection.player_id).order_by(Projection.fantasy_points_per_game.desc()))
    )


# --- GET /sources -------------------------------------------------------------------------


def test_the_dynasty_horizon_lists_the_projection_the_market_and_the_dynasty_board(
    api, synced, make_ranking_set, top_ids
):
    make_ranking_set("Dynasty Top 200", TAG_DYNASTY, {top_ids[0]: 1, top_ids[1]: 2})

    body = api.get("/sources?horizon=dynasty").json()

    assert body["ranking_horizon"] == "dynasty"
    assert [source["id"] for source in body["sources"]] == [PROJECTION, ADP, "ranking:1"]
    assert [source["kind"] for source in body["sources"]] == ["projection", "adp", "ranking"]
    assert body["sources"][2]["label"] == "Dynasty Top 200"
    assert body["sources"][2]["horizon"] == "dynasty"
    assert body["sources"][2]["player_count"] == 2
    # A value source declares no tag: it derives both horizons from production.
    assert body["sources"][0]["horizon"] is None


def test_the_win_now_horizon_swaps_the_dynasty_board_out_for_the_redraft_one(
    api, synced, make_ranking_set, top_ids
):
    """The acceptance case for the whole two-vocabulary split."""
    make_ranking_set("Dynasty Top 200", TAG_DYNASTY, {top_ids[0]: 1, top_ids[1]: 2})
    make_ranking_set("Rest of Season", TAG_REDRAFT, {top_ids[1]: 1, top_ids[2]: 2})

    dynasty = api.get("/sources?horizon=dynasty").json()
    win_now = api.get("/sources?horizon=current_year").json()

    assert win_now["ranking_horizon"] == "redraft"
    assert [source["label"] for source in dynasty["sources"]][2:] == ["Dynasty Top 200"]
    assert [source["label"] for source in win_now["sources"]][2:] == ["Rest of Season"]
    # The value and market sources survive the switch; only their ORDER changes.
    assert {source["id"] for source in win_now["sources"]} >= {PROJECTION, ADP}


def test_the_source_list_reports_the_shared_pool_every_percentile_is_against(api, db, synced):
    body = api.get("/sources?horizon=dynasty").json()

    projected = set(db.scalars(select(Projection.player_id)))
    priced = set(db.scalars(select(AdpEntry.player_id)))
    assert body["pool_size"] == len(projected | priced)


def test_an_unknown_horizon_is_a_400_that_names_the_two_real_ones(api, synced):
    response = api.get("/sources?horizon=redraft")

    assert response.status_code == 400
    assert "current_year" in response.json()["detail"]


def test_a_cold_database_lists_no_sources_rather_than_failing(api, db):
    body = api.get("/sources?horizon=dynasty").json()

    assert body["sources"] == []
    assert body["pool_size"] == 0


# --- GET /board/consensus -----------------------------------------------------------------


def test_two_sources_give_a_column_each_a_consensus_and_a_spread(
    api, synced, make_ranking_set, top_ids
):
    # Deliberately disagrees with the projection: its #1 is the projection's #3.
    make_ranking_set("Dynasty Top 200", TAG_DYNASTY, {top_ids[2]: 1, top_ids[0]: 2, top_ids[1]: 3})

    body = api.get(
        f"/board/consensus?horizon=dynasty&sources={PROJECTION},ranking:1&method=rank"
    ).json()

    assert [source["id"] for source in body["sources"]] == [PROJECTION, "ranking:1"]
    assert body["method"] == "rank"

    top = body["players"][0]
    assert set(top["cells"]) == {PROJECTION, "ranking:1"}
    assert top["cells"][PROJECTION]["rank"] == 1
    assert top["cells"]["ranking:1"]["rank"] == 2
    assert top["consensus"] == 1.5
    assert top["sources_present"] == 2
    assert top["spread"] is not None and top["rank_spread"] == 1.0
    assert top["name"] and top["positions"]


def test_the_rows_are_ordered_by_the_consensus(api, synced):
    body = api.get(f"/board/consensus?horizon=dynasty&sources={PROJECTION},{ADP}").json()

    values = [row["consensus"] for row in body["players"]]
    assert values == sorted(values)
    assert [row["rank"] for row in body["players"]] == list(range(1, len(values) + 1))


def test_the_percentile_method_orders_the_other_way_up(api, synced):
    body = api.get(
        f"/board/consensus?horizon=dynasty&sources={PROJECTION},{ADP}&method=percentile"
    ).json()

    values = [row["consensus"] for row in body["players"]]
    assert values == sorted(values, reverse=True)
    assert body["players"][0]["cells"][PROJECTION]["percentile"] == 100.0


def test_one_row_per_player_over_the_union_of_the_selected_sources(api, db, synced):
    body = api.get(f"/board/consensus?horizon=dynasty&sources={PROJECTION},{ADP}&limit=1000").json()

    ids = [row["espn_player_id"] for row in body["players"]]
    projected = set(db.scalars(select(Projection.player_id)))
    priced = set(db.scalars(select(AdpEntry.player_id)))

    assert len(ids) == len(set(ids))
    assert set(ids) == projected | priced


def test_a_player_missing_from_a_source_is_flagged_rather_than_sunk(
    api, synced, make_ranking_set, top_ids
):
    """The rule the whole engine turns on, asserted end to end."""
    make_ranking_set("Dynasty Top 200", TAG_DYNASTY, {top_ids[0]: 1})

    body = api.get(
        f"/board/consensus?horizon=dynasty&sources={PROJECTION},ranking:1&limit=1000"
    ).json()
    rows = {row["espn_player_id"]: row for row in body["players"]}

    skipped = rows[top_ids[1]]
    assert skipped["sources_present"] == 1
    assert skipped["sources_missing"] == ["ranking:1"]
    assert set(skipped["cells"]) == {PROJECTION}
    # His consensus is his projection rank, NOT that averaged with a fabricated last place.
    assert skipped["consensus"] == skipped["cells"][PROJECTION]["rank"]
    assert skipped["spread"] is None


def test_selecting_no_sources_at_all_defaults_to_every_eligible_one(api, synced):
    body = api.get("/board/consensus?horizon=dynasty").json()

    assert [source["id"] for source in body["sources"]] == [PROJECTION, ADP]


def test_an_unknown_source_id_is_a_400_rather_than_a_quietly_thinner_average(api, synced):
    response = api.get(f"/board/consensus?horizon=dynasty&sources={PROJECTION},ranking:404")

    assert response.status_code == 400
    assert "ranking:404" in response.json()["detail"]


def test_a_rank_source_from_the_other_horizon_is_unknown_here_not_silently_included(
    api, synced, make_ranking_set, top_ids
):
    make_ranking_set("Rest of Season", TAG_REDRAFT, {top_ids[0]: 1, top_ids[1]: 2})

    response = api.get(f"/board/consensus?horizon=dynasty&sources={PROJECTION},ranking:1")

    assert response.status_code == 400
    assert "ranking:1" in response.json()["detail"]
    assert api.get("/board/consensus?horizon=current_year&sources=ranking:1").status_code == 200


def test_an_unknown_method_is_a_400_that_names_the_real_ones(api, synced):
    response = api.get("/board/consensus?horizon=dynasty&method=average")

    assert response.status_code == 400
    assert "rank" in response.json()["detail"] and "percentile" in response.json()["detail"]


def test_an_empty_sources_parameter_says_to_pick_one(api, synced):
    response = api.get("/board/consensus?horizon=dynasty&sources=")

    assert response.status_code == 400
    assert "at least one" in response.json()["detail"]


def test_a_database_with_nothing_synced_says_so_instead_of_returning_nothing(api, db):
    response = api.get("/board/consensus?horizon=dynasty")

    assert response.status_code == 404
    assert "sync" in response.json()["detail"].lower()


def test_the_position_filter_narrows_the_board_and_renumbers_it(api, synced):
    body = api.get("/board/consensus?horizon=dynasty&position=c&limit=1000").json()

    assert body["position"] == "C"
    assert body["players"]
    assert all("C" in row["positions"] for row in body["players"])
    assert [row["rank"] for row in body["players"]] == list(range(1, len(body["players"]) + 1))
    assert body["total_ranked"] < api.get("/board/consensus?limit=1000").json()["total_ranked"]


def test_limit_pages_the_board_without_changing_what_it_counted(api, synced):
    full = api.get("/board/consensus?horizon=dynasty&limit=1000").json()
    page = api.get("/board/consensus?horizon=dynasty&limit=3").json()

    assert len(page["players"]) == 3
    assert page["total_ranked"] == full["total_ranked"]


def test_the_rows_carry_the_age_and_the_date_it_was_computed_at(api, db, aged):
    body = api.get("/board/consensus?horizon=dynasty&limit=1000").json()

    assert body["age_as_of"] == AGE_AS_OF.isoformat()
    ages = [row["age"] for row in body["players"] if row["age"] is not None]
    assert ages and all(18 <= age <= 45 for age in ages)
    assert next(row["age"] for row in body["players"] if row["name"] == "LeBron James") == 41


def test_a_player_we_can_name_but_nobody_ranks_is_not_a_row(api, db, synced):
    """The pool is what the sources cover, not everyone in the player table."""
    unranked = Player(espn_player_id=999999, full_name="Nobody At All")
    db.add(unranked)
    db.commit()

    body = api.get("/board/consensus?horizon=dynasty&limit=1000").json()

    assert 999999 not in {row["espn_player_id"] for row in body["players"]}


# --- the board that was already there ------------------------------------------------------


def test_the_single_source_value_board_is_untouched(api, synced):
    """Consensus is a second view, not a replacement — /players/board still ranks and tiers."""
    body = api.get("/players/board?horizon=dynasty").json()

    assert body["source"] == "espn"
    assert body["players"][0]["dynasty_value"] > 0
    assert body["tiers"] == "auto"
    assert body["tier_summary"]


# --- the market as a third source ------------------------------------------------------------
#
# The point of the whole market_line task: sportsbook lines are derived into a `Projection`
# under their own source, so they are discovered by the SAME value-source adapter ESPN's
# projection uses. There is no market branch in `app.ranking.sources` and there should never
# be one — these tests are what says so.

MARKET = "projection:market"


def test_the_market_appears_as_a_value_source_under_both_horizons(
    api, db, synced, make_market_lines
):
    """Acceptance criterion 3. A value source can be aged, so it answers both questions."""
    make_market_lines({JOKIC: {"PTS": 27.5, "REB": 12.5, "AST": 9.5}})

    for horizon in ("dynasty", "current_year"):
        body = api.get(f"/sources?horizon={horizon}").json()
        market = next(source for source in body["sources"] if source["id"] == MARKET)
        assert market["kind"] == "projection"
        assert market["source"] == "market"
        assert market["season"] == SEASON
        assert market["player_count"] == 1
        # No declared tag: it derives both horizons from production, as ESPN's projection does.
        assert market["horizon"] is None


def test_a_second_book_is_a_second_source_rather_than_a_second_opinion_in_one(
    api, db, synced, make_market_lines
):
    make_market_lines({JOKIC: {"PTS": 27.5}})
    make_market_lines({JOKIC: {"PTS": 29.5}}, source="draftkings")

    ids = {source["id"] for source in api.get("/sources?horizon=dynasty").json()["sources"]}

    assert {MARKET, "projection:draftkings"} <= ids


def test_the_market_is_selectable_and_gives_a_cell_per_player(api, db, synced, make_market_lines):
    make_market_lines({JOKIC: {"PTS": 27.5, "REB": 12.5}, WEMBY: {"BLK": 3.5, "REB": 11.5}})

    body = api.get(f"/board/consensus?sources={PROJECTION},{MARKET}&horizon=dynasty").json()

    assert [source["id"] for source in body["sources"]] == [PROJECTION, MARKET]
    rows = {row["espn_player_id"]: row for row in body["players"]}
    assert MARKET in rows[JOKIC]["cells"]
    assert MARKET in rows[WEMBY]["cells"]
    assert rows[JOKIC]["sources_present"] == 2
    # Two sources that disagree have a spread; one that doesn't rank him has no cell at all.
    assert rows[JOKIC]["spread"] is not None


def test_a_player_with_no_lines_is_missing_from_the_market_not_last_on_it(
    api, db, synced, make_market_lines
):
    """The market covers whoever has props, which is a handful of players, not a board."""
    make_market_lines({JOKIC: {"PTS": 27.5}})

    body = api.get(f"/board/consensus?sources={PROJECTION},{MARKET}&horizon=dynasty").json()

    rows = {row["espn_player_id"]: row for row in body["players"]}
    assert MARKET in rows[JOKIC]["cells"]
    others = [row for row in body["players"] if row["espn_player_id"] != JOKIC]
    assert others and all(MARKET not in row["cells"] for row in others)
    assert all(MARKET in row["sources_missing"] for row in others)


def test_the_age_curve_applies_to_the_market_exactly_as_it_does_to_a_projection(
    api, db, aged, make_market_lines
):
    """Acceptance criterion 3's second half — and the reason it is a value source, not a rank one.

    The same two market lines rank a different board under the two horizons, because the
    dynasty one prices them through the age curve. A rank-only list cannot do this; that is
    the whole distinction `app.ranking.sources` is built on.
    """
    young, old = _youngest_and_oldest(db)
    # Priced so the older player is clearly the better win-now market read.
    make_market_lines({young: {"PTS": 20.0}, old: {"PTS": 26.0}})

    def market_ranks(horizon):
        body = api.get(f"/board/consensus?sources={MARKET}&horizon={horizon}&limit=1000").json()
        return {row["espn_player_id"]: row["cells"][MARKET]["rank"] for row in body["players"]}

    win_now = market_ranks("current_year")
    dynasty = market_ranks("dynasty")

    assert win_now[old] < win_now[young]  # more points per game today
    assert dynasty[young] < dynasty[old]  # and the curve turns that around
    assert db.get(Player, young).age < db.get(Player, old).age


def test_the_market_joins_the_shared_pool_it_is_measured_against(
    api, db, synced, make_market_lines
):
    """One denominator for every source: a market cell's percentile means what ESPN's does."""
    before = api.get("/sources?horizon=dynasty").json()["pool_size"]
    make_market_lines({JOKIC: {"PTS": 27.5}})

    body = api.get("/sources?horizon=dynasty").json()

    # Jokic is already in the pool via ESPN, so adding a line about him widens nothing.
    assert body["pool_size"] == before
    consensus = api.get(f"/board/consensus?sources={MARKET}&horizon=dynasty").json()
    assert consensus["pool_size"] == before
    assert 0.0 <= consensus["players"][0]["cells"][MARKET]["percentile"] <= 100.0


def _youngest_and_oldest(db) -> tuple[int, int]:
    """Two players with ages far enough apart for the curve to have a real opinion."""
    aged_players = sorted(
        (player for player in db.scalars(select(Player)) if player.age is not None),
        key=lambda player: player.age,
    )
    return aged_players[0].espn_player_id, aged_players[-1].espn_player_id


# --- and the single-source board is still exactly what it was ---------------------------------


def test_market_rows_do_not_change_the_espn_board_at_all(api, db, synced, make_market_lines):
    """Acceptance criterion 4, asserted on the whole response body rather than a field or two.

    `GET /players/board` defaults to source='espn'; a market projection is a row under a
    different source, so it must be invisible here — same players, same order, same tiers,
    same numbers. Byte for byte.
    """
    before = api.get("/players/board?limit=1000&horizon=dynasty").json()

    make_market_lines(
        {JOKIC: {"PTS": 27.5, "REB": 12.5, "AST": 9.5}, WEMBY: {"BLK": 3.5, "REB": 11.5}}
    )

    assert api.get("/players/board?limit=1000&horizon=dynasty").json() == before
    assert (
        api.get("/players/board?limit=1000&horizon=current_year").json()
        == api.get("/players/board?limit=1000&horizon=current_year").json()
    )
    # The market projections really were written — this is not a vacuous comparison.
    assert db.scalars(select(Projection).where(Projection.source == "market")).all()


def test_the_market_board_is_available_on_request_though(api, db, synced, make_market_lines):
    """Nothing is hidden: it is an ordinary projection source, so it ranks like one."""
    make_market_lines({JOKIC: {"PTS": 27.5, "REB": 12.5, "AST": 9.5}})

    body = api.get("/players/board?source=market").json()

    assert body["source"] == "market"
    assert [row["espn_player_id"] for row in body["players"]] == [JOKIC]
