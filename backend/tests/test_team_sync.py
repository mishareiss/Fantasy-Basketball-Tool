"""The in-season half of the league sync: schedule, teams (and which is ours), rosters,
transactions, and the wiring of all of it into `sync_league`."""

import copy
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app.db.models import (
    EspnTransaction,
    FantasyTeam,
    LeagueSettings,
    LineupAssignment,
    NbaGame,
    Player,
    RosterEntry,
)
from app.espn.client import ESPNCredentialsError
from app.espn.league import (
    parse_pro_schedule,
    parse_teams,
    parse_transactions,
    resolve_my_team,
    waiver_entry_keys,
)
from app.espn.players import parse_player_pool
from app.espn.sync import (
    SyncSummary,
    sync_league,
    sync_players,
    sync_rosters,
    sync_schedule,
    sync_teams,
    sync_transactions,
)
from app.team.calendar import eastern_date, period_date
from app.team.store import my_team
from tests.conftest import (
    FAKE_SWID,
    LEAGUE_ID,
    MY_TEAM_ID,
    PRESEASON_NOW,
    SEASON,
    roster_pool_entries,
)


def _summary() -> SyncSummary:
    return SyncSummary(league_id=LEAGUE_ID, season=SEASON)


def _count(db, model) -> int:
    return db.scalar(select(func.count()).select_from(model))


# --- schedule -------------------------------------------------------------------------------


def _game_ids_by_team(payload) -> list[int]:
    return [
        game["id"]
        for team in payload["settings"]["proTeams"]
        for games in team["proGamesByScoringPeriod"].values()
        for game in games
    ]


def test_the_schedule_is_deduped_across_home_and_away(pro_schedule_payload):
    listed = _game_ids_by_team(pro_schedule_payload)
    games = parse_pro_schedule(pro_schedule_payload)

    assert len(listed) > len(set(listed)), "fixture needs a game listed under both teams"
    assert sorted(game.id for game in games) == sorted(set(listed))
    # Team 0 is ESPN's placeholder and has no games; nothing came from it.
    assert all(game.home_pro_team_id and game.away_pro_team_id for game in games)
    assert any(game.start_time_tbd for game in games)


def test_every_recorded_game_falls_on_its_periods_eastern_date(pro_schedule_payload):
    for game in parse_pro_schedule(pro_schedule_payload):
        assert eastern_date(game.starts_at) == period_date(SEASON, game.scoring_period_id)


def test_a_schedule_sync_stores_games_and_resyncing_changes_nothing(db, pro_schedule_payload):
    first = _summary()
    sync_schedule(db, pro_schedule_payload, SEASON, first)
    db.commit()
    second = _summary()
    sync_schedule(db, pro_schedule_payload, SEASON, second)
    db.commit()

    assert first.games_created == first.games_seen == _count(db, NbaGame) > 0
    assert first.schedule_date_mismatches == 0
    assert (second.games_created, second.games_updated, second.games_removed) == (0, 0, 0)
    assert second.games_unchanged == first.games_seen

    stored = db.scalar(select(NbaGame).order_by(NbaGame.starts_at).limit(1))
    assert stored.starts_at.tzinfo is not None
    assert stored.season == SEASON


def _move_game(payload, game_id, new_period, new_date_ms):
    moved = copy.deepcopy(payload)
    for team in moved["settings"]["proTeams"]:
        by_period = team["proGamesByScoringPeriod"]
        for games in list(by_period.values()):
            for game in list(games):
                if game["id"] == game_id:
                    games.remove(game)
                    game.update(scoringPeriodId=new_period, date=new_date_ms)
                    by_period.setdefault(str(new_period), []).append(game)
    return moved


