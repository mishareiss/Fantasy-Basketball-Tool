"""The Master Ranking: our own board, seeded once and then OURS.

Every test here is really one assertion in two halves — the order is ours, and the reference
column is theirs. A consensus change has to move the second and never the first; a rookie has
to be able to arrive without re-seeding; excluding a player has to be undoable. The last
section says the boards that were already here are untouched by all of it.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import MasterRankEntry, Player, Projection
from app.db.models.ranking import HORIZON_DYNASTY as TAG_DYNASTY
from app.db.session import get_db
from app.main import app
from tests.conftest import AGE_AS_OF

# Two of the fixture pool, by ESPN id.
JOKIC = 3112335
WEMBY = 5104157

# A player we carry an identity for and nobody ranks — the one every "he isn't in the pool"
# case is built from.
NOBODY = 999999


@pytest.fixture
def api(db):
    app.dependency_overrides[get_db] = lambda: db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def unranked(db) -> int:
    """A player in the player table who no source has an opinion about."""
    db.add(Player(espn_player_id=NOBODY, full_name="Nobody At All", positions=["SF"]))
    db.commit()
    return NOBODY


def ids_of(body) -> list[int]:
    return [row["espn_player_id"] for row in body["players"]]


def ranks_of(body) -> dict[int, int]:
    return {row["espn_player_id"]: row["rank"] for row in body["players"]}


def row_for(body, player_id: int, key: str = "players"):
    return next(row for row in body[key] if row["espn_player_id"] == player_id)


# --- the seed -------------------------------------------------------------------------------


def test_an_empty_board_seeds_itself_from_the_consensus_in_consensus_order(api, synced):
    """Acceptance criterion 1: the first GET is a complete board, not an empty one."""
    consensus = api.get("/board/consensus?horizon=dynasty&limit=1000").json()
    body = api.get("/master/board").json()

    assert body["seeded"] is True
    assert body["total_ranked"] == consensus["total_ranked"]
    assert ids_of(body) == ids_of(consensus)
    assert [row["rank"] for row in body["players"]] == list(range(1, len(body["players"]) + 1))
    # Seeded FROM the consensus, so at the moment of the seed we agree with it exactly.
    assert all(row["consensus_rank"] == row["rank"] for row in body["players"])
    assert all(row["delta"] == 0 for row in body["players"])
    assert body["set_aside"] == []


def test_the_seed_covers_every_player_in_the_pool_and_nobody_else(api, db, synced, unranked):
    body = api.get("/master/board").json()

    stored = set(db.scalars(select(MasterRankEntry.player_id)))
    assert stored == set(db.scalars(select(Projection.player_id))) | set(ids_of(body))
    # A player we can name but nobody ranks is not seeded onto the board.
    assert unranked not in stored


def test_a_second_read_does_not_reseed_or_reorder(api, synced):
    """The board is stored, so reading it twice is reading the same thing twice."""
    first = api.get("/master/board").json()
    second = api.get("/master/board").json()

    assert second["seeded"] is False
    assert second["added"] == 0
    assert ids_of(second) == ids_of(first)
    assert ranks_of(second) == ranks_of(first)


def test_a_cold_database_is_an_empty_board_rather_than_a_404(api, db):
    """Nothing synced, nothing imported, nothing ranked. That is a starting state, not an error."""
    response = api.get("/master/board")

    assert response.status_code == 200
    assert response.json()["players"] == []
    assert response.json()["total_ranked"] == 0


def test_the_board_names_the_sources_its_reference_is_averaged_from(api, synced):
    body = api.get("/master/board").json()

    assert [source["id"] for source in body["sources"]] == ["projection:espn", "adp:espn"]
    assert body["pool_size"] == api.get("/sources?horizon=dynasty").json()["pool_size"]
    assert body["age_as_of"] == AGE_AS_OF.isoformat()


# --- reordering, and the stability that is the whole point ------------------------------------


def test_a_reorder_is_persisted_and_read_back(api, synced):
    """Acceptance criterion 2: what a drag-drop saves."""
    seeded = ids_of(api.get("/master/board").json())
    moved = [seeded[3], *seeded[:3], *seeded[4:]]

    saved = api.put("/master/order", json={"ordered_player_ids": moved})

    assert saved.status_code == 200
    assert ids_of(saved.json()) == moved
    assert ids_of(api.get("/master/board").json()) == moved
    # The reference column followed him rather than being rewritten: we now have the fourth-best
    # consensus player first, and the delta says so.
    top = saved.json()["players"][0]
    assert top["consensus_rank"] == 4
    assert top["delta"] == 1 - 4


def test_the_order_survives_a_consensus_change_and_only_the_reference_moves(
    api, db, synced, make_ranking_set
):
    """Acceptance criterion 1's second half, and the reason the table exists.

    A new source lands that violently disagrees with the board — it ranks the pool backwards.
    Our ranks must not move a millimetre; the consensus column beside them must move a lot.
    """
    seeded = api.get("/master/board").json()
    before_ranks = ranks_of(seeded)
    before_reference = {row["espn_player_id"]: row["consensus_rank"] for row in seeded["players"]}

    backwards = list(reversed(ids_of(seeded)))
    make_ranking_set(
        "Contrarian Dynasty 60",
        TAG_DYNASTY,
        {player_id: place for place, player_id in enumerate(backwards, start=1)},
    )

    after = api.get("/master/board").json()

    assert ranks_of(after) == before_ranks
    assert ids_of(after) == ids_of(seeded)
    assert after["added"] == 0 and after["seeded"] is False
    # The field changed its mind, so our gap to it changed. Everything else held still.
    after_reference = {row["espn_player_id"]: row["consensus_rank"] for row in after["players"]}
    assert after_reference != before_reference
    assert all(
        row["delta"] == row["rank"] - row["consensus_rank"]
        for row in after["players"]
        if row["consensus_rank"] is not None
    )


def test_a_reorder_survives_a_consensus_change_too(api, db, synced, make_ranking_set):
    """The two halves together: a hand-made order, then the ground moving under it."""
    seeded = ids_of(api.get("/master/board").json())
    mine = [seeded[5], seeded[2], *[pid for pid in seeded if pid not in (seeded[5], seeded[2])]]
    api.put("/master/order", json={"ordered_player_ids": mine})

    make_ranking_set("Dynasty Top 3", TAG_DYNASTY, {seeded[9]: 1, seeded[8]: 2, seeded[7]: 3})

    after = api.get("/master/board").json()

    assert ids_of(after) == mine
    assert row_for(after, seeded[5])["rank"] == 1


def test_an_order_that_is_not_a_permutation_of_the_board_is_a_422_that_names_the_difference(
    api, synced, unranked
):
    """A stale drag-drop must fail loudly: the client's fix is to refresh, and it needs to know."""
    seeded = ids_of(api.get("/master/board").json())

    short = api.put("/master/order", json={"ordered_player_ids": seeded[:-1]})
    assert short.status_code == 422
    assert str(seeded[-1]) in short.json()["detail"]

    spare = api.put("/master/order", json={"ordered_player_ids": [*seeded, unranked]})
    assert spare.status_code == 422
    assert str(unranked) in spare.json()["detail"]

    twice = api.put("/master/order", json={"ordered_player_ids": [seeded[0], *seeded]})
    assert twice.status_code == 422
    assert "twice" in twice.json()["detail"]

    # And none of the three wrote anything.
    assert ids_of(api.get("/master/board").json()) == seeded


