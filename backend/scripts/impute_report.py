"""`make impute-report`: what imputing DD/TD/OREB/DREB/PF does to ESPN's projections.

    uv run python -m scripts.impute_report [--season 2027] [--top 150] [--movers 15]
    uv run python -m scripts.impute_report --calibrate

Read-only. Works from the stored rows whether or not the last sync imputed: "before" is ESPN's
line as sent (any stats a previous sync imputed are stripped), "after" is that line imputed
under the current IMPUTE_* settings, both priced by the league's stored scoring. Prints the
top `--top` players by imputed FPTS/G, sorted by the biggest change, then the biggest rank
movers each way, then how the Team tab's `projected` basis sources the free-agent pool.

`--calibrate` (`make impute-calibrate`) refits IMPUTE_SD_K on the stored last-season actuals
instead and prints the fit; copy the k into `app.projections.impute` and the settings default.
"""

import argparse
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

# Before app.team.views: importing that module first trips the app.api <-> app.team cycle.
import app.api  # noqa: F401
from app.config import get_settings
from app.db.models import Player, Projection, RosterEntry
from app.db.session import SessionLocal
from app.espn.sync import ACTUAL_SEASON_KIND, ESPN_SOURCE, SEASON_PROJECTION_KIND
from app.projections.impute import (
    CalibrationSample,
    LastSeason,
    fit_sd_k,
    impute_line,
    league_baselines,
)
from app.scoring import load_scoring_engine_for_season
from app.team.views import POOL_STATUSES, projected_lines

CALIBRATION_MIN_GP = 20


@dataclass(frozen=True)
class Row:
    name: str
    position: str | None
    before: float
    after: float
    imputed: dict[str, float]
    rank_before: int = 0
    rank_after: int = 0


def _espn_line(row: Projection) -> dict[str, float]:
    """The per-game line ESPN sent: the stored one minus anything we imputed."""
    imputed = row.imputed_stats or {}
    return {k: float(v) for k, v in (row.per_game_stats or {}).items() if k not in imputed}


def _actuals(db: Session) -> dict[int, LastSeason]:
    """Each player's newest last-season actual line, as the imputation reads it."""
    out: dict[int, LastSeason] = {}
    for row in db.scalars(
        select(Projection)
        .where(Projection.source == ESPN_SOURCE, Projection.kind == ACTUAL_SEASON_KIND)
        .order_by(Projection.season)
    ):
        if row.projected_games and row.per_game_stats:
            out[row.player_id] = LastSeason(row.per_game_stats, row.projected_games)
    return out


def build_rows(db: Session, season: int) -> list[Row]:
    engine = load_scoring_engine_for_season(db, get_settings().espn_season or season)
    params = get_settings().impute_params()
    players = {p.espn_player_id: p for p in db.scalars(select(Player))}
    actuals = _actuals(db)
    baselines = league_baselines(
        (players[pid].primary_position if pid in players else None, last)
        for pid, last in actuals.items()
    )
    rows: list[Row] = []
    for proj in db.scalars(
        select(Projection).where(
            Projection.source == ESPN_SOURCE,
            Projection.kind == SEASON_PROJECTION_KIND,
            Projection.season == season,
        )
    ):
        player = players.get(proj.player_id)
        line = _espn_line(proj)
        if not line:
            continue
        position = player.primary_position if player else None
        imputed = impute_line(
            line,
            last_season=actuals.get(proj.player_id),
            position=position,
            baselines=baselines,
            params=params,
        )
        rows.append(
            Row(
                name=player.full_name if player else str(proj.player_id),
                position=position,
                before=engine.score(line),
                after=engine.score(imputed.per_game),
                imputed={k: imputed.per_game[k] for k in imputed.imputed},
            )
        )

    def ranks(key) -> dict[str, int]:
        return {r.name: i for i, r in enumerate(sorted(rows, key=key), start=1)}

    before = ranks(lambda r: (-r.before, r.name))
    after = ranks(lambda r: (-r.after, r.name))
    return [
        Row(**{**r.__dict__, "rank_before": before[r.name], "rank_after": after[r.name]})
        for r in rows
    ]


