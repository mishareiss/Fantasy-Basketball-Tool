"""The import pipeline: parse, match, preview, commit. Two phases, on purpose.

A CSV of 300 foreign player names is never entirely right. Some names are spelled differently,
one is a nickname, two are players we don't carry, and one is a "Jalen Williams" that is
actually a different Jalen Williams. Writing all of that and finding out afterwards is the
wrong shape, so every import runs twice:

* **Dry run** (the default) parses, matches, and reports every row with its candidates —
  including how many rows *would* be created, updated, or left alone. It writes nothing at all.
* **Commit** does the same work and persists it: an alias per accepted row (via
  `record_match`, so the next import of that same file resolves instantly and identically),
  and the kind's own rows via its handler.

Both phases are idempotent. Re-committing the same file writes no duplicate alias and no
duplicate row — it reports every row as `unchanged`, which is the signal that the file has
already landed.

What does *not* get written is just as deliberate: review rows (a fuzzy match a stricter kind
wants confirmed, or an ambiguity between two of our players) and unmatched rows come back as a
worklist instead. Fixing one is a single `POST /players/{espn_player_id}/aliases`, after which
a re-import resolves it as `alias` and stores it.
"""

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field

from sqlalchemy.orm import Session

from app.ingest.parser import ImportParseError, ParsedRow, ParsedTable, parse_table
from app.ingest.registry import (
    ImportKind,
    ResolvedRow,
    UpsertContext,
    UpsertCounts,
    get_kind,
)
from app.matching import (
    METHOD_UNMATCHED,
    MatchResult,
    build_matcher,
    find_alias,
    record_match,
)

# Where a parsed row ended up.
STATUS_MATCHED = "matched"  # resolved and accepted; written on commit
STATUS_REVIEW = "review"  # resolved-but-unconfirmed, or ambiguous — has candidates
STATUS_UNMATCHED = "unmatched"  # nothing in our pool looks like this name
STATUS_DUPLICATE = "duplicate"  # a row earlier in the file already claimed this player
STATUS_INVALID = "invalid"  # no number in a required value column

# How many candidates to carry back per review row. Five is more than anyone reads and still
# small enough to put in a JSON response for 300 rows.
MAX_CANDIDATES = 5


@dataclass
class RowOutcome:
    """One row of the file, and what became of it. This is the worklist and the receipt."""

    line: int
    source_name: str
    status: str
    values: dict[str, float | str | None] = field(default_factory=dict)
    team: str | None = None
    positions: list[str] = field(default_factory=list)

    # Filled in when the name resolved to one of our players.
    player_id: int | None = None
    player_name: str | None = None
    confidence: float = 0.0
    method: str = ""
    # For a review row: who it might be, best first. For an unmatched row: the near misses,
    # which is usually enough to see at a glance that the player isn't one of ours.
    candidates: list[dict[str, object]] = field(default_factory=list)
    # Why a row was skipped, when the status alone doesn't say.
    note: str | None = None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class ImportSummary:
    """What one import did, or would do. Printed by the CLI, returned by the endpoint."""

    kind: str
    source: str
    season: int
    dry_run: bool
    # The per-kind options this run used, e.g. {'basis': 'per_game'}. Echoed back because a
    # projection imported on the wrong basis looks perfectly plausible until you notice
    # everyone is projected for 2,300 points a game.
    options: dict[str, str] = field(default_factory=dict)

    # Which column played which role, `field -> the header it was found under`. Worth showing:
    # a silently mis-detected column is the one failure mode a row-by-row list won't reveal.
    columns: dict[str, str] = field(default_factory=dict)
    delimiter: str = ","

    rows_parsed: int = 0
    rows_skipped_blank: int = 0

    matched: int = 0
    review: int = 0
    unmatched: int = 0
    duplicate: int = 0
    invalid: int = 0

    # Aliases are how a match becomes permanent. On a first import these are nearly all new;
    # on a re-import they are all existing, which is the cheap proof the file already landed.
    aliases_created: int = 0
    aliases_existing: int = 0

    rows_created: int = 0
    rows_updated: int = 0
    rows_unchanged: int = 0
    # Whatever the handler wanted to say that the counters can't — "replaced the set 'Top 200':
    # 14 entries -> 12". Straight through from `UpsertCounts.notes`, uninterpreted.
    notes: list[str] = field(default_factory=list)

    rows: list[RowOutcome] = field(default_factory=list)

    def of_status(self, status: str) -> list[RowOutcome]:
        return [row for row in self.rows if row.status == status]

    @property
    def worklist(self) -> list[RowOutcome]:
        """The rows a human has to deal with, review before unmatched."""
        return self.of_status(STATUS_REVIEW) + self.of_status(STATUS_UNMATCHED)


