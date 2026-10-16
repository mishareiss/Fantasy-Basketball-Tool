"""The waiver plan's API: move CRUD and its shape rules, the Plan view of a day, the plan
lineup, the acquisition override, free-agent `in_plan`, and the Actual view left alone.

The recorded league (team 8 is ours) plus the Team API tests' seeded pool: Alpha (LAL, PG),
Bravo (GSW, SF/PF), Charlie (CLE, C, on WAIVERS until Thu 10/22), Delta (ATL, SG). LAL plays
days 1-3, GSW 1-2, CLE 2-3, ATL 2. "Today" is pinned pre-season, so it is period 1.
"""

import json

import pytest
from sqlalchemy import func, select

from app.db.models import (
    FantasyTeam,
    LineupAssignment,
    PlanCandidate,
    PlanLineupAssignment,
    PlanMove,
    RosterEntry,
)
from tests.conftest import FIXTURE_DIR, MY_TEAM_ID, PRESEASON_NOW, SEASON
from tests.test_api_team_views import (  # noqa: F401  (fixtures)
    ALPHA,
    BE,
    BRAVO,
    CHARLIE,
    DELTA,
    GOBERT,
    HALIBURTON,
    IR,
    LAL,
    PG,
    SG,
    SIMONS,
    _fa,
    _lineup,
    _move,
    _player,
    _slot_of,
    api,
    client_stub,
    league,
    pool,
)

UT = 11


def _create(api, expected=201, **body):
    response = api.post("/team/plan/moves", json=body)
    assert response.status_code == expected, response.text
    return response.json()


def _plan(api) -> dict:
    response = api.get("/team/plan")
    assert response.status_code == 200, response.text
    return response.json()


def _by_id(plan: dict, move_id: int) -> dict:
    return next(m for m in plan["moves"] if m["id"] == move_id)


def _plan_day(api, sp: int) -> dict:
    response = api.get("/team/day", params={"sp": sp, "view": "plan"})
    assert response.status_code == 200, response.text
    return response.json()


def _player_row(day: dict, pid: int) -> dict | None:
    return next(
        (r["player"] for r in day["slots"] if r["player"] and r["player"]["id"] == pid), None
    )


@pytest.fixture
def chain(pool):
    """Move 1 (sp 2): drop Simons (PG), pick up Alpha, backup Bravo. Move 2 (sp 4): drop
    "move 1's pickup", pick up Delta."""
    first = _create(pool, sp=2, drop_player_id=SIMONS, candidate_ids=[ALPHA, BRAVO])["move_id"]
    second = _create(pool, sp=4, drop_from_move_id=first, candidate_ids=[DELTA])["move_id"]
    return pool, first, second


# --- CRUD -----------------------------------------------------------------------------------


def test_an_empty_plan(pool):
    body = _plan(pool)
    assert body["moves"] == [] and body["warnings"] == []
    assert body["today_sp"] == 1
    assert body["acquisitions"] == [
        {
            "matchup_id": 1,
            "label": "Matchup 1",
            "used": 0,
            "used_source": "espn",
            "planned": 0,
            "limit": 7,
            "over": False,
        }
    ]


def test_creating_moves_and_the_chained_drop_reads_a_or_b(chain):
    api, first, second = chain
    body = _plan(api)

    assert [m["id"] for m in body["moves"]] == [first, second]
    move1, move2 = body["moves"]
    assert (move1["sp"], move1["date"], move1["matchup_id"], move1["status"]) == (
        2,
        "2026-10-21",
        1,
        "planned",
    )
    assert [(c["rank"], c["player"]["name"]) for c in move1["candidates"]] == [
        (1, "Alpha Laker"),
        (2, "Bravo Warrior"),
    ]
    assert move1["pickup"]["id"] == ALPHA
    assert [p["id"] for p in move1["alternatives"]] == [BRAVO]
    assert move1["drop"]["display"] == "Anfernee Simons"
    assert move2["drop"] == {
        "player": move2["drop"]["player"],
        "alternatives": [move2["drop"]["alternatives"][0]],
        "from_move_id": first,
        "problem": None,
        "display": "Alpha Laker (or Bravo Warrior)",
    }
    assert move2["drop"]["player"]["id"] == ALPHA
    assert move1["claim_pending"] is False and move1["espn_transaction_id"] is None
    assert move1["sequence"] == 0 and move1["lands_sp"] == 2 and move1["overdue"] is False
    assert body["acquisitions"][0]["planned"] == 2


