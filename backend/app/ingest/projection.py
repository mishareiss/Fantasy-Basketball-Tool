"""The `projection` import kind: someone else's stat projections, priced under *our* scoring.

The second kind, and the one the board was built for. ESPN publishes a projection for every
player and prices it with our custom coefficients server-side, which is a fine baseline and a
single opinion. Hashtag, FantasyPros, Basketball Monster and a spreadsheet of your own all
have better ones for some players — and none of them know what a rebound is worth in our
league. So we take their *stats* and do the pricing ourselves.

That is the whole design constraint, and it is why this module is small:

* **Only counting stats come in.** A coefficient multiplies a count. FG%, MPG-as-a-rate, ranks
  and tiers are either underived or unmultipliable, so columns we have no stat name for are
  ignored outright — the same rule `app.espn.statsplits` follows via `COUNTING_STAT_IDS`, for
  the same reason: scoring a percentage is silently wrong rather than loudly wrong.
* **The scoring is not reimplemented.** Season totals and per-game rates both go through
  `app.scoring.projections.score_projection` with a `ScoringEngine` built from the stored
  league rules. An imported projection and ESPN's are therefore comparable by construction; if
  they were priced by two code paths, the gap between them would mean nothing.
* **A file declares its basis, once.** Almost every export is per-game averages plus a games
  column, so `basis=per_game` (the default) multiplies through by GP to get season totals.
  `basis=season` says the numbers are already totals and divides instead. Both shapes end up
  storing *both* lines, because the board reads per-game and a draft plan budgets totals.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Projection
from app.ingest.parser import ImportParseError, ValueColumn
from app.ingest.registry import (
    ImportKind,
    ResolvedRow,
    UpsertContext,
    UpsertCounts,
    accept_matcher_threshold,
    register,
)
from app.scoring.engine import load_scoring_engine_for_season
from app.scoring.projections import score_projection

# The horizon we store these at. Same string ESPN's own projections use, so the two are one
# `source` apart and nothing else — that is what lets `/players/board?source=` swap them.
PROJECTION_KIND = "projected_season"

# Our stat name for games played (`app.scoring.stats`). Not a stat we price: it's the divisor.
GAMES_FIELD = "GP"

# How a file's numbers should be read. Declared per import (`--basis`), not guessed: a column
# called "PTS" is 30.1 in one export and 2,200 in another, and guessing from magnitude would
# be right until the day it wasn't.
BASIS_PER_GAME = "per_game"
BASIS_SEASON = "season"
BASES = (BASIS_PER_GAME, BASIS_SEASON)

# Stat values are stored to this many decimals. Season totals are per-game numbers multiplied
# by a games count, which lands on 1720.3999999999999 often enough to matter — not for the
# scoring (the difference is invisible) but for the JSON, and for a re-import comparing equal
# to what it wrote last time.
PRECISION = 4

# Every counting stat a projection export publishes, and what sources call it. Matched against
# a *normalized* header ("3PT/G" -> "3pt g", "% Owned" -> "% owned"), and the parser tries an
# exact hit on every column before it will accept a header that merely contains an alias — so
# "REB" wins over "OREB", and "FG%" matches nothing at all because no alias here is "fg%".
#
# Anything absent from this table is ignored, which is the point: FG%/FT%/TS%, MPG-derived
# ratios, rank, tier, ADP and value columns all pass through untouched instead of being stored
# as a stat nothing can multiply.
PROJECTION_STAT_ALIASES: dict[str, tuple[str, ...]] = {
    "PTS": ("pts", "points", "pt", "ppg"),
    # "TREB"/"TRB" are total rebounds; OREB/DREB are the split. A source that publishes only
    # the split still gets a REB line — see `_derive`.
    "REB": ("reb", "treb", "trb", "rebounds", "total rebounds", "rpg"),
    "OREB": ("oreb", "orb", "off reb", "offensive rebounds", "orpg"),
    "DREB": ("dreb", "drb", "def reb", "defensive rebounds", "drpg"),
    "AST": ("ast", "assists", "apg"),
    "STL": ("stl", "steals", "spg"),
    "BLK": ("blk", "blocks", "bpg"),
    "TO": ("to", "tov", "tos", "turnovers", "turnover", "topg"),
    "3PM": ("3pm", "3ptm", "tpm", "3s", "threes", "fg3m", "3p made", "3pt made", "3pm g"),
    "3PA": ("3pa", "3pta", "tpa", "fg3a", "3p att", "3pt att"),
    "FGM": ("fgm", "fg made", "field goals made"),
    "FGA": ("fga", "fg att", "fg attempts", "field goals attempted"),
    "FTM": ("ftm", "ft made", "free throws made"),
    "FTA": ("fta", "ft att", "ft attempts", "free throws attempted"),
    # A count of minutes, not a rate — under `basis=per_game` "MPG" is exactly its per-game
    # form, and ESPN's own projections carry MIN too, so dropping it would make the two
    # sources structurally different for no gain. Unscored in our league today; a settings
    # change is all it would take.
    "MIN": ("min", "mins", "minutes", "mp", "mpg"),
    "PF": ("pf", "fouls", "personal fouls"),
    "TF": ("tf", "techs", "technical fouls"),
    "DD": ("dd", "dd2", "double doubles"),
    "TD": ("td", "td3", "triple doubles"),
    # Misses. Rarely printed, usually derivable — and scored in our league (FTMI is -0.5).
    "FGMI": ("fgmi", "fg missed", "missed fg"),
    "FTMI": ("ftmi", "ft missed", "missed ft"),
    "3PMI": ("3pmi", "3pt missed"),
    # Deliberately NOT aliased to a bare "g": a per-game export whose columns are "PTS/G",
    # "REB/G" would hand us a stat column as the games count.
    GAMES_FIELD: ("gp", "games", "games played", "gms"),
}

# Stats that can be filled in exactly from two others. Never an estimate — a miss *is* an
# attempt that wasn't a make — and it happens before the per-game/total split so both lines
# agree. Without this, a source that prints OREB/DREB but no REB would import as a player who
# grabs no rebounds, and in a league that pays 4 points for one, that is not a rounding error.
_SUMS = (("REB", ("OREB", "DREB")),)
_DIFFERENCES = (("FGMI", "FGA", "FGM"), ("FTMI", "FTA", "FTM"), ("3PMI", "3PA", "3PM"))

PROJECTION_COLUMNS = tuple(
    # Points is the one column a projection is unusable without, and requiring it means a file
    # with no stat columns at all fails at detection with a list of what we looked for, rather
    # than importing 300 empty projections.
    ValueColumn(stat, aliases, required=(stat == "PTS"))
    for stat, aliases in PROJECTION_STAT_ALIASES.items()
)


@dataclass(frozen=True)
class StatLines:
    """One row's numbers in both shapes, plus the games count that relates them."""

    season_totals: dict[str, float]
    per_game_stats: dict[str, float]
    projected_games: float | None