def test_an_excluded_player_must_not_be_sent_in_the_order(api, synced):
    seeded = ids_of(api.get("/master/board").json())
    api.put(f"/master/entries/{seeded[1]}", json={"excluded": True})

    response = api.put("/master/order", json={"ordered_player_ids": seeded})

    assert response.status_code == 422
    assert str(seeded[1]) in response.json()["detail"]


# --- tags, notes, and setting a player aside --------------------------------------------------


def test_a_tag_and_a_note_are_stored_without_touching_his_place(api, synced):
    seeded = ids_of(api.get("/master/board").json())

    body = api.put(
        f"/master/entries/{seeded[2]}", json={"tag": "target", "note": "reach a round early"}
    ).json()

    row = row_for(body, seeded[2])
    assert (row["tag"], row["note"], row["rank"]) == ("target", "reach a round early", 3)
    assert ids_of(body) == seeded

    # And a later write of one field leaves the other alone.
    body = api.put(f"/master/entries/{seeded[2]}", json={"note": "or not"}).json()
    assert row_for(body, seeded[2])["tag"] == "target"
    # An explicit null is still how you clear one.
    body = api.put(f"/master/entries/{seeded[2]}", json={"tag": None}).json()
    assert row_for(body, seeded[2])["tag"] is None


def test_a_tag_we_do_not_recognise_is_a_422_naming_the_ones_we_do(api, synced):
    seeded = ids_of(api.get("/master/board").json())

    response = api.put(f"/master/entries/{seeded[0]}", json={"tag": "sleeper"})

    assert response.status_code == 422
    assert "target" in response.json()["detail"] and "fade" in response.json()["detail"]


