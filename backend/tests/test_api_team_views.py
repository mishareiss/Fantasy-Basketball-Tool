"""The Team tab's API: the day view, lineup edits, the waiver list, the player card and the
calendar. ESPN is the recorded league behind `FakeESPNClient`. The waiver-list and card tests
add a small hand-made pool and schedule on top, so their expectations can be read off the
seed instead of off a recording."""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select, update

from app.api import sync as sync_route
from app.api import team as team_route
from app.db.models import (
    EspnTransaction,
    FantasyTeam,
    LineupAssignment,
    NbaGame,
    Player,
    Projection,
    RosterEntry,
)
from app.db.session import get_db
from app.espn.sync import ACTUAL_CURRENT_KIND, ACTUAL_LAST_7_KIND, sync_league
from app.main import app
from app.team import views
from tests.conftest import MY_TEAM_ID, PRESEASON_NOW, SEASON

PG, SG, SF, PF, C, UT, BE, IR = 0, 1, 2, 3, 4, 11, 12, 13

# Team 8 in the recorded league.
SIMONS, HALIBURTON, GOBERT, LUKA, MILLER = 4351851, 4396993, 3032976, 3945274, 4433287
OUTSIDER = 4871139  # a free agent in the recorded pool

LAL, GSW, ATL, CLE = 13, 9, 1, 5


@pytest.fixture
def client_stub(fake_espn):
    return fake_espn()


@pytest.fixture
def api(db, client_stub, monkeypatch):
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[sync_route.get_espn_client] = lambda: client_stub
    monkeypatch.setattr(
        sync_route,
        "sync_league",
        lambda session: sync_league(session, client_stub, now=PRESEASON_NOW),
    )
    monkeypatch.setattr(team_route, "_now", lambda: PRESEASON_NOW)
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def league(api):
    """The recorded league, synced: settings, players, schedule, teams, rosters."""
    assert api.post("/sync/league").status_code == 200
    return api


def _projection(db, pid, *, kind="projected_season", season=SEASON, ppg=20.0, games=10.0, **pg):
    per_game = pg or {"PTS": ppg / 2, "REB": 5.0}
    db.add(
        Projection(
            player_id=pid,
            source="espn",
            kind=kind,
            season=season,
            raw_stats={name: value * games for name, value in per_game.items()},
            per_game_stats=per_game,
            projected_games=games,
            fantasy_points_total=ppg * games,
            fantasy_points_per_game=ppg,
            per_game_basis="projected_games",
            as_of=PRESEASON_NOW,
        )
    )


def _game(db, game_id, sp, home, away, starts_at, tbd=False):
    db.add(
        NbaGame(
            id=game_id,
            season=SEASON,
            scoring_period_id=sp,
            starts_at=starts_at,
            home_pro_team_id=home,
            away_pro_team_id=away,
            start_time_tbd=tbd,
        )
    )


def _lineup(day: dict) -> list[dict]:
    return [
        {"player_id": row["player"]["id"], "slot_id": row["slot_id"]}
        for row in day["slots"]
        if row["player"] is not None
    ]


def _slot_of(day: dict, pid: int) -> str:
    return next(row["slot"] for row in day["slots"] if row["player"] and row["player"]["id"] == pid)


def _move(assignments: list[dict], pid: int, slot_id: int) -> list[dict]:
    return [a | {"slot_id": slot_id} if a["player_id"] == pid else a for a in assignments]


# --- GET /team/day --------------------------------------------------------------------------


def test_the_day_view_needs_a_synced_team(api):
    assert api.get("/team/day").status_code == 409


def test_the_default_day_before_tip_off_is_period_one(league):
    body = league.get("/team/day").json()

    assert (body["season"], body["sp"], body["date"]) == (SEASON, 1, "2026-10-20")
    assert body["is_today"] is False  # Oct 15 is not a fantasy day
    assert body["matchup"] == {
        "id": 1,
        "label": "Matchup 1",
        "start": "2026-10-20",
        "end": "2026-10-25",
    }
    assert body["team"] == {"id": MY_TEAM_ID, "name": "Team 8", "abbrev": "T8"}
    assert body["basis"] == "projected"
    assert body["lineup_source"] == "roster"
    assert body["warnings"] == []


def test_is_today_on_a_game_day(league, monkeypatch):
    # 11pm Pacific on Oct 21 is still period 2.
    monkeypatch.setattr(team_route, "_now", lambda: datetime(2026, 10, 22, 6, 0, tzinfo=UTC))
    body = league.get("/team/day").json()
    assert (body["sp"], body["is_today"]) == (2, True)