def test_resolving_move_one_to_the_backup_moves_the_chain(chain):
    api, first, second = chain
    response = api.patch(f"/team/plan/moves/{first}", json={"resolved_player_id": BRAVO})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["move_id"] == first
    assert _by_id(body, first)["pickup"]["id"] == BRAVO
    assert _by_id(body, first)["alternatives"] == []
    assert _by_id(body, second)["drop"]["display"] == "Bravo Warrior"


def test_sequence_defaults_to_last_of_the_day(pool):
    a = _create(pool, sp=3, drop_player_id=SIMONS, candidate_ids=[ALPHA])["move_id"]
    b = _create(pool, sp=3, drop_player_id=GOBERT, candidate_ids=[BRAVO])["move_id"]
    c = _create(pool, sp=3, drop_player_id=HALIBURTON, candidate_ids=[DELTA], sequence=-1)
    body = _plan(pool)
    assert [m["id"] for m in body["moves"]] == [c["move_id"], a, b]
    assert [_by_id(body, i)["sequence"] for i in (a, b)] == [0, 1]
    # Moving a move to another day puts it last there.
    moved = pool.patch(f"/team/plan/moves/{a}", json={"sp": 5}).json()
    assert (_by_id(moved, a)["sp"], _by_id(moved, a)["sequence"]) == (5, 0)


def test_patching_every_field(chain, db):
    api, first, second = chain
    body = api.patch(
        f"/team/plan/moves/{second}",
        json={
            "sp": 6,
            "sequence": 3,
            "drop_player_id": GOBERT,
            "candidate_ids": [CHARLIE, DELTA],
            "note": "if Delta's hurt",
        },
    ).json()
    move = _by_id(body, second)
    assert (move["sp"], move["sequence"], move["note"]) == (6, 3, "if Delta's hurt")
    # A named drop clears the chained one.
    assert (move["drop"]["display"], move["drop"]["from_move_id"]) == ("Rudy Gobert", None)
    assert [c["player"]["id"] for c in move["candidates"]] == [CHARLIE, DELTA]
    assert db.scalar(select(func.count()).select_from(PlanCandidate)) == 4

    # Back to the chain; status changes; skip, then plan again.
    body = api.patch(f"/team/plan/moves/{second}", json={"drop_from_move_id": first}).json()
    assert _by_id(body, second)["drop"]["from_move_id"] == first
    body = api.patch(f"/team/plan/moves/{second}", json={"status": "skipped"}).json()
    assert _by_id(body, second)["status"] == "skipped"
    body = api.patch(f"/team/plan/moves/{second}", json={"status": "done"}).json()
    assert _by_id(body, second)["status"] == "done"
    body = api.patch(f"/team/plan/moves/{second}", json={"status": "planned"}).json()
    assert _by_id(body, second)["status"] == "planned"


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({"sp": 0}, "sp must be between 1 and 160"),
        ({"sp": 161}, "sp must be between 1 and 160"),
        ({"candidate_ids": []}, "at least one player"),
        ({"candidate_ids": [ALPHA, BRAVO, ALPHA]}, f"lists {ALPHA} more than once"),
        ({"drop_from_move_id": "existing"}, "exactly one of drop_player_id and drop_from_move_id"),
        ({"drop_player_id": None}, "exactly one of drop_player_id and drop_from_move_id"),
        ({"drop_player_id": None, "drop_from_move_id": 9999}, "9999 is not a move in my plan"),
        ({"candidate_ids": [123456789]}, "unknown player id(s): 123456789"),
    ],
)
def test_shape_rules_are_422s(pool, db, body, message):
    existing = _create(pool, sp=2, drop_player_id=GOBERT, candidate_ids=[DELTA])["move_id"]
    payload = {"sp": 2, "drop_player_id": SIMONS, "candidate_ids": [ALPHA]} | body
    if payload.get("drop_from_move_id") == "existing":
        payload["drop_from_move_id"] = existing
    response = pool.post("/team/plan/moves", json=payload)
    assert response.status_code == 422, response.text
    assert message in json.dumps(response.json()["detail"])
    assert db.scalar(select(func.count()).select_from(PlanMove)) == 1  # nothing written