def resolve_basis(basis: str | None) -> str:
    """Validate the `basis` option, defaulting to the shape exports actually come in."""
    resolved = (basis or BASIS_PER_GAME).strip().lower()
    if resolved not in BASES:
        raise ImportParseError(
            f"unknown basis {basis!r}: expected one of {list(BASES)}. "
            f"{BASIS_PER_GAME!r} means the columns are per-game averages (the usual export); "
            f"{BASIS_SEASON!r} means they are season totals."
        )
    return resolved


def _round(value: float) -> float:
    return round(value, PRECISION)


def _derive(stats: dict[str, float]) -> None:
    """Fill in stats the source implies but doesn't print. In place, exact, never a guess."""
    for target, parts in _SUMS:
        if target not in stats and all(part in stats for part in parts):
            stats[target] = _round(sum(stats[part] for part in parts))
    for target, attempted, made in _DIFFERENCES:
        if target not in stats and attempted in stats and made in stats:
            # Clamped at zero: a source whose makes exceed its attempts is inconsistent, and a
            # negative miss count would pay a player for it.
            stats[target] = _round(max(stats[attempted] - stats[made], 0.0))


def stat_lines(values: Mapping[str, float | None], *, basis: str = BASIS_PER_GAME) -> StatLines:
    """A parsed row's values -> season totals, per-game rates, and the games count.

    Whichever basis the file is in, the other line is the first one scaled by games. When
    there is no usable games count only the line the file actually gave us is populated; the
    other comes back empty, and `score_projection` degrades exactly as it does for an ESPN
    player projected to play zero games — see its `per_game_basis` ladder.
    """
    stats = {
        name: float(value)
        for name, value in values.items()
        if value is not None and name != GAMES_FIELD
    }
    _derive(stats)

    games = values.get(GAMES_FIELD)
    # 0 games is not a divisor and not a multiplier; it reads as "unknown", the same way
    # `app.espn.statsplits` treats ESPN's zero-game projections.
    games = float(games) if games is not None and games > 0 else None

    if basis == BASIS_SEASON:
        totals = {name: _round(value) for name, value in stats.items()}
        per_game = {name: _round(value / games) for name, value in stats.items()} if games else {}
    else:
        per_game = {name: _round(value) for name, value in stats.items()}
        totals = {name: _round(value * games) for name, value in stats.items()} if games else {}

    if games is not None:
        # Both maps carry GP, as ESPN's own splits do — the games count is a property of the
        # projection, not of one of its two shapes.
        totals[GAMES_FIELD] = games
        per_game[GAMES_FIELD] = games

    return StatLines(season_totals=totals, per_game_stats=per_game, projected_games=games)


