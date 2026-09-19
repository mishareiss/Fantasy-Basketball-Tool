"""Ranking logic: many sources of opinion, placed on one scale and averaged.

The *storage* was already here: `app.db.models.ranking` holds `RankingSet` / `RankingEntry`,
`app.db.models.projection` and `app.db.models.adp` hold the per-player numbers, and
`app.ingest.ranking` imports a board into the first of them. What this package adds is the
layer that makes those three shapes comparable (FEATURE_SPEC 5):

* `horizons` — the ONE translation between the board's value horizon (`current_year` |
  `dynasty`) and an imported list's tag (`dynasty` | `redraft`). Two vocabularies on purpose.
* `market` — sportsbook odds -> a fair per-game number: implied probability, de-vig, and a
  normal model with one configurable dispersion dial. Pure arithmetic, used by
  `app.ingest.market_line` to turn stored lines into a market projection, which then reaches
  the board as an ordinary value source below.
* `sources` — one adapter per storage shape, each answering the same question: for this
  horizon, where does this source put each player (a rank, and a percentile over the shared
  draftable pool)?
* `consensus` — pure, equal-weight averaging of the selected sources, plus the per-player
  spread that says how much they disagreed.

`GET /sources` and `GET /board/consensus` (`app.api.consensus`) are thin views over these.
`GET /players/board` is untouched and still the single-source value/tiers board.

The market is not a fourth adapter, on purpose. `app.ingest.market_line` derives a
`Projection` from the stored lines, so it is discovered by the value-source adapter as
`projection:market` and gets the age curve, the shared pool and the percentile scale with no
code in `sources` that knows a sportsbook exists.

Still ahead, and deliberately not here yet: per-source WEIGHTING and the personal composite
model (learn-from-edits), manual per-player overrides, and versioned snapshots of a board.
"""

from app.ranking.consensus import (
    METHOD_PERCENTILE,
    METHOD_RANK,
    METHODS,
    ConsensusRow,
    UnknownMethod,
    consensus_board,
)
from app.ranking.horizons import (
    RANKING_TAG_BY_HORIZON,
    UnknownHorizon,
    ranking_tag_for_horizon,
)
from app.ranking.market import (
    EVEN_PROBABILITY,
    MIN_LINE_SCALE,
    devig,
    dispersion,
    fair_value,
    implied_probability,
)
from app.ranking.master import (
    BOARD_METHOD,
    BoardRow,
    MasterBoard,
    OrderMismatch,
    UnknownTag,
    consensus_positions,
    load_entries,
    reconcile,
    reorder,
    reseed,
    upsert_entry,
)
from app.ranking.sources import (
    KIND_ADP,
    KIND_PROJECTION,
    KIND_RANKING,
    KINDS,
    Placement,
    RankingSource,
    SourceCatalog,
    SourceSpec,
    available_specs,
    load_catalog,
    percentile_for,
)

__all__ = [
    "BOARD_METHOD",
    "BoardRow",
    "ConsensusRow",
    "EVEN_PROBABILITY",
    "KINDS",
    "KIND_ADP",
    "KIND_PROJECTION",
    "KIND_RANKING",
    "METHODS",
    "METHOD_PERCENTILE",
    "METHOD_RANK",
    "MIN_LINE_SCALE",
    "MasterBoard",
    "OrderMismatch",
    "Placement",
    "RANKING_TAG_BY_HORIZON",
    "RankingSource",
    "SourceCatalog",
    "SourceSpec",
    "UnknownHorizon",
    "UnknownMethod",
    "UnknownTag",
    "available_specs",
    "consensus_board",
    "consensus_positions",
    "devig",
    "dispersion",
    "fair_value",
    "implied_probability",
    "load_catalog",
    "load_entries",
    "percentile_for",
    "ranking_tag_for_horizon",
    "reconcile",
    "reorder",
    "reseed",
    "upsert_entry",
]