def test_a_move_of_another_team_cant_be_chained_or_edited(pool, db):
    theirs = PlanMove(
        season=SEASON,
        team_id=3,
        scoring_period_id=2,
        sequence=0,
        drop_player_id=GOBERT,
        status="planned",
        drop_mismatch=False,
        created_at=PRESEASON_NOW,
        updated_at=PRESEASON_NOW,
    )
    db.add(theirs)
    db.commit()
    response = pool.post(
        "/team/plan/moves",
        json={"sp": 2, "drop_from_move_id": theirs.id, "candidate_ids": [ALPHA]},
    )
    assert response.status_code == 422
    assert pool.patch(f"/team/plan/moves/{theirs.id}", json={"note": "x"}).status_code == 404
    assert pool.delete(f"/team/plan/moves/{theirs.id}").status_code == 404
    assert _plan(pool)["moves"] == []


def test_resolved_must_be_a_candidate(chain):
    api, first, _ = chain
    response = api.patch(f"/team/plan/moves/{first}", json={"resolved_player_id": DELTA})
    assert response.status_code == 422
    assert "not one of the candidates" in json.dumps(response.json()["detail"])
    # Replacing the candidates out from under a resolved player is the same rule.
    api.patch(f"/team/plan/moves/{first}", json={"resolved_player_id": BRAVO})
    response = api.patch(f"/team/plan/moves/{first}", json={"candidate_ids": [ALPHA]})
    assert response.status_code == 422


def test_unknown_moves_are_404s(pool):
    assert pool.patch("/team/plan/moves/999", json={"note": "x"}).status_code == 404
    assert pool.delete("/team/plan/moves/999").status_code == 404


def test_league_rules_never_refuse_a_move(pool):
    """Dropping someone not on my roster and picking up someone already on it are warnings."""
    body = _create(pool, sp=2, drop_player_id=ALPHA, candidate_ids=[HALIBURTON])
    assert {w["code"] for w in body["warnings"] if w["severity"] == "warning"} == {
        "drop_not_rostered",
        "pickup_already_rostered",
    }


# --- delete ---------------------------------------------------------------------------------


def test_deleting_a_move_another_move_chains_to_is_a_409(chain):
    api, first, second = chain
    response = api.delete(f"/team/plan/moves/{first}")
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["move_id"] == first
    assert detail["dependent_move_ids"] == [second]
    assert len(_plan(api)["moves"]) == 2


