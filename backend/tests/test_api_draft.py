"""The draft room's endpoints: one live draft, the picks in it, and the plan.

Four sections, and they are four different kinds of claim:

* the LIFECYCLE — there is one draft, it carries its own config, and the destructive verbs are
  guarded;
* the PICKS — the clock is strict, everything the engine refuses comes back as a 422, and undo
  is a real undo (the player is available again);
* the SIM ADVANCE — it commits the room's picks and it STOPS AT MY SEAT. That last one is the
  modelling rule the whole feature rests on, so it is asserted directly rather than inferred;
* the PLAN — two lists per upcoming pick, joined to availability numbers that are in [0, 1],
  non-increasing across my later picks, and reproducible under a seed;
* the SEATS AND THE WHOLE-BOARD AVAILABILITY — what a seat is called (cosmetic, editable at
  any point), which seat is mine (frozen the moment a pick is made), and the one number the
  plan answers for a shortlist answered for everybody still on the board.

Offline throughout: the field is the consensus of the recorded ESPN fixtures, the room is the
seeded autopick, and every availability is a fixed-seed Monte Carlo over a hundred iterations.
`test_draft_engine` owns whether the numbers are right; this file owns whether the endpoints
persist, refuse and serialize them correctly.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import get_settings
from app.db.models import Player
from app.db.models.draft import Draft, DraftPick
from app.db.session import get_db
from app.draft import field_ranks
from app.main import app
from app.valuation import HORIZON_DYNASTY

# Enough iterations for a percentage to be a percentage, few enough to run in a blink. The plan
# tests assert shape, order and determinism — never a specific number, which is the engine's job.
ITERATIONS = 100


@pytest.fixture
def api(db):
    app.dependency_overrides[get_db] = lambda: db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def field(db) -> list[int]:
    """The field's board, best first — the only players a draft can take."""
    ranks = field_ranks(db, HORIZON_DYNASTY)
    return sorted(ranks, key=lambda player_id: ranks[player_id])


@pytest.fixture
def small(monkeypatch):
    """Our shape, shrunk to a draft that can actually be COMPLETED: 3 teams, 2 rounds, 6 picks.

    `team_count` and `rounds` come from `DRAFT_*` rather than from the request (a mock of a
    different-sized league is a setting), so this is how a test reaches a finished draft without
    entering two hundred picks.
    """
    settings = get_settings()
    monkeypatch.setattr(settings, "draft_team_count", 3)
    monkeypatch.setattr(settings, "draft_rounds", 2)
    monkeypatch.setattr(settings, "draft_my_slot", 2)
    return settings


