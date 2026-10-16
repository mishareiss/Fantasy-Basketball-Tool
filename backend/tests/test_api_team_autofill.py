"""Auto-fill's API: `POST /team/lineup/auto` (Actual) and `POST /team/plan/lineup/auto` (Plan).

The recorded league with my roster replaced by a hand-made squad on made-up pro teams, so the
games each day are exactly the ones seeded here:

    day 2: every squad team plays (9 play for 7 slots; Oscar is OUT, Ivy is on IR)
    day 3: only Pete, Sam and Cole's teams play

"Today" is pinned pre-season (period 1) unless a test moves it.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, func, select

from app.api import team as team_route
from app.db.models import LineupAssignment, PlanLineupAssignment, RosterEntry
from app.espn.sync import ACTUAL_CURRENT_KIND
from app.team.autofill_views import player_values
from tests.conftest import MY_TEAM_ID, SEASON
from tests.test_api_team_plan import _create
from tests.test_api_team_views import (  # noqa: F401  (fixtures)
    BE,
    IR,
    PF,
    PG,
    SF,
    SG,
    UT,
    C,
    _game,
    _player,
    _projection,
    api,
    client_stub,
    league,
)

# (id, name, pro team, eligible slots, value, injury, starting slot)
PETE, SAM, SFRED, PAUL, COLE, CARL, GABE, FINN, OSCAR, DREW, IVY, ZED = range(910001, 910013)
SQUAD = [
    (PETE, "Pete Guard", 70, [PG, 5, UT, BE, IR], 50.0, None, BE),
    (SAM, "Sam Shooter", 71, [SG, 5, UT, BE, IR], 45.0, None, BE),
    (SFRED, "Fred Wing", 72, [SF, 6, UT, BE, IR], 40.0, None, BE),
    (PAUL, "Paul Forward", 73, [PF, 6, UT, BE, IR], 35.0, None, PG),
    (COLE, "Cole Center", 74, [C, UT, BE, IR], 30.0, None, BE),
    (CARL, "Carl Big", 75, [PF, C, 6, UT, BE, IR], 32.0, None, BE),
    (GABE, "Gabe Combo", 76, [PG, SG, 5, UT, BE, IR], 20.0, None, BE),
    (FINN, "Finn Flex", 77, [SF, PF, 6, UT, BE, IR], 15.0, None, BE),
    (OSCAR, "Oscar Out", 78, [PG, 5, UT, BE, IR], 60.0, "OUT", BE),
    (DREW, "Drew Doubtful", 79, [SG, 5, UT, BE, IR], 10.0, "DAY_TO_DAY", BE),
    (IVY, "Ivy Injured", 70, [PG, 5, UT, BE, IR], 70.0, "OUT", IR),
]
NAMES = {row[0]: row[1] for row in SQUAD}
DAY2_TIP = datetime(2026, 10, 21, 23, 0, tzinfo=UTC)  # Pete's game, 4pm PT
DAY2_LATE = datetime(2026, 10, 22, 3, 0, tzinfo=UTC)  # everyone else, 8pm PT


@pytest.fixture
def squad(league, db):  # noqa: F811  (the imported fixture)
    db.execute(
        delete(RosterEntry).where(RosterEntry.season == SEASON, RosterEntry.team_id == MY_TEAM_ID)
    )
    for pid, name, team, slots, value, injury, slot in SQUAD:
        positions = ["C"] if slots[0] == C else [{0: "PG", 1: "SG", 2: "SF", 3: "PF"}[slots[0]]]
        _player(db, pid, name, team, positions, slots, "ONTEAM", injury)
        db.flush()
        db.add(RosterEntry(season=SEASON, team_id=MY_TEAM_ID, player_id=pid, lineup_slot_id=slot))
        _projection(db, pid, ppg=value)
    # A free agent the plan can pick up: plays day 2, better than anyone.
    _player(db, ZED, "Zed Pickup", 80, ["SF"], [SF, 6, UT, BE, IR], "FREEAGENT")
    _projection(db, ZED, ppg=100.0)
    # Pete's team tips early on day 2; the rest late. Day 3: Pete, Sam and Cole only.
    _game(db, 9001, 2, 70, 81, DAY2_TIP)
    for game_id, (home, away) in enumerate([(71, 72), (73, 74), (75, 76), (77, 78), (79, 80)]):
        _game(db, 9002 + game_id, 2, home, away, DAY2_LATE)
    _game(db, 9101, 3, 70, 71, datetime(2026, 10, 23, 2, 0, tzinfo=UTC))
    _game(db, 9102, 3, 74, 82, datetime(2026, 10, 23, 2, 0, tzinfo=UTC))
    db.commit()
    return league


def _auto(client, path="/team/lineup/auto", expected=200, **body):
    response = client.post(path, json=body)
    assert response.status_code == expected, response.text
    return response.json()


def _slots(day: dict) -> dict[int, int]:
    return {row["player"]["id"]: row["slot_id"] for row in day["slots"] if row["player"]}


def _starters(day: dict) -> set[int]:
    return {pid for pid, slot in _slots(day).items() if slot not in (BE, IR)}


def _rows(db, model, sp) -> dict[int, int]:
    return {
        row.player_id: row.lineup_slot_id
        for row in db.scalars(
            select(model).where(
                model.season == SEASON, model.team_id == MY_TEAM_ID, model.scoring_period_id == sp
            )
        )
    }


def _count(db, model) -> int:
    return db.scalar(select(func.count()).select_from(model).where(model.team_id == MY_TEAM_ID))


TOP_SEVEN_DAY2 = {PETE, SAM, SFRED, PAUL, CARL, COLE, GABE}


def test_actual_auto_fill_starts_the_seven_best_who_play(squad, db):
    body = _auto(squad, sp=2)

    assert _starters(body) == TOP_SEVEN_DAY2
    assert _slots(body)[IVY] == IR and _slots(body)[OSCAR] == BE
    assert body["days"] == [
        {
            "sp": 2,
            "starters_playing": 7,
            "slots": 7,
            "benched_playing": [NAMES[FINN], NAMES[DREW]],
        }
    ]
    assert body["days_written"] == 1 and body["changed"] is True
    assert body["lineup_source"] == "manual"
    assert _rows(db, LineupAssignment, 2) == _slots(body)
    assert _count(db, PlanLineupAssignment) == 0


def test_a_second_run_is_already_optimal_and_writes_nothing(squad, db):
    _auto(squad, sp=2)
    body = _auto(squad, sp=2)
    assert body["changed"] is False and body["days_written"] == 0
    assert _starters(body) == TOP_SEVEN_DAY2


def test_through_sp_computes_each_day_from_its_own_games(squad, db):
    body = _auto(squad, sp=2, through_sp=3)

    assert body["days_written"] == 2
    assert [d["sp"] for d in body["days"]] == [2, 3]
    day3 = body["days"][1]
    assert day3 == {"sp": 3, "starters_playing": 3, "slots": 7, "benched_playing": []}
    rows2, rows3 = _rows(db, LineupAssignment, 2), _rows(db, LineupAssignment, 3)
    assert {p for p, s in rows2.items() if s not in (BE, IR)} == TOP_SEVEN_DAY2
    starters3 = {p for p, s in rows3.items() if s not in (BE, IR)}
    # The three who play, plus the best non-players who fit (Oscar is OUT but has value).
    assert {PETE, SAM, COLE} <= starters3 and len(starters3) == 7
    assert rows2 != rows3


def test_leave_empty_leaves_slots_nobody_playing_can_fill(squad, db):
    body = _auto(squad, sp=3, leave_empty=True)
    assert _starters(body) == {PETE, SAM, COLE}
    assert sum(row["player"] is None for row in body["slots"] if row["slot_id"] < BE) == 4


def test_today_a_started_game_locks_the_player_where_he_is(squad, db, monkeypatch):
    # 5pm PT on day 2: Pete's game has started with him on the bench, so he stays there and
    # Finn takes the seventh slot. (Paul starts on the bench too: the squad seeds him in PG.)
    db.execute(
        RosterEntry.__table__.update()
        .where(RosterEntry.team_id == MY_TEAM_ID, RosterEntry.player_id == PAUL)
        .values(lineup_slot_id=BE)
    )
    db.commit()
    monkeypatch.setattr(team_route, "_now", lambda: datetime(2026, 10, 22, 0, 0, tzinfo=UTC))

    body = _auto(squad, sp=2)

    assert _slots(body)[PETE] == BE  # locked on the bench
    assert _starters(body) == {SAM, SFRED, PAUL, CARL, COLE, GABE, FINN}
    assert body["days"][0]["benched_playing"] == [NAMES[PETE], NAMES[DREW]]


def test_a_started_game_locks_a_starter_in_his_slot(squad, db, monkeypatch):
    db.execute(
        RosterEntry.__table__.update()
        .where(RosterEntry.team_id == MY_TEAM_ID, RosterEntry.player_id.in_([PAUL, DREW]))
        .values(lineup_slot_id=BE)
    )
    db.execute(
        RosterEntry.__table__.update()
        .where(RosterEntry.team_id == MY_TEAM_ID, RosterEntry.player_id == PETE)
        .values(lineup_slot_id=UT)
    )
    db.commit()
    monkeypatch.setattr(team_route, "_now", lambda: datetime(2026, 10, 22, 0, 0, tzinfo=UTC))

    body = _auto(squad, sp=2)
    assert _slots(body)[PETE] == UT
    assert _starters(body) == TOP_SEVEN_DAY2


def test_locks_apply_only_to_today(squad, db, monkeypatch):
    # Today is day 2 after Pete tipped off; day 3 has no locks even with Pete benched there.
    monkeypatch.setattr(team_route, "_now", lambda: datetime(2026, 10, 22, 0, 0, tzinfo=UTC))
    body = _auto(squad, sp=3, leave_empty=True)
    assert _starters(body) == {PETE, SAM, COLE}


def test_plan_auto_fill_uses_the_plan_roster_and_writes_only_plan_rows(squad, db):
    _create(squad, sp=2, drop_player_id=FINN, candidate_ids=[ZED])
    before = _count(db, LineupAssignment)

    body = _auto(squad, "/team/plan/lineup/auto", sp=2)

    assert body["view"] == "plan"
    starters = _starters(body)
    assert ZED in starters and FINN not in _slots(body)
    assert starters == {ZED, PETE, SAM, SFRED, PAUL, CARL, COLE}
    assert body["days"][0]["benched_playing"] == [NAMES[GABE], NAMES[DREW]]
    assert _rows(db, PlanLineupAssignment, 2) == _slots(body)
    assert _count(db, LineupAssignment) == before == 0


def test_plan_auto_fill_never_locks(squad, db, monkeypatch):
    monkeypatch.setattr(team_route, "_now", lambda: datetime(2026, 10, 22, 0, 0, tzinfo=UTC))
    body = _auto(squad, "/team/plan/lineup/auto", sp=2)
    assert PETE in _starters(body)


def test_plan_through_sp_is_per_day(squad, db):
    body = _auto(squad, "/team/plan/lineup/auto", sp=2, through_sp=3, leave_empty=True)
    assert [d["starters_playing"] for d in body["days"]] == [7, 3]
    starters3 = {p for p, s in _rows(db, PlanLineupAssignment, 3).items() if s not in (BE, IR)}
    assert starters3 == {PETE, SAM, COLE}


@pytest.mark.parametrize(
    "body", [{"sp": 0}, {"sp": 3, "through_sp": 2}, {"sp": 2, "through_sp": 161}]
)
@pytest.mark.parametrize("path", ["/team/lineup/auto", "/team/plan/lineup/auto"])
def test_bad_days_are_422(squad, path, body):
    _auto(squad, path, expected=422, **body)


def test_value_prefers_this_seasons_line_once_he_has_played(squad, db):
    _projection(db, PETE, kind=ACTUAL_CURRENT_KIND, ppg=12.0, games=3.0)
    _projection(db, SAM, kind=ACTUAL_CURRENT_KIND, ppg=99.0, games=0.0)  # no games yet
    db.commit()
    values = player_values(db, SEASON, [PETE, SAM, ZED, 999999])
    assert values == {PETE: 12.0, SAM: 45.0, ZED: 100.0, 999999: 0.0}
