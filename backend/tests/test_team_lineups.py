"""Lineups: the explicit pull from ESPN, and the pure day-by-day resolver."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app.db.models import LINEUP_SOURCE_ESPN, LINEUP_SOURCE_MANUAL, LineupAssignment
from app.espn.client import ESPNRequestError
from app.espn.lineups import pull_lineups
from app.espn.sync import sync_league
from app.team.lineup import BENCH_SLOT, resolve_lineup
from tests.conftest import MY_TEAM_ID, PRESEASON_NOW, SEASON


@pytest.fixture
def league(db, fake_espn):
    """A database after one full league sync, and the client that did it."""
    client = fake_espn()
    sync_league(db, client, now=PRESEASON_NOW)
    return client


def _day(db, sp: int) -> dict[int, tuple[int, str]]:
    return {
        row.player_id: (row.lineup_slot_id, row.source)
        for row in db.scalars(
            select(LineupAssignment).where(
                LineupAssignment.team_id == MY_TEAM_ID,
                LineupAssignment.scoring_period_id == sp,
            )
        )
    }


def _manual(db, sp: int, player_id: int, slot: int) -> None:
    """A manual edit, the way the Team tab will make one: set his slot that day, upserting."""
    row = db.scalar(
        select(LineupAssignment).where(
            LineupAssignment.season == SEASON,
            LineupAssignment.team_id == MY_TEAM_ID,
            LineupAssignment.scoring_period_id == sp,
            LineupAssignment.player_id == player_id,
        )
    )
    if row is None:
        row = LineupAssignment(
            season=SEASON, team_id=MY_TEAM_ID, scoring_period_id=sp, player_id=player_id
        )
        db.add(row)
    row.lineup_slot_id = slot
    row.source = LINEUP_SOURCE_MANUAL
    row.updated_at = datetime.now(UTC)
    db.commit()


# --- the pull -------------------------------------------------------------------------------


def test_a_pull_stores_one_row_per_player_per_day(db, league, mroster_payload):
    pulled = pull_lineups(db, league, MY_TEAM_ID, 1, 10)

    roster = next(t for t in mroster_payload["teams"] if t["id"] == MY_TEAM_ID)
    size = len(roster["roster"]["entries"])
    assert (pulled.team_id, pulled.first_sp, pulled.last_sp, pulled.days) == (
        MY_TEAM_ID,
        1,
        10,
        10,
    )
    assert pulled.assignments == db.scalar(select(func.count()).select_from(LineupAssignment))
    assert all(len(_day(db, sp)) == size for sp in range(1, 11))
    assert {source for _, source in _day(db, 5).values()} == {LINEUP_SOURCE_ESPN}
    assert [call for call in league.calls if call[0] == "mRoster"][-10:] == [
        ("mRoster", (sp, MY_TEAM_ID)) for sp in range(1, 11)
    ]


def test_a_future_day_can_differ_from_today(db, league):
    pull_lineups(db, league, MY_TEAM_ID, 1, 8)
    today, later = _day(db, 1), _day(db, 8)
    assert today.keys() == later.keys()
    assert today != later, "ESPN stored a different lineup for day 8"


def test_a_pull_overwrites_a_manual_edit_in_range_and_leaves_one_outside_alone(db, league):
    pull_lineups(db, league, MY_TEAM_ID, 1, 3)
    player_id = next(iter(_day(db, 2)))
    _manual(db, 2, player_id, BENCH_SLOT if _day(db, 2)[player_id][0] != BENCH_SLOT else 11)
    _manual(db, 20, player_id, 11)
    assert _day(db, 2)[player_id][1] == LINEUP_SOURCE_MANUAL

    pull_lineups(db, league, MY_TEAM_ID, 1, 3)

    assert _day(db, 2)[player_id][1] == LINEUP_SOURCE_ESPN
    assert _day(db, 20) == {player_id: (11, LINEUP_SOURCE_MANUAL)}


def test_the_default_range_is_today_through_the_end_of_next_matchup(db, league):
    pulled = pull_lineups(db, league, MY_TEAM_ID, now=PRESEASON_NOW)
    assert (pulled.first_sp, pulled.last_sp) == (1, 13)


def test_the_range_is_clamped_to_the_fantasy_season(db, league):
    pulled = pull_lineups(db, league, MY_TEAM_ID, 155, 170)
    assert (pulled.first_sp, pulled.last_sp, pulled.days) == (155, 160, 6)


def test_a_backwards_range_is_refused(db, league):
    with pytest.raises(ValueError):
        pull_lineups(db, league, MY_TEAM_ID, 10, 5)


def test_a_failed_fetch_partway_writes_nothing(db, league):
    pull_lineups(db, league, MY_TEAM_ID, 1, 3)
    _manual(db, 20, next(iter(_day(db, 1))), 11)
    before = {sp: _day(db, sp) for sp in (1, 2, 3, 20)}
    real = league.fetch_rosters

    def flaky(sp=None, *, team_id=None):
        if sp == 3:
            raise ESPNRequestError("ESPN returned HTTP 500")
        return real(sp, team_id=team_id)

    league.fetch_rosters = flaky
    with pytest.raises(ESPNRequestError):
        pull_lineups(db, league, MY_TEAM_ID, 1, 20)
    db.rollback()

    assert {sp: _day(db, sp) for sp in (1, 2, 3, 20)} == before


def test_a_league_sync_after_a_pull_leaves_the_lineups_alone(db, league):
    pull_lineups(db, league, MY_TEAM_ID, 1, 5)
    _manual(db, 7, next(iter(_day(db, 1))), 11)
    before = {sp: _day(db, sp) for sp in range(1, 8)}

    sync_league(db, league, now=PRESEASON_NOW)

    assert {sp: _day(db, sp) for sp in range(1, 8)} == before


# --- the resolver ---------------------------------------------------------------------------

ROSTER = {1: 0, 2: 1, 3: 12, 4: 13}  # player -> the slot the roster sync saw


def test_a_day_with_rows_uses_them():
    stored = {5: {1: 12, 2: 0, 3: 1, 4: 13}}
    assert resolve_lineup(stored, ROSTER, 5) == {1: 12, 2: 0, 3: 1, 4: 13}


def test_a_day_without_rows_carries_the_nearest_earlier_day_forward():
    stored = {3: {1: 11, 2: 12, 3: 0, 4: 13}, 6: {1: 0, 2: 1, 3: 12, 4: 13}, 9: {}}
    assert resolve_lineup(stored, ROSTER, 5) == stored[3]
    # Day 9 has an empty mapping, which is "nothing stored", not "everyone benched".
    assert resolve_lineup(stored, ROSTER, 9) == stored[6]


def test_a_later_day_never_leaks_backwards():
    stored = {6: {1: 12, 2: 12, 3: 12, 4: 12}}
    assert resolve_lineup(stored, ROSTER, 5) == ROSTER


def test_nothing_stored_falls_back_to_the_roster_slots():
    assert resolve_lineup({}, ROSTER, 1) == ROSTER


def test_a_dropped_player_is_removed_and_a_new_one_goes_to_the_bench():
    stored = {2: {1: 0, 2: 1, 3: 12, 4: 13, 99: 4}}  # 99 was dropped since
    roster = {**ROSTER, 7: 3}  # 7 was picked up since; ESPN had him at PF
    resolved = resolve_lineup(stored, roster, 4)
    assert 99 not in resolved
    assert resolved[7] == BENCH_SLOT
    assert resolved == {1: 0, 2: 1, 3: 12, 4: 13, 7: BENCH_SLOT}
