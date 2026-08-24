"""Player-facing reads. The board is the first of them, and the point of the whole sync.

It answers the only question that matters two weeks before a dynasty startup: under *our*
scoring, who is worth the most per game — and where does ESPN's redraft room have them?
"""

from datetime import date

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ages import NBA_SOURCE, compute_age
from app.config import get_settings
from app.db.models import AdpEntry, Player, PlayerAlias, Projection
from app.db.session import get_db
from app.espn.sync import ESPN_SOURCE, SEASON_PROJECTION_KIND
from app.matching import MANUAL_SOURCE, record_alias

router = APIRouter(prefix="/players", tags=["players"])

DEFAULT_LIMIT = 50

# What `GET /players/unresolved?need=` accepts. Only ages have a source today; the CSV import
# pipeline adds its own needs here rather than growing a second endpoint.
NEED_AGE = "age"


class BoardRow(BaseModel):
    """One player's line on the board."""

    rank: int
    espn_player_id: int
    name: str
    nba_team: str | None = None
    positions: list[str] = []

    # Whole years old at the board's `age_as_of`, from nba.com. None means we have no
    # birthdate for them — see GET /players/unresolved.
    age: int | None = None

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
    # The date every `age` on this board was computed at. Ages are stored as a number, so the
    # board has to say what date that number is true on, or it means nothing three months
    # from now.
    age_as_of: date
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
        age_as_of=get_settings().resolved_age_as_of(),
        players=[
            BoardRow(
                rank=rank,
                espn_player_id=player.espn_player_id,
                name=player.full_name,
                nba_team=player.nba_team,
                positions=player.positions or [],
                age=player.age,
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


class UnresolvedPlayer(BaseModel):
    """One of our players that a source hasn't been matched to, and why they're missing."""

    espn_player_id: int
    name: str
    nba_team: str | None = None
    positions: list[str] = []
    # True when a `nba_api` alias exists but the birthdate fetch hasn't run or came back empty.
    has_alias: bool
    # Their projected value, so the worklist is ordered by who actually matters.
    fantasy_points_per_game: float | None = None


class UnresolvedResponse(BaseModel):
    """The manual-alias worklist."""

    need: str
    source: str
    total: int
    players: list[UnresolvedPlayer]


@router.get("/unresolved", response_model=UnresolvedResponse)
def unresolved_players(
    db: Session = Depends(get_db),
    need: str = Query(NEED_AGE, description="What's missing. Only 'age' today."),
    limit: int = Query(100, ge=1, le=1000),
) -> UnresolvedResponse:
    """Players missing an age, most valuable first — the list to hand-resolve.

    Two kinds of row show up here. Most have no `nba_api` alias at all: nba.com has never
    heard of them (a draft-and-stash prospect, a G-League call-up, or a rookie newer than the
    installed `nba_api` roster). A few have an alias but no birthdate, meaning the fetch
    hasn't reached them yet. Fix the first kind with `POST /players/{id}/aliases`, the second
    by re-running the age sync.
    """
    if need != NEED_AGE:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"Unknown need {need!r}; only {NEED_AGE!r} is supported."
        )

    aliased = set(db.scalars(select(PlayerAlias.player_id).where(PlayerAlias.source == NBA_SOURCE)))
    best: dict[int, float] = {}
    for player_id, per_game in db.execute(
        select(Projection.player_id, Projection.fantasy_points_per_game)
    ):
        best[player_id] = max(best.get(player_id, 0.0), per_game)

    missing = [
        UnresolvedPlayer(
            espn_player_id=player.espn_player_id,
            name=player.full_name,
            nba_team=player.nba_team,
            positions=player.positions or [],
            has_alias=player.espn_player_id in aliased,
            fantasy_points_per_game=best.get(player.espn_player_id),
        )
        for player in db.scalars(select(Player).where(Player.age.is_(None)))
    ]
    # Projected value first, then name, so the same call twice gives the same order.
    missing.sort(key=lambda row: (-(row.fantasy_points_per_game or 0.0), row.name))

    return UnresolvedResponse(
        need=need, source=NBA_SOURCE, total=len(missing), players=missing[:limit]
    )


class AliasRequest(BaseModel):
    """A hand-made match: "this source calls our player that"."""

    source: str = Field(NBA_SOURCE, description="Which source names them this way")
    source_name: str = Field(..., min_length=1, description="The name as that source writes it")
    source_id: str | None = Field(None, description="That source's id, if it has one")


class AliasResponse(BaseModel):
    """The recorded alias."""

    espn_player_id: int
    name: str
    source: str
    source_name: str
    source_id: str | None = None
    confidence: float | None = None
    match_method: str | None = None
    created: bool
    # Set when the alias immediately gave us an age (a manual alias carrying no id can't).
    birthdate: date | None = None
    age: int | None = None


@router.post(
    "/{espn_player_id}/aliases", response_model=AliasResponse, status_code=status.HTTP_201_CREATED
)
def add_player_alias(
    espn_player_id: int = Path(..., description="Our canonical (ESPN) player id"),
    payload: AliasRequest = Body(...),
    db: Session = Depends(get_db),
) -> AliasResponse:
    """Record by hand what no matcher could work out — the escape hatch for the long tail.

    Nicknames, name changes, and players two of ours share a name with all end up here. Once
    recorded, every later run reads the alias instead of guessing, so this is a one-time fix
    per player. For `nba_api` specifically, adding the alias with the nba.com player id is
    what lets the next age sync fetch that player's birthdate.
    """
    player = db.get(Player, espn_player_id)
    if player is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No player {espn_player_id}")

    source = payload.source.strip() or MANUAL_SOURCE
    alias, created = record_alias(
        db,
        source=source,
        source_name=payload.source_name.strip(),
        source_id=payload.source_id.strip() if payload.source_id else None,
        player_id=espn_player_id,
        # A human said so; that's as confident as it gets, and 'manual' is how we'll tell
        # these apart from the automatic ones later.
        confidence=1.0,
        match_method=MANUAL_SOURCE,
        # A human's answer supersedes whatever the matcher had concluded before.
        restate_provenance=True,
    )
    db.commit()

    return AliasResponse(
        espn_player_id=espn_player_id,
        name=player.full_name,
        source=alias.source,
        source_name=alias.source_name,
        source_id=alias.source_id,
        confidence=alias.confidence,
        match_method=alias.match_method,
        created=created,
        birthdate=player.birthdate,
        age=compute_age(player.birthdate, get_settings().resolved_age_as_of())
        if player.birthdate
        else None,
    )
