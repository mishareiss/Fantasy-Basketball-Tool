"""The league sync: it stores what ESPN gave us, and running it twice changes nothing."""

from sqlalchemy import func, select

from app.db.models import LeagueSettings, Player, ScoringRule
from app.espn.players import parse_player_pool
from app.espn.sync import SyncSummary, sync_players, sync_scoring_settings
from app.scoring import load_scoring_engine, parse_league_settings

LEAGUE_ID = 999999
SEASON = 2027


def _sync(db, msettings_payload, player_pool_payload) -> SyncSummary:
    """One full sync against the recorded fixtures, exactly as `sync_league` sequences it."""
    summary = SyncSummary(league_id=LEAGUE_ID, season=SEASON)
    parsed = parse_league_settings(msettings_payload)
    sync_scoring_settings(
        db, espn_league_id=LEAGUE_ID, season=SEASON, parsed=parsed, summary=summary
    )
    sync_players(db, parse_player_pool(player_pool_payload), summary)
    db.commit()
    return summary


def test_first_sync_stores_settings_and_players(db, msettings_payload, player_pool_payload):
    summary = _sync(db, msettings_payload, player_pool_payload)

    settings_row = db.scalar(select(LeagueSettings))
    assert settings_row is not None
    assert settings_row.scoring_type == "H2H_POINTS"
    assert len(settings_row.scoring_rules) == summary.scoring_rules == 17

    assert summary.players_created == len(player_pool_payload)
    assert db.scalar(select(func.count()).select_from(Player)) == len(player_pool_payload)


def test_resync_is_idempotent(db, msettings_payload, player_pool_payload):
    first = _sync(db, msettings_payload, player_pool_payload)
    second = _sync(db, msettings_payload, player_pool_payload)

    assert second.players_created == 0
    assert second.players_updated == 0
    assert second.players_unchanged == first.players_created
    assert second.scoring_rules_created == 0
    assert second.scoring_rules_updated == 0

    assert db.scalar(select(func.count()).select_from(Player)) == len(player_pool_payload)
    assert db.scalar(select(func.count()).select_from(ScoringRule)) == first.scoring_rules
    assert db.scalar(select(func.count()).select_from(LeagueSettings)) == 1


def test_resync_picks_up_a_changed_coefficient(db, msettings_payload, player_pool_payload):
    _sync(db, msettings_payload, player_pool_payload)

    # ESPN mid-season scoring change: points are now worth 4 instead of 3.
    changed = {
        "settings": {
            **msettings_payload["settings"],
            "scoringSettings": {
                **msettings_payload["settings"]["scoringSettings"],
                "scoringItems": [
                    {**item, "points": 4.0} if item["statId"] == 0 else item
                    for item in msettings_payload["settings"]["scoringSettings"]["scoringItems"]
                ],
            },
        }
    }
    summary = _sync(db, changed, player_pool_payload)

    assert summary.scoring_rules_updated == 1
    assert load_scoring_engine(db, LEAGUE_ID, SEASON).as_points_map()["PTS"] == 4.0


def test_resync_drops_a_stat_that_stopped_being_scored(db, msettings_payload, player_pool_payload):
    first = _sync(db, msettings_payload, player_pool_payload)

    items = msettings_payload["settings"]["scoringSettings"]["scoringItems"]
    trimmed = {
        "settings": {
            **msettings_payload["settings"],
            "scoringSettings": {
                **msettings_payload["settings"]["scoringSettings"],
                "scoringItems": [item for item in items if item["statId"] != 39],  # QD
            },
        }
    }
    summary = _sync(db, trimmed, player_pool_payload)

    assert summary.scoring_rules_removed == 1
    assert db.scalar(select(func.count()).select_from(ScoringRule)) == first.scoring_rules - 1
    assert "QD" not in load_scoring_engine(db, LEAGUE_ID, SEASON).as_points_map()


def test_resync_updates_a_player_who_changed_teams(db, msettings_payload, player_pool_payload):
    _sync(db, msettings_payload, player_pool_payload)

    traded = [
        {**entry, "player": {**entry["player"], "proTeamId": 2}}
        if entry["player"]["fullName"] == "Shai Gilgeous-Alexander"
        else entry
        for entry in player_pool_payload
    ]
    summary = _sync(db, msettings_payload, traded)

    assert summary.players_updated == 1
    assert summary.players_created == 0
    assert db.get(Player, 4278073).nba_team == "BOS"


def test_stored_rules_score_a_stat_line(db, msettings_payload, player_pool_payload):
    """The round trip that matters: ESPN -> database -> fantasy points."""
    _sync(db, msettings_payload, player_pool_payload)

    engine = load_scoring_engine(db, LEAGUE_ID, SEASON)

    assert len(engine) == 17
    assert engine.score({"PTS": 20, "REB": 10, "AST": 5, "DD": 1}) == 20 * 3 + 10 * 4 + 5 * 4 + 10