def test_the_slot_grid_is_espn_order_with_empty_slots_and_a_grown_bench(league):
    body = league.get("/team/day", params={"sp": 1}).json()
    slots = [row["slot"] for row in body["slots"]]

    # 21 rostered: 7 starters, 12 on a 10-man bench (it grows), 2 on IR, 1 IR slot empty.
    assert slots == ["PG", "SG", "SF", "PF", "C", "UT", "UT"] + ["BE"] * 12 + ["IR"] * 3
    assert body["slots"][-1]["player"] is None
    assert sum(row["player"] is not None for row in body["slots"]) == 21
    assert body["slots"][0]["slot_id"] == PG


def test_a_player_with_a_game_and_one_without(league):
    body = league.get("/team/day", params={"sp": 1}).json()
    simons = body["slots"][0]["player"]
    miller = next(
        row["player"] for row in body["slots"] if row["player"] and row["player"]["id"] == MILLER
    )

    assert simons["id"] == SIMONS
    assert simons["game"]["display"] == "@ NYK 4:00 PM"
    assert (simons["game"]["display_matchup"], simons["game"]["display_time"]) == (
        "@ NYK",
        "4:00 PM",
    )
    assert simons["game"]["is_home"] is False
    assert simons["game"]["starts_at"] == "2026-10-20T23:00:00Z"
    assert 0 in simons["eligible_slots"]
    assert miller["game"] is None


def test_the_projected_basis_carries_stat_lines_and_columns(league):
    body = league.get("/team/day", params={"sp": 1}).json()
    luka = next(
        row["player"] for row in body["slots"] if row["player"] and row["player"]["id"] == LUKA
    )

    assert luka["stats"]["fantasy_ppg"] > 100
    assert "PTS" in luka["stats"]["per_game"]
    assert body["stat_columns"][:2] == ["MIN", "PTS"]
    assert set(body["stat_columns"]) <= set(views.DISPLAY_STATS)


@pytest.mark.parametrize("basis", ["season", "l7", "l15", "l30"])
def test_actual_bases_before_any_games_answer_with_every_line_absent(league, basis):
    body = league.get("/team/day", params={"sp": 1, "basis": basis}).json()

    assert body["basis"] == basis
    assert body["actuals_available"] is False
    assert all(row["player"]["stats"] is None for row in body["slots"] if row["player"])
    assert body["stat_columns"] == []


def test_actual_bases_with_seeded_rows(league, db):
    _projection(db, LUKA, kind=ACTUAL_CURRENT_KIND, ppg=55.0, games=3.0, PTS=30.0, DD=1.0)
    _projection(db, LUKA, kind=ACTUAL_LAST_7_KIND, ppg=60.0, games=3.0, PTS=33.0)
    # Last season's line under a current kind must not count as this season's.
    _projection(db, SIMONS, kind=ACTUAL_CURRENT_KIND, season=SEASON - 1, ppg=40.0)
    db.commit()

    season = league.get("/team/day", params={"sp": 1, "basis": "season"}).json()
    l7 = league.get("/team/day", params={"sp": 1, "basis": "l7"}).json()
    l15 = league.get("/team/day", params={"sp": 1, "basis": "l15"}).json()

    def line(body, pid):
        return next(
            row["player"]["stats"]
            for row in body["slots"]
            if row["player"] and row["player"]["id"] == pid
        )

    assert season["actuals_available"] is True
    assert line(season, LUKA) == {
        "season": SEASON,
        "games": 3.0,
        "fantasy_ppg": 55.0,
        "fantasy_total": 165.0,
        "per_game": {"PTS": 30.0, "DD": 1.0},
    }
    assert season["stat_columns"] == ["PTS", "DD"]
    assert line(season, SIMONS) is None
    assert line(l7, LUKA)["fantasy_ppg"] == 60.0
    assert line(l15, LUKA) is None


def test_lineup_source_roster_espn_carried_and_manual(league):
    assert league.get("/team/day", params={"sp": 3}).json()["lineup_source"] == "roster"

    league.post("/sync/lineup", params={"first_sp": 1, "last_sp": 2})
    assert league.get("/team/day", params={"sp": 2}).json()["lineup_source"] == "espn"
    carried = league.get("/team/day", params={"sp": 5}).json()
    assert carried["lineup_source"] == "carried"

    edited = _move(_lineup(carried), HALIBURTON, PG)
    edited = _move(edited, SIMONS, BE)
    assert league.put("/team/lineup/5", json={"assignments": edited}).status_code == 200
    assert league.get("/team/day", params={"sp": 5}).json()["lineup_source"] == "manual"
    # And day 6 now carries the manual day forward.
    assert league.get("/team/day", params={"sp": 6}).json()["lineup_source"] == "carried"


