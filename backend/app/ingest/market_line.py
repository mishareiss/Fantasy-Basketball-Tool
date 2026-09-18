"""The `market_line` import kind: season-long sportsbook props -> a market projection.

The fourth kind, and the third source on the consensus board (FEATURE_SPEC 5). Books publish
season-long over/unders — "Jokic 9.5 assists per game, over -135 / under +110" — and that is a
projection made by people with money on it. No API sells it season-long and cheaply, so it
arrives the way everything else external does: as a pasted table.

Three things make this kind different from the three before it, and each one is a declaration
rather than a special case in the pipeline:

* **The file is LONG, not wide.** One row per (player, STAT), so four props on Jokic are four
  rows naming Jokic. `row_key` says the stat is what makes two rows different, which is what
  stops the second row being called a duplicate — and what keeps a genuine repeat of
  (Jokic, AST) *being* one.
* **A cell can be wrong rather than missing.** "Stat: Rebounds+Assists" is a real prop and not
  something our league scores. `validate` turns that into one `invalid` row with a message,
  the same treatment an empty required column gets, instead of an exception that costs the
  other 200 rows.
* **Storing a row is only half the write.** The board ranks players, not stats, so every
  touched player's lines are re-read and re-priced into ONE `Projection` under this source —
  see `derive_market_projection`. That happens on every import, so changing one odds pair
  re-derives that player and nobody else.

And one thing that is deliberately the same: the projection is written at the ordinary
`projected_season` kind, so `app.ranking.sources` discovers it as `projection:market` through
the value-source adapter every other projection uses. The age curve, the shared draftable
pool, the percentile scale and `GET /board/consensus` all apply to it with no code that knows
the market exists. A second discovery path would be a second board.

**The projection is PARTIAL by construction.** It is built from the stats that have lines and
nothing else: a player with only a points prop gets a market projection worth only his points,
which will rank him far below where anyone thinks he belongs. That is the honest reading of
"the market has an opinion about his scoring and no opinion about the rest", and it is why a
market column belongs *beside* a full projection rather than instead of one. Enter the stats
your league actually pays for and the number becomes comparable; enter one and it doesn't.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import MarketLine, Projection
from app.espn.statsplits import COUNTING_STAT_IDS
from app.espn.sync import ESPN_SOURCE, SEASON_PROJECTION_KIND
from app.ingest.parser import PARSE_TEXT, ParsedRow, ValueColumn, normalize_header
from app.ingest.projection import (
    GAMES_FIELD,
    PRECISION,
    PROJECTION_STAT_ALIASES,
    derive_implied_stats,
)
from app.ingest.registry import (
    ImportKind,
    ResolvedRow,
    UpsertContext,
    UpsertCounts,
    accept_only_certain,
    register,
)
from app.ranking.market import fair_value
from app.scoring.engine import ScoringEngine, load_scoring_engine_for_season
from app.scoring.stats import STAT_ID_TO_NAME, STAT_NAME_TO_ID, stat_name

# The default publisher, and therefore the default source id on the consensus board
# (`projection:market`). Overridable per import (`--source draftkings`) so two books can sit
# side by side as two sources; 'market' is right for the usual case of one hand-kept set of
# lines aggregated from wherever they were best.
MARKET_SOURCE = "market"

# The `Projection.kind` the derived rows are stored at. The SAME kind ESPN's projections use,
# on purpose — see the module docstring: that is what makes the market a value source the
# existing adapter already understands, rather than a fourth thing to teach it about.
MARKET_PROJECTION_KIND = SEASON_PROJECTION_KIND

# How the derived per-game number was arrived at, in `app.scoring.projections`' vocabulary.
# A market line IS a per-game rate, so the fantasy points come from scoring the per-game stats
# directly rather than from dividing a season total by a games count.
MARKET_PER_GAME_BASIS = "per_game_stats"

STAT_FIELD = "stat"
LINE_FIELD = "line"
OVER_FIELD = "over_odds"
UNDER_FIELD = "under_odds"

MARKET_LINE_COLUMNS = (
    # Which stat the prop is on. Text, not a number: it's "Assists" / "AST" / "3PM", and
    # parsing it as a number would throw the column away (the same reason `ranking` reads its
    # tier column as text).
    ValueColumn(
        STAT_FIELD,
        ("stat", "market", "prop", "category", "cat", "stat type", "prop type", "type"),
        required=True,
        parse_value=PARSE_TEXT,
    ),
    # The number itself. Declared BEFORE the odds columns so that if a file's only relevant
    # header is "Over/Under", the line takes it and the odds fields go empty — see the
    # column-claiming rule in `app.ingest.parser.detect_columns`.
    ValueColumn(
        LINE_FIELD,
        ("line", "ou", "o u", "over under", "total", "number", "prop line", "market line"),
        required=True,
    ),
    ValueColumn(OVER_FIELD, ("over odds", "over price", "over juice", "over vig", "over", "o")),
    ValueColumn(
        UNDER_FIELD, ("under odds", "under price", "under juice", "under vig", "under", "u")
    ),
)


def _stat_vocabulary() -> dict[str, int]:
    """Every spelling of a stat we accept in the `stat` cell -> its ESPN stat id.

    Built from two tables we already have and no third one:

    * `app.espn.statsplits.COUNTING_STAT_IDS` — the stats that are safe to multiply by a
      coefficient. That is the same rule the ESPN sync and the projection importer follow, and
      it is why a rate gets no entry here: ESPN really does publish stats called "APG" and
      "FG%", and scoring either is silently wrong rather than loudly wrong.
    * `app.ingest.projection.PROJECTION_STAT_ALIASES` — every word sources use for those
      stats. A market file's stat CELL and a projection file's stat HEADER are one vocabulary
      written in two places, and two lists of it would drift.

    Aliases are added with `setdefault`, so a canonical counting-stat name always wins over
    another stat's alias — and "apg" resolves to AST rather than to ESPN's unscoreable rate of
    the same name, because that rate is not a counting stat and never entered the map.
    """
    vocabulary = {
        normalize_header(STAT_ID_TO_NAME[stat_id]): stat_id
        for stat_id in COUNTING_STAT_IDS
        if stat_id in STAT_ID_TO_NAME
    }
    for name, aliases in PROJECTION_STAT_ALIASES.items():
        stat_id = STAT_NAME_TO_ID.get(name)
        if stat_id is None or stat_id not in COUNTING_STAT_IDS:  # pragma: no cover
            continue
        for alias in aliases:
            vocabulary.setdefault(normalize_header(alias), stat_id)
    return vocabulary


STAT_VOCABULARY: dict[str, int] = _stat_vocabulary()

# What to suggest when a cell names something we can't price. The stats a source actually
# publishes, which is a readable subset of what's accepted — every ESPN counting stat resolves,
# including the ones nobody writes a prop on (EJ, DQ).
SCOREABLE_STATS: list[str] = sorted(PROJECTION_STAT_ALIASES)


class UnknownStatError(ValueError):
    """The `stat` cell names something we can't score. Reported per row, never raised at a file."""


