"""This season's actual lines (season so far, last 7 / 15 / 30 days), and the proof that they
stay off every board."""

import copy

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import Projection
from app.db.session import get_db
from app.espn.players import parse_player_pool
from app.espn.statsplits import (
    parse_actuals,
    parse_current_actuals,
    select_current_split,
)
from app.espn.sync import (
    ACTUAL_CURRENT_KIND,
    ACTUAL_LAST_7_KIND,
    ACTUAL_LAST_15_KIND,
    ACTUAL_LAST_30_KIND,
    ACTUAL_SEASON_KIND,
    CURRENT_ACTUAL_KINDS,
    SEASON_PROJECTION_KIND,
    SyncSummary,
    espn_projection_splits,
    sync_actuals,
    sync_current_actuals,
    sync_league,
    sync_projections,
)
from app.main import app
from app.scoring import load_scoring_engine
from app.scoring.projections import score_projection
from tests.conftest import LEAGUE_ID, PRESEASON_NOW, SEASON

SGA, WEMBY, JOKIC, GIANNIS, EDWARDS = 4278073, 5104157, 3112335, 3032977, 4594268


def _rows(db, kind: str) -> dict[int, Projection]:
    return {
        row.player_id: row for row in db.scalars(select(Projection).where(Projection.kind == kind))
    }


def _current(db, synced, entries) -> SyncSummary:
    summary = SyncSummary(league_id=LEAGUE_ID, season=SEASON)
    summary.players_seen = synced.players_seen
    sync_current_actuals(
        db, entries, load_scoring_engine(db, LEAGUE_ID, SEASON), summary, season=SEASON
    )
    db.commit()
    return summary


# --- selection ------------------------------------------------------------------------------


def test_only_this_exact_season_is_selected(inseason_pool_payload):
    jokic = next(e for e in inseason_pool_payload if e["player"]["id"] == JOKIC)["player"]
    # He has a full 2026 actual; the old selector would fall back to it. This one must not.
    assert any(s["seasonId"] == 2026 and s["statSourceId"] == 0 for s in jokic["stats"])
    assert select_current_split(jokic, SEASON, 0) is None
    ids = {s.espn_player_id for s in parse_current_actuals(inseason_pool_payload, SEASON, 0)}
    assert JOKIC not in ids


def test_a_split_with_zero_games_is_skipped(inseason_pool_payload):
    ids = {s.espn_player_id for s in parse_current_actuals(inseason_pool_payload, SEASON, 0)}
    assert GIANNIS not in ids
    assert {SGA, WEMBY, EDWARDS} <= ids


def test_the_recorded_preseason_pool_has_no_current_season_lines(player_pool_payload):
    for split_type in range(4):
        assert parse_current_actuals(player_pool_payload, SEASON, split_type) == []


# --- storage --------------------------------------------------------------------------------


def test_every_window_is_stored_under_its_own_kind_and_priced_by_our_scoring(
    db, synced, inseason_pool_payload
):
    summary = _current(db, synced, inseason_pool_payload)

    assert set(_rows(db, ACTUAL_CURRENT_KIND)) == {SGA, WEMBY, EDWARDS}
    assert set(_rows(db, ACTUAL_LAST_7_KIND)) == {SGA}
    assert set(_rows(db, ACTUAL_LAST_15_KIND)) == {SGA}
    assert set(_rows(db, ACTUAL_LAST_30_KIND)) == {SGA, WEMBY}
    assert summary.actuals_available
    assert summary.current_actuals[ACTUAL_CURRENT_KIND] == {
        "seen": 3,
        "created": 3,
        "updated": 0,
        "unchanged": 0,
        "removed": 0,
    }

    engine = load_scoring_engine(db, LEAGUE_ID, SEASON)
    row = _rows(db, ACTUAL_LAST_7_KIND)[SGA]
    assert row.season == SEASON and row.source == "espn"
    assert row.projected_games == 3
    expected = score_projection(
        engine, row.raw_stats, per_game_stats=row.per_game_stats, projected_games=3
    )
    assert row.fantasy_points_total == pytest.approx(expected.fantasy_points_total)
    assert row.fantasy_points_per_game == pytest.approx(row.fantasy_points_total / 3)
    assert row.fantasy_points_per_game > 30  # a real line, priced at this league's 3/point


def test_a_resync_changes_nothing(db, synced, inseason_pool_payload):
    _current(db, synced, inseason_pool_payload)
    again = _current(db, synced, inseason_pool_payload)
    for kind in CURRENT_ACTUAL_KINDS:
        counts = again.current_actuals[kind]
        assert counts["created"] == counts["updated"] == counts["removed"] == 0
        assert counts["unchanged"] == counts["seen"]