def create(api, **body) -> dict:
    response = api.post("/draft", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def pick(api, player_id: int, **body) -> dict:
    response = api.post("/draft/picks", json={"player_id": player_id, **body})
    assert response.status_code == 200, response.text
    return response.json()


def team(state: dict, slot: int) -> dict:
    return next(row for row in state["teams"] if row["team_slot"] == slot)


def ids_of(rows) -> list[int]:
    return [row["espn_player_id"] for row in rows]


# --- the lifecycle ------------------------------------------------------------------------------


def test_a_draft_is_created_from_the_settings_and_the_consensus(api, synced, field):
    body = create(api)

    assert (body["team_count"], body["rounds"], body["my_slot"]) == (10, 20, 2)
    assert body["mode"] == "simulation"
    assert body["field_horizon"] == HORIZON_DYNASTY
    # Null, not [], because "every source the horizon offers" is a different instruction from
    # "no sources at all".
    assert body["field_source_ids"] is None
    assert body["roster_slots"]["PG"] == 1 and body["roster_slots"]["UT"] == 2
    # The universe is the field's board, which is what makes availability a number about a
    # board the simulation actually draws from.
    assert body["universe_size"] == len(field)
    assert body["total_picks"] == 200
    assert body["picks_made"] == 0
    assert body["on_the_clock"] == 1 and body["next_pick_number"] == 1
    assert body["current_round"] == 1
    assert body["is_my_pick"] is False and body["is_complete"] is False
    # The snake, which is the whole reason availability is worth computing: a 17-pick wait, then
    # a 3-pick wait, forever alternating.
    assert body["my_pick_numbers"][:5] == [2, 19, 22, 39, 42]
    assert body["my_remaining_pick_numbers"] == body["my_pick_numbers"]
    assert body["log"] == []
    assert [row["team_slot"] for row in body["teams"]] == list(range(1, 11))
    assert team(body, 2)["is_me"] is True and team(body, 1)["is_me"] is False
    # Nobody has drafted anyone, so every seat still starts nobody anywhere.
    assert team(body, 5)["open_needs"] == ["PG", "SG", "SF", "PF", "C"]


def test_the_body_overrides_the_seat_the_mode_the_field_and_the_roster(api, synced, db):
    body = create(
        api,
        my_slot=7,
        mode="manual",
        field_source_ids=["adp:espn"],
        roster_slots={"PG": 2, "C": 1, "BE": 3},
    )

    assert body["my_slot"] == 7
    assert body["mode"] == "manual"
    assert body["field_source_ids"] == ["adp:espn"]
    assert body["roster_slots"] == {"PG": 2, "C": 1, "BE": 3}
    # Two point guard slots is two point guards of need, and the positions with no slot are
    # simply not needs at all.
    assert team(body, 7)["open_needs"] == ["PG", "C"]
    # A narrower field is a smaller universe: this is the ADP board, not every source's.
    assert body["universe_size"] == len(field_ranks_for(db, ["adp:espn"]))
    assert body["my_pick_numbers"][:3] == [7, 14, 27]


def field_ranks_for(db, source_ids: list[str] | None = None) -> dict[int, int]:
    """The field the draft is modelled against — the same adapter the endpoints use."""
    return field_ranks(db, HORIZON_DYNASTY, source_ids)


def test_an_empty_request_is_a_complete_one(api, synced):
    """No body at all: the shape comes from `DRAFT_*` and the field from the consensus."""
    created = api.post("/draft")
    advanced = api.post("/draft/simulate")

    assert created.status_code == 201
    assert created.json()["my_slot"] == 2
    assert advanced.status_code == 200
    # A seed was drawn for it, and the room moved to my seat.
    assert isinstance(advanced.json()["seed"], int)
    assert advanced.json()["state"]["on_the_clock"] == 2


def test_a_second_draft_is_a_409_until_it_is_told_to_replace_the_first(api, synced, field):
    create(api)
    pick(api, field[0])

    refused = api.post("/draft", json={})

    assert refused.status_code == 409
    assert "reset=true" in refused.json()["detail"]
    # Nothing happened to the draft that was already there.
    assert api.get("/draft").json()["picks_made"] == 1


def test_replacing_the_draft_reconfigures_it_and_drops_its_picks(api, synced, field, db):
    create(api)
    pick(api, field[0])

    body = api.post("/draft?reset=true", json={"my_slot": 5, "mode": "manual"})

    assert body.status_code == 201
    assert body.json()["my_slot"] == 5 and body.json()["mode"] == "manual"
    assert body.json()["picks_made"] == 0
    assert body.json()["log"] == []
    # One draft, not two: the replaced row is gone rather than parked, and its picks went with
    # it (the ORM cascade, since SQLite has foreign keys off).
    assert len(db.scalars(select(Draft)).all()) == 1
    assert db.scalars(select(DraftPick)).all() == []


def test_reset_starts_the_same_draft_over_and_keeps_its_config(api, synced, field):
    create(api, my_slot=4, mode="manual", roster_slots={"PG": 1, "C": 1, "BE": 5})
    pick(api, field[0])
    pick(api, field[1])

    body = api.post("/draft/reset")

    assert body.status_code == 200
    state = body.json()
    assert state["picks_made"] == 0 and state["next_pick_number"] == 1
    # The config survived: this is the same draft, from pick 1.
    assert (state["my_slot"], state["mode"]) == (4, "manual")
    assert state["roster_slots"] == {"PG": 1, "C": 1, "BE": 5}
    assert state["updated_at"] >= state["created_at"]


def test_every_endpoint_is_a_404_with_a_way_forward_before_a_draft_exists(api, synced, field):
    for method, path, body in (
        ("get", "/draft", None),
        ("get", "/draft/plan", None),
        ("get", "/draft/availability", None),
        ("put", "/draft/config", {"my_slot": 3}),
        ("post", "/draft/reset", None),
        ("post", "/draft/undo", None),
        ("post", "/draft/picks", {"player_id": field[0]}),
        ("put", "/draft/picks/1", {"player_id": field[0]}),
        ("post", "/draft/simulate", {}),
    ):
        response = getattr(api, method)(path, **({"json": body} if body is not None else {}))
        assert response.status_code == 404, (path, response.text)
        assert "POST /draft" in response.json()["detail"]


def test_a_source_nobody_has_heard_of_is_a_400_and_writes_nothing(api, synced):
    response = api.post("/draft", json={"field_source_ids": ["ranking:9999"]})

    assert response.status_code == 400
    assert "ranking:9999" in response.json()["detail"]
    # The 400 came before the row: there is no half-created draft to trip over.
    assert api.get("/draft").status_code == 404


def test_an_unknown_horizon_is_a_400_too(api, synced):
    response = api.post("/draft", json={"field_horizon": "next_tuesday"})

    assert response.status_code == 400
    assert api.get("/draft").status_code == 404


def test_a_mode_we_do_not_recognise_is_a_422_naming_the_ones_we_do(api, synced):
    response = api.post("/draft", json={"mode": "autodraft"})

    assert response.status_code == 422
    assert "'simulation'" in response.json()["detail"]


def test_a_seat_that_does_not_exist_in_this_league_is_a_422(api, synced):
    response = api.post("/draft", json={"my_slot": 14})

    assert response.status_code == 422
    assert "1..10" in response.json()["detail"]


def test_a_roster_with_no_slots_in_it_is_a_422(api, synced):
    response = api.post("/draft", json={"roster_slots": {}})

    assert response.status_code == 422
    assert "at least one slot" in response.json()["detail"]


# --- entering picks -----------------------------------------------------------------------------


def test_a_pick_advances_the_clock_and_lands_on_the_right_roster(api, synced, field):
    create(api)

    state = pick(api, field[0])

    assert state["picks_made"] == 1
    assert state["next_pick_number"] == 2 and state["on_the_clock"] == 2
    # Pick 2 is mine, so this is the moment the page starts asking "who do I take".
    assert state["is_my_pick"] is True
    assert team(state, 1)["player_ids"] == [field[0]]
    assert state["log"] == [
        {
            "pick_number": 1,
            "round": 1,
            "team_slot": 1,
            "is_mine": False,
            "espn_player_id": field[0],
            "name": state["log"][0]["name"],
            "positions": state["log"][0]["positions"],
            "is_auto": False,
        }
    ]
    assert state["my_remaining_pick_numbers"][0] == 2


def test_a_pick_fills_the_drafting_teams_need_and_nobody_elses(api, synced, field):
    create(api)
    before = team(api.get("/draft").json(), 1)["open_needs"]

    state = pick(api, field[0])

    taken = next(row for row in state["log"] if row["pick_number"] == 1)
    assert taken["positions"]  # the fixture pool has positions for its top players
    assert set(team(state, 1)["open_needs"]) < set(before)
    assert team(state, 3)["open_needs"] == ["PG", "SG", "SF", "PF", "C"]


def test_the_same_player_cannot_be_drafted_twice(api, synced, field):
    create(api)
    pick(api, field[0])

    response = api.post("/draft/picks", json={"player_id": field[0]})

    assert response.status_code == 422
    assert "already been drafted" in response.json()["detail"]


def test_a_player_the_field_does_not_rank_cannot_be_drafted(api, synced, db):
    """The universe rule, surfaced: availability over a board the sim never draws from is noise."""
    db.add(Player(espn_player_id=999999, full_name="Nobody At All", positions=["SF"]))
    db.commit()
    create(api)

    response = api.post("/draft/picks", json={"player_id": 999999})

    assert response.status_code == 422
    assert "universe" in response.json()["detail"]


def test_a_pick_entered_under_the_wrong_seat_is_refused_rather_than_recorded(api, synced, field):
    """ "Team 7 took Jokic" has to fail loudly when the room thinks team 1 is up."""
    create(api)

    response = api.post("/draft/picks", json={"player_id": field[0], "team_slot": 7})

    assert response.status_code == 422
    assert "belongs to team slot 1" in response.json()["detail"]
    assert api.get("/draft").json()["picks_made"] == 0


def test_the_seat_on_the_clock_is_accepted_as_the_assertion_it_is(api, synced, field):
    create(api)

    state = pick(api, field[0], team_slot=1)

    assert team(state, 1)["player_ids"] == [field[0]]


def test_a_pick_after_the_last_one_is_a_422(api, synced, small, field):
    create(api)
    for player_id in field[:6]:
        pick(api, player_id)
    complete = api.get("/draft").json()
    assert complete["is_complete"] is True
    assert complete["on_the_clock"] is None and complete["next_pick_number"] is None
    assert complete["my_remaining_pick_numbers"] == []

    response = api.post("/draft/picks", json={"player_id": field[6]})

    assert response.status_code == 422
    assert "complete" in response.json()["detail"]


def test_undo_takes_the_last_pick_back_and_puts_him_on_the_board_again(api, synced, field):
    create(api)
    pick(api, field[0])
    pick(api, field[1])

    state = api.post("/draft/undo").json()

    assert state["picks_made"] == 1
    assert state["next_pick_number"] == 2 and state["on_the_clock"] == 2
    assert team(state, 2)["player_ids"] == []
    # Really undone: the player he was is draftable again, by anyone.
    assert pick(api, field[1])["picks_made"] == 2


def test_undo_is_callable_until_the_draft_is_empty_and_then_is_a_409(api, synced, field):
    create(api)
    pick(api, field[0])
    pick(api, field[1])

    assert api.post("/draft/undo").json()["picks_made"] == 1
    assert api.post("/draft/undo").json()["picks_made"] == 0

    refused = api.post("/draft/undo")
    assert refused.status_code == 409
    assert "nothing to undo" in refused.json()["detail"]


def test_editing_a_pick_swaps_the_player_and_frees_the_one_he_replaced(api, synced, field):
    """The mis-entry noticed at pick 30, fixed without throwing pick 8 through 29 away."""
    create(api)
    pick(api, field[0])
    pick(api, field[1])
    pick(api, field[2])

    state = api.put("/draft/picks/2", json={"player_id": field[5]}).json()

    assert state["picks_made"] == 3
    entry = next(row for row in state["log"] if row["pick_number"] == 2)
    assert entry["espn_player_id"] == field[5]
    # The snake owns the seat: an edit changes who was taken, never whose turn it was.
    assert entry["team_slot"] == 2 and entry["is_mine"] is True
    assert team(state, 2)["player_ids"] == [field[5]]
    # The player he replaced is back on the board, for anyone.
    assert pick(api, field[1])["picks_made"] == 4


def test_an_edited_pick_is_a_manual_decision_however_it_was_made(api, synced, field):
    create(api)
    advance = api.post("/draft/simulate", json={"seed": 7}).json()
    assert advance["picks"] and all(row["is_auto"] for row in advance["picks"])
    edited = advance["picks"][0]["pick_number"]

    state = api.put(f"/draft/picks/{edited}", json={"player_id": field[-1]}).json()

    row = next(entry for entry in state["log"] if entry["pick_number"] == edited)
    assert row["espn_player_id"] == field[-1] and row["is_auto"] is False
    # Only the edited one: the rest of the advance is still the room's.
    assert all(entry["is_auto"] for entry in state["log"] if entry["pick_number"] != edited)


def test_editing_a_pick_to_the_player_he_already_is_changes_nothing(api, synced, field):
    create(api)
    pick(api, field[0])

    response = api.put("/draft/picks/1", json={"player_id": field[0]})

    assert response.status_code == 200
    state = response.json()
    assert state["picks_made"] == 1
    assert state["log"][0]["espn_player_id"] == field[0]


def test_editing_a_pick_to_someone_drafted_elsewhere_is_a_422_naming_the_clash(api, synced, field):
    create(api)
    pick(api, field[0])
    pick(api, field[1])

    response = api.put("/draft/picks/1", json={"player_id": field[1]})

    assert response.status_code == 422
    assert "already drafted at pick 2" in response.json()["detail"]
    # Refused, not half-applied.
    assert ids_of(api.get("/draft").json()["log"]) == [field[0], field[1]]


def test_editing_a_pick_that_has_not_happened_is_a_422(api, synced, field):
    create(api)
    pick(api, field[0])

    for number in (2, 0, 999):
        response = api.put(f"/draft/picks/{number}", json={"player_id": field[3]})
        assert response.status_code == 422, number
        assert "has not been made" in response.json()["detail"]


def test_a_player_the_field_does_not_rank_cannot_be_edited_in_either(api, synced, field, db):
    db.add(Player(espn_player_id=888888, full_name="Nobody At All", positions=["SF"]))
    db.commit()
    create(api)
    pick(api, field[0])

    response = api.put("/draft/picks/1", json={"player_id": 888888})

    assert response.status_code == 422
    assert "universe" in response.json()["detail"]


def test_an_edited_log_still_replays_into_the_same_state(api, synced, field, db):
    """The rule the edit rests on: replay walks by pick number and applies whoever is there."""
    create(api)
    for player_id in field[:4]:
        pick(api, player_id)
    edited = api.put("/draft/picks/3", json={"player_id": field[9]}).json()

    # A second read is a fresh `build_state` off the stored rows — it cannot disagree.
    replayed = api.get("/draft").json()

    assert (
        ids_of(replayed["log"])
        == ids_of(edited["log"])
        == [
            field[0],
            field[1],
            field[9],
            field[3],
        ]
    )
    assert [row["team_slot"] for row in replayed["log"]] == [1, 2, 3, 4]
    stored = db.scalars(select(DraftPick).order_by(DraftPick.pick_number)).all()
    assert [row.player_id for row in stored] == [field[0], field[1], field[9], field[3]]


# --- the sim advance ----------------------------------------------------------------------------


def test_the_advance_drafts_the_room_up_to_my_seat_and_stops_there(api, synced, field):
    """Acceptance criterion 5's first half, and the engine's central rule."""
    create(api)

    body = api.post("/draft/simulate", json={"seed": 11})

    assert body.status_code == 200
    advance = body.json()
    # My seat is pick 2, so exactly one opponent pick happens: team 1's.
    assert [row["pick_number"] for row in advance["picks"]] == [1]
    assert [row["team_slot"] for row in advance["picks"]] == [1]
    assert advance["state"]["on_the_clock"] == 2
    assert advance["state"]["is_my_pick"] is True
    assert advance["seed"] == 11


def test_the_advance_never_creates_a_pick_for_my_seat(api, synced, field, db):
    """Acceptance criterion 5: not one `draft_pick` row belongs to `my_slot`.

    Asserted against the TABLE rather than against the response, because the response is what
    this advance did and the table is everything that has ever been committed.
    """
    create(api)
    api.post("/draft/simulate", json={"seed": 3})
    # I take somebody — the room will not do it for me, at pick 2 or at pick 19 — and then let
    # it run on to my next pick.
    still_there = next(
        player_id
        for player_id in field
        if player_id not in {row["espn_player_id"] for row in api.get("/draft").json()["log"]}
    )
    pick(api, still_there)
    api.post("/draft/simulate", json={"seed": 4})

    auto_slots = set(
        db.scalars(select(DraftPick.team_slot).where(DraftPick.is_auto.is_(True))).all()
    )
    assert 2 not in auto_slots
    assert auto_slots  # it really did draft for somebody
    # The one pick at my seat is the one I entered myself.
    mine = db.scalars(select(DraftPick).where(DraftPick.team_slot == 2)).all()
    assert [row.is_auto for row in mine] == [False]


def test_the_advance_marks_its_picks_as_the_rooms_and_the_log_agrees(api, synced, field, db):
    create(api)

    advance = api.post("/draft/simulate", json={"seed": 5}).json()

    assert all(row["is_auto"] for row in advance["picks"])
    log = advance["state"]["log"]
    assert [row["pick_number"] for row in log] == [1]
    assert log[0]["is_auto"] is True
    assert log[0]["name"]
    stored = db.scalars(select(DraftPick).order_by(DraftPick.pick_number)).all()
    assert [row.is_auto for row in stored] == [True]


def test_the_same_seed_advances_the_same_room_and_a_different_one_does_not(api, synced, field):
    """Determinism is what makes a mock reproducible; the default seed is random on purpose."""
    create(api)
    pick(api, field[0])
    pick(api, field[1])
    seeded = api.post("/draft/simulate", json={"seed": 42}).json()
    taken = ids_of(seeded["picks"])

    api.post("/draft/reset")
    pick(api, field[0])
    pick(api, field[1])
    again = ids_of(api.post("/draft/simulate", json={"seed": 42}).json()["picks"])

    api.post("/draft/reset")
    pick(api, field[0])
    pick(api, field[1])
    other = ids_of(api.post("/draft/simulate", json={"seed": 43}).json()["picks"])

    assert again == taken
    assert len(taken) == 16  # pick 3 through pick 18: my seat at 19 is where it stops
    assert other != taken


def test_an_omitted_seed_is_drawn_and_echoed_so_a_mock_can_be_replayed(api, synced, field):
    create(api)

    first = api.post("/draft/simulate", json={}).json()

    assert isinstance(first["seed"], int)
    replayed = (
        api.post("/draft/reset")
        and api.post("/draft/simulate", json={"seed": first["seed"]}).json()
    )
    assert ids_of(replayed["picks"]) == ids_of(first["picks"])


def test_an_advance_while_i_am_on_the_clock_does_nothing_at_all(api, synced, field):
    create(api)
    pick(api, field[0])
    assert api.get("/draft").json()["is_my_pick"] is True

    advance = api.post("/draft/simulate", json={"seed": 1}).json()

    assert advance["picks"] == []
    assert advance["state"]["picks_made"] == 1
    assert advance["state"]["on_the_clock"] == 2


def test_a_second_advance_carries_the_room_to_my_following_pick(api, synced, field):
    create(api)
    api.post("/draft/simulate", json={"seed": 8})
    # My pick at 2, taken from the bottom of the field so no advance can have taken him.
    pick(api, field[-1])

    advance = api.post("/draft/simulate", json={"seed": 9}).json()

    assert advance["state"]["on_the_clock"] == 2
    assert advance["state"]["next_pick_number"] == 19
    assert [row["pick_number"] for row in advance["picks"]] == list(range(3, 19))
    assert advance["state"]["my_remaining_pick_numbers"][:2] == [19, 22]


def test_undo_re_rolls_one_pick_of_an_advance(api, synced, field):
    create(api)
    advance = api.post("/draft/simulate", json={"seed": 2}).json()
    took = ids_of(advance["picks"])[-1]

    state = api.post("/draft/undo").json()

    assert state["picks_made"] == len(advance["picks"]) - 1
    # He is on the board again, whoever put him on a roster.
    assert pick(api, took)["picks_made"] == len(advance["picks"])


def test_a_count_of_one_makes_exactly_one_opponent_pick(api, synced, field):
    """The step button: the room, one name at a time."""
    create(api)

    advance = api.post("/draft/simulate", json={"seed": 13, "count": 1}).json()

    assert len(advance["picks"]) == 1
    assert advance["picks"][0]["pick_number"] == 1 and advance["picks"][0]["is_auto"] is True
    assert advance["state"]["picks_made"] == 1
    assert advance["state"]["on_the_clock"] == 2 and advance["state"]["is_my_pick"] is True


def test_a_count_is_a_cap_and_my_seat_still_stops_it(api, synced, field):
    """Sixteen picks between my 2 and my 19, so a count of fifty still stops at nineteen."""
    create(api)
    pick(api, field[0])
    # Mine, taken from the bottom of the field so no advance can have wanted him.
    pick(api, field[-1])

    advance = api.post("/draft/simulate", json={"seed": 14, "count": 50}).json()

    assert [row["pick_number"] for row in advance["picks"]] == list(range(3, 19))
    assert advance["state"]["next_pick_number"] == 19
    assert advance["state"]["is_my_pick"] is True


def test_a_counted_advance_stops_at_the_end_of_the_draft_too(api, synced, small, field):
    """3 teams x 2 rounds, my seat at 2: my picks are 2 and 5, and 6 ends it."""
    create(api)
    pick(api, field[0])
    pick(api, field[1])
    # Picks 3 and 4 are the room's; a count of 99 still stops at my 5.
    assert [
        row["pick_number"]
        for row in api.post("/draft/simulate", json={"seed": 15, "count": 99}).json()["picks"]
    ] == [3, 4]
    pick(api, field[-1])

    advance = api.post("/draft/simulate", json={"seed": 15, "count": 99}).json()

    # One pick left in the draft, so the cap is not what stopped it.
    assert [row["pick_number"] for row in advance["picks"]] == [6]
    assert advance["state"]["is_complete"] is True
    assert advance["state"]["next_pick_number"] is None


def test_a_counted_advance_while_i_am_on_the_clock_makes_no_picks(api, synced, field):
    create(api)
    pick(api, field[0])
    assert api.get("/draft").json()["is_my_pick"] is True

    response = api.post("/draft/simulate", json={"seed": 16, "count": 1})

    assert response.status_code == 200
    assert response.json()["picks"] == []
    assert response.json()["state"]["picks_made"] == 1


def test_a_cap_stops_a_mock_early_rather_than_rolling_a_different_one(api, synced, field):
    """`count` is a predicate inside the one seeded roll, so a capped advance is a PREFIX.

    The reason it is implemented that way rather than as a loop of one-pick advances: re-
    rolling would draw from a fresh `Random` each time, and stepping the room three times
    would show a different draft from letting it run.
    """
    create(api)
    pick(api, field[0])
    pick(api, field[-1])
    capped = ids_of(api.post("/draft/simulate", json={"seed": 17, "count": 3}).json()["picks"])
    api.post("/draft/reset")
    pick(api, field[0])
    pick(api, field[-1])

    whole = ids_of(api.post("/draft/simulate", json={"seed": 17}).json()["picks"])

    assert len(capped) == 3 and len(whole) == 16
    assert whole[:3] == capped


def test_an_unseeded_counted_advance_is_still_reproducible(api, synced, field):
    create(api)
    pick(api, field[0])
    pick(api, field[-1])

    first = api.post("/draft/simulate", json={"count": 4}).json()
    api.post("/draft/reset")
    pick(api, field[0])
    pick(api, field[-1])
    again = api.post("/draft/simulate", json={"seed": first["seed"], "count": 4}).json()

    assert len(first["picks"]) == 4
    assert ids_of(again["picks"]) == ids_of(first["picks"])


# --- the plan -----------------------------------------------------------------------------------


@pytest.fixture
def board(api, aged) -> list[int]:
    """Our own board, seeded from the consensus — the order the plan's lists are read off."""
    body = api.get("/master/board").json()
    return ids_of(body["players"])


def tag(api, player_id: int, value: str | None = "target") -> None:
    assert api.put(f"/master/entries/{player_id}", json={"tag": value}).status_code == 200


def plan(api, **params) -> dict:
    query = "&".join(
        f"{key}={value}" for key, value in {"iterations": ITERATIONS, **params}.items()
    )
    response = api.get(f"/draft/plan?{query}")
    assert response.status_code == 200, response.text
    return response.json()


def test_the_plan_is_my_board_at_each_of_my_upcoming_picks(api, aged, board):
    create(api)
    tag(api, board[8])
    tag(api, board[12])

    body = plan(api, picks=3, size=5)

    assert body["iterations"] == ITERATIONS and body["seed"] == 0 and body["size"] == 5
    assert body["field_horizon"] == HORIZON_DYNASTY
    assert body["is_complete"] is False
    assert [row["pick_number"] for row in body["picks"]] == [2, 19, 22]
    assert [row["round"] for row in body["picks"]] == [1, 2, 3]
    assert [row["picks_away"] for row in body["picks"]] == [1, 18, 21]
    for row in body["picks"]:
        # The top of MY board, in MY order — never the consensus.
        assert ids_of(row["best_available"]) == board[:5]
        assert ids_of(row["targets"]) == [board[8], board[12]]
        assert all(entry["tag"] == "target" for entry in row["targets"])
        assert row["open_needs"] == ["PG", "SG", "SF", "PF", "C"]
    # My rank, my tier and the field's rank all ride along, because "I have him 9th and the room
    # has him 14th" is the reason he is on this list at all.
    top = body["picks"][0]["best_available"][0]
    assert top["rank"] == 1 and top["tier"] == 1
    assert top["field_rank"] == 1
    assert body["available_on_board"] >= 5


def test_every_availability_is_a_probability_and_falls_as_the_wait_gets_longer(api, aged, board):
    """Acceptance criterion 5's second half: in [0, 1], and non-increasing across my picks."""
    create(api)
    # Advanced to my seat, so the first planned pick is one I am ON the clock for — which is the
    # one case with an exact answer (nothing intervenes) and therefore worth asserting exactly.
    api.post("/draft/simulate", json={"seed": 5})

    body = plan(api, picks=4, size=8)

    series: dict[int, list[float]] = {}
    for row in body["picks"]:
        for entry in row["best_available"]:
            assert 0.0 <= entry["availability"] <= 1.0
            series.setdefault(entry["espn_player_id"], []).append(entry["availability"])
    assert series
    for values in series.values():
        assert values == sorted(values, reverse=True)
    # Nothing happens between now and a pick I am already on the clock for, so every name on
    # the list is a certainty at it.
    assert body["picks"][0]["picks_away"] == 0
    assert all(entry["availability"] == 1.0 for entry in body["picks"][0]["best_available"])
    # And by my fourth pick from here the room has had dozens at the top of my board.
    assert body["picks"][-1]["best_available"][0]["availability"] < 1.0


def test_the_same_seed_and_iterations_give_the_same_plan_twice(api, aged, board):
    """A percentage someone compares between two refreshes must not move on its own."""
    create(api)

    first = api.get(f"/draft/plan?picks=2&size=6&iterations={ITERATIONS}&seed=7").text
    second = api.get(f"/draft/plan?picks=2&size=6&iterations={ITERATIONS}&seed=7").text
    third = api.get(f"/draft/plan?picks=2&size=6&iterations={ITERATIONS}&seed=8").text

    assert first == second
    assert first != third


def test_a_drafted_player_is_on_neither_list(api, aged, board):
    create(api)
    tag(api, board[0])
    tag(api, board[4])
    assert ids_of(plan(api, picks=1, size=6)["picks"][0]["targets"]) == [board[0], board[4]]

    pick(api, board[0])

    body = plan(api, picks=1, size=6)
    row = body["picks"][0]
    assert board[0] not in ids_of(row["best_available"])
    assert ids_of(row["targets"]) == [board[4]]
    # The list is still `size` long: it fills in from further down my board.
    assert ids_of(row["best_available"]) == board[1:7]


def test_the_targets_are_exactly_the_tagged_and_available_ones(api, aged, board):
    create(api)
    for player_id in (board[1], board[3], board[20]):
        tag(api, player_id)
    tag(api, board[5], "fade")

    api.post("/draft/simulate", json={"seed": 6})
    body = plan(api, picks=1, size=30)

    row = body["picks"][0]
    drafted = {entry["espn_player_id"] for entry in api.get("/draft").json()["log"]}
    expected = [
        player_id for player_id in (board[1], board[3], board[20]) if player_id not in drafted
    ]
    assert ids_of(row["targets"]) == expected
    # A fade is not a target, and being on the board is not being tagged.
    assert board[5] not in ids_of(row["targets"])
    assert all(entry["tag"] == "target" for entry in row["targets"])


def test_a_player_set_aside_is_not_planned_for(api, aged, board):
    create(api)
    assert api.put(f"/master/entries/{board[0]}", json={"excluded": True}).status_code == 200

    body = plan(api, picks=1, size=4)

    assert board[0] not in ids_of(body["picks"][0]["best_available"])
    assert ids_of(body["picks"][0]["best_available"]) == board[1:5]


def test_the_plan_defaults_to_every_pick_i_have_left(api, aged, board, small):
    """`picks` bounds the Monte Carlo; unbounded means all of them, which a small draft can show."""
    create(api)

    body = plan(api, size=3)

    assert [row["pick_number"] for row in body["picks"]] == [2, 5]
    assert body["size"] == 3


def test_a_complete_draft_is_a_clean_empty_plan(api, aged, board, small, field):
    create(api)
    for player_id in field[:6]:
        pick(api, player_id)

    body = plan(api)

    assert body["is_complete"] is True
    assert body["picks"] == []
    assert body["available_on_board"] > 0  # there are players left; there are no picks left


def test_my_open_needs_narrow_as_i_draft(api, aged, board, field):
    create(api)
    pick(api, field[0])
    first = plan(api, picks=1, size=3)["picks"][0]
    assert first["open_needs"] == ["PG", "SG", "SF", "PF", "C"]

    centre = next(
        entry
        for entry in first["best_available"]
        + plan(api, picks=1, size=40)["picks"][0]["best_available"]
        if "C" in entry["positions"]
    )
    pick(api, centre["espn_player_id"])
    api.post("/draft/simulate", json={"seed": 12})

    body = plan(api, picks=1, size=3)
    assert "C" not in body["picks"][0]["open_needs"]
    # And the column that says why a name is on the list agrees with the needs beside it.
    for entry in body["picks"][0]["best_available"]:
        assert entry["fills_need"] == bool(
            set(entry["positions"]) & set(body["picks"][0]["open_needs"])
        )


def test_the_plan_uses_the_drafts_own_field_and_not_a_request_parameter(api, aged, board):
    """Consistency: the room being modelled is the one the draft was created against."""
    create(api, field_source_ids=["adp:espn"])

    body = plan(api, picks=1, size=5)

    assert body["field_source_ids"] == ["adp:espn"]
    # Read against the ADP board, so the field ranks on the rows are ADP's places.
    adp = field_ranks_for(aged, ["adp:espn"])
    for entry in body["picks"][0]["best_available"]:
        assert entry["field_rank"] == adp.get(entry["espn_player_id"])


def test_an_empty_board_is_an_empty_plan_rather_than_an_error(api, synced):
    """A draft can be started before the board has been looked at once."""
    create(api)

    body = plan(api, picks=2)

    assert body["available_on_board"] == 0
    assert body["picks"] == []
    assert body["is_complete"] is False


# --- the seats: what they are called, and which one is mine -------------------------------------


def test_the_seats_are_named_team_n_until_somebody_names_them(api, synced):
    body = create(api)

    assert [row["name"] for row in body["teams"]][:3] == ["Team 1", "Team 2", "Team 3"]
    # Mine is a seat like any other: the name is cosmetic, `is_me` is the fact.
    assert team(body, 2)["name"] == "Team 2" and team(body, 2)["is_me"] is True


def test_team_names_round_trip_through_create_and_default_the_rest(api, synced):
    body = create(api, team_names={"1": "Sam", "4": "The Process"})

    assert team(body, 1)["name"] == "Sam"
    assert team(body, 4)["name"] == "The Process"
    # A seat nobody named is still "Team {slot}" — the default is computed, not stored.
    assert team(body, 2)["name"] == "Team 2"
    # And it survives a re-read, which is the half that says it was persisted.
    assert team(api.get("/draft").json(), 1)["name"] == "Sam"


def test_a_name_for_a_seat_that_does_not_exist_is_a_422(api, synced):
    refused = api.post("/draft", json={"team_names": {"11": "Nobody"}})

    assert refused.status_code == 422
    assert "1..10" in refused.json()["detail"]
    # The 422 came before the row, like every other validation on create.
    assert api.get("/draft").status_code == 404


def test_the_seat_can_be_changed_while_the_draft_is_empty(api, synced):
    create(api)

    body = api.put("/draft/config", json={"my_slot": 7})

    assert body.status_code == 200
    state = body.json()
    assert state["my_slot"] == 7
    assert team(state, 7)["is_me"] is True and team(state, 2)["is_me"] is False
    # The snake re-reads off the new seat, which is the point of changing it.
    assert state["my_pick_numbers"][:3] == [7, 14, 27]
    assert state["picks_made"] == 0


def test_the_seat_is_frozen_once_a_pick_has_been_made(api, synced, field):
    create(api)
    pick(api, field[0])

    refused = api.put("/draft/config", json={"my_slot": 7})

    assert refused.status_code == 422
    assert "reconfigure" in refused.json()["detail"]
    # Nothing moved: the pick that was made is still somebody else's.
    assert api.get("/draft").json()["my_slot"] == 2


def test_the_seat_it_already_is_is_accepted_whatever_the_draft_has_done(api, synced, field):
    """Re-submitting the current seat is a no-op, not a refusal — the form sends both fields."""
    create(api)
    pick(api, field[0])

    body = api.put("/draft/config", json={"my_slot": 2, "team_names": {"3": "Kev"}})

    assert body.status_code == 200
    assert body.json()["my_slot"] == 2
    assert team(body.json(), 3)["name"] == "Kev"


def test_names_merge_at_any_point_in_the_draft_and_clear_with_a_blank(api, synced, field):
    create(api, team_names={"1": "Sam", "3": "Kev"})
    pick(api, field[0])

    merged = api.put("/draft/config", json={"team_names": {"3": "Kevin", "5": "Zo"}}).json()

    # Merged, not replaced: seat 1 was not in the body and kept its name.
    assert team(merged, 1)["name"] == "Sam"
    assert team(merged, 3)["name"] == "Kevin"
    assert team(merged, 5)["name"] == "Zo"

    cleared = api.put("/draft/config", json={"team_names": {"1": ""}}).json()
    assert team(cleared, 1)["name"] == "Team 1"
    assert team(cleared, 3)["name"] == "Kevin"
    # A name is cosmetic all the way down: the pick that was made is untouched by any of it.
    assert cleared["picks_made"] == 1


def test_a_nonsense_name_key_is_a_422_on_the_config_too(api, synced):
    create(api)

    assert api.put("/draft/config", json={"team_names": {"nope": "x"}}).status_code == 422
    assert api.put("/draft/config", json={"team_names": {"0": "x"}}).status_code == 422
    assert api.put("/draft/config", json={"my_slot": 0}).status_code == 422
    assert api.put("/draft/config", json={"my_slot": 11}).status_code == 422
    assert api.get("/draft").json()["my_slot"] == 2


# --- availability over the whole board ----------------------------------------------------------


def availability(api, **params) -> dict:
    query = "&".join(
        f"{key}={value}" for key, value in {"iterations": ITERATIONS, **params}.items()
    )
    response = api.get(f"/draft/availability?{query}")
    assert response.status_code == 200, response.text
    return response.json()


def test_availability_covers_every_available_player_at_my_next_pick(api, synced, field):
    create(api)
    # Two picks, so the clock has moved PAST my seat: I am waiting again, which is the case
    # where the target is simply my next pick.
    pick(api, field[0])
    pick(api, field[1])

    body = availability(api)

    state = api.get("/draft").json()
    assert state["is_my_pick"] is False
    assert body["is_complete"] is False
    assert body["pick_number"] == state["my_remaining_pick_numbers"][0]
    # The whole available field-ranked board, not a shortlist: every player in the universe
    # minus the ones already taken.
    drafted = {row["espn_player_id"] for row in state["log"]}
    assert set(map(int, body["availability"])) == {
        player_id for player_id in field if player_id not in drafted
    }
    assert all(0.0 <= value <= 1.0 for value in body["availability"].values())


def test_the_top_of_the_field_is_less_available_than_a_deep_name(api, synced, field):
    """The one direction the simulation guarantees, over a wait long enough to show it."""
    create(api, my_slot=10)

    body = availability(api)

    numbers = {int(key): value for key, value in body["availability"].items()}
    # Nine opponent picks before my seat comes up: the consensus #1 is very unlikely to last,
    # and the bottom of the field's board is all but certain to.
    assert numbers[field[0]] < numbers[field[-1]]
    assert numbers[field[0]] < 0.5
    assert numbers[field[-1]] > 0.9


def test_availability_reaches_far_past_the_plan_s_size(api, aged, field):
    """What this endpoint is FOR: a number for any name a deep column might draw."""
    create(api)
    settings = get_settings()

    body = availability(api)

    # The plan lists `size` names; this covers the board, which here is the whole fixture
    # field and in the real one is a thousand.
    assert len(body["availability"]) == len(field)
    assert len(body["availability"]) > settings.draft_plan_size
    # Names the plan's default list would never have reached still carry a number.
    for player_id in field[settings.draft_plan_size :]:
        assert str(player_id) in body["availability"]


def test_the_same_seed_gives_the_same_availability_twice(api, synced):
    create(api)

    first = api.get(f"/draft/availability?iterations={ITERATIONS}&seed=7").text
    second = api.get(f"/draft/availability?iterations={ITERATIONS}&seed=7").text
    third = api.get(f"/draft/availability?iterations={ITERATIONS}&seed=8").text

    assert first == second
    assert first != third


def test_a_player_already_taken_is_simply_not_in_the_map(api, synced, field):
    create(api)
    pick(api, field[0])

    body = availability(api)

    assert str(field[0]) not in body["availability"]
    assert str(field[1]) in body["availability"]


def test_on_the_clock_the_target_is_the_pick_after_this_one(api, synced, field):
    """The number that is worth having on the clock is about the NEXT time round.

    At the pick I am making, nothing intervenes and everybody is trivially 1.0 — a column of
    100% answering no question. "Can I wait on him?" is about my following pick, so that is
    the pick the percentages are computed at and `pick_number` says so.
    """
    create(api)
    api.post("/draft/simulate", json={"seed": 4})

    body = availability(api)

    state = api.get("/draft").json()
    assert state["is_my_pick"] is True
    remaining = state["my_remaining_pick_numbers"]
    # Pick 19, not the pick 2 I am on the clock for.
    assert body["pick_number"] == remaining[1]
    assert body["pick_number"] != state["next_pick_number"]
    assert body["is_complete"] is False
    # Seventeen opponent picks of waiting, so nobody near the top is a certainty any more.
    numbers = {int(key): value for key, value in body["availability"].items()}
    best = next(player_id for player_id in field if player_id in numbers)
    assert numbers[best] < 1.0
    assert set(numbers.values()) != {1.0}


def test_on_the_clock_at_my_last_pick_there_is_nothing_to_wait_for(api, synced, small, field):
    """The edge of the rule above: no following pick, so no wait, so nothing to report."""
    create(api)
    # 3 teams x 2 rounds puts my picks at 2 and 5; four picks in leaves me on the clock at 5.
    for player_id in field[:4]:
        pick(api, player_id)
    state = api.get("/draft").json()
    assert (state["is_my_pick"], state["next_pick_number"]) == (True, 5)
    assert state["my_remaining_pick_numbers"] == [5]

    body = availability(api)

    # The same shape a finished draft answers with: no pick to be available for.
    assert body["is_complete"] is True
    assert body["pick_number"] is None
    assert body["availability"] == {}


def test_a_complete_draft_has_nothing_left_to_be_available_for(api, synced, small, field):
    create(api)
    for player_id in field[:6]:
        pick(api, player_id)

    body = availability(api)

    assert body["is_complete"] is True
    assert body["pick_number"] is None
    assert body["availability"] == {}
