"""The Team tab's `projected` basis never ranks on a stale projection silently.

Fresh (this season's ESPN projection) -> `espn`; stale with a usable last season ->
`last_season_actual`; stale without one -> `stale_projection`. The recorded pool's ESPN
projections are all for 2026 (the season before the fixtures' 2027), so every recorded player
starts out stale.
"""

from sqlalchemy import delete, select

from app.db.models import Projection
from app.espn.sync import ACTUAL_SEASON_KIND, SEASON_PROJECTION_KIND
from tests.conftest import SEASON
from tests.test_api_team_views import (  # noqa: F401  (fixtures)
    ALPHA,
    LUKA,
    _fa,
    _projection,
    api,
    client_stub,
    league,
    pool,
)


def _card_projected(api, pid):
    return api.get(f"/team/player/{pid}", params={"from_sp": 1}).json()["stats"]["projected"]


def _row(db, pid, kind):
    """His newest row of `kind` (the sync stores one season of each for the recorded pool)."""
    return db.scalar(
        select(Projection)
        .where(Projection.player_id == pid, Projection.kind == kind)
        .order_by(Projection.season.desc())
        .limit(1)
    )


def test_a_fresh_projection_is_espn_and_carries_its_imputed_stats(league, db):
    _projection(db, LUKA, season=SEASON, ppg=77.0)
    db.flush()
    fresh = db.scalar(
        select(Projection).where(
            Projection.player_id == LUKA,
            Projection.kind == SEASON_PROJECTION_KIND,
            Projection.season == SEASON,
        )
    )
    fresh.imputed_stats = {"DD": "blend", "PF": "own_rate"}
    db.commit()

    line = _card_projected(league, LUKA)
    assert line["projection_source"] == "espn"
    assert line["projection_season"] == SEASON
    assert line["fantasy_ppg"] == 77.0
    assert line["imputed"] == ["DD", "PF"]


def test_a_stale_projection_with_last_season_becomes_last_seasons_actual(league, db):
    stale = _row(db, LUKA, SEASON_PROJECTION_KIND)
    actual = _row(db, LUKA, ACTUAL_SEASON_KIND)
    assert stale.season < SEASON and actual.projected_games >= 15

    line = _card_projected(league, LUKA)
    assert line["projection_source"] == "last_season_actual"
    assert line["projection_season"] == actual.season
    assert line["fantasy_ppg"] == actual.fantasy_points_per_game
    assert line["games"] == actual.projected_games
    # An actual line's DD etc. are what happened, not estimates.
    assert line["imputed"] == []
    assert line["per_game"]["DD"] == actual.per_game_stats["DD"]


def test_a_stale_projection_without_a_usable_last_season_is_labelled_stale(league, db):
    stale = _row(db, LUKA, SEASON_PROJECTION_KIND)
    actual = _row(db, LUKA, ACTUAL_SEASON_KIND)
    actual.projected_games = 14.0  # under IMPUTE_MIN_GP
    db.commit()
    line = _card_projected(league, LUKA)
    assert line["projection_source"] == "stale_projection"
    assert line["projection_season"] == stale.season
    assert line["fantasy_ppg"] == stale.fantasy_points_per_game
    assert line["imputed"] == sorted(stale.imputed_stats)

    db.execute(delete(Projection).where(Projection.id == actual.id))
    db.commit()
    assert _card_projected(league, LUKA)["projection_source"] == "stale_projection"


def test_the_day_view_and_the_waiver_list_say_where_projected_came_from(pool, db):
    day = pool.get("/team/day", params={"sp": 1}).json()
    luka = next(r["player"] for r in day["slots"] if r["player"] and r["player"]["id"] == LUKA)
    assert luka["stats"]["projection_source"] == "last_season_actual"

    body = _fa(pool)
    alpha = next(row for row in body["players"] if row["id"] == ALPHA)
    assert alpha["stats"]["projection_source"] == "espn"
    assert alpha["stats"]["projection_season"] == SEASON
