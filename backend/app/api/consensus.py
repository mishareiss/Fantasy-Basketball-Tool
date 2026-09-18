"""The multi-source board: what can rank players, and what they say when averaged.

Two endpoints, and they are two halves of one question. `GET /sources` answers "whose opinion
do we hold, for this horizon?" — the chips a client offers. `GET /board/consensus` answers
"and what do these ones, together, think?" — a column per source, a consensus, and how much
they disagreed.

`GET /players/board` is untouched: it is still the single-source value board, with the age
curve and the tiers, and it is still what you want when the question is "what is he worth
under our scoring" rather than "who does the room like".

Everything that decides what a source IS lives in `app.ranking.sources`, and everything that
decides how they are averaged lives in `app.ranking.consensus`. This module is serialization
and 400s.
"""

from dataclasses import dataclass
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Player
from app.db.session import get_db
from app.ranking import (
    METHOD_RANK,
    METHODS,
    RankingSource,
    SourceCatalog,
    UnknownHorizon,
    UnknownMethod,
    consensus_board,
    load_catalog,
)
from app.valuation import HORIZON_CURRENT_YEAR, HORIZON_DYNASTY

router = APIRouter(tags=["consensus"])

DEFAULT_LIMIT = 100

HORIZON_DESCRIPTION = (
    f"Which value horizon the board is asking about: {HORIZON_CURRENT_YEAR!r} (win-now) or "
    f"{HORIZON_DYNASTY!r}. Value sources are priced by it directly; imported rank-only lists "
    f"are filtered by the tag it maps to ({HORIZON_DYNASTY!r} -> 'dynasty', "
    f"{HORIZON_CURRENT_YEAR!r} -> 'redraft')."
)


class SourceInfo(BaseModel):
    """One source a board can be built from — app.ranking.sources: SourceSpec + coverage."""

    # The stable handle to select this source with. Built from what identifies the source and
    # not from its season, so next season's sync doesn't invalidate a saved selection.
    id: str
    label: str
    # 'projection' | 'adp' | 'ranking' — the storage shape, so a client can group the chips.
    kind: str
    # The publisher: 'espn', 'Dizzle Dynasty'. One vocabulary across all three kinds.
    source: str
    season: int | None = None
    # The rank-set tag ('dynasty' | 'redraft') a ranking source declared at import. Null for
    # projection and ADP sources, which derive both horizons from production instead.
    horizon: str | None = None
    # How many of the shared draftable pool this source has an opinion about — its coverage.
    # The number to read a consensus against: a source ranking 449 players and one ranking
    # 1,095 disagree about far more than the board can show.
    player_count: int


class SourcesResponse(BaseModel):
    """Everything that can rank players under one horizon."""

    horizon: str
    # The `RankingSet.horizon` tag this board horizon accepts imported lists from.
    ranking_horizon: str
    # Every player at least one AVAILABLE source ranks — the percentile denominator, and the
    # reason ticking a box never restates the other sources' numbers.
    pool_size: int
    sources: list[SourceInfo]


class ConsensusCell(BaseModel):
    """Where one source put one player."""

    # Published for an imported list (gaps and all); a competition rank over the source's own
    # ordering for a projection or an ADP table, so tied opinions share a number.
    rank: int
    # That rank as a position in the shared draftable pool: 100 at the top, 0 at the bottom.
    percentile: float


class ConsensusPlayerRow(BaseModel):
    """One player's line on the consensus board — one row per player, always."""

    # Place on THIS board, after any position filter — same convention as /players/board.
    rank: int
    espn_player_id: int
    name: str
    nba_team: str | None = None
    positions: list[str] = []
    age: int | None = None

    # The equal-weight average over the sources that rank him, in the method's units: places
    # for method='rank' (lower is better), percentile points for 'percentile' (higher is).
    consensus: float
    # source id -> that source's cell. A source with no opinion on him has NO key here; the
    # absence is the fact, and it is named in `sources_missing`.
    cells: dict[str, ConsensusCell]
    # How many of the selected sources rank him, and which ones don't. A player missing from a
    # source is EXCLUDED from the average, never counted as last — so a consensus of 8.0 off
    # one source out of three is not the same claim as a consensus of 8.0 off all three, and
    # this is how you tell them apart.
    sources_present: int
    sources_missing: list[str] = []
    # Disagreement across the sources that DO rank him: `spread` in percentile points (the
    # scale that means the same thing whatever the list lengths — colour by this one),
    # `rank_spread` in places. Both null below two sources: one source can't disagree with
    # itself, and a 0 there would paint perfect agreement.
    spread: float | None = None
    rank_spread: float | None = None


class ConsensusResponse(BaseModel):
    """A consensus board, plus every source that went into it."""

    horizon: str
    ranking_horizon: str
    # 'rank' or 'percentile'.
    method: str
    pool_size: int
    position: str | None = None
    # Players on this board after the position filter, before `limit`.
    total_ranked: int
    # The date every `age` here was computed at — an age without one means nothing later.
    age_as_of: date
    # The SELECTED sources, in the order asked for: the column order, decided by the server so
    # the cells and the headers cannot drift apart.
    sources: list[SourceInfo]
    players: list[ConsensusPlayerRow]


@dataclass(frozen=True)
class _Loaded:
    """A catalog plus the identities of everyone in its pool."""

    catalog: SourceCatalog
    players: dict[int, Player]


def _source_info(source: RankingSource) -> SourceInfo:
    return SourceInfo(
        id=source.spec.id,
        label=source.spec.label,
        kind=source.spec.kind,
        source=source.spec.source,
        season=source.spec.season,
        horizon=source.spec.ranking_horizon,
        player_count=source.player_count,
    )