def resolve_stat(text: str | None) -> int:
    """A `stat` cell -> an ESPN stat id. Raises `UnknownStatError` naming what we understand.

    Accepts the canonical name ('AST'), anything the projection importer calls that stat
    ('assists', 'apg'), and a bare stat id, since a hand-kept sheet may well hold the number.

    >>> resolve_stat("Assists") == resolve_stat("AST") == resolve_stat("apg")
    True
    """
    cell = (text or "").strip()
    normalized = normalize_header(cell)
    if normalized in STAT_VOCABULARY:
        return STAT_VOCABULARY[normalized]
    if normalized.isdigit() and int(normalized) in COUNTING_STAT_IDS:
        return int(normalized)
    raise UnknownStatError(
        f"unknown stat {cell!r}: not a counting stat we can price. Expected one of "
        f"{SCOREABLE_STATS}, or any of the usual spellings ('points', 'assists', 'threes'). "
        f"Combination props ('PRA', 'pts+reb') have no coefficient of their own and rate "
        f"stats (FG%, TS%) can't be multiplied by one, so neither can be priced."
    )


def validate_market_row(row: ParsedRow) -> str | None:
    """A row whose `stat` cell we can't price is invalid — with the reason on the row."""
    value = row.values.get(STAT_FIELD)
    try:
        resolve_stat(value if isinstance(value, str) else None)
    except UnknownStatError as error:
        return str(error)
    return None