def test_delete_with_repoint_names_the_drop_then_deletes(chain, db):
    api, first, second = chain
    api.patch(f"/team/plan/moves/{first}", json={"resolved_player_id": BRAVO})
    response = api.delete(f"/team/plan/moves/{first}", params={"repoint": "true"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["deleted_move_id"], body["repointed"]) == (first, [second])
    move2 = _by_id(body, second)
    assert (move2["drop"]["display"], move2["drop"]["from_move_id"]) == ("Bravo Warrior", None)
    assert [m["id"] for m in body["moves"]] == [second]
    # Its candidates went with it.
    assert db.scalar(select(func.count()).select_from(PlanCandidate)) == 1


def test_a_plain_delete(pool):
    move = _create(pool, sp=2, drop_player_id=SIMONS, candidate_ids=[ALPHA])["move_id"]
    body = pool.delete(f"/team/plan/moves/{move}").json()
    assert (body["moves"], body["repointed"]) == ([], [])


# --- the Plan view of a day -----------------------------------------------------------------


def test_the_plan_view_puts_the_pickup_in_the_dropped_players_slot(chain):
    api, first, _ = chain
    actual = api.get("/team/day", params={"sp": 3}).json()
    assert _slot_of(actual, SIMONS) == "PG"

    day = _plan_day(api, 3)
    assert (day["view"], day["lineup_source"], day["carried_from_sp"]) == (
        "plan",
        "from_actual",
        None,
    )
    assert _player_row(day, SIMONS) is None
    alpha = _player_row(day, ALPHA)
    assert _slot_of(day, ALPHA) == "PG"
    assert alpha["plan"]["status"] == "planned"
    assert alpha["plan"]["move_id"] == first
    assert [p["name"] for p in alpha["plan"]["alternatives"]] == ["Bravo Warrior"]
    assert alpha["game"]["display"] == "vs CLE TBD"  # he's a real player with a real game
    assert _player_row(day, HALIBURTON)["plan"] == {
        "status": "actual",
        "move_id": None,
        "alternatives": [],
        "claim_pending": False,
    }
    assert day["planned_drops"] == []
    assert day["acquisitions"]["planned"] == 2
    # The roster size is unchanged by a 1-for-1 move.
    count = lambda d: sum(r["player"] is not None for r in d["slots"])  # noqa: E731
    assert count(day) == count(actual)


def test_the_plan_view_on_move_days_lists_drops_and_warnings(chain):
    api, first, second = chain
    day2 = _plan_day(api, 2)
    assert [(d["player"]["id"], d["move_id"]) for d in day2["planned_drops"]] == [(SIMONS, first)]

    day4 = _plan_day(api, 4)
    (drop,) = day4["planned_drops"]
    assert (drop["player"]["id"], drop["move_id"]) == (ALPHA, second)
    assert [p["id"] for p in drop["alternatives"]] == [BRAVO]
    assert _player_row(day4, ALPHA) is None
    # Delta inherits Simons' PG through Alpha, but isn't PG-eligible, so he sits.
    assert _slot_of(day4, DELTA) == "BE"
    assert next(r for r in day4["slots"] if r["slot"] == "PG")["player"] is None
    # Delta's team (ATL) doesn't play day 4: an info for that day only.
    assert [(w["code"], w["move_id"]) for w in day4["plan_warnings"]] == [("no_game", second)]
    assert _plan_day(api, 3)["plan_warnings"] == []


def test_the_plan_view_before_any_move_is_the_actual_roster(chain):
    api, *_ = chain
    day = _plan_day(api, 1)
    actual = api.get("/team/day", params={"sp": 1}).json()
    strip = lambda d: [(r["slot"], r["player"]["id"] if r["player"] else None) for r in d["slots"]]  # noqa: E731
    assert strip(day) == strip(actual)


def test_a_waiver_pickup_warns_in_the_plan_view(pool):
    move = _create(pool, sp=2, drop_player_id=GOBERT, candidate_ids=[CHARLIE])["move_id"]
    warnings = _plan_day(pool, 2)["plan_warnings"]
    assert [(w["code"], w["move_id"], w["message"]) for w in warnings] == [
        (
            "on_waivers",
            move,
            "Charlie Cav is on waivers until Thu 10/22, but the move is Wed 10/21",
        )
    ]


# --- acquisitions ---------------------------------------------------------------------------


def _eight_pickups(api, db):
    extra = []
    for index in range(4):
        pid = 910001 + index
        _player(db, pid, f"Extra {index}", LAL, ["SG"], [SG, UT, BE, IR], "FREEAGENT")
        extra.append(pid)
    db.commit()
    bench = [
        e.player_id
        for e in db.scalars(
            select(RosterEntry)
            .where(RosterEntry.team_id == MY_TEAM_ID, RosterEntry.lineup_slot_id == BE)
            .order_by(RosterEntry.player_id)
        )
    ]
    for drop, pickup in zip(bench, [ALPHA, BRAVO, CHARLIE, DELTA, *extra], strict=False):
        _create(api, sp=3, drop_player_id=drop, candidate_ids=[pickup])


def test_eight_planned_pickups_in_matchup_one_warn_and_an_override_changes_the_math(pool, db):
    team = db.scalar(select(FantasyTeam).where(FantasyTeam.team_id == MY_TEAM_ID))
    team.matchup_acquisition_totals = {"1": 1}
    db.commit()
    _eight_pickups(pool, db)

    body = _plan(pool)
    (acq,) = [w for w in body["warnings"] if w["code"] == "acq_limit"]
    assert acq["message"] == "Matchup 1: 9 of 7 acquisitions (1 used + 8 planned)"
    assert body["acquisitions"][0] | {} == {
        "matchup_id": 1,
        "label": "Matchup 1",
        "used": 1,
        "used_source": "espn",
        "planned": 8,
        "limit": 7,
        "over": True,
    }

    response = pool.put("/team/plan/acquisition-override", json={"matchup_id": 1, "used": 0})
    assert response.status_code == 200, response.text
    (acq,) = [w for w in response.json()["warnings"] if w["code"] == "acq_limit"]
    assert acq["message"] == "Matchup 1: 8 of 7 acquisitions (0 used + 8 planned)"
    assert acq["detail"]["used_source"] == "override"
    for view in ("actual", "plan"):
        day = pool.get("/team/day", params={"sp": 3, "view": view}).json()
        assert day["acquisitions"] | {"limit": 7} == {
            "used": 0,
            "limit": 7,
            "matchup_id": 1,
            "used_source": "override",
            "planned": 8,
        }

    pool.put("/team/plan/acquisition-override", json={"matchup_id": 1, "used": None})
    assert _plan(pool)["acquisitions"][0]["used_source"] == "espn"


@pytest.mark.parametrize("body", [{"matchup_id": 99, "used": 1}, {"matchup_id": 1, "used": -1}])
def test_a_bad_override_is_a_422(pool, body):
    assert pool.put("/team/plan/acquisition-override", json=body).status_code == 422


# --- the plan lineup ------------------------------------------------------------------------


def test_a_plan_lineup_edit_carries_forward_and_never_touches_the_actual_lineup(chain, db):
    api, *_ = chain
    day3 = _plan_day(api, 3)
    edited = _move(_move(_lineup(day3), ALPHA, BE), HALIBURTON, PG)

    response = api.put("/team/plan/lineup/3", json={"assignments": edited})
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["view"], body["lineup_source"], body["days_written"]) == ("plan", "plan_edit", 1)
    assert _slot_of(body, HALIBURTON) == "PG"

    carried = _plan_day(api, 3 + 1)
    assert carried["lineup_source"] == "plan_carried"
    assert (carried["carried_from_sp"], carried["carried_from_date"]) == (3, "2026-10-22")
    assert _slot_of(carried, HALIBURTON) == "PG"
    # Delta lands on day 4 and takes Alpha's PLANNED slot (the bench), not Simons' PG.
    assert _slot_of(carried, DELTA) == "BE"

    assert db.scalar(select(func.count()).select_from(LineupAssignment)) == 0
    actual = api.get("/team/day", params={"sp": 3}).json()
    assert (_slot_of(actual, SIMONS), actual["lineup_source"]) == ("PG", "roster")