def test_a_player_we_have_never_heard_of_is_a_404(api, synced):
    response = api.put("/master/entries/424242", json={"tag": "target"})

    assert response.status_code == 404
    assert "424242" in response.json()["detail"]


def test_excluding_a_player_sets_him_aside_and_reflows_the_ranks_below_him(api, synced):
    """Acceptance criterion 3's second half."""
    seeded = ids_of(api.get("/master/board").json())
    parked = seeded[1]

    body = api.put(f"/master/entries/{parked}", json={"excluded": True, "tag": "fade"}).json()

    assert ids_of(body) == [pid for pid in seeded if pid != parked]
    assert [row["rank"] for row in body["players"]] == list(range(1, len(seeded)))
    aside = row_for(body, parked, key="set_aside")
    assert aside["rank"] is None and aside["excluded"] is True
    # Parked, not forgotten: the tag he was given on the way out came back with him.
    assert aside["tag"] == "fade"
    assert api.get("/master/board").json()["set_aside"][0]["espn_player_id"] == parked


def test_restoring_him_puts_him_back_at_the_slot_the_consensus_implies(api, synced):
    seeded = ids_of(api.get("/master/board").json())
    parked = seeded[1]
    api.put(f"/master/entries/{parked}", json={"excluded": True, "note": "see how the knee is"})

    body = api.put(f"/master/entries/{parked}", json={"excluded": False}).json()

    row = row_for(body, parked)
    assert row["rank"] == row["consensus_rank"] == 2
    assert row["excluded"] is False
    # Flagged the way an arrival is, because that is what he is to the board he came back to.
    assert row["is_new"] is True
    assert row["note"] == "see how the knee is"
    assert body["set_aside"] == []
    assert ids_of(body) == seeded


def test_a_player_nobody_ranks_can_still_be_put_on_the_board_by_hand(api, synced, unranked):
    """Half of what a personal board is for: the guy the field hasn't noticed yet."""
    seeded = ids_of(api.get("/master/board").json())

    body = api.put(f"/master/entries/{unranked}", json={"note": "deep sleeper"}).json()

    row = row_for(body, unranked)
    # At the bottom: there is no opinion to place him by, and inventing one would be worse.
    assert row["rank"] == len(seeded) + 1
    assert row["consensus_rank"] is None and row["delta"] is None
    assert row["is_stale"] is True


# --- reconcile on read: new players and stale ones ---------------------------------------------


