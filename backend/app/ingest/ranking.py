"""The `ranking` import kind: someone's ordered board -> one `RankingSet` and its entries.

The third kind, and the first that needs storage of its own. `adp` and `projection` both store
a number *about a player*, so a row is the whole unit and an upsert is the whole write. A
ranking isn't a number, it's a **list**: a consensus board, an expert's Top 200, or the order
we like. Its meaning is collective — who is on it, who fell off, where the tier breaks are —
so it gets `RankingSet` / `RankingEntry` (FEATURE_SPEC 5, 10) rather than a column somewhere.

Three consequences run through everything below:

* **A set has an identity: (source, name, season, horizon).** `--name "Dynasty Top 200"` is
  what makes a re-import land on the same list, and what lets one source publish several lists
  that don't overwrite each other. Absent, the name falls back to the source, which is right
  for the common case of a source with exactly one board.
* **The horizon is REQUIRED, and part of that identity.** A rank-only list has no production
  numbers on it, so nothing downstream can work out whether it is a dynasty board or a redraft
  one — the file has to say. Making it part of the key is what lets Yahoo publish "Top 200"
  twice for one season, once per horizon, without the second import replacing the first.
  Value kinds (`projection`, and `market_line` when it lands) take no horizon: they carry
  production, and the age curve derives both horizons from it.
* **Re-importing REPLACES the set.** Version two of a list is a different list: players drop
  off it and the rest shift. Upserting row by row would leave last week's fallen players
  sitting in the set at their old ranks, which is worse than not importing at all — so the
  set's entries are deleted and rewritten. The reported created/updated/unchanged still
  describe the *players*, so a re-import of an unchanged file still reads as all-unchanged.
* **Rank comes from the column if there is one, and from the file's order if there isn't.**
  Plenty of boards are pasted as a bare ordered list of names. The fallback uses the row's
  position in the *file* (`ParsedRow.index`), never its position among the accepted rows: a
  name held for review has to leave a gap at #37, not silently promote everyone below it by
  one and hand us a board that is wrong from #37 down.
"""

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import RankingEntry, RankingSet
from app.db.models.ranking import RANKING_HORIZONS
from app.ingest.parser import PARSE_TEXT, ImportParseError, ValueColumn
from app.ingest.registry import (
    ImportKind,
    ResolvedRow,
    UpsertContext,
    UpsertCounts,
    accept_matcher_threshold,
    register,
)

# The set's label and its horizon, both read off `context.options`. `name` is optional (it
# falls back to the source); `horizon` is not — see the module docstring.
NAME_OPTION = "name"
HORIZON_OPTION = "horizon"
RANKING_OPTIONS = (NAME_OPTION, HORIZON_OPTION)

# How a row's rank was arrived at, reported in the preview so the two cases are never confused.
RANK_FROM_COLUMN = "column"
RANK_FROM_FILE_ORDER = "file order"

RANKING_COLUMNS = (
    ValueColumn(
        "rank",
        # Not required: a pasted board is often just names in order, and that order *is* the
        # ranking — see `_rank_of`.
        #
        # The positional-rank aliases are spelled out ("pos rank", "posrk", ...) rather than
        # left as a bare "pos", which would be worse than useless: in these exports a column
        # headed "Pos" is the *position* column every time, `POSITION_ALIASES` already claims
        # it, and letting rank claim it too puts `rank: 'Pos'` in the preview's column map for
        # a file whose ranks actually came from its row order. A wrong column map is the one
        # failure a row-by-row list won't reveal, so it isn't worth an alias nobody uses.
        ("rank", "rk", "#", "overall", "ovr", "pos rank", "posrk", "pos rk", "positional rank"),
    ),
    ValueColumn(
        "tier",
        ("tier", "tr", "grp", "group"),
        # Text, not a number: sources number tiers ("1", "2") and name them ("Elite", "Tier 2")
        # about equally often, and reading the second kind as a number stores nothing at all.
        parse_value=PARSE_TEXT,
    ),
    ValueColumn("value", ("value", "score", "rating", "proj")),
)


def resolve_options(options: Mapping[str, str] | None, *, source: str) -> tuple[str, str]:
    """Validate this kind's options and return `(set name, horizon)`. Rejects what it can't use.

    Refusing an unknown option rather than ignoring it, for the reason `resolve_basis` does:
    `--nmae "Top 200"` that silently becomes the source's default name doesn't fail, it
    replaces the wrong list — and the entries it overwrote are gone.

    The horizon gets no default for the same reason in reverse: guessing 'redraft' for an
    expert's dynasty board would file it under the wrong lens, and a wrong answer that looks
    right is worse than a refusal the caller fixes in one keystroke.
    """
    unknown = sorted(set(options or {}) - set(RANKING_OPTIONS))
    if unknown:
        raise ImportParseError(
            f"unknown option(s) for a ranking import: {unknown}. "
            f"Supported: {list(RANKING_OPTIONS)} — {NAME_OPTION!r} is the set's label "
            f'(e.g. "Dynasty Top 200"), and defaults to the source name; '
            f"{HORIZON_OPTION!r} is one of {list(RANKING_HORIZONS)} and is required."
        )

    horizon = ((options or {}).get(HORIZON_OPTION) or "").strip().lower()
    if horizon not in RANKING_HORIZONS:
        raise ImportParseError(
            f"a ranking import needs {HORIZON_OPTION}={list(RANKING_HORIZONS)}, got "
            f"{((options or {}).get(HORIZON_OPTION))!r}. A rank-only list carries no stats to "
            "age-adjust, so it has to declare which question it answers; projections and other "
            "value sources derive both horizons from the age curve and take no horizon."
        )

    name = ((options or {}).get(NAME_OPTION) or "").strip()
    # A source with one board doesn't need to name it twice; a source with several does.
    return name or source, horizon


