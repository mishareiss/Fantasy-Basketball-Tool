"""The league sync: it stores what ESPN gave us, and running it twice changes nothing."""

from datetime import date

import pytest
from sqlalchemy import func, select

from app.db.models import AdpEntry, LeagueSettings, Player, Projection, ScoringRule
from app.espn.ownership import parse_ownership
from app.espn.players import parse_player_pool
from app.espn.statsplits import parse_actuals, parse_projections
from app.espn.sync import (
    ACTUAL_SEASON_KIND,
    SyncSummary,
    sync_actuals,
    sync_adp,
    sync_league,
    sync_players,
    sync_projections,
    sync_scoring_settings,
)
from app.scoring import ScoringEngine, load_scoring_engine, parse_league_settings

LEAGUE_ID = 999999
SEASON = 2027


def _sync(db, msettings_payload, player_pool_payload) -> SyncSummary:
    """One full sync against the recorded fixtures, exactly as `sync_league` sequences it."""
    summary = SyncSummary(league_id=LEAGUE_ID, season=SEASON)
    parsed = parse_league_settings(msettings_payload)
    settings_row = sync_scoring_settings(
        db, espn_league_id=LEAGUE_ID, season=SEASON, parsed=parsed, summary=summary
    )
    sync_players(db, parse_player_pool(player_pool_payload), summary)
    sync_projections(
        db,
        parse_projections(player_pool_payload, SEASON),
        ScoringEngine(settings_row.scoring_rules),
        summary,
    )
    sync_adp(db, parse_ownership(player_pool_payload), summary, season=SEASON)
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
    assert second.projections_created == 0
    assert second.projections_updated == 0
    assert second.projections_unchanged == first.projections_created
    assert second.adp_created == 0
    assert second.adp_updated == 0
    assert second.adp_unchanged == first.adp_created

    assert db.scalar(select(func.count()).select_from(Player)) == len(player_pool_payload)
    assert db.scalar(select(func.count()).select_from(ScoringRule)) == first.scoring_rules
    assert db.scalar(select(func.count()).select_from(LeagueSettings)) == 1
    assert db.scalar(select(func.count()).select_from(Projection)) == first.projections_created
    assert db.scalar(select(func.count()).select_from(AdpEntry)) == first.adp_created


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


def test_stores_a_priced_projection_for_every_player_espn_projects(
    db, msettings_payload, player_pool_payload
):
    summary = _sync(db, msettings_payload, player_pool_payload)

    # The fixture spans owned stars and the unowned tail, so both halves are exercised.
    assert 0 < summary.projections_seen < summary.players_seen
    assert summary.projections_missing == summary.players_seen - summary.projections_seen
    assert summary.projections_created == summary.projections_seen
    assert db.scalar(select(func.count()).select_from(Projection)) == summary.projections_seen


def test_a_player_without_a_projection_gets_no_row(db, msettings_payload, player_pool_payload):
    _sync(db, msettings_payload, player_pool_payload)

    projected_ids = set(db.scalars(select(Projection.player_id)))
    unprojected = [
        entry["player"]["id"]
        for entry in player_pool_payload
        if not any(
            split.get("statSourceId") == 1 and split.get("statSplitTypeId") == 0
            for split in entry["player"].get("stats") or []
        )
    ]

    assert unprojected, "fixture needs at least one player ESPN publishes no projection for"
    assert not projected_ids & set(unprojected)
    # They still exist as players, and still carry ADP — only the projection is absent.
    assert all(db.get(Player, player_id) is not None for player_id in unprojected)


def test_projection_records_the_season_it_is_really_for(db, msettings_payload, player_pool_payload):
    """We sync season 2027; ESPN's newest published projection may still be an earlier one."""
    summary = _sync(db, msettings_payload, player_pool_payload)

    seasons = set(db.scalars(select(Projection.season)))
    assert len(seasons) == 1
    assert summary.projection_season == seasons.pop()


def test_projection_totals_agree_with_espns_own_numbers(db, msettings_payload, player_pool_payload):
    """`source_fantasy_points_total` is ESPN scoring the same line with our coefficients."""
    _sync(db, msettings_payload, player_pool_payload)

    for projection in db.scalars(select(Projection)):
        assert projection.source_fantasy_points_total is not None
        assert abs(projection.fantasy_points_total - projection.source_fantasy_points_total) < 0.01
        assert projection.per_game_basis == "projected_games"
        assert projection.fantasy_points_per_game == (
            projection.fantasy_points_total / projection.projected_games
        )