@pytest.mark.parametrize(("totals", "used"), [({"1": 2}, 2), ({1: 2}, 2), ({}, 0), ({"2": 4}, 0)])
def test_acquisitions_read_espn_counter_for_the_days_matchup(league, db, totals, used):
    team = db.scalar(select(FantasyTeam).where(FantasyTeam.team_id == MY_TEAM_ID))
    team.matchup_acquisition_totals = totals
    db.flush()

    body = league.get("/team/day", params={"sp": 3}).json()
    # Task 30 adds `used_source` and `planned` (no plan here, so ESPN's count and 0).
    assert body["acquisitions"] == {
        "used": used,
        "limit": 7,
        "matchup_id": 1,
        "used_source": "espn",
        "planned": 0,
    }


def test_acquisitions_follow_the_days_matchup(league, db):
    team = db.scalar(select(FantasyTeam).where(FantasyTeam.team_id == MY_TEAM_ID))
    team.matchup_acquisition_totals = {"1": 2, "2": 5}
    db.flush()
    body = league.get("/team/day", params={"sp": 7}).json()
    assert body["acquisitions"] == {
        "used": 5,
        "limit": 7,
        "matchup_id": 2,
        "used_source": "espn",
        "planned": 0,
    }


@pytest.mark.parametrize("sp", [0, -1, 161])
def test_a_day_outside_the_season_is_a_422(league, sp):
    assert league.get("/team/day", params={"sp": sp}).status_code == 422


def test_an_unknown_basis_is_a_422(league):
    assert league.get("/team/day", params={"basis": "l99"}).status_code == 422


# --- PUT /team/lineup/{sp} ------------------------------------------------------------------


def test_a_manual_swap_round_trips(league, db):
    day = league.get("/team/day", params={"sp": 2}).json()
    swapped = _move(_move(_lineup(day), HALIBURTON, PG), SIMONS, BE)

    response = league.put("/team/lineup/2", json={"assignments": swapped})

    assert response.status_code == 200
    body = response.json()
    assert body["days_written"] == 1
    assert body["lineup_source"] == "manual"
    assert body["sp"] == 2
    assert body["slots"][0]["player"]["id"] == HALIBURTON
    again = league.get("/team/day", params={"sp": 2}).json()
    assert _slot_of(again, HALIBURTON) == "PG"
    assert _slot_of(again, SIMONS) == "BE"
    rows = db.scalars(select(LineupAssignment)).all()
    assert {row.source for row in rows} == {"manual"}
    assert {row.scoring_period_id for row in rows} == {2}
    assert len(rows) == 21


def _rejected(league, assignments, sp=2):
    response = league.put(f"/team/lineup/{sp}", json={"assignments": assignments})
    assert response.status_code == 422
    return response.json()["detail"]


def test_a_missing_player_is_rejected_and_nothing_is_written(league, db):
    lineup = [a for a in _lineup(league.get("/team/day").json()) if a["player_id"] != LUKA]
    detail = _rejected(league, lineup)
    assert detail == [f"Luka Doncic ({LUKA}) is missing from the lineup"]
    assert db.scalars(select(LineupAssignment)).all() == []


def test_an_extra_player_is_rejected(league):
    lineup = _lineup(league.get("/team/day").json()) + [{"player_id": OUTSIDER, "slot_id": BE}]
    detail = _rejected(league, lineup)
    assert detail == [f"player {OUTSIDER} is not on the roster"]


def test_a_slot_over_capacity_is_rejected(league):
    lineup = _move(_lineup(league.get("/team/day").json()), HALIBURTON, PG)
    assert _rejected(league, lineup) == ["2 players in PG, which has room for 1"]


def test_an_ineligible_slot_is_rejected(league, db):
    lineup = _move(_move(_lineup(league.get("/team/day").json()), GOBERT, PG), SIMONS, BE)
    assert _rejected(league, lineup) == [f"Rudy Gobert ({GOBERT}) can't play PG"]
    assert db.scalars(select(LineupAssignment)).all() == []


def test_every_error_is_listed_at_once(league):
    lineup = [a for a in _lineup(league.get("/team/day").json()) if a["player_id"] != LUKA]
    lineup = _move(_move(lineup, GOBERT, PG), HALIBURTON, PG)
    assert len(_rejected(league, lineup)) == 3  # missing, ineligible, over capacity


def test_a_healthy_player_on_ir_is_a_warning(league, db):
    healthy = db.get(Player, LUKA)
    healthy.injury_status = "ACTIVE"
    db.flush()
    day = league.get("/team/day").json()
    response = league.put("/team/lineup/2", json={"assignments": _move(_lineup(day), LUKA, IR)})

    assert response.status_code == 200
    assert response.json()["warnings"] == [
        f"Luka Doncic ({LUKA}) is on IR but not listed as injured"
    ]
    assert _slot_of(response.json(), LUKA) == "IR"