def _rank_of(resolved: ResolvedRow) -> tuple[int, str]:
    """This row's rank, and where it came from.

    The parsed value wins whenever the file printed one. Otherwise the row's 1-based position
    among the file's data rows — which is stable under review holds, unlike counting accepted
    rows, and is exactly what an ordered paste is asserting.
    """
    printed = resolved.value("rank")
    if printed is None:
        return resolved.row.index, RANK_FROM_FILE_ORDER
    # int, not float: a rank is a place in a list. Sources do print "1.0" and "12th"; the
    # parser has already turned both into a number by here.
    return int(printed), RANK_FROM_COLUMN


def upsert_ranking(
    db: Session, rows: Sequence[ResolvedRow], context: UpsertContext
) -> UpsertCounts:
    """Find-or-create the (source, name, season, horizon) set, then replace its entries.

    Not an upsert per row, despite the name the registry gives the hook: see the module
    docstring. The counters still describe players — created (new to the set), updated (moved,
    re-tiered, re-valued) and unchanged — because that is what tells you whether a file has
    already landed; the fact the set itself was rewritten, and what it cost, goes in `notes`.

    A dry run reads everything and writes nothing, exactly as `app.ingest.adp` explains, so the
    preview's numbers (including "2 players would drop off") are the real ones.
    """
    counts = UpsertCounts()
    name, horizon = resolve_options(context.options, source=context.source)
    label = f"set {name!r} ({context.source}, {horizon}, season {context.season})"

    if not rows:
        # The safe half of "replace wholesale". An import that resolved nothing is a matching
        # failure or an empty file, and emptying last week's board over one helps nobody — so
        # the set is left exactly as it was, and the worklist says why.
        counts.notes.append(f"{label}: no rows resolved, left untouched")
        return counts

    ranking_set = db.scalar(
        select(RankingSet).where(
            RankingSet.source == context.source,
            RankingSet.name == name,
            RankingSet.season == context.season,
            # The horizon is part of the key, so a source's dynasty board and its redraft board
            # are two sets and a re-import lands on exactly the one it names.
            RankingSet.horizon == horizon,
        )
    )
    existing = (
        {entry.player_id: entry for entry in ranking_set.entries} if ranking_set is not None else {}
    )

    resolved_ranks = [(resolved, *_rank_of(resolved)) for resolved in rows]
    from_column = sum(1 for _, _, origin in resolved_ranks if origin == RANK_FROM_COLUMN)

    for resolved, rank, _ in resolved_ranks:
        tier = resolved.text("tier")
        value = resolved.value("value")
        entry = existing.get(resolved.player_id)
        if entry is None:
            counts.created += 1
        elif (entry.rank, entry.tier, entry.value) != (rank, tier, value):
            counts.updated += 1
        else:
            counts.unchanged += 1

    dropped = sorted(set(existing) - {resolved.player_id for resolved in rows})
    counts.notes.append(
        f"{label}: "
        + (
            f"replacing {len(existing)} entries with {len(rows)}"
            if ranking_set is not None
            else f"new set of {len(rows)} entries"
        )
        + (f", {len(dropped)} player(s) drop off" if dropped else "")
    )
    counts.notes.append(
        f"rank from {RANK_FROM_COLUMN} for {from_column} row(s), "
        f"from {RANK_FROM_FILE_ORDER} for {len(rows) - from_column}"
    )

    if context.dry_run:
        return counts

    now = datetime.now(UTC)
    if ranking_set is None:
        ranking_set = RankingSet(
            source=context.source,
            name=name,
            season=context.season,
            horizon=horizon,
            as_of=now,
        )
        db.add(ranking_set)
    else:
        # Clear-then-refill through the relationship, so `delete-orphan` issues the deletes and
        # the in-session collection can't be left holding rows that no longer exist. Flushed in
        # between because the deletes have to reach the database before the inserts do — the
        # (set, player) unique constraint is what would otherwise object.
        ranking_set.entries.clear()
        db.flush()
        # A ranking's freshness is a property of the list, so this moves on every import —
        # unlike `AdpEntry.as_of`, which only moves when a number does.
        ranking_set.as_of = now

    ranking_set.entries.extend(
        RankingEntry(
            player_id=resolved.player_id,
            rank=rank,
            tier=resolved.text("tier"),
            value=resolved.value("value"),
        )
        for resolved, rank, _ in resolved_ranks
    )
    db.flush()
    return counts


RANKING_KIND = register(
    ImportKind(
        name="ranking",
        label="An ordered list of players from one source — a board, with optional tiers",
        columns=RANKING_COLUMNS,
        upsert=upsert_ranking,
        # As generous as ADP, and for the same reason: a ranking is read by eye, so a wrong
        # row costs a double-take rather than a bad bet, and holding 200 rows for confirmation
        # means nobody imports anything. (`accept_only_certain` is reserved for `market_line`,
        # where a mis-attributed row is a number we'd act on.) Names the matcher won't place
        # still come back as the review list — and leave a gap in the ranks, not a renumbering.
        accept=accept_matcher_threshold,
    )
)
