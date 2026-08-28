"""The per-kind handler registry: what makes an ADP import different from a projection import.

Everything an import does is the same up to the last step — parse a table, find the columns,
resolve each name to a canonical `Player`, decide which matches are good enough — and the
pipeline owns all of it. A *kind* only has to declare the three things that genuinely differ:

1. **Which numbers it wants** off each row (`columns`), with the header aliases sources use.
2. **How to store a resolved (player, values) pair** (`upsert`) — the one function that knows
   which table this kind lives in.
3. **How careful to be** (`accept`) — whether a fuzzy name is good enough to write without a
   human looking at it.

That is the whole extension surface, and `projection` and `ranking` prove it: each added its
own columns and an upsert, and neither touched the parse/match/review flow. Their new
requirements — a per-file `basis`, a per-file set `name` — are carried as an opaque `options`
mapping on `UpsertContext`, so the pipeline still doesn't know what either kind is.

`ranking` also stretched the surface in the one place it had to: its upsert replaces a whole
set rather than upserting rows, and says so through `UpsertCounts.notes`. Even that is a
handler decision, not a pipeline one.

See `PLANNED_KINDS` for what the one remaining kind needs, which is deliberately *not*
pipeline work: it's a model, a migration, and some odds arithmetic.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.ingest.parser import ParsedRow, ValueColumn
from app.matching import DEFAULT_THRESHOLD, METHOD_FUZZY, MatchResult


class UnknownKindError(KeyError):
    """Asked to import a kind nothing is registered for."""


@dataclass(frozen=True)
class ResolvedRow:
    """A parsed row that resolved to one of our players — what a handler actually stores."""

    player_id: int
    row: ParsedRow

    def value(self, field_name: str) -> float | None:
        """One numeric column off the row. None when the source left the cell empty."""
        value = self.row.values.get(field_name)
        return value if isinstance(value, float | int) else None

    def text(self, field_name: str) -> str | None:
        """One text column off the row (a `PARSE_TEXT` `ValueColumn`, e.g. a tier label)."""
        value = self.row.values.get(field_name)
        return value if isinstance(value, str) else None


@dataclass
class UpsertCounts:
    """What an upsert did (or, in a dry run, would do)."""

    created: int = 0
    updated: int = 0
    unchanged: int = 0
    # Anything the three counters can't say, in the handler's own words, surfaced in the
    # preview and the receipt. `ranking` needs it: its counters describe rows, and the fact a
    # whole set was replaced — 14 entries out, 12 in — is about the *set*. Free text on
    # purpose; nothing downstream parses these.
    notes: list[str] = field(default_factory=list)

    @property
    def written(self) -> int:
        return self.created + self.updated


@dataclass(frozen=True)
class UpsertContext:
    """Everything a handler needs beyond the rows themselves."""

    source: str
    season: int
    # A dry run must read the same way and write nothing, so the preview's created/updated
    # counts are the real ones rather than a guess. Handlers honour this instead of the
    # pipeline trying to roll a transaction back.
    dry_run: bool = False
    # Per-kind knobs, passed through from `--basis per_game` / the API's `options` body. A
    # free-form mapping rather than typed fields, because the pipeline has no business knowing
    # what any of them mean — `projection` reads 'basis', `ranking` reads 'name'; `adp` reads
    # nothing. A handler that gets an option it doesn't understand should say so (see
    # `resolve_basis`, `resolve_options`) rather than ignore it, since a silently-dropped
    # `--basis season` imports numbers off by a factor of seventy, and a silently-dropped
    # `--name` replaces the wrong ranking set.
    options: Mapping[str, str] = field(default_factory=dict)


UpsertFn = Callable[[Session, Sequence[ResolvedRow], UpsertContext], UpsertCounts]
AcceptPolicy = Callable[[MatchResult], bool]


def accept_matcher_threshold(result: MatchResult) -> bool:
    """Auto-accept anything the matcher resolved at all.

    The matcher has already refused everything below its own threshold and everything
    ambiguous, so "it resolved" *is* "it cleared the threshold". Right for ADP: the cost of a
    wrong row is one number on a board we're reading by eye, and the cost of holding 200 rows
    for confirmation is that nobody imports anything.
    """
    return result.matched and result.confidence >= DEFAULT_THRESHOLD


def accept_only_certain(result: MatchResult) -> bool:
    """Auto-accept only names read off the page — an exact, normalized, or aliased hit.

    For a kind where a mis-attributed row is expensive and quietly wrong: a market line is a
    number we'd bet on, and hanging Jalen Williams' over/under on Jalen Wilson is worse than
    an empty cell. Fuzzy hits come back as review rows with their candidates, and confirming
    one writes an alias, so the confirmation is asked once per player ever.
    """
    return result.matched and result.method != METHOD_FUZZY


@dataclass(frozen=True)
class ImportKind:
    """One importable kind of data, and the three things that make it itself."""

    name: str
    # One line, shown by the CLI and the API's kind listing.
    label: str
    columns: tuple[ValueColumn, ...]
    upsert: UpsertFn
    accept: AcceptPolicy = accept_matcher_threshold

    @property
    def required_fields(self) -> tuple[str, ...]:
        return tuple(column.field for column in self.columns if column.required)


KINDS: dict[str, ImportKind] = {}


def register(kind: ImportKind) -> ImportKind:
    """Add a kind to the registry. Called at import time by each handler module."""
    KINDS[kind.name] = kind
    return kind


def get_kind(name: str) -> ImportKind:
    """The handler for this kind, or `UnknownKindError` naming the ones that exist."""
    kind = KINDS.get((name or "").strip().lower())
    if kind is None:
        known = ", ".join(sorted(KINDS)) or "none"
        planned = ", ".join(sorted(PLANNED_KINDS))
        raise UnknownKindError(
            f"unknown import kind {name!r}. Registered: {known}. Planned (not built): {planned}."
        )
    return kind


def kind_names() -> list[str]:
    return sorted(KINDS)


# The deferred kinds, and what each one needs before it can be registered. None of it is
# pipeline work — parsing, matching, review, and idempotency are already done and shared.
# `adp`, `projection` and `ranking` are built; this one is what's left.
PLANNED_KINDS: dict[str, str] = {
    "market_line": (
        "Season-long sportsbook props (season totals / PPG over-unders with American odds) -> "
        "new `MarketLine` model, then de-vig: implied probability per side, remove the "
        "overround, take the fair line, and turn per-stat lines into a market-implied "
        "projection priced under our scoring. Needs: that model plus a migration, an odds "
        "parser (+130 / -155), the de-vig maths, over/under column pairing, and "
        "`accept_only_certain` as its policy."
    ),
}
