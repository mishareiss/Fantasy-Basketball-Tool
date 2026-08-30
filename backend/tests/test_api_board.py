"""GET /players/board — the first data-driven draft board."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.ages import sync_ages
from app.config import get_settings
from app.db.models import AdpEntry, Projection
from app.db.session import get_db
from app.ingest import run_import
from app.main import app
from tests.conftest import AGE_AS_OF, SEASON

LEAGUE_ID = 999999


@pytest.fixture
def api(db):
    app.dependency_overrides[get_db] = lambda: db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


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


def test_an_imported_projection_ranks_the_board_and_leaves_espn_untouched(
    api, db, synced, projection_csv
):
    """Two sources, one board endpoint, one `source=` away from each other.

    This is what the whole import kind is for: rank by Hashtag's numbers priced under our
    coefficients, then flip back to ESPN's and find the players the two disagree about.
    """
    espn_before = {
        row["espn_player_id"]: row["fantasy_points_per_game"]
        for row in api.get("/players/board?limit=1000").json()["players"]
    }

    run_import(
        db,
        kind="projection",
        source="hashtag",
        season=SEASON,
        text=projection_csv,
        dry_run=False,
        options={"basis": "per_game"},
    )

    imported = api.get("/players/board?limit=1000&source=hashtag").json()

    assert imported["source"] == "hashtag"
    assert imported["season"] == SEASON
    # Only the players that file carried, ranked by our pricing of *its* numbers.
    assert imported["total_ranked"] == 12
    per_game = [row["fantasy_points_per_game"] for row in imported["players"]]
    assert per_game == sorted(per_game, reverse=True)
    assert per_game[0] > 0

    # ESPN's board is byte-for-byte what it was: the two coexist, keyed by source.
    espn_after = {
        row["espn_player_id"]: row["fantasy_points_per_game"]
        for row in api.get("/players/board?limit=1000").json()["players"]
    }
    assert espn_after == espn_before
    assert len(espn_after) > imported["total_ranked"]

    # ...and both are stored, for the same player, under the same key but a different source.
    rows = db.scalars(select(Projection).where(Projection.player_id == 3112335)).all()
    assert {row.source for row in rows} == {"espn", "hashtag"}


def test_an_imported_projection_prices_a_player_espn_has_no_read_on(
    api, db, synced, projection_csv
):
    """D.J. Carton has no ESPN projection; the import gives us one, and a rankable player."""
    carton = 4432820
    assert (
        db.scalar(
            select(Projection).where(Projection.player_id == carton, Projection.source == "espn")
        )
        is None
    )

    run_import(
        db,
        kind="projection",
        source="hashtag",
        season=SEASON,
        text=projection_csv,
        dry_run=False,
    )

    board = api.get("/players/board?limit=1000&source=hashtag").json()

    assert carton in {row["espn_player_id"] for row in board["players"]}
    assert carton not in {
        row["espn_player_id"] for row in api.get("/players/board?limit=1000").json()["players"]
    }


def test_the_adp_column_is_still_espn_when_the_ranking_source_is_imported(
    api, db, synced, projection_csv
):
    """Rank on someone else's projection, read ESPN's market — the gap is the whole point."""
    run_import(
        db, kind="projection", source="hashtag", season=SEASON, text=projection_csv, dry_run=False
    )

    top = api.get("/players/board?limit=1&source=hashtag").json()

    assert top["adp_source"] == "espn"
    assert top["players"][0]["adp"] is not None


# --- the value horizons (FEATURE_SPEC 4) -------------------------------------------------


def test_the_board_is_dynasty_by_default(api, aged):
    """It's a dynasty startup: the win-now lens is the one you have to ask for."""
    body = api.get("/players/board").json()

    assert body["horizon"] == "dynasty"
    assert body == api.get("/players/board?horizon=dynasty").json()


def test_current_year_reproduces_the_board_we_had_before_horizons(api, aged):
    """The regression check on the refactor: win-now ordering is the old fppg ordering."""
    body = api.get("/players/board?limit=1000&horizon=current_year").json()

    per_game = [row["fantasy_points_per_game"] for row in body["players"]]
    assert per_game == sorted(per_game, reverse=True)
    assert [row["current_year_value"] for row in body["players"]] == per_game
    assert [row["rank"] for row in body["players"]] == list(range(1, len(per_game) + 1))