def test_a_plan_lineup_is_checked_against_the_plan_roster(chain):
    api, *_ = chain
    actual_lineup = _lineup(api.get("/team/day", params={"sp": 3}).json())
    response = api.put("/team/plan/lineup/3", json={"assignments": actual_lineup})
    assert response.status_code == 422
    detail = json.dumps(response.json()["detail"])
    assert f"player {SIMONS} is not on the roster" in detail
    assert "Alpha Laker" in detail and "missing" in detail

    # Across a planned move the roster changes, so one lineup can't fit every day.
    day3 = _lineup(_plan_day(api, 3))
    response = api.put("/team/plan/lineup/3", json={"assignments": day3, "through_sp": 4})
    assert response.status_code == 422
    assert "Day 4:" in json.dumps(response.json()["detail"])


def test_actual_edits_and_the_espn_pull_leave_plan_rows_alone(chain, db):
    api, *_ = chain
    plan_lineup = _lineup(_plan_day(api, 3))
    assert api.put("/team/plan/lineup/3", json={"assignments": plan_lineup}).status_code == 200
    planned = db.scalar(select(func.count()).select_from(PlanLineupAssignment))

    actual = api.get("/team/day", params={"sp": 3}).json()
    edited = _move(_move(_lineup(actual), HALIBURTON, PG), SIMONS, BE)
    assert api.put("/team/lineup/3", json={"assignments": edited}).status_code == 200
    assert api.post("/sync/lineup", params={"first_sp": 1, "last_sp": 5}).status_code == 200

    assert db.scalar(select(func.count()).select_from(PlanLineupAssignment)) == planned
    assert _plan_day(api, 3)["lineup_source"] == "plan_edit"


def test_a_deleted_move_reconciles_out_of_a_plan_edit(pool):
    move = _create(pool, sp=2, drop_player_id=SIMONS, candidate_ids=[ALPHA])["move_id"]
    edited = _move(_move(_lineup(_plan_day(pool, 2)), ALPHA, BE), HALIBURTON, PG)
    assert pool.put("/team/plan/lineup/2", json={"assignments": edited}).status_code == 200

    pool.delete(f"/team/plan/moves/{move}")
    day = _plan_day(pool, 3)
    assert day["lineup_source"] == "plan_carried"
    assert _player_row(day, ALPHA) is None
    assert _slot_of(day, SIMONS) == "BE"  # back on the roster, not in the edit: the bench
    assert _slot_of(day, HALIBURTON) == "PG"


