"""Tiers on our board: seeded once from the value gaps, then bands the order reflows through.

The arithmetic has its own file (`test_master_tiers`). What is asserted here is the half that
needs a database and an endpoint: that the first GET comes back tiered and the second doesn't
re-cut it, that dragging a player between bands changes his tier and nothing else, that a
dragged divider persists, and — the section at the bottom — that none of it moves a rank or
touches a board that was already here.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import MasterTierBreak, Player
from app.db.models.ranking import HORIZON_DYNASTY as TAG_DYNASTY
from app.db.session import get_db
from app.main import app
from app.ranking.tiers import POSITIONS, value_envelope
from tests.conftest import TIER_PARAMS

# Two of the fixture pool, by ESPN id — the same two `test_api_master` works with.
JOKIC = 3112335
WEMBY = 5104157


@pytest.fixture
def api(db):
    app.dependency_overrides[get_db] = lambda: db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def ids_of(body) -> list[int]:
    return [row["espn_player_id"] for row in body["players"]]


def ranks_of(body) -> dict[int, int]:
    return {row["espn_player_id"]: row["rank"] for row in body["players"]}


def tiers_of(body) -> dict[int, int]:
    return {row["espn_player_id"]: row["overall_tier"] for row in body["players"]}


def scope(body, name: str) -> dict:
    return next(row for row in body["tiers"] if row["scope"] == name)


def cuts_of(body, name: str = "overall") -> list[int]:
    return scope(body, name)["cut_ranks"]


def row_for(body, player_id: int, key: str = "players"):
    return next(row for row in body[key] if row["espn_player_id"] == player_id)


# --- the auto seed ----------------------------------------------------------------------------


def test_the_first_read_comes_back_tiered_without_anyone_asking(api, aged):
    """Acceptance criterion 1: a board nobody has tiered is still a tiered board."""
    body = api.get("/master/board").json()

    assert body["seeded"] is True
    overall = scope(body, "overall")
    assert overall["size"] == body["total_ranked"]
    assert overall["cut_ranks"][0] == 1
    assert overall["tier_count"] == len(overall["cut_ranks"]) > 1
    # Every ranked player is in a band. Bands cover the whole board, so there are no holes.
    assert all(row["overall_tier"] is not None for row in body["players"])


def test_the_tiers_are_bands_so_the_numbers_only_ever_go_up_down_the_board(api, aged):
    body = api.get("/master/board").json()

    numbers = [row["overall_tier"] for row in body["players"]]

    assert numbers == sorted(numbers)
    assert numbers[0] == 1
    assert max(numbers) == scope(body, "overall")["tier_count"]


def test_a_tier_starts_at_exactly_the_ranks_the_cut_list_names(api, aged):
    body = api.get("/master/board").json()
    by_rank = {row["rank"]: row["overall_tier"] for row in body["players"]}

    for number, cut in enumerate(cuts_of(body), start=1):
        assert by_rank[cut] == number
        if cut > 1:
            assert by_rank[cut - 1] == number - 1


def test_the_seed_opens_a_tier_where_the_dynasty_values_cliff(api, aged):
    """The breaks are the value board's breaks, because they came from the same tierer.

    Asserted against the tierer's own yardstick — every cut sits on a drop bigger than
    `gap_multiple` times the median drop — rather than against numbers copied out of the
    fixture, which would be a second tierer written in the test file.
    """
    from statistics import median

    body = api.get("/master/board").json()
    values = {
        row["espn_player_id"]: row["dynasty_value"]
        for row in api.get("/players/board?horizon=dynasty&limit=1000").json()["players"]
    }
    ordered = value_envelope([values.get(player_id) for player_id in ids_of(body)])
    gaps = [above - below for above, below in zip(ordered, ordered[1:], strict=False)]
    threshold = TIER_PARAMS["tier_gap_multiple"] * median(gaps)

    assert len(cuts_of(body)) > 1
    for cut in cuts_of(body)[1:]:
        # Gap `cut - 2` is the drop from rank `cut - 1` into rank `cut`: the cliff it opened on.
        assert gaps[cut - 2] > threshold


def test_a_second_read_does_not_reseed(api, db, aged):
    """Seeded once, ever — the same promise `reconcile` makes about the order."""
    first = api.get("/master/board").json()
    stored = sorted(
        db.scalars(select(MasterTierBreak.cut_rank).where(MasterTierBreak.scope == "overall"))
    )

    second = api.get("/master/board").json()

    assert cuts_of(second) == cuts_of(first) == stored
    assert tiers_of(second) == tiers_of(first)


def test_a_valueless_player_is_carried_rather_than_stranded_in_a_tier_of_his_own(api, db, aged):
    """A rank-only import has no projection, so no dynasty value. He must not open a band.

    Dropped into the middle of the board on purpose: that is where a break opened on a missing
    value would be unmistakable, because a valueless player read as a value of zero is the
    biggest cliff on the board.
    """
    nobody = 424242
    db.add(Player(espn_player_id=nobody, full_name="Unpriced Prospect", positions=["SF"]))
    db.commit()
    # Nobody ranks him, so he isn't seeded in; putting him on the board by hand is what a
    # rank-only arrival looks like from here.
    api.put(f"/master/entries/{nobody}", json={"note": "stash"})
    board = ids_of(api.get("/master/board").json())
    board.remove(nobody)
    slot = 20
    api.put("/master/order", json={"ordered_player_ids": [*board[:slot], nobody, *board[slot:]]})
    api.post("/master/tiers/reseed?scope=overall")

    body = api.get("/master/board").json()
    his = row_for(body, nobody)
    above = next(row for row in body["players"] if row["rank"] == his["rank"] - 1)
    below = next(row for row in body["players"] if row["rank"] == his["rank"] + 1)

    assert his["rank"] == slot + 1
    assert his["overall_tier"] == above["overall_tier"] == below["overall_tier"]
    assert his["rank"] not in cuts_of(body)


def test_a_cold_database_is_an_untiered_board_rather_than_an_error(api, db):
    body = api.get("/master/board").json()

    assert body["players"] == []
    assert all(row["cut_ranks"] == [] and row["size"] == 0 for row in body["tiers"])


# --- rank bands: the tiers reflow, nothing is rewritten ----------------------------------------


def test_moving_a_player_into_the_top_band_makes_him_tier_one_with_no_tier_edit(api, aged):
    """Acceptance criterion 3, and the reason tiers are stored as ranks rather than per player."""
    seeded = api.get("/master/board").json()
    stored = cuts_of(seeded)
    # Somebody well down the board, and definitely not already in tier 1.
    mover = next(row for row in seeded["players"] if row["overall_tier"] > 1)
    before = mover["overall_tier"]
    order = [row["espn_player_id"] for row in seeded["players"]]
    order.remove(mover["espn_player_id"])

    moved = api.put(
        "/master/order", json={"ordered_player_ids": [mover["espn_player_id"], *order]}
    ).json()

    assert before > 1
    assert row_for(moved, mover["espn_player_id"])["overall_tier"] == 1
    # Nothing was written to the tier table: the band did not move, he did.
    assert cuts_of(moved) == stored


def test_reordering_the_board_never_touches_the_stored_cuts(api, db, aged):
    seeded = cuts_of(api.get("/master/board").json())
    order = ids_of(api.get("/master/board").json())

    api.put("/master/order", json={"ordered_player_ids": list(reversed(order))})

    assert (
        sorted(
            db.scalars(select(MasterTierBreak.cut_rank).where(MasterTierBreak.scope == "overall"))
        )
        == seeded
    )


def test_the_player_who_was_tier_one_falls_to_the_band_he_lands_in(api, aged):
    """The mirror of the promotion: bands are about the slot, never about the person."""
    seeded = api.get("/master/board").json()
    top = seeded["players"][0]["espn_player_id"]
    order = ids_of(seeded)
    order.remove(top)

    dropped = api.put("/master/order", json={"ordered_player_ids": [*order, top]}).json()

    assert row_for(dropped, top)["overall_tier"] == scope(dropped, "overall")["tier_count"]


def test_setting_a_player_aside_takes_him_out_of_every_band(api, aged):
    """He has no rank, and a tier here is a band over the ranks."""
    api.get("/master/board")

    body = api.put(f"/master/entries/{JOKIC}", json={"excluded": True}).json()

    parked = row_for(body, JOKIC, key="set_aside")
    assert parked["overall_tier"] is None and parked["position_tier"] is None


# --- per-position tiers, and the position filter -----------------------------------------------


def test_every_position_gets_its_own_bands_over_its_own_sub_order(api, aged):
    body = api.get("/master/board").json()

    for position in POSITIONS:
        row = scope(body, position)
        assert row["size"] == sum(
            1 for player in body["players"] if position in player["positions"]
        )
        assert row["cut_ranks"][0] == 1
        assert row["tier_count"] == len(row["cut_ranks"])


def test_a_position_tier_counts_that_position_not_the_board(api, aged):
    """The eighth-best point guard is at PG-rank 8 whatever his overall rank is."""
    body = api.get("/master/board?position=PG").json()
    expected = []
    number, position = 0, 0
    cuts = cuts_of(body, "PG")
    for rank in range(1, len(body["players"]) + 1):
        while position < len(cuts) and cuts[position] <= rank:
            number, position = number + 1, position + 1
        expected.append(number)

    assert [row["position_tier"] for row in body["players"]] == expected
    # Which is emphatically NOT the overall tier column beside it.
    assert [row["overall_tier"] for row in body["players"]] != expected


def test_the_position_filter_keeps_the_true_overall_rank_and_the_true_overall_tier(api, aged):
    """Acceptance criterion 2. A point guard's place on our board is his place on our board."""
    full = api.get("/master/board").json()
    pg = api.get("/master/board?position=PG").json()
    expected = [row for row in full["players"] if "PG" in row["positions"]]

    assert ids_of(pg) == [row["espn_player_id"] for row in expected]
    assert ranks_of(pg) == {row["espn_player_id"]: row["rank"] for row in expected}
    assert tiers_of(pg) == {row["espn_player_id"]: row["overall_tier"] for row in expected}
    assert pg["position"] == "PG"
    assert pg["total_ranked"] == len(expected) < full["total_ranked"]
    # The whole board's size is still on the response, as the overall scope's.
    assert scope(pg, "overall")["size"] == full["total_ranked"]