def test_every_row_carries_both_horizons(api, aged):
    body = api.get("/players/board?limit=1000&horizon=dynasty").json()

    for row in body["players"]:
        assert row["current_year_value"] == row["fantasy_points_per_game"]
        assert row["dynasty_value"] == pytest.approx(
            row["current_year_value"] * row["age_multiplier"]
        )
        assert row["age_adjusted"] is (row["age"] is not None)


def test_the_dynasty_board_is_ordered_by_dynasty_value(api, aged):
    body = api.get("/players/board?limit=1000&horizon=dynasty").json()

    values = [row["dynasty_value"] for row in body["players"]]
    assert values == sorted(values, reverse=True)
    # ...and it is genuinely a different board, not the same one relabelled.
    current = api.get("/players/board?limit=1000&horizon=current_year").json()
    assert [row["espn_player_id"] for row in body["players"]] != [
        row["espn_player_id"] for row in current["players"]
    ]


def test_the_youth_weighting_lifts_the_young_and_drops_the_old(api, aged):
    """Equal production, twelve years apart: the dynasty board has to separate them."""
    dynasty = {row["name"]: row for row in api.get("/players/board?limit=1000").json()["players"]}
    current = {
        row["name"]: row
        for row in api.get("/players/board?limit=1000&horizon=current_year").json()["players"]
    }

    # LeBron at 41 is the extreme case: real win-now value, floored dynasty value.
    lebron = dynasty["LeBron James"]
    assert lebron["age"] == 41
    assert lebron["age_multiplier"] == 0.4
    assert lebron["rank"] > current["LeBron James"]["rank"], "an aging star must fall"

    risers = [
        name
        for name, row in dynasty.items()
        if row["age"] is not None and row["age"] <= 21 and row["rank"] < current[name]["rank"]
    ]
    assert risers, "someone under 22 has to rise when youth is rewarded"


def test_a_player_with_no_age_is_ranked_on_his_projection_and_says_so(api, synced):
    """No birthdate is missing information: rank him where his production puts him, flagged."""
    body = api.get("/players/board?limit=1000&horizon=dynasty").json()

    assert all(row["age"] is None for row in body["players"])
    assert all(row["age_multiplier"] == 1.0 for row in body["players"])
    assert all(row["age_adjusted"] is False for row in body["players"])
    # With nothing to adjust by, the dynasty board IS the current-year board.
    assert [row["espn_player_id"] for row in body["players"]] == [
        row["espn_player_id"]
        for row in api.get("/players/board?limit=1000&horizon=current_year").json()["players"]
    ]


def test_a_dynasty_board_is_still_one_row_per_player(api, aged):
    body = api.get("/players/board?limit=1000&horizon=dynasty").json()

    ids = [row["espn_player_id"] for row in body["players"]]
    assert len(ids) == len(set(ids))
    assert body["total_ranked"] == len(ids)


def test_the_horizon_reorders_without_changing_who_is_on_the_board(api, aged):
    dynasty = api.get("/players/board?limit=1000&horizon=dynasty").json()
    current = api.get("/players/board?limit=1000&horizon=current_year").json()

    assert dynasty["total_ranked"] == current["total_ranked"]
    assert {row["espn_player_id"] for row in dynasty["players"]} == {
        row["espn_player_id"] for row in current["players"]
    }


def test_the_horizon_composes_with_the_position_filter(api, aged):
    body = api.get("/players/board?limit=1000&position=C&horizon=dynasty").json()

    assert body["players"]
    assert all("C" in row["positions"] for row in body["players"])
    values = [row["dynasty_value"] for row in body["players"]]
    assert values == sorted(values, reverse=True)


def test_an_unknown_horizon_lists_the_ones_that_work(api, synced):
    response = api.get("/players/board?horizon=next_decade")

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "current_year" in detail and "dynasty" in detail