def test_stores_espn_adp_for_every_player(db, msettings_payload, player_pool_payload):
    summary = _sync(db, msettings_payload, player_pool_payload)

    assert summary.adp_created == len(player_pool_payload)

    sga = db.scalar(select(AdpEntry).where(AdpEntry.player_id == 4278073))
    assert sga is not None
    assert sga.source == "espn"
    assert 0 < sga.adp < 20  # a first-round redraft pick
    assert sga.percent_owned > 90


def test_resync_picks_up_a_moved_projection(db, msettings_payload, player_pool_payload):
    _sync(db, msettings_payload, player_pool_payload)

    bumped = [
        {
            **entry,
            "player": {
                **entry["player"],
                "stats": [
                    {**split, "stats": {**split["stats"], "0": split["stats"]["0"] + 100}}
                    if split.get("statSourceId") == 1 and split.get("statSplitTypeId") == 0
                    else split
                    for split in entry["player"]["stats"]
                ],
            },
        }
        if entry["player"]["id"] == 4278073
        else entry
        for entry in player_pool_payload
    ]
    summary = _sync(db, msettings_payload, bumped)

    assert summary.projections_updated == 1
    assert summary.projections_created == 0
    original = next(
        split["stats"]["0"]
        for entry in player_pool_payload
        if entry["player"]["id"] == 4278073
        for split in entry["player"]["stats"]
        if split.get("statSourceId") == 1 and split.get("statSplitTypeId") == 0
    )
    row = db.scalar(select(Projection).where(Projection.player_id == 4278073))
    assert row.raw_stats["PTS"] == pytest.approx(original + 100)


def test_resync_picks_up_a_moved_adp(db, msettings_payload, player_pool_payload):
    _sync(db, msettings_payload, player_pool_payload)

    drifted = [
        {
            **entry,
            "player": {
                **entry["player"],
                "ownership": {**entry["player"]["ownership"], "averageDraftPosition": 12.0},
            },
        }
        if entry["player"]["id"] == 4278073
        else entry
        for entry in player_pool_payload
    ]
    summary = _sync(db, msettings_payload, drifted)

    assert summary.adp_updated == 1
    assert summary.adp_created == 0
    assert db.scalar(select(AdpEntry).where(AdpEntry.player_id == 4278073)).adp == 12.0


def test_a_changed_coefficient_reprices_projections(db, msettings_payload, player_pool_payload):
    """The scoring rules land before projections, so one sync picks up both."""
    _sync(db, msettings_payload, player_pool_payload)
    before = {row.player_id: row.fantasy_points_total for row in db.scalars(select(Projection))}

    doubled_points = {
        "settings": {
            **msettings_payload["settings"],
            "scoringSettings": {
                **msettings_payload["settings"]["scoringSettings"],
                "scoringItems": [
                    {**item, "points": 6.0} if item["statId"] == 0 else item
                    for item in msettings_payload["settings"]["scoringSettings"]["scoringItems"]
                ],
            },
        }
    }
    summary = _sync(db, doubled_points, player_pool_payload)

    assert summary.projections_updated == summary.projections_seen
    for row in db.scalars(select(Projection)):
        assert row.fantasy_points_total == pytest.approx(
            before[row.player_id] + row.raw_stats["PTS"] * 3
        )


def test_a_resync_does_not_wipe_an_age_espn_never_gave_us(
    db, msettings_payload, player_pool_payload
):
    """The bug this guards against would have silently emptied the age column every sync.

    ESPN publishes no birthdate, so its parser yields None for both fields. When they were
    listed among the columns the sync owns, the next `make sync` after an age sync would write
    that None straight over every birthdate nba.com had given us — and the dynasty curve would
    quietly go back to ranking a 38-year-old like a 22-year-old.
    """
    _sync(db, msettings_payload, player_pool_payload)

    lebron = db.get(Player, 1966)
    lebron.birthdate = date(1984, 12, 30)
    lebron.age = 41
    db.commit()

    summary = _sync(db, msettings_payload, player_pool_payload)

    assert summary.players_updated == 0, "ESPN saw nothing new; nothing should have changed"
    assert db.get(Player, 1966).birthdate == date(1984, 12, 30)
    assert db.get(Player, 1966).age == 41


def test_a_resync_still_updates_the_fields_espn_does_own(
    db, msettings_payload, player_pool_payload
):
    """The other half of the same rule: dropping age from the owned set drops only age."""
    _sync(db, msettings_payload, player_pool_payload)
    player = db.get(Player, 1966)
    player.birthdate = date(1984, 12, 30)
    player.age = 41
    player.injury_status = "OUT"
    db.commit()

    _sync(db, msettings_payload, player_pool_payload)

    refreshed = db.get(Player, 1966)
    assert refreshed.injury_status != "OUT", "ESPN owns injury status and should have reset it"
    assert refreshed.age == 41