def test_the_filter_is_case_insensitive_and_the_structure_is_unchanged_by_it(api, aged):
    full = api.get("/master/board").json()
    lower = api.get("/master/board?position=pg").json()

    assert lower["position"] == "PG"
    assert lower["tiers"] == full["tiers"]
    assert ids_of(lower) == [
        row["espn_player_id"] for row in full["players"] if "PG" in row["positions"]
    ]


def test_a_position_we_do_not_have_is_a_422_that_names_the_ones_we_do(api, aged):
    response = api.get("/master/board?position=GUARD")

    assert response.status_code == 422
    assert "PG" in response.json()["detail"]


def test_a_player_listed_at_two_positions_is_reported_at_the_one_you_asked_for(api, aged):
    body = api.get("/master/board").json()
    both = next(row for row in body["players"] if len(row["positions"]) > 1)
    second = both["positions"][1]

    unfiltered = row_for(body, both["espn_player_id"])
    filtered = row_for(api.get(f"/master/board?position={second}").json(), both["espn_player_id"])

    assert unfiltered["position_scope"] == both["positions"][0]
    assert filtered["position_scope"] == second


def test_a_player_we_have_no_position_for_has_no_position_tier(api, db, synced):
    positionless = 515151
    db.add(Player(espn_player_id=positionless, full_name="No Position", positions=[]))
    db.commit()
    api.put(f"/master/entries/{positionless}", json={"note": "somewhere"})

    row = row_for(api.get("/master/board").json(), positionless)

    assert row["overall_tier"] is not None
    assert row["position_tier"] is None and row["position_scope"] is None