def _outcome(row: ParsedRow, status: str, result: MatchResult | None = None, **extra) -> RowOutcome:
    outcome = RowOutcome(
        line=row.line,
        source_name=row.name,
        status=status,
        values=dict(row.values),
        team=row.team,
        positions=list(row.positions),
        **extra,
    )
    if result is not None:
        outcome.player_id = result.player_id
        outcome.confidence = round(result.confidence, 4)
        outcome.method = result.method
        outcome.candidates = [
            candidate.as_dict() for candidate in result.candidates[:MAX_CANDIDATES]
        ]
    return outcome


def match_rows(
    db: Session,
    table: ParsedTable,
    kind: ImportKind,
    *,
    source: str,
    matcher=None,
) -> tuple[list[ResolvedRow], list[RowOutcome]]:
    """Resolve every parsed row against our canonical players. Writes nothing.

    Returns the accepted rows (for the handler) alongside an outcome per parsed row, in file
    order — the caller needs both, and the outcome list is what a preview is made of.
    """
    matcher = matcher or build_matcher(db, source=source)
    accepted: list[ResolvedRow] = []
    outcomes: list[RowOutcome] = []
    claimed: dict[int, int] = {}  # player_id -> the line that got there first

    for row in table.rows:
        missing = row.missing(kind.columns)
        if missing:
            outcomes.append(
                _outcome(
                    row,
                    STATUS_INVALID,
                    note=f"no value in required column(s): {', '.join(missing)}",
                )
            )
            continue

        result = matcher.match(row.name, team=row.team, positions=row.positions, source=source)

        if not kind.accept(result):
            # An ambiguity between two of our players, or a fuzzy hit this kind wants
            # confirmed, is a *review*: there is something specific to look at, and the
            # candidates say what. A name nothing in our pool resembles is unmatched — most
            # often a player we don't carry at all, which is not a decision anyone needs to
            # make. (Its near misses still ride along, because seeing them is what tells you
            # which of the two it was.)
            status = STATUS_UNMATCHED if result.method == METHOD_UNMATCHED else STATUS_REVIEW
            outcomes.append(_outcome(row, status, result))
            continue

        player = matcher.get(result.player_id)
        first = claimed.get(result.player_id)
        if first is not None:
            outcomes.append(
                _outcome(
                    row,
                    STATUS_DUPLICATE,
                    result,
                    player_name=player.full_name if player else None,
                    note=f"line {first} already resolved to this player",
                )
            )
            continue

        claimed[result.player_id] = row.line
        accepted.append(ResolvedRow(player_id=result.player_id, row=row))
        outcome = _outcome(row, STATUS_MATCHED, result)
        outcome.player_name = player.full_name if player else None
        outcomes.append(outcome)

    return accepted, outcomes


