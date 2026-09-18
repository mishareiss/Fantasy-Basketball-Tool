"""Ranking logic: many sources of opinion, placed on one scale and averaged.

The *storage* was already here: `app.db.models.ranking` holds `RankingSet` / `RankingEntry`,
`app.db.models.projection` and `app.db.models.adp` hold the per-player numbers, and
`app.ingest.ranking` imports a board into the first of them. What this package adds is the
layer that makes those three shapes comparable (FEATURE_SPEC 5):

* `horizons` — the ONE translation between the board's value horizon (`current_year` |
  `dynasty`) and an imported list's tag (`dynasty` | `redraft`). Two vocabularies on purpose.
* `sources` — one adapter per storage shape, each answering the same question: for this
  horizon, where does this source put each player (a rank, and a percentile over the shared
  draftable pool)?
* `consensus` — pure, equal-weight averaging of the selected sources, plus the per-player
  spread that says how much they disagreed.

`GET /sources` and `GET /board/consensus` (`app.api.consensus`) are thin views over these.
`GET /players/board` is untouched and still the single-source value/tiers board.

Still ahead, and deliberately not here yet: per-source WEIGHTING and the personal composite
model (learn-from-edits), manual per-player overrides, market lines as a fourth source, and
versioned snapshots of a board.
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
    "KINDS",
    "KIND_ADP",
    "KIND_PROJECTION",
    "KIND_RANKING",
    "METHODS",
    "METHOD_PERCENTILE",
    "METHOD_RANK",
    "RANKING_TAG_BY_HORIZON",
    "ConsensusRow",
    "Placement",
    "RankingSource",
    "SourceCatalog",
    "SourceSpec",
    "UnknownHorizon",
    "UnknownMethod",
    "available_specs",
    "consensus_board",
    "load_catalog",
    "percentile_for",
    "ranking_tag_for_horizon",
]