# --- PUT /master/tiers, and the reseed -----------------------------------------------------------


def test_a_dragged_divider_is_persisted_and_read_back(api, aged):
    api.get("/master/board")

    saved = api.put("/master/tiers", json={"scope": "overall", "cut_ranks": [1, 3, 9]})

    assert saved.status_code == 200
    assert cuts_of(saved.json()) == [1, 3, 9]
    reread = api.get("/master/board").json()
    assert cuts_of(reread) == [1, 3, 9]
    assert [row["overall_tier"] for row in reread["players"][:10]] == [1, 1, 2, 2, 2, 2, 2, 2, 3, 3]


def test_saving_the_point_guards_dividers_leaves_the_overall_ones_alone(api, aged):
    before = api.get("/master/board").json()

    after = api.put("/master/tiers", json={"scope": "PG", "cut_ranks": [1, 2]}).json()

    assert cuts_of(after, "PG") == [1, 2]
    assert cuts_of(after, "overall") == cuts_of(before, "overall")


def test_a_divider_save_moves_nobody(api, aged):
    before = api.get("/master/board").json()

    after = api.put("/master/tiers", json={"scope": "overall", "cut_ranks": [1, 5]}).json()

    assert ids_of(after) == ids_of(before)
    assert ranks_of(after) == ranks_of(before)


@pytest.mark.parametrize(
    "cut_ranks",
    [
        pytest.param([4, 9], id="does not start at 1"),
        pytest.param([1, 9, 4], id="out of order"),
        pytest.param([1, 4, 4], id="duplicated"),
        pytest.param([1, 99999], id="past the end of the board"),
        pytest.param([], id="empty"),
    ],
)
def test_dividers_that_cannot_describe_bands_are_a_422(api, aged, cut_ranks):
    api.get("/master/board")

    response = api.put("/master/tiers", json={"scope": "overall", "cut_ranks": cut_ranks})

    assert response.status_code == 422


def test_a_refused_save_leaves_the_stored_dividers_where_they_were(api, aged):
    before = cuts_of(api.get("/master/board").json())

    api.put("/master/tiers", json={"scope": "overall", "cut_ranks": [1, 99999]})

    assert cuts_of(api.get("/master/board").json()) == before