def test_a_postponed_game_that_moves_is_updated_in_place(db, pro_schedule_payload):
    sync_schedule(db, pro_schedule_payload, SEASON, _summary())
    game = parse_pro_schedule(pro_schedule_payload)[0]
    # Two days later, same tip time: Oct 22 is period 3.
    new_ms = int(game.starts_at.timestamp() * 1000) + 2 * 86_400_000
    moved = _move_game(pro_schedule_payload, game.id, game.scoring_period_id + 2, new_ms)

    summary = _summary()
    sync_schedule(db, moved, SEASON, summary)

    assert summary.games_updated == 1
    assert summary.schedule_date_mismatches == 0
    row = db.get(NbaGame, game.id)
    assert row.scoring_period_id == game.scoring_period_id + 2
    assert eastern_date(row.starts_at) == period_date(SEASON, row.scoring_period_id)


def test_a_game_that_vanishes_is_deleted(db, pro_schedule_payload):
    sync_schedule(db, pro_schedule_payload, SEASON, _summary())
    gone = parse_pro_schedule(pro_schedule_payload)[0].id
    trimmed = copy.deepcopy(pro_schedule_payload)
    for team in trimmed["settings"]["proTeams"]:
        for games in team["proGamesByScoringPeriod"].values():
            games[:] = [game for game in games if game["id"] != gone]

    summary = _summary()
    sync_schedule(db, trimmed, SEASON, summary)

    assert summary.games_removed == 1
    assert db.get(NbaGame, gone) is None


def test_an_empty_schedule_deletes_nothing(db, pro_schedule_payload):
    """An empty payload is ESPN failing, not the season being cancelled."""
    sync_schedule(db, pro_schedule_payload, SEASON, _summary())
    before = _count(db, NbaGame)

    summary = _summary()
    sync_schedule(db, {"settings": {"proTeams": []}}, SEASON, summary)

    assert summary.games_removed == 0
    assert _count(db, NbaGame) == before


def test_a_game_on_the_wrong_eastern_date_is_counted_not_fatal(db, pro_schedule_payload):
    game = parse_pro_schedule(pro_schedule_payload)[0]
    # Same game, filed one period later than its date: deliberately wrong.
    wrong = _move_game(
        pro_schedule_payload,
        game.id,
        game.scoring_period_id + 1,
        int(game.starts_at.timestamp() * 1000),
    )

    summary = _summary()
    sync_schedule(db, wrong, SEASON, summary)

    assert summary.schedule_date_mismatches == 1
    assert str(game.id) in summary.schedule_mismatched_games[0]
    assert db.get(NbaGame, game.id) is not None, "a mismatch is reported, and still stored"


# --- teams and which one is ours ------------------------------------------------------------


@pytest.mark.parametrize(
    "swid",
    [
        FAKE_SWID,
        FAKE_SWID.strip("{}"),  # braces lost in a paste
        FAKE_SWID.lower(),
        "  " + FAKE_SWID.strip("{}").lower() + " ",
    ],
)
def test_our_team_is_found_by_swid_however_it_was_pasted(mteam_payload, swid):
    resolution = resolve_my_team(parse_teams(mteam_payload), swid=swid, override=None)
    assert resolution.team_id == MY_TEAM_ID
    assert resolution.warning is None


def test_the_override_wins_over_the_swid(mteam_payload):
    resolution = resolve_my_team(parse_teams(mteam_payload), swid=FAKE_SWID, override=4)
    assert resolution.team_id == 4


def test_an_override_naming_no_team_is_a_warning(mteam_payload):
    resolution = resolve_my_team(parse_teams(mteam_payload), swid=FAKE_SWID, override=3)
    assert resolution.team_id is None
    assert "ESPN_MY_TEAM_ID=3" in resolution.warning


def test_a_sync_marks_exactly_one_team_as_ours(db, mteam_payload):
    summary = _summary()
    sync_teams(db, mteam_payload, SEASON, summary, swid=FAKE_SWID)
    db.commit()

    assert summary.teams_seen == 9
    assert summary.my_team_id == MY_TEAM_ID
    assert summary.my_team_warning is None
    assert summary.my_team_matchup_acquisition_totals == {}
    mine = list(db.scalars(select(FantasyTeam).where(FantasyTeam.is_mine.is_(True))))
    assert [team.team_id for team in mine] == [MY_TEAM_ID]
    assert my_team(db, SEASON).team_id == MY_TEAM_ID
    assert FAKE_SWID in my_team(db, SEASON).owners

    # Switching with the override moves the flag; it never leaves two set.
    sync_teams(db, mteam_payload, SEASON, _summary(), swid=FAKE_SWID, my_team_override=4)
    db.commit()
    mine = list(db.scalars(select(FantasyTeam).where(FantasyTeam.is_mine.is_(True))))
    assert [team.team_id for team in mine] == [4]