def _fmt(row: Row) -> str:
    est = " ".join(
        f"{k} {row.imputed[k]:.2f}" for k in ("DD", "TD", "OREB", "PF") if k in row.imputed
    )
    return (
        f"{row.name[:24]:<24} {row.position or '-':<3} "
        f"{row.before:6.1f} -> {row.after:6.1f} ({row.after - row.before:+5.1f})  "
        f"rank {row.rank_before:>3} -> {row.rank_after:>3} ({row.rank_before - row.rank_after:+4d})"
        f"  {est}"
    )


def report(db: Session, season: int, top: int, movers: int) -> None:
    rows = build_rows(db, season)
    if not rows:
        print(f"No ESPN projections stored for season {season}.")
        return
    pool = sorted(rows, key=lambda r: r.rank_after)[:top]
    print(f"Season {season}: {len(rows)} ESPN projections; top {len(pool)} by imputed FPTS/G")
    print("\nBy biggest FPTS/G change:")
    for row in sorted(pool, key=lambda r: (-(r.after - r.before), r.name)):
        print("  " + _fmt(row))
    by_move = sorted(pool, key=lambda r: (r.rank_after - r.rank_before, r.name))
    print(f"\nTop {movers} risers (rank):")
    for row in by_move[:movers]:
        print("  " + _fmt(row))
    print(f"\nTop {movers} fallers (rank):")
    for row in list(reversed(by_move))[:movers]:
        print("  " + _fmt(row))


def free_agent_sources(db: Session, season: int) -> None:
    """How the Team tab's `projected` basis sources each free agent / waiver player."""
    rostered = select(RosterEntry.player_id).where(RosterEntry.season == season)
    ids = list(
        db.scalars(
            select(Player.espn_player_id).where(
                Player.roster_status.in_(POOL_STATUSES), Player.espn_player_id.not_in(rostered)
            )
        )
    )
    lines = projected_lines(db, season, ids)
    counts: dict[str, int] = {}
    for line in lines.values():
        counts[line.projection_source] = counts.get(line.projection_source, 0) + 1
    top = sorted(lines.items(), key=lambda item: -item[1].fantasy_ppg)[:40]
    top_counts: dict[str, int] = {}
    for _, line in top:
        top_counts[line.projection_source] = top_counts.get(line.projection_source, 0) + 1
    print(f"\nFree agents ({len(ids)}, {len(lines)} with a projected line), by projected source:")
    print(f"  all: {dict(sorted(counts.items()))}")
    print(f"  top 40 by FPTS/G: {dict(sorted(top_counts.items()))}")


def calibrate(db: Session) -> None:
    samples = [
        CalibrationSample(
            row.per_game_stats, row.per_game_stats.get("DD", 0.0), row.projected_games
        )
        for row in db.scalars(
            select(Projection).where(
                Projection.source == ESPN_SOURCE, Projection.kind == ACTUAL_SEASON_KIND
            )
        )
        if (row.projected_games or 0) >= CALIBRATION_MIN_GP and row.per_game_stats
    ]
    fit = fit_sd_k(samples)
    print(
        f"IMPUTE_SD_K fit over {fit.players} players with >= {CALIBRATION_MIN_GP} GP: "
        f"k = {fit.k:.3f}, weighted RMSE {fit.rmse:.3f} DD/game against a spread of "
        f"{fit.spread:.3f} (R^2 {fit.r_squared:.2f})"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", type=int, default=None, help="default: ESPN_SEASON")
    parser.add_argument("--top", type=int, default=150)
    parser.add_argument("--movers", type=int, default=15)
    parser.add_argument("--calibrate", action="store_true", help="refit IMPUTE_SD_K instead")
    args = parser.parse_args()
    season = args.season or get_settings().espn_season
    db = SessionLocal()
    try:
        if args.calibrate:
            calibrate(db)
        else:
            report(db, season, args.top, args.movers)
            free_agent_sources(db, season)
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