def test_a_steeper_decline_setting_re_ranks_the_dynasty_board(api, aged):
    """Proof the board reads the settings, not a constant: move one, the board moves."""
    settings = get_settings()
    before = [row["name"] for row in api.get("/players/board?limit=1000").json()["players"]]
    original = settings.dynasty_decline_per_year
    try:
        settings.dynasty_decline_per_year = 0.15
        body = api.get("/players/board?limit=1000").json()
    finally:
        settings.dynasty_decline_per_year = original

    assert [row["name"] for row in body["players"]] != before
    lebron = next(row for row in body["players"] if row["name"] == "LeBron James")
    assert lebron["age_multiplier"] == 0.4, "the floor still holds"
    veteran = next(row for row in body["players"] if row["age"] == 29)
    assert veteran["age_multiplier"] == pytest.approx(0.7)
    # ...and the win-now board is untouched by any of it.
    assert [row["name"] for row in body["players"]] != [
        row["name"]
        for row in api.get("/players/board?limit=1000&horizon=current_year").json()["players"]
    ]


# --- draft tiers (FEATURE_SPEC 6) ---------------------------------------------------------


def test_the_board_is_tiered_by_default(api, aged):
    """A flat 1,095-row list is not a draft plan, so the tiers are what you get unasked."""
    body = api.get("/players/board?limit=1000").json()

    assert body["tiers"] == "auto"
    assert body["tier_pool"] == body["total_ranked"], "the fixture board fits inside TIER_POOL"
    assert body["tier_summary"]
    assert body["players"][0]["tier"] == 1


def test_tiers_only_ever_go_down_the_page(api, aged):
    """The invariant a divider is drawn from: worth less can never mean a better tier."""
    rows = api.get("/players/board?limit=1000").json()["players"]

    tiers = [row["tier"] for row in rows]
    assert tiers == sorted(tiers)
    assert set(tiers) == set(range(1, max(tiers) + 1)), "no tier number is skipped"


def test_a_break_falls_where_the_value_drop_is_unusual(api, aged):
    """Every boundary is a real drop, bigger than the bar the settings set."""
    body = api.get("/players/board?limit=1000").json()
    rows = body["players"]
    threshold = api.get("/valuation/tiers").json()["break_threshold"]

    assert body["tier_summary"][0]["gap"] is None, "the top of the board opens tier 1"
    for tier in body["tier_summary"][1:]:
        start = tier["start_rank"] - 1
        drop = rows[start - 1]["dynasty_value"] - rows[start]["dynasty_value"]
        assert tier["gap"] == pytest.approx(drop, abs=1e-4)
        assert tier["gap"] > threshold
        assert tier["gap_ratio"] > get_settings().tier_gap_multiple


def test_the_summary_describes_the_same_tiers_the_rows_carry(api, aged):
    """So a client can draw the dividers without recomputing — or disagreeing."""
    body = api.get("/players/board?limit=1000").json()
    rows = body["players"]

    for tier in body["tier_summary"]:
        members = [row for row in rows if row["tier"] == tier["tier"]]
        assert len(members) == tier["size"]
        assert members[0]["rank"] == tier["start_rank"]
        assert members[0]["dynasty_value"] == pytest.approx(tier["value_high"])
        assert members[-1]["dynasty_value"] == pytest.approx(tier["value_low"])
    assert sum(tier["size"] for tier in body["tier_summary"]) == body["tier_pool"]


def test_tiers_are_the_same_whether_you_ask_for_twenty_rows_or_two_hundred(api, aged):
    """They are cut over the pool, not over the page. A tier that changed with `limit` would
    mean the divider moved because you scrolled."""
    page = api.get("/players/board?limit=20").json()
    full = api.get("/players/board?limit=1000").json()

    assert page["tier_summary"] == full["tier_summary"]
    assert page["tier_pool"] == full["tier_pool"]
    by_id = {row["espn_player_id"]: row["tier"] for row in full["players"]}
    assert all(by_id[row["espn_player_id"]] == row["tier"] for row in page["players"])


def test_tiers_off_leaves_every_row_untiered(api, aged):
    body = api.get("/players/board?limit=1000&tiers=off").json()

    assert body["tiers"] == "off"
    assert body["tier_pool"] == 0
    assert body["tier_summary"] == []
    assert all(row["tier"] is None for row in body["players"])
    # ...and nothing else about the board moved.
    auto = api.get("/players/board?limit=1000").json()
    assert [row["espn_player_id"] for row in body["players"]] == [
        row["espn_player_id"] for row in auto["players"]
    ]