def test_through_sp_writes_every_day_in_range(league, db):
    lineup = _move(_move(_lineup(league.get("/team/day").json()), HALIBURTON, PG), SIMONS, BE)
    body = league.put("/team/lineup/2", json={"assignments": lineup, "through_sp": 4}).json()

    assert body["days_written"] == 3
    days = {row.scoring_period_id for row in db.scalars(select(LineupAssignment))}
    assert days == {2, 3, 4}


@pytest.mark.parametrize(("sp", "through"), [(4, 3), (2, 161), (0, None), (161, None)])
def test_bad_day_ranges_are_422(league, sp, through):
    lineup = _lineup(league.get("/team/day").json())
    body = {"assignments": lineup} | ({"through_sp": through} if through else {})
    assert league.put(f"/team/lineup/{sp}", json=body).status_code == 422


def test_an_edit_replaces_only_its_days_and_only_my_team(league, db):
    league.post("/sync/lineup", params={"first_sp": 1, "last_sp": 5})
    db.add(
        LineupAssignment(
            season=SEASON,
            team_id=4,
            scoring_period_id=3,
            player_id=OUTSIDER,
            lineup_slot_id=BE,
            source="manual",
        )
    )
    db.commit()

    lineup = _move(_move(_lineup(league.get("/team/day").json()), HALIBURTON, PG), SIMONS, BE)
    assert league.put("/team/lineup/3", json={"assignments": lineup}).status_code == 200

    mine = db.scalars(select(LineupAssignment).where(LineupAssignment.team_id == MY_TEAM_ID))
    sources = {}
    for row in mine:
        sources.setdefault(row.scoring_period_id, set()).add(row.source)
    assert sources == {1: {"espn"}, 2: {"espn"}, 3: {"manual"}, 4: {"espn"}, 5: {"espn"}}
    other = db.scalars(select(LineupAssignment).where(LineupAssignment.team_id == 4)).all()
    assert len(other) == 1


def test_the_espn_pull_overwrites_edits_in_its_range_and_keeps_the_rest(league, client_stub):
    lineup = _move(_move(_lineup(league.get("/team/day").json()), HALIBURTON, PG), SIMONS, BE)
    league.put("/team/lineup/2", json={"assignments": lineup})
    league.put("/team/lineup/20", json={"assignments": lineup})

    assert league.post("/sync/lineup", params={"first_sp": 1, "last_sp": 8}).status_code == 200

    inside = league.get("/team/day", params={"sp": 2}).json()
    outside = league.get("/team/day", params={"sp": 20}).json()
    assert inside["lineup_source"] == "espn"
    assert _slot_of(inside, SIMONS) == "PG"
    assert outside["lineup_source"] == "manual"
    assert _slot_of(outside, HALIBURTON) == "PG"
    assert ("mRoster", (2, MY_TEAM_ID)) in client_stub.calls


# --- GET /team/free-agents ------------------------------------------------------------------

ALPHA, BRAVO, CHARLIE, DELTA, ECHO, FOXTROT, GOLF = range(900001, 900008)
ADDED = datetime(2026, 10, 18, 20, 0, tzinfo=UTC)
DROPPED = datetime(2026, 10, 20, 22, 0, tzinfo=UTC)


def _player(db, pid, name, team, positions, slots, status, injury=None):
    db.add(
        Player(
            espn_player_id=pid,
            full_name=name,
            nba_team={LAL: "LAL", GSW: "GSW", ATL: "ATL", CLE: "CLE"}.get(team, "FA"),
            pro_team_id=team,
            positions=positions,
            eligible_slots=slots,
            roster_status=status,
            injury_status=injury,
        )
    )