# --- last season's actuals: the same table, a different kind ----------------------------------


def _engine(db) -> ScoringEngine:
    return load_scoring_engine(db, LEAGUE_ID, SEASON)


def test_actuals_land_under_their_own_kind_and_are_priced_by_our_scoring(
    db, msettings_payload, player_pool_payload
):
    """The whole point: last year's production, valued under the scoring THIS league runs."""
    summary = _sync(db, msettings_payload, player_pool_payload)
    splits = parse_actuals(player_pool_payload, SEASON)

    sync_actuals(db, splits, _engine(db), summary)
    db.commit()

    rows = list(db.scalars(select(Projection).where(Projection.kind == ACTUAL_SEASON_KIND)))
    assert len(rows) == summary.actuals_created == len(splits)
    assert {row.source for row in rows} == {"espn"}
    # The season it is FOR is a season that was played, never the one being drafted.
    assert {row.season for row in rows} == {summary.actual_season}
    assert summary.actual_season < SEASON

    # Priced, and priced sanely: a completed season is worth real fantasy points per game
    # (this league pays 3 per point, so the numbers are large), and the per-game number is the
    # total over the games he actually played.
    best = max(rows, key=lambda row: row.fantasy_points_per_game)
    assert best.fantasy_points_per_game > 50.0
    assert best.projected_games and best.projected_games > 0
    assert best.fantasy_points_per_game == pytest.approx(
        best.fantasy_points_total / best.projected_games
    )
    assert best.per_game_basis == "projected_games"


def test_an_actual_row_cannot_collide_with_the_projected_row_for_the_same_season(
    db, msettings_payload, player_pool_payload
):
    """Both fixtures land at 2026; only `kind` keeps them apart, so assert that it does."""
    summary = _sync(db, msettings_payload, player_pool_payload)
    sync_actuals(db, parse_actuals(player_pool_payload, SEASON), _engine(db), summary)
    db.commit()

    both = [
        row
        for row in db.scalars(select(Projection).where(Projection.source == "espn"))
        if row.player_id == 3112335  # Jokic, who has both a projection and a season played
    ]
    assert {row.kind for row in both} == {"projected_season", ACTUAL_SEASON_KIND}
    assert len({row.season for row in both}) == 1
    # Two different stat lines, not one written twice.
    projected = next(row for row in both if row.kind == "projected_season")
    actual = next(row for row in both if row.kind == ACTUAL_SEASON_KIND)
    assert projected.raw_stats != actual.raw_stats


def test_actuals_keep_their_own_counters_and_re_running_changes_nothing(
    db, msettings_payload, player_pool_payload
):
    """Two passes over one table: a shared counter would report one of them as the other."""
    summary = _sync(db, msettings_payload, player_pool_payload)
    projections_created = summary.projections_created

    sync_actuals(db, parse_actuals(player_pool_payload, SEASON), _engine(db), summary)
    db.commit()

    assert summary.projections_created == projections_created
    assert summary.projections_seen == len(parse_projections(player_pool_payload, SEASON))
    assert summary.actuals_created > 0
    assert summary.actuals_seen == len(parse_actuals(player_pool_payload, SEASON))
    assert summary.actuals_missing == summary.players_seen - summary.actuals_seen

    again = SyncSummary(league_id=LEAGUE_ID, season=SEASON)
    sync_actuals(db, parse_actuals(player_pool_payload, SEASON), _engine(db), again)
    db.commit()

    assert again.actuals_created == 0
    assert again.actuals_updated == 0
    assert again.actuals_unchanged == summary.actuals_created


def test_the_full_sync_stores_both_splits_from_one_fetch(
    db, msettings_payload, player_pool_payload
):
    """`sync_league` itself, against a stubbed client — the wiring, not the parsers."""

    class FakeClient:
        league_id = LEAGUE_ID
        season = SEASON

        def fetch_settings_view(self):
            return msettings_payload

        def fetch_player_pool_pages(self):
            return player_pool_payload

    summary = sync_league(db, FakeClient())

    assert summary.projections_created == len(parse_projections(player_pool_payload, SEASON))
    assert summary.actuals_created == len(parse_actuals(player_pool_payload, SEASON))
    kinds = set(db.scalars(select(Projection.kind).distinct()))
    assert kinds == {"projected_season", ACTUAL_SEASON_KIND}