def market_row_key(row: ParsedRow) -> object:
    """What makes two rows of a market file different: the stat, not just the player.

    Returns the canonical stat NAME rather than the id, because it also ends up in the
    duplicate row's note ("line 12 already resolved to this player and AST"), and 'AST' reads
    where '3' does not. A cell we can't resolve never gets here — `validate_market_row` has
    already turned that row into an `invalid` — but it falls back to the raw text rather than
    raising, so the key function is total.
    """
    value = row.values.get(STAT_FIELD)
    try:
        return stat_name(resolve_stat(value if isinstance(value, str) else None))
    except UnknownStatError:  # pragma: no cover - validation rejects these first
        return str(value)


def _odds(value: float | None) -> int | None:
    """An odds cell -> American odds as an int, or None for an unpriced side.

    Odds are whole numbers; the parser hands back floats because every other column is one.
    A literal 0 is not a price anyone writes, so it reads as absent (as it does in
    `app.ranking.market.implied_probability`).
    """
    if value is None:
        return None
    rounded = int(round(float(value)))
    return rounded or None


# The stored (line, over, under) for one player's stats, keyed by stat id.
PlayerLines = dict[int, tuple[float, int | None, int | None]]


@dataclass(frozen=True)
class DerivedProjection:
    """One player's market projection, before it is compared with what's stored."""

    per_game_stats: dict[str, float]
    fantasy_points_per_game: float
    fantasy_points_total: float
    projected_games: float


def price_lines(
    lines: PlayerLines, engine: ScoringEngine, *, sigma_fraction: float, games: float
) -> DerivedProjection:
    """One player's lines -> a priced per-game stat line. Pure; no database.

    Each line becomes a fair per-game number (`app.ranking.market.fair_value`), the stats that
    can be filled in exactly from others are (a book pricing OREB and DREB but not REB is the
    same case the projection importer already handles), and the result is scored with OUR
    stored coefficients — the same engine that prices ESPN's projection and every imported
    one. A market projection and an ESPN projection are therefore comparable by construction.

    A line on GP is not scored; it is used as the games count, exactly as the projection
    importer treats its GP column. Games only set the displayed season total — the board ranks
    on per-game — so a missing count costs nothing that matters.
    """
    per_game: dict[str, float] = {}
    for stat_id, (line, over_odds, under_odds) in sorted(lines.items()):
        value = fair_value(line, over_odds, under_odds, sigma_fraction=sigma_fraction)
        name = stat_name(stat_id)
        if name == GAMES_FIELD:
            continue
        per_game[name] = round(value, PRECISION)

    derive_implied_stats(per_game)
    per_game_points = engine.score(per_game)
    return DerivedProjection(
        per_game_stats=per_game,
        fantasy_points_per_game=per_game_points,
        fantasy_points_total=round(per_game_points * games, PRECISION),
        projected_games=games,
    )


def _games_for(lines: PlayerLines, espn_games: float | None, default_games: float) -> float:
    """How many games this market player is credited with, best source first.

    A GP line if the book priced one, then ESPN's own projected games for him, then the
    configured default. Only the displayed season total depends on it.
    """
    games_stat_id = STAT_NAME_TO_ID.get(GAMES_FIELD)
    if games_stat_id is not None and games_stat_id in lines:
        line, over_odds, under_odds = lines[games_stat_id]
        priced = fair_value(line, over_odds, under_odds, sigma_fraction=0.0)
        if priced > 0:
            return round(priced, PRECISION)
    if espn_games and espn_games > 0:
        return float(espn_games)
    return float(default_games)


def _espn_games(db: Session, player_ids: Sequence[int], season: int) -> dict[int, float]:
    """ESPN's projected games for these players — this season's if stored, else the newest."""
    rows = db.execute(
        select(Projection.player_id, Projection.season, Projection.projected_games)
        .where(
            Projection.source == ESPN_SOURCE,
            Projection.kind == SEASON_PROJECTION_KIND,
            Projection.player_id.in_(player_ids),
            Projection.projected_games.is_not(None),
        )
        # This season last, so it wins the dict comprehension below.
        .order_by(Projection.season.asc())
    ).all()
    preferred = {player_id: games for player_id, row_season, games in rows if row_season == season}
    newest = {player_id: games for player_id, _, games in rows}
    return newest | preferred


