"""When a dropped player clears waivers: the pure rule, and the status built on ESPN's data."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.db.models import EspnTransaction, LeagueSettings, Player, RosterEntry
from app.espn.sync import sync_league
from app.team.calendar import PACIFIC
from app.team.waivers import waiver_clears_at, waiver_status, waiver_statuses
from tests.conftest import PRESEASON_NOW, SEASON

LA = "America/Los_Angeles"


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


def pacific(*args: int) -> datetime:
    return datetime(*args, tzinfo=PACIFIC)


# --- the pure rule --------------------------------------------------------------------------


def test_the_real_data_point():
    """Picked up 10/12, dropped 10/13 22:54Z, cleared by the 10/15 nightly run (00:00 PT)."""
    cleared = waiver_clears_at(utc(2026, 10, 13, 22, 54, 39), utc(2026, 10, 12, 14, 29, 8), 24, LA)
    assert cleared == utc(2026, 10, 15, 7, 0)


def test_held_under_the_window_goes_straight_to_free_agency():
    assert waiver_clears_at(utc(2026, 10, 13, 22, 54), utc(2026, 10, 13, 10, 0), 24, LA) is None


def test_held_exactly_the_window_still_goes_straight_to_free_agency():
    added = utc(2026, 10, 13, 10, 0)
    assert waiver_clears_at(added + timedelta(hours=24), added, 24, LA) is None
    assert waiver_clears_at(added + timedelta(hours=24, seconds=1), added, 24, LA) is not None


def test_no_known_acquisition_counts_as_held_long_enough():
    assert waiver_clears_at(utc(2026, 10, 13, 22, 54, 39), None, 24, LA) == utc(2026, 10, 15, 7)


def test_a_drop_at_one_minute_to_midnight_pacific():
    # Tue 11/10 23:59 PST + 24h = Wed 23:59 PST -> Thu 00:00 PST = 08:00Z.
    assert waiver_clears_at(pacific(2026, 11, 10, 23, 59), None, 24, LA) == utc(2026, 11, 12, 8)


def test_landing_exactly_on_midnight_waits_for_the_next_one():
    """Strictly after: drop + 24h at 00:00 sharp clears at the FOLLOWING midnight."""
    assert waiver_clears_at(pacific(2026, 11, 10, 0, 0), None, 24, LA) == utc(2026, 11, 12, 8)


@pytest.mark.parametrize(
    ("dropped", "clears"),
    [
        # Sat 10/31 20:00 PDT; +24h crosses the Nov 1 fall-back and lands 11/1 19:00 PST.
        (pacific(2026, 10, 31, 20, 0), utc(2026, 11, 2, 8, 0)),
        # Sat 10/31 23:30 PDT -> 11/1 22:30 PST -> midnight PST.
        (pacific(2026, 10, 31, 23, 30), utc(2026, 11, 2, 8, 0)),
        # Fri 10/30 23:30 PDT -> Sat 23:30 PDT -> the midnight that is still PDT (07:00Z).
        (pacific(2026, 10, 30, 23, 30), utc(2026, 11, 1, 7, 0)),
    ],
)
def test_the_fall_back_boundary(dropped, clears):
    assert waiver_clears_at(dropped, None, 24, LA) == clears


@pytest.mark.parametrize(
    ("dropped", "clears"),
    [
        # Sat 3/13 01:00 PST; +24h is Sun 3/14 01:00 PST, an hour before spring-forward. The
        # next midnight is Mon 3/15 00:00 PDT = 07:00Z.
        (pacific(2027, 3, 13, 1, 0), utc(2027, 3, 15, 7, 0)),
        # Fri 3/12 23:00 PST -> Sat 23:00 PST -> Sun 00:00 PST, still standard time (08:00Z).
        (pacific(2027, 3, 12, 23, 0), utc(2027, 3, 14, 8, 0)),
    ],
)
def test_the_spring_forward_boundary(dropped, clears):
    assert waiver_clears_at(dropped, None, 24, LA) == clears


def test_the_window_is_a_parameter_not_a_constant():
    dropped = utc(2026, 10, 13, 22, 54, 39)
    assert waiver_clears_at(dropped, None, 48, LA) == utc(2026, 10, 16, 7, 0)
    # 30h held is under a 48h window: straight to free agency.
    assert waiver_clears_at(dropped, dropped - timedelta(hours=30), 48, LA) is None


def test_naive_datetimes_are_refused():
    with pytest.raises(ValueError):
        waiver_clears_at(datetime(2026, 10, 13, 22, 54), None, 24, LA)


# --- the status, from stored data -----------------------------------------------------------

DROPPED = 3138196  # added by team 6 on 10/12 14:29Z, dropped 10/13 22:54Z (recorded)
DRAFTED = 3945274  # Luka: drafted by team 8, no ADD on record


@pytest.fixture
def league(db, fake_espn):
    sync_league(db, fake_espn(), now=PRESEASON_NOW)
    if db.get(Player, DROPPED) is None:
        db.add(Player(espn_player_id=DROPPED, full_name="Dropped Player", positions=[]))
    db.commit()
    return db


def _on_waivers(db, player_id: int) -> None:
    db.get(Player, player_id).roster_status = "WAIVERS"
    db.commit()


def test_a_player_not_on_waivers(league):
    status = waiver_status(league, SEASON, DRAFTED)
    assert (status.on_waivers, status.clears_at, status.clears_at_known) == (False, None, True)


def test_a_waivers_player_clears_by_the_rule_from_his_drop_and_add(league):
    _on_waivers(league, DROPPED)
    status = waiver_status(league, SEASON, DROPPED)
    assert status.on_waivers
    assert status.clears_at == utc(2026, 10, 15, 7, 0)
    assert status.clears_at_known


def _set_waiver_hours(db, hours: int) -> None:
    row = db.scalar(select(LeagueSettings))
    row.acquisition_settings = {**row.acquisition_settings, "waiverHours": hours}
    db.commit()


def test_waiver_hours_come_from_the_stored_settings(league):
    _on_waivers(league, DROPPED)

    # A 1-hour window: drop 10/13 22:54Z + 1h is 16:54 PDT on the 13th -> clears that midnight.
    _set_waiver_hours(league, 1)
    assert waiver_status(league, SEASON, DROPPED).clears_at == utc(2026, 10, 14, 7, 0)

    # A 48-hour window: he was held 32h, under it, so by the rule he went straight to free
    # agency. ESPN still says WAIVERS, so the time is unknown rather than invented.
    _set_waiver_hours(league, 48)
    status = waiver_status(league, SEASON, DROPPED)
    assert status.on_waivers and status.clears_at is None and not status.clears_at_known


def test_no_stored_settings_means_the_clear_time_is_unknown(league):
    _on_waivers(league, DROPPED)
    row = league.scalar(select(LeagueSettings))
    row.acquisition_settings = None
    league.commit()
    status = waiver_status(league, SEASON, DROPPED)
    assert status.on_waivers and status.clears_at is None and not status.clears_at_known


def test_a_waivers_player_with_no_drop_on_record_is_unknown(league):
    _on_waivers(league, DRAFTED)
    status = waiver_status(league, SEASON, DRAFTED)
    assert (status.on_waivers, status.clears_at, status.clears_at_known) == (True, None, False)


def test_with_no_add_on_record_the_roster_acquisition_date_is_used(league):
    """Drafted, then dropped 10 hours later: held under 24h, so the rule says free agency."""
    acquired = league.scalar(
        select(RosterEntry.acquisition_date).where(RosterEntry.player_id == DRAFTED)
    )
    league.add(
        EspnTransaction(
            id="00000000-test-drop-luka",
            season=SEASON,
            type="FREEAGENT",
            status="EXECUTED",
            team_id=8,
            scoring_period_id=1,
            proposed_at=acquired + timedelta(hours=10),
            items=[{"type": "DROP", "playerId": DRAFTED, "fromTeamId": 8, "toTeamId": 0}],
        )
    )
    league.commit()
    _on_waivers(league, DRAFTED)

    status = waiver_status(league, SEASON, DRAFTED)

    # ESPN says waivers, our rule says free agency: ESPN wins the status, the time is unknown.
    assert status.on_waivers
    assert status.clears_at is None
    assert not status.clears_at_known


def test_a_pending_or_failed_drop_is_not_a_drop(league):
    """Only EXECUTED moves count: the pending waiver claim's drop never happened."""
    pending_drop = 4433192  # dropped in team 8's claim: PENDING row and EXECUTED row recorded
    if league.get(Player, pending_drop) is None:
        league.add(Player(espn_player_id=pending_drop, full_name="Claimed Away", positions=[]))
        league.commit()
    _on_waivers(league, pending_drop)
    status = waiver_status(league, SEASON, pending_drop)
    assert status.clears_at_known
    # Cleared one waiver window after the EXECUTED claim's process time, not the proposal's.
    drop_rows = [
        tx
        for tx in league.scalars(select(EspnTransaction))
        if any(i.get("playerId") == pending_drop and i.get("type") == "DROP" for i in tx.items)
    ]
    assert {tx.status for tx in drop_rows} == {"PENDING", "EXECUTED"}
    executed = next(tx for tx in drop_rows if tx.status == "EXECUTED")
    assert status.clears_at == waiver_clears_at(executed.effective_at, None, 24, LA)


def test_many_players_at_once(league):
    _on_waivers(league, DROPPED)
    statuses = waiver_statuses(league, SEASON, [DROPPED, DRAFTED])
    assert statuses[DROPPED].on_waivers and not statuses[DRAFTED].on_waivers
