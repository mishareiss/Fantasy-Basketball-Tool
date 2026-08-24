"""Player-facing reads. The board is the first of them, and the point of the whole sync.

It answers the only question that matters two weeks before a dynasty startup: under *our*
scoring, who is worth the most per game — and where does ESPN's redraft room have them?
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AdpEntry, Player, Projection
from app.db.session import get_db
from app.espn.sync import ESPN_SOURCE, SEASON_PROJECTION_KIND

router = APIRouter(prefix="/players", tags=["players"])

DEFAULT_LIMIT = 50


class BoardRow(BaseModel):
    """One player's line on the board."""

    rank: int
    espn_player_id: int
    name: str
    nba_team: str | None = None
    positions: list[str] = []

    fantasy_points_per_game: float
    fantasy_points_total: float
    projected_games: float | None = None
    # How the per-game number was derived — see app.scoring.projections.
    per_game_basis: str

    # ESPN's REDRAFT average draft position, under ESPN's default scoring, not ours. The gap
    # between this and the rank on the left is the thing worth staring at.
    espn_adp: float | None = None
    espn_auction_value: float | None = None
    percent_owned: float | None = None


class BoardResponse(BaseModel):
    """A ranked slice of the board, plus what it was built from."""

    source: str
    kind: str
    season: int
    total_ranked: int
    position: str | None = None
    players: list[BoardRow]


@router.get("/board", response_model=BoardResponse)
def player_board(
    db: Session = Depends(get_db),
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=1000, description="How many rows to return"),
    position: str | None = Query(None, description="Filter to one position: PG, SG, SF, PF, or C"),
    source: str = Query(ESPN_SOURCE, description="Projection source to rank by"),
    season: int | None = Query(
        None, description="Projection season; defaults to the newest stored for this source"
    ),
) -> BoardResponse:
    """Players ranked by projected fantasy points per game under our custom scoring."""
    kind = SEASON_PROJECTION_KIND

    if season is None:
        season = db.scalar(
            select(Projection.season)
            .where(Projection.source == source, Projection.kind == kind)
            .order_by(Projection.season.desc())
            .limit(1)
        )
    if season is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"No {source!r} projections stored yet; run POST /sync/league (or `make sync`) first.",
        )

    rows = db.execute(
        select(Projection, Player, AdpEntry)
        .join(Player, Player.espn_player_id == Projection.player_id)
        # Outer join: a player we can price but ESPN has no market for still belongs on the
        # board — that is exactly the player we want to find.
        .outerjoin(
            AdpEntry,
            (AdpEntry.player_id == Projection.player_id) & (AdpEntry.source == source),
        )
        .where(
            Projection.source == source,
            Projection.kind == kind,
            Projection.season == season,
        )
        .order_by(Projection.fantasy_points_per_game.desc(), Player.full_name)
    ).all()

    # Positions live in a JSON column, and JSON containment is spelled differently in Postgres
    # and the SQLite the tests run on. The pool is ~1k rows, so filtering here costs nothing
    # and keeps one code path.
    if position:
        wanted = position.strip().upper()
        rows = [row for row in rows if wanted in (row.Player.positions or [])]

    return BoardResponse(
        source=source,
        kind=kind,
        season=season,
        total_ranked=len(rows),
        position=position.strip().upper() if position else None,
        players=[
            BoardRow(
                rank=rank,
                espn_player_id=player.espn_player_id,
                name=player.full_name,
                nba_team=player.nba_team,
                positions=player.positions or [],
                fantasy_points_per_game=projection.fantasy_points_per_game,
                fantasy_points_total=projection.fantasy_points_total,
                projected_games=projection.projected_games,
                per_game_basis=projection.per_game_basis,
                espn_adp=adp.adp if adp else None,
                espn_auction_value=adp.auction_value if adp else None,
                percent_owned=adp.percent_owned if adp else None,
            )
            for rank, (projection, player, adp) in enumerate(rows[:limit], start=1)
        ],
    )