def test_a_newly_ranked_player_is_inserted_at_his_consensus_slot_and_flagged(
    api, db, synced, make_ranking_set, unranked
):
    """Acceptance criterion 3: this year's rookie, arriving on a board that already exists."""
    seeded = api.get("/master/board").json()
    before = ranks_of(seeded)

    # A source that ranks exactly one player: the one nobody had heard of.
    make_ranking_set("Rookie List", TAG_DYNASTY, {unranked: 25})

    body = api.get("/master/board").json()

    row = row_for(body, unranked)
    assert row["is_new"] is True
    assert body["added"] == 1 and body["seeded"] is False
    # He lands where the field says he belongs...
    assert row["rank"] == row["consensus_rank"]
    assert 1 < row["rank"] < len(before)
    # ...and everyone else keeps their ORDER, shifted by exactly the one player above them.
    after = ranks_of(body)
    assert [pid for pid in ids_of(body) if pid != unranked] == ids_of(seeded)
    assert all(
        after[pid] == before[pid] + (1 if before[pid] >= row["rank"] else 0) for pid in before
    )


def test_a_new_player_is_only_new_once(api, synced, make_ranking_set, unranked):
    """`is_new` is about the response, not the row: by the next GET he is simply on the board."""
    api.get("/master/board")
    make_ranking_set("Rookie List", TAG_DYNASTY, {unranked: 25})
    first = api.get("/master/board").json()

    second = api.get("/master/board").json()

    assert row_for(first, unranked)["is_new"] is True
    assert row_for(second, unranked)["is_new"] is False
    assert second["added"] == 0
    assert ranks_of(second) == ranks_of(first)


def test_a_player_who_drops_out_of_the_pool_keeps_his_rank_and_is_flagged_stale(
    api, db, synced, make_ranking_set, unranked
):
    """His rank was a decision. The sources going quiet is not a reason to throw it away."""
    ranking_set = make_ranking_set("Rookie List", TAG_DYNASTY, {unranked: 25})
    seeded = api.get("/master/board").json()
    before = ranks_of(seeded)

    db.delete(ranking_set)
    db.commit()

    body = api.get("/master/board").json()

    row = row_for(body, unranked)
    assert row["is_stale"] is True
    assert row["consensus_rank"] is None and row["delta"] is None
    assert body["stale"] == 1
    assert ranks_of(body) == before


# --- the reference column ----------------------------------------------------------------------


def test_the_reference_is_the_consensus_board_read_off_the_same_call(api, synced):
    """Not a second consensus: the same one, as an order."""
    consensus = {
        row["espn_player_id"]: row["rank"]
        for row in api.get("/board/consensus?horizon=dynasty&limit=1000").json()["players"]
    }
    seeded = ids_of(api.get("/master/board").json())
    api.put("/master/order", json={"ordered_player_ids": list(reversed(seeded))})

    body = api.get("/master/board?horizon=dynasty").json()

    for row in body["players"]:
        assert row["consensus_rank"] == consensus[row["espn_player_id"]]
        assert row["delta"] == row["rank"] - row["consensus_rank"]


def test_the_horizon_changes_the_reference_and_nothing_else(api, aged):
    """One board, two lenses. The ranks are the same rows; the field's opinion isn't."""
    dynasty = api.get("/master/board?horizon=dynasty").json()
    win_now = api.get("/master/board?horizon=current_year").json()

    assert ids_of(win_now) == ids_of(dynasty)
    assert ranks_of(win_now) == ranks_of(dynasty)
    assert win_now["seed_horizon"] == dynasty["seed_horizon"] == "dynasty"
    assert win_now["horizon"] == "current_year" and win_now["ranking_horizon"] == "redraft"

    win_now_consensus = {
        row["espn_player_id"]: row["rank"]
        for row in api.get("/board/consensus?horizon=current_year&limit=1000").json()["players"]
    }
    for row in win_now["players"]:
        assert row["consensus_rank"] == win_now_consensus[row["espn_player_id"]]
    # The age curve prices the two horizons differently, so the two references disagree.
    assert [row["consensus_rank"] for row in win_now["players"]] != [
        row["consensus_rank"] for row in dynasty["players"]
    ]


