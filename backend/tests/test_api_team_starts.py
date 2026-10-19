"""The starts tracker's API (`GET /team/starts`) and the "Wk G" games-left numbers on the day
view and the waiver list.

Auto-fill's squad (`tests.test_api_team_autofill`): made-up pro teams, so the only games in
Matchup 1 (days 1-6) are the ones seeded there.

    day 2: 9 play for 7 slots (Oscar is OUT, Ivy is on IR); Paul is seeded in PG
    day 3: only Pete, Sam and Cole play

"Today" is pinned pre-season (period 1) unless a test moves it.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import event

from app.api import team as team_route
from tests.test_api_team_autofill import (  # noqa: F401  (fixtures)
    COLE,
    DREW,
    FINN,
    NAMES,
    PAUL,
    PETE,
    SAM,
    SFRED,
    ZED,
    _auto,
    squad,
)
from tests.test_api_team_plan import _create
from tests.test_api_team_views import _game, api, client_stub, league  # noqa: F401

DAY2_OPTIMAL = 50 + 45 + 40 + 35 + 32 + 30 + 20  # Pete Sam Fred Paul Carl Cole Gabe
DAY3_OPTIMAL = 50 + 45 + 30  # Pete Sam Cole


def _starts(client, expected=200, **params):
    response = client.get("/team/starts", params=params)
    assert response.status_code == expected, response.text
    return response.json()


def _numbers(body: dict) -> dict[int, tuple[int, int, int, int]]:
    return {
        d["sp"]: (d["filled"], d["possible"], d["empty"], d["left_on_bench"]) for d in body["days"]
    }


@pytest.fixture
def client(squad):  # noqa: F811  (the imported fixture)
    """Auto-fill's squad, synced and seeded: the API client."""
    return squad


@pytest.fixture
def zed_day3(client, db):
    """Zed's team plays day 3 too, so a pickup landing on day 3 has a game to start in."""
    _game(db, 9201, 3, 80, 83, datetime(2026, 10, 23, 2, 0, tzinfo=UTC))
    db.commit()
    return client


def test_the_default_is_the_current_matchup_in_the_actual_view(client):
    body = _starts(client)
    assert body["matchup"] == {
        "id": 1,
        "label": "Matchup 1",
        "start": "2026-10-20",
        "end": "2026-10-25",
    }
    assert body["view"] == "actual"
    assert body["actual_totals"] is None and body["vs_actual"] is None
    assert [(d["sp"], d["date"], d["weekday"]) for d in body["days"]] == [
        (1, "2026-10-20", "Tue"),
        (2, "2026-10-21", "Wed"),
        (3, "2026-10-22", "Thu"),
        (4, "2026-10-23", "Fri"),
        (5, "2026-10-24", "Sat"),
        (6, "2026-10-25", "Sun"),
    ]
    assert {d["slots"] for d in body["days"]} == {7}


def test_actual_numbers_per_day_and_totals(client):
    body = _starts(client, matchup_id=1, view="actual")
    no_games = (0, 0, 7, 0)
    assert _numbers(body) == {
        1: no_games,
        # Paul (seeded in PG) is the only starter as set who plays: six lost to the bench.
        2: (1, 7, 0, 6),
        # Three play, nobody starts as set: four open, three benched.
        3: (0, 3, 4, 3),
        4: no_games,
        5: no_games,
        6: no_games,
    }
    day2, day3 = body["days"][1], body["days"][2]
    assert day2["benched_with_games"] == [
        {"id": FINN, "name": NAMES[FINN]},
        {"id": DREW, "name": NAMES[DREW]},
    ]
    assert day2["projected_points"] == DAY2_OPTIMAL
    assert day2["projected_points_as_set"] == 35.0  # Paul
    assert day3["projected_points"] == DAY3_OPTIMAL
    assert day3["projected_points_as_set"] == 0.0
    assert body["totals"] == {
        "days": 6,
        "max": 42,
        "filled": 1,
        "possible": 10,
        "empty": 4 * 7 + 4,
        "left_on_bench": 9,
        "projected_points": DAY2_OPTIMAL + DAY3_OPTIMAL,
        "projected_points_as_set": 35.0,
    }