@pytest.fixture
def pool(league, db):
    """The recorded league with its own free agents rostered away, and a seeded pool:

    LAL plays days 1, 2, 3; GSW 1, 2; CLE 2, 3; ATL 2. Alpha (LAL, PG, 30), Bravo (GSW, SF/PF,
    25), Charlie (CLE, C, 20, on WAIVERS: dropped Oct 20 after two days held), Delta (ATL, SG,
    no projection). Echo is unsigned, Foxtrot is on a roster, Golf is ONTEAM: none of them
    are available.
    """
    db.execute(
        update(Player)
        .where(Player.roster_status.in_(["FREEAGENT", "WAIVERS"]))
        .values(roster_status="ONTEAM")
    )
    _player(db, ALPHA, "Alpha Laker", LAL, ["PG"], [PG, 5, UT, BE, IR], "FREEAGENT", "ACTIVE")
    _player(db, BRAVO, "Bravo Warrior", GSW, ["SF", "PF"], [SF, PF, 6, UT, BE, IR], "FREEAGENT")
    _player(db, CHARLIE, "Charlie Cav", CLE, ["C"], [C, UT, BE, IR], "WAIVERS")
    _player(db, DELTA, "Delta Hawk", ATL, ["SG"], [SG, 5, UT, BE, IR], "FREEAGENT")
    _player(db, ECHO, "Echo Unsigned", 0, ["PG"], [PG, UT, BE, IR], "FREEAGENT")
    _player(db, FOXTROT, "Foxtrot Rostered", LAL, ["SG"], [SG, UT, BE, IR], "FREEAGENT")
    _player(db, GOLF, "Golf Owned", LAL, ["SG"], [SG, UT, BE, IR], "ONTEAM")
    db.flush()
    db.add(RosterEntry(season=SEASON, team_id=3, player_id=FOXTROT, lineup_slot_id=BE))
    for pid, ppg in ((ALPHA, 30.0), (BRAVO, 25.0), (CHARLIE, 20.0), (ECHO, 40.0), (FOXTROT, 50.0)):
        _projection(db, pid, ppg=ppg)
    _game(db, 1, 1, LAL, GSW, datetime(2026, 10, 21, 2, 0, tzinfo=UTC))  # Tue 7pm PT
    _game(db, 2, 2, ATL, LAL, datetime(2026, 10, 21, 23, 30, tzinfo=UTC))
    _game(db, 3, 3, LAL, CLE, datetime(2026, 10, 23, 2, 30, tzinfo=UTC), tbd=True)
    _game(db, 4, 2, GSW, CLE, datetime(2026, 10, 22, 2, 0, tzinfo=UTC))
    _game(db, 5, 7, LAL, ATL, datetime(2026, 10, 27, 2, 0, tzinfo=UTC))
    for tx_id, kind, when in (("tx-add", "ADD", ADDED), ("tx-drop", "DROP", DROPPED)):
        db.add(
            EspnTransaction(
                id=tx_id,
                season=SEASON,
                type="FREEAGENT",
                status="EXECUTED",
                team_id=3,
                scoring_period_id=1,
                proposed_at=when,
                items=[{"type": kind, "playerId": CHARLIE}],
            )
        )
    db.commit()
    return league


def _ids(body) -> list[int]:
    return [row["id"] for row in body["players"]]


def _fa(api, **params):
    response = api.get("/team/free-agents", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_the_pool_excludes_rostered_owned_and_unsigned_players(pool):
    body = _fa(pool)
    assert _ids(body) == [ALPHA, BRAVO, CHARLIE, DELTA]
    assert body["total"] == 4
    assert body["days"] == [{"sp": 1, "date": "2026-10-20", "matchup_id": 1}]
    assert body["mode"] == "none"
    assert _ids(_fa(pool, include_unsigned=True)) == [ECHO, ALPHA, BRAVO, CHARLIE, DELTA]


@pytest.mark.parametrize(
    ("days", "mode", "expected"),
    [
        ("1", "any", [ALPHA, BRAVO]),
        ("1", "all", [ALPHA, BRAVO]),
        ("3", "any", [ALPHA, CHARLIE]),
        ("2", "all", [ALPHA, BRAVO, CHARLIE, DELTA]),
        ("1,2,3", "any", [ALPHA, BRAVO, CHARLIE, DELTA]),
        ("1,2,3", "all", [ALPHA]),
        ("1,3", "all", [ALPHA]),
        ("4", "any", []),
        ("4", "none", [ALPHA, BRAVO, CHARLIE, DELTA]),
    ],
)
def test_day_filter_modes(pool, days, mode, expected):
    body = _fa(pool, days=days, mode=mode)
    assert _ids(body) == expected
    assert body["total"] == len(expected)


def test_games_are_aligned_to_the_days(pool):
    body = _fa(pool, days="1,2,3", mode="all")
    alpha = body["players"][0]

    assert [day["sp"] for day in alpha["games"]] == [1, 2, 3]
    assert [day["game"]["display"] for day in alpha["games"]] == [
        "vs GSW 7:00 PM",
        "@ ATL 4:30 PM",
        "vs CLE TBD",
    ]
    assert [
        (day["game"]["display_matchup"], day["game"]["display_time"]) for day in alpha["games"]
    ] == [("vs GSW", "7:00 PM"), ("@ ATL", "4:30 PM"), ("vs CLE", "TBD")]
    assert alpha["games_in_days"] == 3
    delta = _fa(pool, days="1,2,3", q="delta")["players"][0]
    assert [day["game"] is not None for day in delta["games"]] == [False, True, False]


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"position": "PG"}, [ALPHA]),
        ({"position": "PF"}, [BRAVO]),
        ({"position": "C"}, [CHARLIE]),
        # A comma list is OR: eligible at any of them.
        ({"position": "SF,PF"}, [BRAVO]),
        ({"position": "PG,C"}, [ALPHA, CHARLIE]),
        ({"position": "PG,SG"}, [ALPHA, DELTA]),
        ({"position": "pg, c"}, [ALPHA, CHARLIE]),
        ({"position": ""}, [ALPHA, BRAVO, CHARLIE, DELTA]),
        ({"status": "WAIVERS"}, [CHARLIE]),
        ({"status": "FREEAGENT"}, [ALPHA, BRAVO, DELTA]),
        ({"q": "WARR"}, [BRAVO]),
        ({"q": "zzz"}, []),
    ],
)
def test_filters(pool, params, expected):
    assert _ids(_fa(pool, **params)) == expected