def test_an_unknown_horizon_is_a_400_that_names_the_real_ones(api, synced):
    response = api.get("/master/board?horizon=redraft")

    assert response.status_code == 400
    assert "current_year" in response.json()["detail"]


def test_reading_through_the_win_now_lens_cannot_change_who_is_on_the_board(
    api, synced, make_ranking_set, unranked
):
    """Membership comes from the seed horizon, so a redraft-only name is not admitted by a view."""
    api.get("/master/board")
    make_ranking_set("Rest of Season", "redraft", {unranked: 1})

    body = api.get("/master/board?horizon=current_year").json()

    assert unranked not in ids_of(body)
    assert body["added"] == 0


# --- POST /master/seed ---------------------------------------------------------------------------


def test_seeding_a_board_that_has_anything_on_it_needs_reset(api, synced):
    api.get("/master/board")

    response = api.post("/master/seed")

    assert response.status_code == 409
    assert "reset=true" in response.json()["detail"]


def test_a_reset_throws_the_board_away_and_rebuilds_it_from_the_consensus(api, synced):
    seeded = ids_of(api.get("/master/board").json())
    api.put("/master/order", json={"ordered_player_ids": list(reversed(seeded))})
    api.put(f"/master/entries/{seeded[0]}", json={"tag": "target", "excluded": True})

    body = api.post("/master/seed?reset=true").json()

    assert ids_of(body) == seeded
    assert body["set_aside"] == []
    assert row_for(body, seeded[0])["tag"] is None


def test_seeding_an_empty_board_needs_no_reset(api, synced):
    body = api.post("/master/seed")

    assert body.status_code == 200
    assert body.json()["seeded"] is True


# --- the boards that were already there ----------------------------------------------------------


def test_the_existing_boards_are_byte_identical_before_and_after_all_of_this(
    api, synced, make_ranking_set
):
    """Acceptance criterion 4. This task ADDS a table and a router; it changes no board."""
    make_ranking_set("Dynasty Top 3", TAG_DYNASTY, {JOKIC: 1, WEMBY: 2})
    value_board = api.get("/players/board?horizon=dynasty").text
    consensus = api.get("/board/consensus?horizon=dynasty&limit=1000").text
    sources = api.get("/sources?horizon=dynasty").text

    seeded = ids_of(api.get("/master/board").json())
    api.put("/master/order", json={"ordered_player_ids": list(reversed(seeded))})
    api.put(f"/master/entries/{JOKIC}", json={"tag": "fade", "note": "no", "excluded": True})

    assert api.get("/players/board?horizon=dynasty").text == value_board
    assert api.get("/board/consensus?horizon=dynasty&limit=1000").text == consensus
    assert api.get("/sources?horizon=dynasty").text == sources


# --- draft mode: the board, read while a draft is happening ---------------------------------------


def start_draft(api, my_slot: int = 2) -> dict:
    response = api.post("/draft", json={"my_slot": my_slot})
    assert response.status_code == 201, response.text
    return response.json()


def draft_pick(api, player_id: int) -> dict:
    response = api.post("/draft/picks", json={"player_id": player_id})
    assert response.status_code == 200, response.text
    return response.json()


def test_draft_mode_flags_every_row_with_who_took_him(api, synced):
    board = ids_of(api.get("/master/board").json())
    start_draft(api)
    # Pick 1 is team 1's and pick 2 is mine, so this is one of each.
    draft_pick(api, board[0])
    draft_pick(api, board[1])

    body = api.get("/master/board?draft_mode=true").json()

    theirs = row_for(body, board[0])
    mine = row_for(body, board[1])
    assert (theirs["drafted"], theirs["drafted_by_slot"], theirs["drafted_by_me"]) == (
        True,
        1,
        False,
    )
    assert (mine["drafted"], mine["drafted_by_slot"], mine["drafted_by_me"]) == (True, 2, True)
    # Nothing is hidden: a drafted player is still a row, in his place, with his rank.
    assert ids_of(body) == board
    assert theirs["rank"] == 1 and mine["rank"] == 2
    # And everyone still on the board is unmarked.
    assert all(not row["drafted"] for row in body["players"][2:])