def test_auto_fill_turns_left_on_bench_into_filled(client):
    _auto(client, sp=2, through_sp=3, leave_empty=True)
    body = _starts(client)
    assert _numbers(body)[2] == (7, 7, 0, 0)
    assert _numbers(body)[3] == (3, 3, 4, 0)
    assert body["totals"]["projected_points_as_set"] == body["totals"]["projected_points"]


def test_the_lineup_carries_forward_from_an_earlier_stored_day(client):
    # A lineup stored on day 2 only governs day 3 too, as the day view resolves it.
    _auto(client, sp=2)
    day3 = client.get("/team/day", params={"sp": 3}).json()
    starters_playing = sum(
        1
        for row in day3["slots"]
        if row["player"] and row["slot_id"] < 12 and row["player"]["id"] in (PETE, SAM, COLE)
    )
    assert _numbers(_starts(client))[3][0] == starters_playing


def test_the_plan_view_counts_a_pickup_from_the_day_he_lands(zed_day3):
    # After tip-off on day 2: Finn's day-2 game counts, Zed joins on day 3 (and not before,
    # though his team plays day 2 as well).
    _create(zed_day3, sp=2, drop_player_id=FINN, candidate_ids=[ZED], timing="after_tipoff")
    actual = _starts(zed_day3)
    plan = _starts(zed_day3, view="plan")

    assert plan["view"] == "plan"
    assert _numbers(plan)[2] == _numbers(actual)[2]
    assert plan["days"][1]["benched_with_games"] == actual["days"][1]["benched_with_games"]
    # Day 3: Zed (SF) fills one of the four open starts. He takes Finn's bench slot as set.
    assert _numbers(actual)[3] == (0, 3, 4, 3)
    assert _numbers(plan)[3] == (0, 4, 3, 4)
    assert plan["days"][2]["projected_points"] == DAY3_OPTIMAL + 100.0

    assert plan["actual_totals"] == actual["totals"]
    assert plan["vs_actual"] == {
        "filled": 0,
        "possible": 1,
        "projected_points": 100.0,
        "projected_points_as_set": 0.0,
    }


def test_a_before_games_pickup_counts_on_its_own_day(client):
    _create(client, sp=2, drop_player_id=FINN, candidate_ids=[ZED], timing="before_games")
    plan = _starts(client, view="plan")
    # Zed (100) is in, Finn is out; Gabe (20) now sits instead.
    day2 = plan["days"][1]
    assert day2["possible"] == 7
    assert [p["id"] for p in day2["benched_with_games"]][-1] == DREW
    assert FINN not in [p["id"] for p in day2["benched_with_games"]]
    assert day2["projected_points"] == DAY2_OPTIMAL - 20 + 100


def test_a_leaving_player_counts_through_his_last_day(client):
    # Pete plays days 2 and 3; dropped after tip-off on day 2, his day-2 start still counts.
    _create(client, sp=2, drop_player_id=PETE, candidate_ids=[ZED], timing="after_tipoff")
    plan = _starts(client, view="plan")
    actual = _starts(client)
    assert plan["days"][1]["projected_points"] == actual["days"][1]["projected_points"]
    assert _numbers(plan)[3] == (0, 2, 5, 2)
    assert plan["vs_actual"]["possible"] == -1
    assert plan["vs_actual"]["projected_points"] == -50.0


def test_past_days_are_flagged(client, monkeypatch):
    # Friday 10/23 (day 4), Pacific: days 1-3 are past, computed the same way.
    monkeypatch.setattr(team_route, "_now", lambda: datetime(2026, 10, 23, 19, 0, tzinfo=UTC))
    body = _starts(client)
    assert [d["past"] for d in body["days"]] == [True, True, True, False, False, False]
    assert _numbers(body)[3] == (0, 3, 4, 3)