def test_sorts_put_missing_numbers_last(pool):
    assert _ids(_fa(pool, sort="fpts")) == [ALPHA, BRAVO, CHARLIE, DELTA]
    assert _ids(_fa(pool, sort="name")) == [ALPHA, BRAVO, CHARLIE, DELTA]
    # Games over days 1-3: Alpha 3, Bravo 2, Charlie 2 (fpts breaks the tie), Delta 1.
    assert _ids(_fa(pool, sort="games", days="1,2,3")) == [ALPHA, BRAVO, CHARLIE, DELTA]
    assert _ids(_fa(pool, sort="games", days="3")) == [ALPHA, CHARLIE, BRAVO, DELTA]
    delta = _fa(pool, q="delta")["players"][0]
    assert delta["stats"] is None


def test_pagination_reports_the_total(pool):
    body = _fa(pool, limit=2, offset=1)
    assert _ids(body) == [BRAVO, CHARLIE]
    assert body["total"] == 4
    assert _fa(pool, limit=2, offset=10)["players"] == []


@pytest.mark.parametrize("position", ["GUARD", "PG,XX", "UT"])
def test_an_unknown_position_is_a_422_not_ignored(pool, position):
    response = pool.get("/team/free-agents", params={"position": position})
    assert response.status_code == 422
    assert "PG, SG, SF, PF, C" in response.json()["detail"]


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 501}, {"offset": -1}])
def test_bad_paging_is_a_422(pool, params):
    assert pool.get("/team/free-agents", params=params).status_code == 422


def test_waiver_players_show_when_they_clear(pool):
    rows = {row["id"]: row for row in _fa(pool)["players"]}

    # Held two days, dropped Oct 20 3pm PT: + 24h = Oct 21 3pm PT, clears at the next
    # Pacific midnight, Thu Oct 22 00:00 PDT.
    assert rows[CHARLIE]["status"] == "WAIVERS"
    assert rows[CHARLIE]["waiver"] == {
        "clears_at": "2026-10-22T07:00:00Z",
        "clears_at_display": "clears Thu 10/22",
        "clears_at_known": True,
    }
    assert rows[ALPHA]["waiver"] is None
    assert rows[ALPHA]["stats"] == {"fantasy_ppg": 30.0, "games": 10.0}


def test_actual_basis_rows(pool, db):
    _projection(db, BRAVO, kind=ACTUAL_CURRENT_KIND, ppg=33.0, games=2.0)
    db.commit()
    body = _fa(pool, basis="season")
    assert body["actuals_available"] is True
    assert _ids(body) == [BRAVO, ALPHA, CHARLIE, DELTA]
    assert body["players"][0]["stats"] == {"fantasy_ppg": 33.0, "games": 2.0}
    assert body["players"][1]["stats"] is None


@pytest.mark.parametrize("days", ["", ",", "a,b", "0", "1,161"])
def test_bad_days_are_a_422(pool, days):
    assert pool.get("/team/free-agents", params={"days": days}).status_code == 422


