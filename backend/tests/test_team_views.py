"""The Team tab's shared DB reads: the per-day games index and the per-basis stat lines."""

from datetime import UTC, datetime

from app.db.models import NbaGame, Player, Projection
from app.espn.sync import ACTUAL_CURRENT_KIND, ACTUAL_LAST_30_KIND
from app.team import views
from app.team.views import games_index, stat_columns, stat_lines
from tests.conftest import SEASON


def _game(game_id, sp, home, away, hour):
    return NbaGame(
        id=game_id,
        season=SEASON,
        scoring_period_id=sp,
        starts_at=datetime(2026, 10, 20 + sp, hour, 0, tzinfo=UTC),
        home_pro_team_id=home,
        away_pro_team_id=away,
        start_time_tbd=False,
    )


def test_games_index_has_both_sides_of_every_game_for_the_requested_days(db):
    db.add_all([_game(1, 1, 13, 9, 2), _game(2, 2, 1, 13, 23), _game(3, 5, 13, 5, 2)])
    db.flush()

    index = games_index(db, SEASON, [1, 2])

    assert set(index) == {(1, 13), (1, 9), (2, 1), (2, 13)}
    home, away = index[(1, 13)], index[(1, 9)]
    assert (home.opponent, home.is_home, home.game_id) == ("GSW", True, 1)
    assert (away.opponent, away.is_home, away.opponent_pro_team_id) == ("LAL", False, 13)
    assert games_index(db, SEASON, []) == {}
    assert games_index(db, SEASON + 1, [1, 2]) == {}


def test_two_games_for_one_team_on_one_day_keeps_the_earlier(db, monkeypatch):
    db.add_all([_game(10, 1, 13, 9, 23), _game(11, 1, 1, 13, 2)])
    db.flush()
    # A stub rather than caplog: the migration tests' logging.config call disables existing
    # loggers, so whether caplog sees this one depends on test order.
    warned: list[str] = []
    monkeypatch.setattr(views.log, "warning", lambda msg, *args: warned.append(msg % args))

    index = games_index(db, SEASON, [1])

    assert index[(1, 13)].game_id == 11  # 02:00Z beats 23:00Z
    assert index[(1, 9)].game_id == 10
    assert len(warned) == 1 and "two games" in warned[0]


def _row(pid, kind, season, ppg, **per_game):
    return Projection(
        player_id=pid,
        source="espn",
        kind=kind,
        season=season,
        raw_stats={},
        per_game_stats=per_game,
        projected_games=4.0,
        fantasy_points_total=ppg * 4,
        fantasy_points_per_game=ppg,
        per_game_basis="projected_games",
        as_of=datetime(2026, 10, 15, tzinfo=UTC),
    )


def test_stat_lines_per_basis(db):
    db.add_all([Player(espn_player_id=pid, full_name=f"P{pid}", positions=[]) for pid in (1, 2)])
    db.flush()
    db.add_all(
        [
            _row(1, "projected_season", SEASON - 1, 10.0, PTS=5.0),
            _row(1, "projected_season", SEASON, 12.0, PTS=6.0, MIN=30.0),
            _row(1, ACTUAL_CURRENT_KIND, SEASON, 15.0, PTS=8.0, FGA=12.0),
            # An actual from an older season is not this season's line.
            _row(2, ACTUAL_CURRENT_KIND, SEASON - 1, 40.0, PTS=20.0),
            _row(2, ACTUAL_LAST_30_KIND, SEASON, 22.0, DD=1.0),
        ]
    )
    db.flush()

    projected = stat_lines(db, SEASON, [1, 2], "projected")
    assert set(projected) == {1}
    assert (projected[1].season, projected[1].fantasy_ppg) == (SEASON, 12.0)

    season = stat_lines(db, SEASON, [1, 2], "season")
    assert set(season) == {1}
    assert season[1].per_game == {"PTS": 8.0, "FGA": 12.0}
    assert stat_lines(db, SEASON, [1, 2], "l7") == {}
    assert set(stat_lines(db, SEASON, [1, 2], "l30")) == {2}
    assert stat_lines(db, SEASON, [], "projected") == {}

    # Display columns: DISPLAY_STATS order, only what some line has (FGA isn't displayed).
    assert stat_columns([projected[1], season[1], None]) == ["MIN", "PTS"]