def test_preseason_nothing_is_past(client):
    assert not any(d["past"] for d in _starts(client)["days"])


def test_another_matchup_and_a_bad_one(client):
    body = _starts(client, matchup_id=18)  # the All-Star break: two weeks
    assert body["matchup"]["label"] == "Matchup 18"
    assert len(body["days"]) == 14 and body["totals"]["max"] == 98
    _starts(client, expected=422, matchup_id=99)
    _starts(client, expected=422, view="nope")


def _statements(client, db, **params) -> list[str]:
    statements: list[str] = []

    def count(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", count)
    try:
        _starts(client, **params)
    finally:
        event.remove(engine, "before_cursor_execute", count)
    return statements


@pytest.mark.parametrize("view", ["actual", "plan"])
def test_the_query_count_does_not_grow_with_the_days(client, db, view):
    _create(client, sp=2, drop_player_id=FINN, candidate_ids=[ZED], timing="after_tipoff")
    six = _statements(client, db, matchup_id=1, view=view)
    fourteen = _statements(client, db, matchup_id=18, view=view)
    assert len(six) == len(fourteen), (six, fourteen)
    assert len([s for s in six if "FROM nba_game" in s]) == 2  # the plan's, and the matchup's
    # Each lineup table: the nearest earlier day, then the range.
    assert len([s for s in six if "FROM lineup_assignment" in s]) == 2
    expected_plan_rows = 2 if view == "plan" else 0
    assert len([s for s in six if "FROM plan_lineup_assignment" in s]) == expected_plan_rows


# --- Wk G -----------------------------------------------------------------------------------


def _wkg(day: dict) -> dict[int, int]:
    return {
        row["player"]["id"]: row["player"]["matchup_games_left"]
        for row in day["slots"]
        if row["player"]
    }


def test_the_roster_shows_games_left_in_the_matchup_from_the_day(client):
    day2 = _wkg(client.get("/team/day", params={"sp": 2}).json())
    assert (day2[PETE], day2[SAM], day2[COLE], day2[SFRED], day2[PAUL]) == (2, 2, 2, 1, 1)
    day3 = _wkg(client.get("/team/day", params={"sp": 3}).json())
    assert (day3[PETE], day3[SFRED]) == (1, 0)
    # Day 6 is the matchup's last: nothing left for anyone.
    assert set(_wkg(client.get("/team/day", params={"sp": 6}).json()).values()) == {0}


def test_the_plan_views_games_left_follow_the_plan_roster(zed_day3):
    _create(zed_day3, sp=2, drop_player_id=PETE, candidate_ids=[ZED], timing="after_tipoff")
    day2 = _wkg(zed_day3.get("/team/day", params={"sp": 2, "view": "plan"}).json())
    # Pete leaves after day 2: his day-3 game is not on the plan roster's books.
    assert day2[PETE] == 1 and ZED not in day2
    day3 = _wkg(zed_day3.get("/team/day", params={"sp": 3, "view": "plan"}).json())
    assert day3[ZED] == 1 and PETE not in day3
    # The Actual view is unchanged.
    assert _wkg(zed_day3.get("/team/day", params={"sp": 2}).json())[PETE] == 2


def test_the_waiver_list_shows_games_left_from_today(zed_day3, monkeypatch):
    rows = zed_day3.get("/team/free-agents", params={"days": "5"}).json()["players"]
    zed = next(row for row in rows if row["id"] == ZED)
    assert zed["matchup_games_left"] == 2  # days 2 and 3, from today (day 1)
    assert zed["games_in_days"] == 0
    monkeypatch.setattr(team_route, "_now", lambda: datetime(2026, 10, 22, 19, 0, tzinfo=UTC))
    rows = zed_day3.get("/team/free-agents", params={"days": "5"}).json()["players"]
    assert next(row for row in rows if row["id"] == ZED)["matchup_games_left"] == 1