# --- free agents ----------------------------------------------------------------------------


def test_free_agents_say_which_moves_they_are_in(chain):
    api, first, second = chain
    rows = {row["id"]: row for row in _fa(api)["players"]}
    assert rows[ALPHA]["in_plan"] == [{"move_id": first, "sp": 2, "rank": 1}]
    assert rows[BRAVO]["in_plan"] == [{"move_id": first, "sp": 2, "rank": 2}]
    assert rows[DELTA]["in_plan"] == [{"move_id": second, "sp": 4, "rank": 1}]
    assert rows[CHARLIE]["in_plan"] == []
    # A skipped move is not "in the plan".
    api.patch(f"/team/plan/moves/{second}", json={"status": "skipped"})
    assert {row["id"]: row for row in _fa(api)["players"]}[DELTA]["in_plan"] == []


# --- the Actual view is unchanged -----------------------------------------------------------

# What task 30 adds to the day view. Everything else must match main byte for byte.
NEW_DAY_KEYS = {"carried_from_sp", "carried_from_date", "view", "planned_drops", "plan_warnings"}
NEW_ACQ_KEYS = {"used_source", "planned"}


def _without_new_keys(day: dict) -> dict:
    out = {k: v for k, v in day.items() if k not in NEW_DAY_KEYS}
    out["acquisitions"] = {k: v for k, v in day["acquisitions"].items() if k not in NEW_ACQ_KEYS}
    out["slots"] = [
        row | {"player": {k: v for k, v in row["player"].items() if k != "plan"}}
        if row["player"]
        else row
        for row in day["slots"]
    ]
    return out


def test_the_actual_view_matches_main(league, db):
    """`tests/fixtures/team_day_actual_snapshot.json` was recorded from main (task 29a) with
    this exact sequence. Every old key is identical; the new ones have their empty values."""
    snapshot = json.loads((FIXTURE_DIR / "team_day_actual_snapshot.json").read_text())
    got = {"sp1": league.get("/team/day", params={"sp": 1}).json()}
    league.post("/sync/lineup", params={"first_sp": 1, "last_sp": 2})
    got["sp2"] = league.get("/team/day", params={"sp": 2}).json()
    got["sp5"] = league.get("/team/day", params={"sp": 5, "view": "actual"}).json()
    edited = _move(_move(_lineup(got["sp5"]), HALIBURTON, PG), SIMONS, BE)
    assert league.put("/team/lineup/5", json={"assignments": edited}).status_code == 200
    got["sp5_manual"] = league.get("/team/day", params={"sp": 5}).json()
    got["sp6"] = league.get("/team/day", params={"sp": 6}).json()
    team = db.scalar(select(FantasyTeam).where(FantasyTeam.team_id == MY_TEAM_ID))
    team.matchup_acquisition_totals = {"1": 2}
    db.commit()
    got["sp3_acq"] = league.get("/team/day", params={"sp": 3, "basis": "season"}).json()

    assert {key: _without_new_keys(day) for key, day in got.items()} == snapshot
    for day in got.values():
        assert day["view"] == "actual"
        assert (day["planned_drops"], day["plan_warnings"]) == ([], [])
        assert all(row["player"]["plan"] is None for row in day["slots"] if row["player"])
        assert day["acquisitions"]["used_source"] == "espn"
    assert (got["sp5"]["carried_from_sp"], got["sp5"]["carried_from_date"]) == (2, "2026-10-21")
    assert (got["sp6"]["carried_from_sp"], got["sp6"]["carried_from_date"]) == (5, "2026-10-24")
    assert got["sp2"]["carried_from_sp"] is None and got["sp1"]["carried_from_sp"] is None


def test_a_plan_never_changes_the_actual_roster(chain):
    api, *_ = chain
    for sp in (1, 3, 5):
        day = api.get("/team/day", params={"sp": sp}).json()
        assert _player_row(day, SIMONS) is not None
        assert _player_row(day, ALPHA) is None