def test_no_match_is_a_warning_and_the_sync_carries_on(db, mteam_payload):
    sync_teams(db, mteam_payload, SEASON, _summary(), swid=FAKE_SWID)
    summary = _summary()

    sync_teams(db, mteam_payload, SEASON, summary, swid="{DEADBEEF-0000-0000-0000-000000000000}")
    db.commit()

    assert summary.my_team_id is None
    assert "SWID" in summary.my_team_warning
    assert summary.teams_seen == 9
    assert my_team(db, SEASON) is None, "yesterday's match must not survive a failed one"


# --- rosters --------------------------------------------------------------------------------


@pytest.fixture
def rostered_players(db, mroster_payload):
    sync_players(db, parse_player_pool(roster_pool_entries(mroster_payload)), _summary())
    db.commit()
    return db


def test_rosters_are_stored_for_every_team(rostered_players, mroster_payload):
    db = rostered_players
    summary = _summary()
    sync_rosters(db, mroster_payload, SEASON, summary)
    db.commit()

    expected = sum(len(team["roster"]["entries"]) for team in mroster_payload["teams"])
    assert summary.roster_entries == expected == _count(db, RosterEntry)
    assert summary.roster_scoring_period == mroster_payload["scoringPeriodId"]
    assert set(db.scalars(select(RosterEntry.team_id).distinct())) == {
        team["id"] for team in mroster_payload["teams"]
    }
    entry = db.scalar(select(RosterEntry).where(RosterEntry.team_id == MY_TEAM_ID).limit(1))
    assert entry.acquisition_date.tzinfo is not None
    assert entry.acquisition_type in {"DRAFT", "ADD"}


def test_a_roster_sync_replaces_the_snapshot(rostered_players, mroster_payload):
    db = rostered_players
    sync_rosters(db, mroster_payload, SEASON, _summary())
    dropped = mroster_payload["teams"][0]["roster"]["entries"][0]["playerId"]
    trimmed = copy.deepcopy(mroster_payload)
    trimmed["teams"][0]["roster"]["entries"].pop(0)

    sync_rosters(db, trimmed, SEASON, _summary())
    db.commit()

    assert db.scalar(select(RosterEntry).where(RosterEntry.player_id == dropped)) is None


def test_a_rostered_player_we_dont_know_is_skipped(db, mroster_payload):
    summary = _summary()
    sync_rosters(db, mroster_payload, SEASON, summary)

    assert summary.roster_entries == 0
    assert summary.roster_entries_skipped > 0


def test_eligible_slots_are_stored_raw(rostered_players):
    luka = rostered_players.get(Player, 3945274)
    assert luka.eligible_slots and all(isinstance(slot, int) for slot in luka.eligible_slots)
    assert 0 in luka.eligible_slots  # PG
    assert 12 in luka.eligible_slots  # BE


# --- transactions ---------------------------------------------------------------------------


def test_transactions_skip_the_draft_and_keep_everything_else(transactions_payload):
    parsed = parse_transactions(transactions_payload)
    types = {row.type for row in parsed}
    statuses = {row.status for row in parsed}

    assert "DRAFT" not in types
    assert {"FREEAGENT", "WAIVER", "FUTURE_ROSTER", "ROSTER"} <= types
    assert {"EXECUTED", "PENDING"} <= statuses
    assert any(status.startswith("FAILED") for status in statuses)
    item = parsed[0].items[0]
    assert set(item) == {
        "type",
        "playerId",
        "fromTeamId",
        "toTeamId",
        "fromLineupSlotId",
        "toLineupSlotId",
    }