def derive_market_projections(
    db: Session,
    effective: Mapping[int, PlayerLines],
    context: UpsertContext,
    engine: ScoringEngine,
) -> tuple[int, int, int, int]:
    """Re-price every touched player: upsert one `Projection` each, or REMOVE it. Counts.

    `effective` is what each player's lines ARE after whatever just happened to them — the
    stored rows with an import's rows laid over them, or simply what is left after one was
    deleted. A dry run therefore derives exactly what a commit would and reports the real
    numbers, the same promise `app.ingest.adp` makes about its own counters.

    Upserted on (player, source, kind, season), the key every projection uses, so a second
    import of a changed line rewrites the row rather than adding one.

    **A player with NO lines left loses his projection.** That is the fourth counter, and it
    is not symmetry for its own sake: the derived `Projection` is the ONLY thing the board
    can see (`app.ranking.sources` discovers `projection:market`, never a `market_line` row),
    so an upsert-only derivation would leave a player who has just had his last line deleted
    ranked by a number nothing underlies any more — present in `GET /sources`' player_count
    and on `GET /board/consensus`, with an empty set of lines under him on the market page.
    Re-pricing him at zero would be worse still: that is a claim the market rates him at
    nothing, when in fact it no longer says anything about him at all. The honest answer to
    "no lines" is "no opinion", and the row's absence is how this source spells that.
    """
    if not effective:
        return 0, 0, 0, 0

    settings = get_settings()
    player_ids = list(effective)
    espn_games = _espn_games(db, player_ids, context.season)
    existing = {
        row.player_id: row
        for row in db.scalars(
            select(Projection).where(
                Projection.source == context.source,
                Projection.kind == MARKET_PROJECTION_KIND,
                Projection.season == context.season,
                Projection.player_id.in_(player_ids),
            )
        )
    }
    now = datetime.now(UTC)
    created = updated = unchanged = deleted = 0

    for player_id in sorted(player_ids):
        lines = effective[player_id]
        row = existing.get(player_id)

        # Nothing priced for him any more — his last line was deleted. See the docstring:
        # the projection goes, rather than being rewritten as a zero nobody meant.
        if not lines:
            if row is not None:
                deleted += 1
                if not context.dry_run:
                    db.delete(row)
            continue

        derived = price_lines(
            lines,
            engine,
            sigma_fraction=settings.market_sigma_frac,
            games=_games_for(lines, espn_games.get(player_id), settings.market_default_games),
        )
        values = {
            # Per-game in BOTH, and deliberately: a market line is quoted per game, so the
            # per-game stat line is the raw thing the book actually said. There is no season
            # line underneath it to store, and multiplying one up would present a derived
            # number as the source's own.
            "raw_stats": derived.per_game_stats,
            "per_game_stats": derived.per_game_stats,
            "projected_games": derived.projected_games,
            "fantasy_points_total": derived.fantasy_points_total,
            "fantasy_points_per_game": derived.fantasy_points_per_game,
            "per_game_basis": MARKET_PER_GAME_BASIS,
            # Books publish odds, not fantasy points. Nothing to compare our scoring against.
            "source_fantasy_points_total": None,
        }

        if row is None:
            created += 1
            if not context.dry_run:
                db.add(
                    Projection(
                        player_id=player_id,
                        source=context.source,
                        kind=MARKET_PROJECTION_KIND,
                        season=context.season,
                        as_of=now,
                        **values,
                    )
                )
        elif any(getattr(row, name) != value for name, value in values.items()):
            updated += 1
            if not context.dry_run:
                for name, value in values.items():
                    setattr(row, name, value)
                row.as_of = now
        else:
            unchanged += 1

    if not context.dry_run:
        db.flush()
    return created, updated, unchanged, deleted


def stored_lines(
    db: Session, player_ids: Sequence[int], *, source: str, season: int
) -> dict[int, PlayerLines]:
    """What these players' lines ARE right now — `derive_market_projections`' input shape.

    Every id asked for gets an entry, and a player with nothing stored gets an EMPTY one
    rather than no entry at all. That distinction is the whole point of the function: an
    absent key is a player the derivation never looks at, while an empty mapping is a player
    it must now un-price. A caller that has just deleted a row wants the second.

    The import path doesn't use this — it already holds the rows it read to upsert against,
    and reading them twice would be a second query for the same answer. The editing path
    (`app.api.market`) does, because a delete leaves it with a player id and nothing else.
    """
    lines: dict[int, PlayerLines] = {player_id: {} for player_id in player_ids}
    for row in db.scalars(
        select(MarketLine).where(
            MarketLine.source == source,
            MarketLine.season == season,
            MarketLine.player_id.in_(list(player_ids)),
        )
    ):
        lines[row.player_id][row.stat_id] = (row.line, row.over_odds, row.under_odds)
    return lines