def test_draft_mode_reads_my_seat_off_the_draft_and_not_off_the_setting(api, synced):
    """A draft started at another seat has to label its own picks, not DRAFT_MY_SLOT's."""
    board = ids_of(api.get("/master/board").json())
    start_draft(api, my_slot=1)
    draft_pick(api, board[0])

    row = row_for(api.get("/master/board?draft_mode=true").json(), board[0])

    assert row["drafted_by_slot"] == 1 and row["drafted_by_me"] is True


def test_hide_drafted_leaves_them_out_without_renumbering_anybody(api, synced):
    board = ids_of(api.get("/master/board").json())
    start_draft(api)
    draft_pick(api, board[0])
    draft_pick(api, board[1])

    body = api.get("/master/board?hide_drafted=true").json()

    assert ids_of(body) == board[2:]
    assert body["total_ranked"] == len(board) - 2
    # The ranks are ours and they are decisions: the gap at 1 and 2 IS the information.
    assert ranks_of(body)[board[2]] == 3
    # Hiding implies annotating, so the rows that remain are still draft-aware.
    assert all(row["drafted"] is False for row in body["players"])


def test_hide_drafted_narrows_the_set_aside_tray_the_same_way(api, synced):
    board = ids_of(api.get("/master/board").json())
    api.put(f"/master/entries/{board[3]}", json={"excluded": True})
    start_draft(api)
    draft_pick(api, board[0])
    # He is set aside AND the room took him — a player parked and then gone.
    api.post("/draft/picks", json={"player_id": board[3]})

    annotated = api.get("/master/board?draft_mode=true").json()
    hidden = api.get("/master/board?hide_drafted=true").json()

    assert row_for(annotated, board[3], "set_aside")["drafted"] is True
    assert board[3] not in ids_of_key(hidden, "set_aside")


def ids_of_key(body, key: str) -> list[int]:
    return [row["espn_player_id"] for row in body[key]]


def test_draft_mode_narrows_with_position_rather_than_fighting_it(api, synced):
    board = api.get("/master/board?position=C").json()
    centres = ids_of(board)
    start_draft(api)
    draft_pick(api, centres[0])

    annotated = api.get("/master/board?position=C&draft_mode=true").json()
    hidden = api.get("/master/board?position=C&hide_drafted=true").json()

    assert row_for(annotated, centres[0])["drafted"] is True
    assert ids_of(hidden) == centres[1:]


def test_the_flags_are_a_no_op_when_no_draft_has_been_started(api, synced):
    api.get("/master/board")  # the seed-on-read, so `seeded` is false on all three below
    plain = api.get("/master/board").text

    assert api.get("/master/board?draft_mode=true").text == plain
    assert api.get("/master/board?hide_drafted=true").text == plain


def test_the_board_with_the_draft_params_off_is_exactly_what_it_was(api, synced):
    """Acceptance criterion 4: the frozen endpoint, guarded against the draft it now knows about.

    The new fields are optional and defaulted, so a response taken while a draft is halfway
    through has to be byte-identical to the one taken before the draft existed.
    """
    board = ids_of(api.get("/master/board").json())  # seeds it; `before` is a warm read
    before = api.get("/master/board").text
    start_draft(api)
    draft_pick(api, board[0])
    api.post("/draft/simulate", json={"seed": 4})

    assert api.get("/master/board").text == before
    assert '"drafted":false' in before.replace(" ", "")
    assert '"drafted_by_slot":null' in before.replace(" ", "")
