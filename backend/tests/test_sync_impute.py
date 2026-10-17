"""Imputation wired into the ESPN projection sync: stored, flagged, priced, switchable.

`synced` (conftest) stores what `sync_league` stores: ESPN's projected splits with DD, TD,
OREB, DREB and PF imputed. These tests compare that against ESPN's raw line.
"""

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.db.models import Player, Projection
from app.espn.players import parse_player_pool
from app.espn.statsplits import parse_projections
from app.espn.sync import (
    SEASON_PROJECTION_KIND,
    SyncSummary,
    espn_projection_splits,
    sync_projections,
)
from app.scoring import load_scoring_engine
from tests.conftest import LEAGUE_ID, SEASON

IMPUTED = {"DD", "TD", "OREB", "DREB", "PF"}


def _rows(db) -> dict[int, Projection]:
    return {
        row.player_id: row
        for row in db.scalars(select(Projection).where(Projection.kind == SEASON_PROJECTION_KIND))
    }


def _id(db, name: str) -> int:
    return db.scalar(select(Player.espn_player_id).where(Player.full_name == name))


def _raw(db, player_pool_payload) -> dict[int, object]:
    return {split.espn_player_id: split for split in parse_projections(player_pool_payload, SEASON)}


def test_imputed_stats_are_stored_and_flagged(db, synced, player_pool_payload):
    rows = _rows(db)
    raw = _raw(db, player_pool_payload)
    assert rows and len(rows) == len(raw)
    for pid, row in rows.items():
        assert set(row.imputed_stats) == IMPUTED
        # ESPN's own stats are stored untouched; ours are added beside them.
        assert {k: row.raw_stats[k] for k in raw[pid].stats} == raw[pid].stats
        assert {k: row.per_game_stats[k] for k in raw[pid].average_stats} == raw[pid].average_stats
        for name in IMPUTED:
            # Season totals are per-game x projected games.
            assert row.raw_stats[name] == pytest.approx(
                row.per_game_stats[name] * row.projected_games
            )
        assert 0.0 <= row.per_game_stats["TD"] <= row.per_game_stats["DD"] <= 1.0
        assert row.per_game_stats["OREB"] + row.per_game_stats["DREB"] == pytest.approx(
            row.per_game_stats["REB"]
        )


def test_a_big_mans_fpts_per_game_rises_and_espns_total_is_untouched(
    db, synced, player_pool_payload
):
    rows = _rows(db)
    raw = _raw(db, player_pool_payload)
    engine = load_scoring_engine(db, LEAGUE_ID, SEASON)
    for name in ("Nikola Jokic", "Karl-Anthony Towns", "Victor Wembanyama"):
        pid = _id(db, name)
        row = rows[pid]
        before = engine.score(raw[pid].stats) / raw[pid].projected_games
        # ESPN's applied total equals our un-imputed score, and stays stored as ESPN's.
        assert row.source_fantasy_points_total == raw[pid].espn_applied_total
        assert row.source_fantasy_points_total == pytest.approx(engine.score(raw[pid].stats))
        assert row.fantasy_points_per_game > before + 4.0, name
    jokic = rows[_id(db, "Nikola Jokic")]
    assert jokic.per_game_stats["DD"] > 0.6 and jokic.per_game_stats["TD"] > 0.2
    assert jokic.imputed_stats["DD"] == "blend"


def test_the_toggle_off_reproduces_the_old_numbers_exactly(db, synced, player_pool_payload):
    settings = get_settings()
    records = parse_player_pool(player_pool_payload)
    settings.impute_espn_projections = False
    try:
        splits = espn_projection_splits(player_pool_payload, SEASON, records)
    finally:
        settings.impute_espn_projections = True
    assert splits == parse_projections(player_pool_payload, SEASON)

    engine = load_scoring_engine(db, LEAGUE_ID, SEASON)
    sync_projections(db, splits, engine, SyncSummary(league_id=LEAGUE_ID, season=SEASON))
    db.commit()
    for pid, row in _rows(db).items():
        split = next(s for s in splits if s.espn_player_id == pid)
        assert row.imputed_stats is None
        assert row.raw_stats == split.stats
        assert row.fantasy_points_total == engine.score(split.stats)
        assert row.fantasy_points_total == pytest.approx(split.espn_applied_total)


def test_an_imputed_resync_is_idempotent(db, synced, player_pool_payload):
    before = {pid: (row.raw_stats, row.as_of) for pid, row in _rows(db).items()}
    records = parse_player_pool(player_pool_payload)
    summary = SyncSummary(league_id=LEAGUE_ID, season=SEASON)
    sync_projections(
        db,
        espn_projection_splits(player_pool_payload, SEASON, records),
        load_scoring_engine(db, LEAGUE_ID, SEASON),
        summary,
    )
    db.commit()
    assert summary.projections_unchanged == summary.projections_seen == len(before)
    assert summary.projections_created == summary.projections_updated == 0
    assert {pid: (row.raw_stats, row.as_of) for pid, row in _rows(db).items()} == before


def test_actual_rows_are_never_imputed(db, with_actuals):
    actuals = db.scalars(select(Projection).where(Projection.kind == "actual_season")).all()
    assert actuals and all(row.imputed_stats is None for row in actuals)