def upsert_market_line(
    db: Session, rows: Sequence[ResolvedRow], context: UpsertContext
) -> UpsertCounts:
    """Upsert one `market_line` per (player, stat), then re-derive every touched player.

    The counters describe LINES — that is the row the file holds, and "3 created, 1 updated"
    is what says whether a paste changed anything. What the derivation did goes in `notes`,
    because it is about players and would otherwise be indistinguishable from the line counts.

    Hand-written rather than an `ON CONFLICT`, and honouring `dry_run` by reading everything
    and writing nothing, for the reasons `app.ingest.adp` spells out.
    """
    counts = UpsertCounts()
    if not rows:
        return counts

    # The same engine ESPN's sync and the projection importer price with. Without it, refusing
    # beats storing a board's worth of market projections scored at zero.
    engine = load_scoring_engine_for_season(
        db, context.season, espn_league_id=get_settings().espn_league_id
    )

    player_ids = sorted({resolved.player_id for resolved in rows})
    existing = {
        (row.player_id, row.stat_id): row
        for row in db.scalars(
            select(MarketLine).where(
                MarketLine.source == context.source,
                MarketLine.season == context.season,
                MarketLine.player_id.in_(player_ids),
            )
        )
    }
    # What each touched player's lines will BE after this import: what is already stored (the
    # rows just read), with this file laid over it below. The derivation reads THIS rather
    # than the database, which is what lets a dry run report the real derived numbers while
    # writing nothing — and it costs no second query.
    effective: dict[int, PlayerLines] = {player_id: {} for player_id in player_ids}
    for (player_id, stat_id), row in existing.items():
        effective[player_id][stat_id] = (row.line, row.over_odds, row.under_odds)
    now = datetime.now(UTC)

    for resolved in rows:
        # Validated at match time, so this cannot raise here.
        stat_id = resolve_stat(resolved.text(STAT_FIELD))
        line = float(resolved.value(LINE_FIELD))
        over_odds = _odds(resolved.value(OVER_FIELD))
        under_odds = _odds(resolved.value(UNDER_FIELD))
        effective[resolved.player_id][stat_id] = (line, over_odds, under_odds)

        row = existing.get((resolved.player_id, stat_id))
        if row is None:
            counts.created += 1
            if not context.dry_run:
                db.add(
                    MarketLine(
                        player_id=resolved.player_id,
                        source=context.source,
                        season=context.season,
                        stat_id=stat_id,
                        line=line,
                        over_odds=over_odds,
                        under_odds=under_odds,
                        as_of=now,
                    )
                )
        elif (row.line, row.over_odds, row.under_odds) != (line, over_odds, under_odds):
            counts.updated += 1
            if not context.dry_run:
                row.line, row.over_odds, row.under_odds = line, over_odds, under_odds
                row.as_of = now
        else:
            counts.unchanged += 1

    if not context.dry_run:
        db.flush()

    derived_created, derived_updated, derived_unchanged, derived_deleted = (
        derive_market_projections(db, effective, context, engine)
    )
    counts.notes.append(
        f"market projection ({context.source}, {MARKET_PROJECTION_KIND}, season "
        f"{context.season}): {derived_created} created, {derived_updated} updated, "
        f"{derived_unchanged} unchanged, {derived_deleted} removed — built from the stats "
        "with lines and nothing else"
    )
    counts.notes.append(
        f"priced with MARKET_SIGMA_FRAC={get_settings().market_sigma_frac}; an even, "
        "one-sided or absent price leaves the value equal to the line"
    )
    return counts


MARKET_LINE_KIND = register(
    ImportKind(
        name="market_line",
        label="Season-long sportsbook lines per (player, stat), priced under our custom scoring",
        columns=MARKET_LINE_COLUMNS,
        upsert=upsert_market_line,
        # Stricter than ADP and projections, and this is the kind that deserves it: a
        # mis-attributed line is a number we would bet on, and hanging Jalen Williams' assists
        # prop on Jalen Wilson is quietly wrong rather than obviously wrong. Fuzzy hits come
        # back as review rows with their candidates; confirming one writes an alias, so the
        # question is asked once per player ever.
        accept=accept_only_certain,
        # Long format: four props on one player are four rows, and it is (player, stat) that
        # can't repeat.
        row_key=market_row_key,
        # A stat we can't score is one invalid row with a message, not a failed import.
        validate=validate_market_row,
    )
)