def _load(db: Session, horizon: str) -> _Loaded:
    """Read the catalog for a horizon, 400ing on a horizon nobody defined."""
    try:
        catalog = load_catalog(db, horizon)
    except UnknownHorizon as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error

    players = {
        player.espn_player_id: player
        for player in db.scalars(select(Player).where(Player.espn_player_id.in_(catalog.pool)))
    }
    return _Loaded(catalog=catalog, players=players)


@router.get("/sources", response_model=SourcesResponse)
def list_sources(
    db: Session = Depends(get_db),
    horizon: str = Query(HORIZON_DYNASTY, description=HORIZON_DESCRIPTION),
) -> SourcesResponse:
    """Every source that can rank players under this horizon, and how deep each one goes.

    The horizon is not a filter on the OUTPUT so much as on what is even eligible. Value
    sources (a `Projection`, and market lines later) appear under both horizons because a
    per-player number can be aged — the same ESPN projection ranks a different board under
    `dynasty` than under `current_year`. Imported rank-only lists carry no stats to age, so
    they appear only under the horizon their declared tag maps to: our dynasty top-200 is not
    an answer to "who helps me this season" and does not show up as one. ADP is offered under
    both: it is a redraft market by nature, and on a dynasty board it is the thing you are
    trying to beat.
    """
    loaded = _load(db, horizon)
    return SourcesResponse(
        horizon=loaded.catalog.horizon,
        ranking_horizon=loaded.catalog.ranking_horizon,
        pool_size=loaded.catalog.pool_size,
        sources=[_source_info(source) for source in loaded.catalog.sources],
    )


@router.get("/board/consensus", response_model=ConsensusResponse)
def consensus_view(
    db: Session = Depends(get_db),
    horizon: str = Query(HORIZON_DYNASTY, description=HORIZON_DESCRIPTION),
    sources: str | None = Query(
        None,
        description="Comma-separated source ids from GET /sources. Defaults to all of them.",
    ),
    method: str = Query(
        METHOD_RANK,
        description="How the sources are averaged — one of "
        + ", ".join(repr(name) for name in METHODS)
        + ": 'rank' averages places (lower is better), 'percentile' averages 0-100 positions "
        "in the shared pool (higher is better).",
    ),
    position: str | None = Query(None, description="Filter to one position: PG, SG, SF, PF, or C"),
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=1000, description="How many rows to return"),
) -> ConsensusResponse:
    """Several sources side by side, averaged, with the disagreement called out.

    Every selected source contributes EQUALLY — per-source weighting is a later task. A player
    missing from a source is left out of that source's average rather than treated as last on
    it, because a list that stops at 200 names is saying nothing about the 201st, not that he
    is worthless. `sources_present` and `sources_missing` are on every row so a consensus
    built from one source out of three is never read as one built from three.

    The rows are ordered by the consensus, and the interesting ones are not at the top: sort
    by `spread` to find the players the sources disagree most violently about, which is where
    a board full of other people's opinions is actually worth something.
    """
    loaded = _load(db, horizon)
    catalog = loaded.catalog

    if not catalog.sources:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"No ranking sources stored for horizon {horizon!r}. Run POST /sync/league (or "
            "`make sync`) for ESPN's projection and ADP, and `make import KIND=ranking ...` "
            "for an imported board.",
        )

    if sources is None:
        selected = list(catalog.sources)
    else:
        requested = [part.strip() for part in sources.split(",") if part.strip()]
        if not requested:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "`sources` was empty; pick at least one id from GET /sources, or omit the "
                "parameter to use all of them.",
            )
        selected, unknown = catalog.select(requested)
        if unknown:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Unknown source id(s) {unknown} for horizon {horizon!r}. GET /sources lists "
                "what is eligible — note that a rank-only list is only eligible under the "
                "horizon its tag maps to.",
            )

    try:
        rows = consensus_board(
            selected,
            method,
            names={player_id: player.full_name for player_id, player in loaded.players.items()},
        )
    except UnknownMethod as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error

    # Same shape as /players/board: positions live in a JSON column, JSON containment is
    # spelled differently in Postgres and the SQLite the tests run on, and the pool is ~1k
    # rows — so it is filtered here, after the consensus, and one code path stays.
    wanted = position.strip().upper() if position else None
    if wanted:
        rows = [row for row in rows if wanted in (loaded.players[row.player_id].positions or [])]

    return ConsensusResponse(
        horizon=catalog.horizon,
        ranking_horizon=catalog.ranking_horizon,
        method=method,
        pool_size=catalog.pool_size,
        position=wanted,
        total_ranked=len(rows),
        age_as_of=get_settings().resolved_age_as_of(),
        sources=[_source_info(source) for source in selected],
        players=[
            ConsensusPlayerRow(
                rank=place,
                espn_player_id=row.player_id,
                name=loaded.players[row.player_id].full_name,
                nba_team=loaded.players[row.player_id].nba_team,
                positions=list(loaded.players[row.player_id].positions or []),
                age=loaded.players[row.player_id].age,
                consensus=row.consensus,
                cells={
                    source_id: ConsensusCell(rank=cell.rank, percentile=cell.percentile)
                    for source_id, cell in row.cells.items()
                },
                sources_present=len(row.sources_present),
                sources_missing=list(row.sources_missing),
                spread=row.spread,
                rank_spread=row.rank_spread,
            )
            for place, row in enumerate(rows[:limit], start=1)
        ],
    )
