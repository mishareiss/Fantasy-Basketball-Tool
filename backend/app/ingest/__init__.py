"""Generic CSV/paste ingestion: any source's table -> our canonical, ESPN-keyed players.

Almost no external fantasy-basketball data has a usable free API. Consensus ADP, expert
projections, imported ranking lists and season-long sportsbook lines are all web-only, so the
architecture is import-first: paste or upload the table, and this package does the rest.

The pieces, in the order a row travels through them:

* `parser` — find the columns (forgivingly, by header alias) and read the rows.
* `app.matching` — resolve each foreign name to a `Player`. Not reimplemented here; the
  matcher, the normalization rules and the `PlayerAlias` memory all live there.
* `registry` — per-kind handlers. A kind declares its value columns, how to store a resolved
  row, and how careful to be about fuzzy matches. `adp` and `projection` are implemented;
  `ranking` and `market_line` are documented in `PLANNED_KINDS`.
* `pipeline` — the two-phase run: a dry run that previews everything and writes nothing, and
  a commit that persists rows plus the aliases that make the next import instant.
"""

from app.ingest.adp import ADP_COLUMNS, ADP_KIND
from app.ingest.parser import (
    NAME_ALIASES,
    POSITION_ALIASES,
    TEAM_ALIASES,
    ColumnMap,
    ImportParseError,
    ParsedRow,
    ParsedTable,
    ValueColumn,
    detect_columns,
    normalize_header,
    parse_number,
    parse_table,
    split_positions,
)
from app.ingest.pipeline import (
    MAX_CANDIDATES,
    STATUS_DUPLICATE,
    STATUS_INVALID,
    STATUS_MATCHED,
    STATUS_REVIEW,
    STATUS_UNMATCHED,
    ImportSummary,
    RowOutcome,
    match_rows,
    run_import,
)
from app.ingest.projection import (
    BASES,
    BASIS_PER_GAME,
    BASIS_SEASON,
    PROJECTION_COLUMNS,
    PROJECTION_IMPORT_KIND,
    PROJECTION_KIND,
    PROJECTION_STAT_ALIASES,
    StatLines,
    resolve_basis,
    stat_lines,
)
from app.ingest.registry import (
    KINDS,
    PLANNED_KINDS,
    ImportKind,
    ResolvedRow,
    UnknownKindError,
    UpsertContext,
    UpsertCounts,
    accept_matcher_threshold,
    accept_only_certain,
    get_kind,
    kind_names,
    register,
)

__all__ = [
    "ADP_COLUMNS",
    "ADP_KIND",
    "BASES",
    "BASIS_PER_GAME",
    "BASIS_SEASON",
    "KINDS",
    "MAX_CANDIDATES",
    "NAME_ALIASES",
    "PLANNED_KINDS",
    "POSITION_ALIASES",
    "PROJECTION_COLUMNS",
    "PROJECTION_IMPORT_KIND",
    "PROJECTION_KIND",
    "PROJECTION_STAT_ALIASES",
    "STATUS_DUPLICATE",
    "STATUS_INVALID",
    "STATUS_MATCHED",
    "STATUS_REVIEW",
    "STATUS_UNMATCHED",
    "TEAM_ALIASES",
    "ColumnMap",
    "ImportKind",
    "ImportParseError",
    "ImportSummary",
    "ParsedRow",
    "ParsedTable",
    "ResolvedRow",
    "RowOutcome",
    "StatLines",
    "UnknownKindError",
    "UpsertContext",
    "UpsertCounts",
    "ValueColumn",
    "accept_matcher_threshold",
    "accept_only_certain",
    "detect_columns",
    "get_kind",
    "kind_names",
    "match_rows",
    "normalize_header",
    "parse_number",
    "parse_table",
    "register",
    "resolve_basis",
    "run_import",
    "split_positions",
    "stat_lines",
]