def test_a_transaction_sync_upserts_and_picks_up_a_status_change(db, transactions_payload):
    first = _summary()
    sync_transactions(db, transactions_payload, SEASON, first)
    db.commit()
    expected = len(parse_transactions(transactions_payload))
    assert first.transactions_created == expected == _count(db, EspnTransaction)

    pending = next(r for r in transactions_payload["transactions"] if r["status"] == "PENDING")
    executed = copy.deepcopy(transactions_payload)
    for row in executed["transactions"]:
        if row["id"] == pending["id"]:
            row["status"] = "EXECUTED"
            row["processDate"] = 1791789132373

    second = _summary()
    sync_transactions(db, executed, SEASON, second)
    db.commit()

    assert second.transactions_created == 0
    assert second.transactions_updated == 1
    assert second.transactions_unchanged == expected - 1
    row = db.get(EspnTransaction, pending["id"])
    assert row.status == "EXECUTED"
    assert row.processed_at == datetime.fromtimestamp(1791789132.373, UTC)


def test_a_free_agent_pickup_takes_effect_when_proposed(db, transactions_payload):
    """ESPN sends no processDate for one; its proposal time is when it happened."""
    sync_transactions(db, transactions_payload, SEASON, _summary())
    pickup = db.scalar(select(EspnTransaction).where(EspnTransaction.type == "FREEAGENT").limit(1))
    assert pickup.processed_at is None
    assert pickup.effective_at == pickup.proposed_at


# --- the waiver probe -----------------------------------------------------------------------


def test_the_waiver_probe_reports_the_keys_of_a_waivers_entry(inseason_pool_payload):
    keys = waiver_entry_keys(inseason_pool_payload)
    assert keys["entry"] == ["id", "onTeamId", "player", "status"]
    assert "fullName" in keys["player"] and keys["player"] == sorted(keys["player"])


def test_the_waiver_probe_is_quiet_with_nobody_on_waivers(player_pool_payload):
    assert waiver_entry_keys(player_pool_payload) is None


# --- the whole sync -------------------------------------------------------------------------


def test_the_full_sync_stores_the_in_season_data(db, fake_espn, mroster_payload):
    summary = sync_league(db, fake_espn(), now=PRESEASON_NOW)

    assert summary.games_seen == _count(db, NbaGame) > 0
    assert summary.schedule_date_mismatches == 0
    assert summary.teams_seen == 9
    assert summary.my_team_id == MY_TEAM_ID
    assert summary.roster_entries == sum(
        len(team["roster"]["entries"]) for team in mroster_payload["teams"]
    )
    assert summary.roster_entries_skipped == 0
    assert summary.transactions_created == _count(db, EspnTransaction) > 0
    assert summary.waiver_entry_keys is None

    settings_row = db.scalar(select(LeagueSettings))
    assert settings_row.acquisition_settings["matchupAcquisitionLimit"] == 7
    assert settings_row.acquisition_settings["waiverHours"] == 24


def test_the_full_sync_reads_rosters_at_todays_period(db, fake_espn):
    client = fake_espn()
    # Wed Nov 4 2026, 10pm Pacific (already Nov 5 in UTC): period 16.
    sync_league(db, client, now=datetime(2026, 11, 5, 6, 0, tzinfo=UTC))
    assert ("mRoster", (16, None)) in client.calls


def test_the_full_sync_never_writes_a_lineup(db, fake_espn):
    """Misha's rule: an edit in the ESPN app must not silently overwrite this site's lineup."""
    sync_league(db, fake_espn(), now=PRESEASON_NOW)
    assert _count(db, LineupAssignment) == 0


def test_rejected_cookies_write_nothing(db, fake_espn):
    client = fake_espn()

    def rejected():
        raise ESPNCredentialsError("ESPN rejected our cookies")

    client.fetch_transactions = rejected  # the LAST fetch: everything before it already ran

    with pytest.raises(ESPNCredentialsError):
        sync_league(db, client, now=PRESEASON_NOW)
    db.rollback()

    for model in (Player, NbaGame, FantasyTeam, RosterEntry, EspnTransaction, LeagueSettings):
        assert _count(db, model) == 0, model.__name__