def test_a_window_a_player_dropped_out_of_is_removed(db, synced, inseason_pool_payload):
    _current(db, synced, inseason_pool_payload)
    last_season = _rows(db, ACTUAL_SEASON_KIND)

    # A week later SGA has not played in 7 days: ESPN's L7 split for him is empty.
    quiet = copy.deepcopy(inseason_pool_payload)
    sga = next(e for e in quiet if e["player"]["id"] == SGA)
    for split in sga["player"]["stats"]:
        if split["id"] == "012027":
            split["stats"] = {}
    summary = _current(db, synced, quiet)

    assert SGA not in _rows(db, ACTUAL_LAST_7_KIND)
    assert summary.current_actuals[ACTUAL_LAST_7_KIND]["removed"] == 1
    assert SGA in _rows(db, ACTUAL_LAST_15_KIND), "only the window he left is touched"
    assert summary.current_actuals[ACTUAL_LAST_15_KIND]["removed"] == 0
    assert _rows(db, ACTUAL_SEASON_KIND) == last_season


def test_an_empty_pool_removes_nothing(db, synced, inseason_pool_payload):
    _current(db, synced, inseason_pool_payload)
    summary = _current(db, synced, [])
    assert set(_rows(db, ACTUAL_CURRENT_KIND)) == {SGA, WEMBY, EDWARDS}
    assert summary.current_actuals[ACTUAL_CURRENT_KIND]["removed"] == 0


def _snapshot(db, kind: str) -> dict[tuple[int, int], tuple]:
    columns = (
        "source",
        "raw_stats",
        "per_game_stats",
        "projected_games",
        "fantasy_points_total",
        "fantasy_points_per_game",
        "per_game_basis",
        "source_fantasy_points_total",
        "imputed_stats",
    )
    return {
        (row.player_id, row.season): tuple(getattr(row, c) for c in columns)
        for row in db.scalars(select(Projection).where(Projection.kind == kind))
    }


def test_projections_and_last_season_are_unchanged_by_the_full_sync(
    db, synced, msettings_payload, player_pool_payload, fake_espn
):
    """Row for row: what the projection + last-season writers store, against what the full
    `sync_league` stores now, in a second database.

    The projection writer is fed the IMPUTED splits (task 34) over the same pool the fake
    client serves, since the position baselines are computed from the whole pool."""
    engine = load_scoring_engine(db, LEAGUE_ID, SEASON)
    summary = SyncSummary(league_id=LEAGUE_ID, season=SEASON)
    pool = fake_espn().pool
    splits = espn_projection_splits(pool, SEASON, parse_player_pool(pool))
    sync_projections(db, splits, engine, summary)
    sync_actuals(db, parse_actuals(player_pool_payload, SEASON), engine, summary)
    db.commit()
    before = {kind: _snapshot(db, kind) for kind in (SEASON_PROJECTION_KIND, ACTUAL_SEASON_KIND)}

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db.base import Base

    other_engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(other_engine)
    other = sessionmaker(bind=other_engine, expire_on_commit=False)()
    try:
        sync_league(other, fake_espn(), now=PRESEASON_NOW)
        after = {
            kind: _snapshot(other, kind) for kind in (SEASON_PROJECTION_KIND, ACTUAL_SEASON_KIND)
        }
    finally:
        other.close()
        other_engine.dispose()

    assert before[SEASON_PROJECTION_KIND] and before[ACTUAL_SEASON_KIND]
    assert after == before


# --- isolation: the boards do not move ------------------------------------------------------


@pytest.fixture
def api(db):
    app.dependency_overrides[get_db] = lambda: db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


BOARD_READS = (
    "/players/board?limit=1000",
    "/players/board?limit=1000&horizon=dynasty",
    "/players/board?limit=1000&horizon=current_year",
    "/board/consensus?horizon=dynasty&limit=1000",
    "/board/consensus?horizon=current_year&limit=1000",
    "/sources?horizon=dynasty",
    "/master/board",
    "/players/unresolved?need=adp",
    "/players/unresolved?need=age",
)


def test_the_boards_are_identical_with_and_without_current_actuals(
    api, db, synced, with_actuals, inseason_pool_payload
):
    """Rankings read `projected_season` by name; nothing ranks on a hot week."""
    # Read once first: the master board seeds itself on its first read and says so
    # (`seeded: true`), which would be a difference between reads, not between databases.
    assert all(api.get(path).status_code == 200 for path in BOARD_READS)
    before = {path: api.get(path).json() for path in BOARD_READS}

    summary = _current(db, synced, inseason_pool_payload)
    assert summary.actuals_available

    after = {path: api.get(path).json() for path in BOARD_READS}
    assert after == before