def test_the_list_is_a_fixed_handful_of_queries(pool, db):
    for index in range(300):
        pid = 800000 + index
        _player(
            db,
            pid,
            f"Bulk {index:03d}",
            (LAL, GSW, ATL, CLE)[index % 4],
            ["SG"],
            [SG, UT, BE, IR],
            "WAIVERS" if index % 10 == 0 else "FREEAGENT",
        )
        _projection(db, pid, ppg=float(index % 37))
    db.commit()

    statements: list[str] = []

    def count(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", count)
    try:
        body = _fa(pool, days="1,2,3", mode="any", limit=500)
    finally:
        event.remove(engine, "before_cursor_execute", count)

    assert body["total"] == 304
    # Was 8; task 29a adds exactly one: the watchlist, read once for every row's `watched` and
    # the `watchlist=true` filter. Task 30 adds exactly one more: the page's plan candidacies
    # (`in_plan`), my team joined in rather than looked up. The other eight are unchanged.
    assert len(statements) <= 10, statements


# --- the watchlist --------------------------------------------------------------------------


def _watchlist(api) -> list[dict]:
    response = api.get("/team/watchlist")
    assert response.status_code == 200, response.text
    return response.json()["entries"]


def test_the_watchlist_starts_empty(pool):
    assert pool.get("/team/watchlist").json() == {"season": SEASON, "entries": []}
    assert all(row["watched"] is False for row in _fa(pool)["players"])
    assert _fa(pool, watchlist=True)["players"] == []


def test_watching_is_idempotent_and_keeps_the_note_unless_sent(pool, monkeypatch):
    first = pool.put(f"/team/watchlist/{BRAVO}", json={"note": "minutes up"})
    assert first.status_code == 200, first.text
    assert first.json() | {"added_at": None} == {
        "player_id": BRAVO,
        "name": "Bravo Warrior",
        "nba_team": "GSW",
        "positions": ["SF", "PF"],
        "injury_status": None,
        "owner": "FREEAGENT",
        "added_at": None,
        "note": "minutes up",
    }

    # Again, later, with no body: still one entry, same added_at, note kept.
    monkeypatch.setattr(team_route, "_now", lambda: datetime(2026, 10, 30, tzinfo=UTC))
    # The frontend's no-note call sends a JSON null body; same as no body at all.
    again = pool.put(
        f"/team/watchlist/{BRAVO}", content="null", headers={"Content-Type": "application/json"}
    )
    assert again.status_code == 200, again.text
    assert again.json()["added_at"] == first.json()["added_at"]
    assert again.json()["note"] == "minutes up"
    assert len(_watchlist(pool)) == 1

    # A sent note replaces it, and null clears it.
    assert pool.put(f"/team/watchlist/{BRAVO}", json={"note": "starting"}).json()["note"] == (
        "starting"
    )
    assert pool.put(f"/team/watchlist/{BRAVO}", json={"note": None}).json()["note"] is None


def test_unwatching_is_idempotent(pool):
    pool.put(f"/team/watchlist/{ALPHA}")
    assert pool.delete(f"/team/watchlist/{ALPHA}").json() == {"player_id": ALPHA, "removed": True}
    again = pool.delete(f"/team/watchlist/{ALPHA}")
    assert (again.status_code, again.json()["removed"]) == (200, False)
    assert pool.delete("/team/watchlist/12345").status_code == 200
    assert _watchlist(pool) == []


def test_watching_an_unknown_player_is_a_404(pool):
    assert pool.put("/team/watchlist/12345").status_code == 404


def test_the_watchlist_is_newest_first(pool, monkeypatch):
    for day, pid in ((16, ALPHA), (17, CHARLIE)):
        monkeypatch.setattr(team_route, "_now", lambda day=day: datetime(2026, 10, day, tzinfo=UTC))
        pool.put(f"/team/watchlist/{pid}")
    entries = _watchlist(pool)
    assert [entry["player_id"] for entry in entries] == [CHARLIE, ALPHA]
    assert entries[0]["owner"] == "WAIVERS"


def test_free_agents_flag_and_filter_the_watched(pool):
    pool.put(f"/team/watchlist/{CHARLIE}")
    pool.put(f"/team/watchlist/{ALPHA}")

    rows = {row["id"]: row["watched"] for row in _fa(pool)["players"]}
    assert rows == {ALPHA: True, BRAVO: False, CHARLIE: True, DELTA: False}

    watched = _fa(pool, watchlist=True)
    assert _ids(watched) == [ALPHA, CHARLIE]
    assert watched["total"] == 2
    # It composes with the other filters.
    assert _ids(_fa(pool, watchlist=True, position="C")) == [CHARLIE]


def test_a_rostered_watched_player_leaves_free_agents_but_stays_watched(pool, db):
    pool.put(f"/team/watchlist/{BRAVO}")
    db.add(RosterEntry(season=SEASON, team_id=MY_TEAM_ID, player_id=BRAVO, lineup_slot_id=BE))
    db.commit()

    assert BRAVO not in _ids(_fa(pool))
    assert _fa(pool, watchlist=True)["players"] == []
    [entry] = _watchlist(pool)
    assert entry["player_id"] == BRAVO
    assert entry["owner"] == {"team_id": MY_TEAM_ID, "name": "Team 8"}


def test_the_card_says_whether_he_is_watched(pool):
    assert pool.get(f"/team/player/{ALPHA}").json()["watched"] is False
    pool.put(f"/team/watchlist/{ALPHA}")
    assert pool.get(f"/team/player/{ALPHA}").json()["watched"] is True


# --- GET /team/player/{id} ------------------------------------------------------------------


def test_the_card_has_every_basis_and_a_seven_day_window(pool, db):
    _projection(db, ALPHA, kind=ACTUAL_CURRENT_KIND, ppg=28.0, games=2.0, PTS=20.0, MIN=31.0)
    db.commit()

    body = pool.get(f"/team/player/{ALPHA}", params={"from_sp": 6}).json()

    assert body["player"]["owner"] == "FREEAGENT"
    assert body["player"]["eligible_slots"] == [PG, 5, UT, BE, IR]
    assert body["waiver"] is None
    assert body["stats"]["projected"]["fantasy_ppg"] == 30.0
    assert body["stats"]["season"] == {
        "season": SEASON,
        "games": 2.0,
        "fantasy_ppg": 28.0,
        "fantasy_total": 56.0,
        "per_game": {"PTS": 20.0, "MIN": 31.0},
    }
    assert body["stats"]["l7"] is None and body["stats"]["l30"] is None
    assert body["stat_columns"] == ["MIN", "PTS", "REB"]

    # Sun 10/25 is the last day of Matchup 1; Mon 10/26 - Sat 10/31 are Matchup 2.
    assert [day["sp"] for day in body["schedule"]] == list(range(6, 13))
    assert body["schedule"][0] | {"game": None} == {
        "sp": 6,
        "date": "2026-10-25",
        "weekday": "Sun",
        "matchup_id": 1,
        "matchup_label": "Matchup 1",
        "game": None,
    }
    assert body["schedule"][1]["game"]["display"] == "vs ATL 7:00 PM"
    assert body["schedule"][1]["game"]["display_matchup"] == "vs ATL"
    assert body["schedule"][1]["game"]["display_time"] == "7:00 PM"
    groups = body["by_matchup"]
    assert [(g["matchup_id"], g["days"], g["games"]) for g in groups] == [
        (1, [6], 0),
        (2, [7, 8, 9, 10, 11, 12], 1),
    ]
    assert (groups[1]["start"], groups[1]["end"]) == ("2026-10-26", "2026-11-01")


def test_the_card_window_stops_at_the_final_period(pool):
    body = pool.get(f"/team/player/{ALPHA}", params={"from_sp": 158}).json()
    assert [day["sp"] for day in body["schedule"]] == [158, 159, 160]
    assert [g["label"] for g in body["by_matchup"]] == ["Playoff Round 3"]


def test_the_card_for_a_waiver_player_and_a_rostered_one(pool):
    charlie = pool.get(f"/team/player/{CHARLIE}").json()
    assert charlie["player"]["owner"] == "WAIVERS"
    assert charlie["waiver"]["clears_at_display"] == "clears Thu 10/22"
    assert [day["sp"] for day in charlie["schedule"]] == list(range(1, 8))  # default: today

    simons = pool.get(f"/team/player/{SIMONS}").json()
    assert simons["player"]["owner"] == {"team_id": MY_TEAM_ID, "name": "Team 8"}


def test_the_card_404s_an_unknown_player_and_422s_a_bad_day(pool):
    assert pool.get("/team/player/1").status_code == 404
    assert pool.get(f"/team/player/{ALPHA}", params={"from_sp": 0}).status_code == 422


# --- GET /team/calendar ---------------------------------------------------------------------


def test_the_calendar_covers_the_season(league):
    body = league.get("/team/calendar").json()

    assert body["season"] == SEASON
    assert (body["today_period"], body["effective_period"], body["final_period"]) == (
        None,
        1,
        160,
    )
    assert len(body["days"]) == 160
    assert len(body["matchups"]) == 21
    assert body["matchups"][0] == {
        "id": 1,
        "label": "Matchup 1",
        "start": "2026-10-20",
        "end": "2026-10-25",
        "first_sp": 1,
        "last_sp": 6,
    }
    assert body["matchups"][-1]["last_sp"] == 160
    assert body["days"][-1] == {"sp": 160, "date": "2027-03-28", "matchup_id": 21, "my_games": 0}


def test_the_calendar_counts_my_players_games(league):
    days = league.get("/team/calendar").json()["days"]

    # Day 1: Simons (PHI), Mitchell and Mara (OKC), Robinson (DET), George (BOS) and
    # Quaintance (SAS). Day 3: PHI and OKC again, and Jamal Murray's DEN. Nobody on day 2.
    assert [day["my_games"] for day in days[:3]] == [6, 0, 4]


def test_the_calendar_without_a_team_still_lists_every_day(api):
    days = api.get("/team/calendar").json()["days"]
    assert len(days) == 160
    assert all(day["my_games"] == 0 for day in days)
