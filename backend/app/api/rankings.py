"""Ranking reads: what boards we hold, and what is on one.

The write half is `POST /import/ranking` (see `app.ingest.ranking`); this is the read half,
and it is deliberately two endpoints rather than one fat board join. A ranking is a *list*, so
the two questions people actually ask are "which lists do we have?" and "what does this one
say?" — and the first has to be answerable without pulling 200 rows of the second.

Blending several sets into a consensus, and the tier/override layers on top (FEATURE_SPEC 5),
read from exactly these rows. Nothing is blended at read time here: a stored set comes back as
the source published it.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Player, RankingEntry, RankingSet
from app.db.session import get_db

router = APIRouter(prefix="/rankings", tags=["rankings"])


class RankingSetSummary(BaseModel):
    """One stored list, without its entries."""

    id: int
    source: str
    name: str
    season: int
    entry_count: int
    # When the set was last imported — a board's age is the first thing to know about it.
    as_of: datetime


class RankingEntryRow(BaseModel):
    """One player's place on a list, joined to who they actually are."""

    rank: int
    espn_player_id: int
    name: str
    nba_team: str | None = None
    positions: list[str] = []
    # As the source labelled it: "1", "Tier 2", "Elite". Text, never re-derived from `rank`.
    tier: str | None = None
    # The score the source printed beside the rank, if it printed one.
    value: float | None = None


class RankingSetDetail(RankingSetSummary):
    """The list, in its own order."""

    entries: list[RankingEntryRow]


@router.get("", response_model=list[RankingSetSummary])
def list_ranking_sets(
    db: Session = Depends(get_db),
    source: str | None = Query(None, description="Only sets published by this source"),
    season: int | None = Query(None, description="Only sets for this season"),
) -> list[RankingSetSummary]:
    """Every stored ranking set, newest import first."""
    counts = (
        select(RankingEntry.ranking_set_id, func.count().label("entry_count"))
        .group_by(RankingEntry.ranking_set_id)
        .subquery()
    )
    statement = (
        select(RankingSet, func.coalesce(counts.c.entry_count, 0))
        # Outer join: a set with no entries is still a set we hold, and hiding it would make
        # an import that resolved nothing look like it never happened.
        .outerjoin(counts, counts.c.ranking_set_id == RankingSet.id)
        .order_by(RankingSet.as_of.desc(), RankingSet.source, RankingSet.name)
    )
    if source is not None:
        statement = statement.where(RankingSet.source == source)
    if season is not None:
        statement = statement.where(RankingSet.season == season)

    return [
        RankingSetSummary(
            id=ranking_set.id,
            source=ranking_set.source,
            name=ranking_set.name,
            season=ranking_set.season,
            entry_count=entry_count,
            as_of=ranking_set.as_of,
        )
        for ranking_set, entry_count in db.execute(statement).all()
    ]


@router.get("/{ranking_set_id}", response_model=RankingSetDetail)
def get_ranking_set(
    ranking_set_id: int = Path(..., description="The set's id, from GET /rankings"),
    db: Session = Depends(get_db),
    limit: int | None = Query(None, ge=1, description="Return only the top N of the list"),
) -> RankingSetDetail:
    """One set's entries, in rank order, each joined to the player it resolved to."""
    ranking_set = db.get(RankingSet, ranking_set_id)
    if ranking_set is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"No ranking set {ranking_set_id}. GET /rankings lists the ones we hold; "
            "`make import KIND=ranking ...` adds one.",
        )

    statement = (
        select(RankingEntry, Player)
        .join(Player, Player.espn_player_id == RankingEntry.player_id)
        .where(RankingEntry.ranking_set_id == ranking_set_id)
        # Rank, then name: ranks can tie (a source that ranks by tier) and a board whose order
        # wobbles between two identical requests is a board nobody trusts.
        .order_by(RankingEntry.rank, Player.full_name)
    )
    if limit is not None:
        statement = statement.limit(limit)
    rows = db.execute(statement).all()

    return RankingSetDetail(
        id=ranking_set.id,
        source=ranking_set.source,
        name=ranking_set.name,
        season=ranking_set.season,
        # The size of the *set*, not of this page — `limit` narrows what you read, not what
        # the list is.
        entry_count=db.scalar(
            select(func.count())
            .select_from(RankingEntry)
            .where(RankingEntry.ranking_set_id == ranking_set_id)
        ),
        as_of=ranking_set.as_of,
        entries=[
            RankingEntryRow(
                rank=entry.rank,
                espn_player_id=player.espn_player_id,
                name=player.full_name,
                nba_team=player.nba_team,
                positions=list(player.positions or []),
                tier=entry.tier,
                value=entry.value,
            )
            for entry, player in rows
        ],
    )