def upsert_projection(
    db: Session, rows: Sequence[ResolvedRow], context: UpsertContext
) -> UpsertCounts:
    """Price each resolved row under our scoring and upsert one `projection` per player.

    Keyed on (player, source, kind, season) — the same key ESPN's sync writes on — so an
    imported source lands *beside* ESPN's projection rather than over it, and re-importing the
    same file updates in place instead of accumulating.

    Hand-written rather than an `ON CONFLICT`, and honouring `dry_run` by reading everything
    and writing nothing, for the reasons `app.ingest.adp` spells out.
    """
    counts = UpsertCounts()
    if not rows:
        return counts

    basis = resolve_basis(context.options.get("basis"))
    # The one thing this handler needs that the pipeline can't give it. Deliberately the same
    # engine the ESPN sync prices with; if it isn't loaded, refusing is better than storing a
    # board's worth of projections scored at zero.
    engine = load_scoring_engine_for_season(
        db, context.season, espn_league_id=get_settings().espn_league_id
    )

    existing = {
        row.player_id: row
        for row in db.scalars(
            select(Projection).where(
                Projection.source == context.source,
                Projection.kind == PROJECTION_KIND,
                Projection.season == context.season,
                Projection.player_id.in_([resolved.player_id for resolved in rows]),
            )
        )
    }
    now = datetime.now(UTC)

    for resolved in rows:
        lines = stat_lines(resolved.row.values, basis=basis)
        scored = score_projection(
            engine,
            lines.season_totals,
            per_game_stats=lines.per_game_stats,
            projected_games=lines.projected_games,
        )
        values = {
            "raw_stats": lines.season_totals,
            "per_game_stats": lines.per_game_stats or None,
            "projected_games": lines.projected_games,
            "fantasy_points_total": scored.fantasy_points_total,
            "fantasy_points_per_game": scored.fantasy_points_per_game,
            "per_game_basis": scored.per_game_basis,
            # Left null on purpose. ESPN fills this with *its* fantasy points under our
            # coefficients, which is a free check on our scoring; an imported source publishes
            # points under its own scoring, if any, and storing those here would invite a
            # comparison between two different formulas.
            "source_fantasy_points_total": None,
        }

        row = existing.get(resolved.player_id)
        if row is None:
            counts.created += 1
            if not context.dry_run:
                db.add(
                    Projection(
                        player_id=resolved.player_id,
                        source=context.source,
                        kind=PROJECTION_KIND,
                        season=context.season,
                        as_of=now,
                        **values,
                    )
                )
            continue

        if any(getattr(row, name) != value for name, value in values.items()):
            counts.updated += 1
            if not context.dry_run:
                for name, value in values.items():
                    setattr(row, name, value)
                row.as_of = now
        else:
            counts.unchanged += 1

    if not context.dry_run:
        db.flush()
    return counts


PROJECTION_IMPORT_KIND = register(
    ImportKind(
        name="projection",
        label="Per-stat season projections from one source, priced under our custom scoring",
        columns=PROJECTION_COLUMNS,
        upsert=upsert_projection,
        # Same generosity as ADP, for the same reason: every value in the row is a number, so
        # the only thing that can go wrong is the name — and a mis-attributed projection shows
        # up immediately as a player ranked somewhere absurd. Fuzzy hits that the matcher
        # wouldn't take, and names we carry nobody for, still come back as the worklist.
        accept=accept_matcher_threshold,
    )
)