def test_a_scope_we_do_not_have_is_a_422_on_the_save_too(api, aged):
    response = api.put("/master/tiers", json={"scope": "guard", "cut_ranks": [1]})

    assert response.status_code == 422
    assert "'PG'" in response.json()["detail"]


def test_a_reseed_throws_the_hand_moved_dividers_away_and_restores_the_auto_split(api, aged):
    auto = cuts_of(api.get("/master/board").json())
    api.put("/master/tiers", json={"scope": "overall", "cut_ranks": [1, 3, 9]})

    reseeded = api.post("/master/tiers/reseed?scope=overall").json()

    assert cuts_of(reseeded) == auto
    assert cuts_of(api.get("/master/board").json()) == auto


def test_a_reseed_is_scoped_and_leaves_the_other_scopes_alone(api, aged):
    api.get("/master/board")
    api.put("/master/tiers", json={"scope": "overall", "cut_ranks": [1, 3]})
    api.put("/master/tiers", json={"scope": "PG", "cut_ranks": [1, 2]})

    after = api.post("/master/tiers/reseed?scope=PG").json()

    assert cuts_of(after, "overall") == [1, 3]
    assert cuts_of(after, "PG") != [1, 2]


def test_reseeding_a_scope_we_do_not_have_is_a_422(api, aged):
    assert api.post("/master/tiers/reseed?scope=guard").status_code == 422


def test_a_reseed_moves_nobody(api, aged):
    before = api.get("/master/board").json()
    api.put("/master/tiers", json={"scope": "overall", "cut_ranks": [1, 3]})

    after = api.post("/master/tiers/reseed?scope=overall").json()

    assert ids_of(after) == ids_of(before) and ranks_of(after) == ranks_of(before)


def test_starting_the_board_over_starts_the_tiers_over_with_it(api, db, aged):
    api.get("/master/board")
    api.put("/master/tiers", json={"scope": "overall", "cut_ranks": [1, 3]})

    body = api.post("/master/seed?reset=true").json()

    assert cuts_of(body) != [1, 3]
    assert cuts_of(body) == cuts_of(api.get("/master/board").json())


# --- guards: nothing above moves a rank, and no other board changed ------------------------------


def test_no_tier_operation_ever_changes_the_order_or_the_ranks(api, aged):
    """Acceptance criterion 4. Tiers draw lines between ranks; they do not assign them."""
    before = api.get("/master/board").json()

    api.put("/master/tiers", json={"scope": "overall", "cut_ranks": [1, 2, 3, 4]})
    api.put("/master/tiers", json={"scope": "C", "cut_ranks": [1, 2]})
    api.post("/master/tiers/reseed?scope=overall")
    api.get("/master/board?position=SF")

    after = api.get("/master/board").json()
    assert ids_of(after) == ids_of(before)
    assert ranks_of(after) == ranks_of(before)
    assert after["total_ranked"] == before["total_ranked"]


def test_the_board_without_a_position_filter_is_what_it_always_was_plus_the_tier_columns(api, aged):
    """The one field-by-field check that this task ADDED to the row and changed nothing on it."""
    added = {"overall_tier", "position_tier", "position_scope"}
    body = api.get("/master/board").json()

    for row in body["players"]:
        assert added <= set(row)
    # The order, the ranks, the reference column and the flags are all still seeded-identical.
    assert all(row["consensus_rank"] == row["rank"] for row in body["players"])
    assert all(row["delta"] == 0 for row in body["players"])
    assert body["seeded"] is True and body["added"] == 0


def test_the_other_boards_are_byte_identical_through_every_tier_operation(
    api, aged, make_ranking_set
):
    """Acceptance criterion 4: this task adds a table and two routes; it changes no board."""
    make_ranking_set("Dynasty Top 3", TAG_DYNASTY, {JOKIC: 1, WEMBY: 2})
    value_board = api.get("/players/board?horizon=dynasty").text
    value_tiers = api.get("/valuation/tiers?horizon=dynasty").text
    consensus = api.get("/board/consensus?horizon=dynasty&limit=1000").text
    sources = api.get("/sources?horizon=dynasty").text
    position_board = api.get("/players/board?position=PG").text

    seeded = ids_of(api.get("/master/board").json())
    api.put("/master/order", json={"ordered_player_ids": list(reversed(seeded))})
    api.put("/master/tiers", json={"scope": "overall", "cut_ranks": [1, 6, 11]})
    api.post("/master/tiers/reseed?scope=PG")
    api.put(f"/master/entries/{JOKIC}", json={"tag": "fade", "note": "no", "excluded": True})

    assert api.get("/players/board?horizon=dynasty").text == value_board
    assert api.get("/valuation/tiers?horizon=dynasty").text == value_tiers
    assert api.get("/board/consensus?horizon=dynasty&limit=1000").text == consensus
    assert api.get("/sources?horizon=dynasty").text == sources
    assert api.get("/players/board?position=PG").text == position_board