def _record_aliases(
    db: Session,
    accepted: Sequence[ResolvedRow],
    outcomes: Sequence[RowOutcome],
    *,
    source: str,
    summary: ImportSummary,
    dry_run: bool,
) -> None:
    """Remember every accepted match, so the next import of this source never re-guesses.

    This is the step that makes a second import fast *and* stable: a 0.91 fuzzy hit becomes an
    `alias` hit at confidence 1.0, and stops depending on the matcher's threshold or on the
    rest of the player pool.
    """
    by_line = {outcome.line: outcome for outcome in outcomes}
    for resolved in accepted:
        outcome = by_line[resolved.row.line]
        if dry_run:
            existing = find_alias(db, source, resolved.row.name)
            if existing is not None and existing.player_id == resolved.player_id:
                summary.aliases_existing += 1
            else:
                summary.aliases_created += 1
            continue

        _, created = record_match(
            db,
            source,
            MatchResult(
                source_name=resolved.row.name,
                player_id=resolved.player_id,
                confidence=outcome.confidence,
                method=outcome.method,
            ),
        )
        if created:
            summary.aliases_created += 1
        else:
            summary.aliases_existing += 1


def run_import(
    db: Session,
    *,
    kind: str,
    source: str,
    season: int,
    text: str,
    column_map: Mapping[str, str] | None = None,
    delimiter: str | None = None,
    dry_run: bool = True,
    accept=None,
    options: Mapping[str, str] | None = None,
) -> ImportSummary:
    """Import one CSV/paste of one kind from one source. Previews unless `dry_run=False`.

    `options` are per-kind knobs passed straight through to the handler (`{'basis':
    'season'}` for a projection file of totals, `{'name': 'Top 200'}` to label a ranking set);
    the pipeline never interprets them.

    `accept` overrides the kind's auto-accept policy for this call — pass
    `accept_only_certain` to hold a file's fuzzy matches for confirmation even for a kind that
    would normally take them, or the other way round once you trust a source.

    Raises `ImportParseError` for a table we can't read and `UnknownKindError` for a kind
    nothing handles. Neither writes anything.
    """
    handler = get_kind(kind)
    if accept is not None:
        handler = ImportKind(
            name=handler.name,
            label=handler.label,
            columns=handler.columns,
            upsert=handler.upsert,
            accept=accept,
        )

    source = (source or "").strip()
    if not source:
        raise ImportParseError("an import needs a source name; it's how the rows are attributed")

    options = dict(options or {})
    table = parse_table(text, handler.columns, overrides=column_map, delimiter=delimiter)
    summary = ImportSummary(
        kind=handler.name,
        source=source,
        season=season,
        dry_run=dry_run,
        options=options,
        columns=table.columns.as_dict(),
        delimiter=table.delimiter,
        rows_parsed=len(table.rows),
        rows_skipped_blank=table.skipped_blank,
    )

    accepted, outcomes = match_rows(db, table, handler, source=source)
    summary.rows = outcomes
    for outcome in outcomes:
        setattr(summary, _COUNTER[outcome.status], getattr(summary, _COUNTER[outcome.status]) + 1)

    _record_aliases(db, accepted, outcomes, source=source, summary=summary, dry_run=dry_run)

    try:
        counts: UpsertCounts = handler.upsert(
            db,
            accepted,
            UpsertContext(source=source, season=season, dry_run=dry_run, options=options),
        )
    except Exception:
        # A handler that refuses (an unusable option, no scoring rules loaded) must not leave
        # this run's aliases half-written. All or nothing, both phases.
        db.rollback()
        raise
    summary.rows_created = counts.created
    summary.rows_updated = counts.updated
    summary.rows_unchanged = counts.unchanged
    summary.notes = list(counts.notes)

    if dry_run:
        # Nothing above wrote, but a stray autoflush would be a nasty surprise; make the
        # no-write promise structural rather than a matter of reading every handler.
        db.rollback()
    else:
        db.commit()
    return summary


_COUNTER = {
    STATUS_MATCHED: "matched",
    STATUS_REVIEW: "review",
    STATUS_UNMATCHED: "unmatched",
    STATUS_DUPLICATE: "duplicate",
    STATUS_INVALID: "invalid",
}