def test_a_filtered_board_still_shows_a_player_his_overall_tier(api, aged):
    """A point guard's tier is his tier on the board, not his tier among point guards."""
    overall = {
        row["espn_player_id"]: row["tier"]
        for row in api.get("/players/board?limit=1000").json()["players"]
    }
    body = api.get("/players/board?limit=1000&position=C").json()

    assert body["players"]
    assert all(overall[row["espn_player_id"]] == row["tier"] for row in body["players"])
    # The filtered page skips tiers nobody in it belongs to — which is the honest answer.
    shown = [row["tier"] for row in body["players"]]
    assert shown == sorted(shown)
    # The summary describes the board, not the page, so it is the unfiltered one either way.
    assert body["tier_summary"] == api.get("/players/board?limit=1000").json()["tier_summary"]
    assert body["tier_pool"] == len(overall) > len(body["players"])


def test_the_two_horizons_tier_differently(api, aged):
    """They rank different numbers, so they break in different places — that's the point."""
    dynasty = api.get("/players/board?limit=1000&horizon=dynasty").json()
    current = api.get("/players/board?limit=1000&horizon=current_year").json()

    assert dynasty["tier_summary"] != current["tier_summary"]
    dynasty_tier = {row["name"]: row["tier"] for row in dynasty["players"]}
    current_tier = {row["name"]: row["tier"] for row in current["players"]}
    assert any(dynasty_tier[name] != current_tier[name] for name in dynasty_tier)
    # LeBron is the extreme case: a top-tier win-now player and a bottom-tier dynasty asset.
    assert dynasty_tier["LeBron James"] > current_tier["LeBron James"]


def test_each_horizon_tiers_the_number_it_ranks_by(api, aged):
    for horizon, column in (("dynasty", "dynasty_value"), ("current_year", "current_year_value")):
        body = api.get(f"/players/board?limit=1000&horizon={horizon}").json()
        rows = body["players"]

        for tier in body["tier_summary"]:
            members = [row for row in rows if row["tier"] == tier["tier"]]
            assert members[0][column] == pytest.approx(tier["value_high"])
            assert members[-1][column] == pytest.approx(tier["value_low"])


def test_a_player_below_the_tiered_pool_is_untiered_not_last(api, aged):
    """TIER_POOL bounds the tiered set: gaps among players nobody drafts are noise."""
    settings = get_settings()
    original = settings.tier_pool
    try:
        settings.tier_pool = 10
        body = api.get("/players/board?limit=1000").json()
    finally:
        settings.tier_pool = original

    assert body["tier_pool"] == 10
    assert body["total_ranked"] > 10
    assert all(row["tier"] is not None for row in body["players"][:10])
    assert all(row["tier"] is None for row in body["players"][10:])


def test_a_higher_gap_multiple_re_cuts_the_board(api, aged):
    """Proof the board reads the settings, not a constant: move one, the tiers move."""
    settings = get_settings()
    before = api.get("/players/board?limit=1000").json()
    original = settings.tier_gap_multiple
    try:
        settings.tier_gap_multiple = 6.0
        body = api.get("/players/board?limit=1000").json()
    finally:
        settings.tier_gap_multiple = original

    assert len(body["tier_summary"]) < len(before["tier_summary"]), "a higher bar, fewer breaks"
    assert body["players"][0]["tier"] == 1
    # ...and the ranking underneath is untouched: only the dividers moved.
    assert [row["espn_player_id"] for row in body["players"]] == [
        row["espn_player_id"] for row in before["players"]
    ]


def test_the_maximum_caps_how_many_tiers_a_board_can_have(api, aged):
    settings = get_settings()
    original = settings.tier_max
    try:
        settings.tier_max = 3
        body = api.get("/players/board?limit=1000").json()
    finally:
        settings.tier_max = original

    assert len(body["tier_summary"]) == 3
    assert max(row["tier"] for row in body["players"]) == 3


def test_an_unknown_tiers_mode_lists_the_ones_that_work(api, synced):
    response = api.get("/players/board?tiers=manual")

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "auto" in detail and "off" in detail


def test_a_board_with_no_ages_is_still_tiered(api, synced):
    """No birthdate means no age adjustment, not no tier: he is tiered on his projection."""
    body = api.get("/players/board?limit=1000").json()

    assert all(row["age"] is None for row in body["players"])
    assert body["tier_summary"]
    assert body["players"][0]["tier"] == 1
    # With nothing to age by, the dynasty tiers are the win-now tiers.
    assert (
        body["tier_summary"]
        == api.get("/players/board?limit=1000&tiers=auto&horizon=current_year").json()[
            "tier_summary"
        ]
    )
