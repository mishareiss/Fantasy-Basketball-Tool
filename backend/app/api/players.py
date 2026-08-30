"""Player-facing reads. The board is the first of them, and the point of the whole sync.

It answers the only question that matters two weeks before a dynasty startup: under *our*
scoring, who is worth the most per game — and where does ESPN's redraft room have them?
"""

from dataclasses import dataclass
from datetime import date

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import false as sa_false
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ages import NBA_SOURCE, compute_age
from app.config import get_settings
from app.db.models import AdpEntry, Player, PlayerAlias, Projection
from app.db.session import get_db
from app.espn.sync import ESPN_SOURCE, SEASON_PROJECTION_KIND
from app.matching import MANUAL_SOURCE, record_alias
from app.valuation import PlayerValue, Tiering, tier_structure, value_player

router = APIRouter(prefix="/players", tags=["players"])

DEFAULT_LIMIT = 50

# The two value horizons the board can be ranked by (FEATURE_SPEC 4). Both are computed for
# every row either way; the horizon only decides the ORDER, so flipping the toggle re-ranks the
# same numbers rather than fetching a different board.
HORIZON_CURRENT_YEAR = "current_year"
HORIZON_DYNASTY = "dynasty"
HORIZONS = (HORIZON_CURRENT_YEAR, HORIZON_DYNASTY)

# What `GET /players/unresolved?need=` accepts. One endpoint per *question* ("who is our
# board still missing X for"), not one per source — a new imported kind adds a need here.
NEED_AGE = "age"
NEED_ADP = "adp"

# Whether the board cuts itself into tiers (FEATURE_SPEC 6). `auto` is the default because a
# flat 1,095-row list is not a draft plan; `off` is for a client that draws its own breaks —
# and, once the UI can drag dividers, for the board those manual breaks are applied to.
TIERS_AUTO = "auto"
TIERS_OFF = "off"
TIER_MODES = (TIERS_AUTO, TIERS_OFF)


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

    # The market's REDRAFT average draft position from `adp_source` (ESPN's is under ESPN's
    # default scoring, not ours). The gap between this and the rank on the left is the thing
    # worth staring at. Not named `espn_adp` any more: the source is now a query parameter.
    adp: float | None = None
    auction_value: float | None = None
    percent_owned: float | None = None

    # Both value horizons, always (FEATURE_SPEC 4) — the toggle picks which one ranks the
    # board, never which one is computed. Current-year IS `fantasy_points_per_game`: win-now
    # value is the projection, and repeating it under a value name is what lets the two
    # columns sit side by side and be compared.
    current_year_value: float
    # Current-year value through the age curve: see GET /valuation/curve for the active shape.
    dynasty_value: float
    age_multiplier: float
    # False when we hold no birthdate for him, in which case the multiplier is 1.0 because
    # there was nothing to adjust with — NOT because the curve judged him to be in his prime.
    # Those two are the same number and mean opposite things, so the board says which it is.
    age_adjusted: bool

    # Which tier of the SELECTED horizon he lands in, 1 being the top. None means he is below
    # the tiered pool (TIER_POOL players deep) or that `tiers=off` — either way, untiered
    # rather than last. Tiers are cut from the OVERALL ranking, so a filtered board still
    # shows a player his real tier, not his tier among the centers.
    tier: int | None = None


class TierSummaryRow(BaseModel):
    """One tier on this board — enough for a client to draw the dividers itself."""

    tier: int
    size: int
    # The band, in the selected horizon's units: the first and last player's value.
    value_high: float
    value_low: float
    # 1-based overall rank the tier starts at.
    start_rank: int
    # The drop that opened it, and that drop as a multiple of the typical (median) drop. Null
    # for tier 1, which the top of the board opens rather than a break.
    gap: float | None = None
    gap_ratio: float | None = None


class BoardResponse(BaseModel):
    """A ranked slice of the board, plus what it was built from."""

    source: str
    kind: str
    season: int
    # Which source's draft market is in the `adp` column, and for which season. Displayed ADP
    # is deliberately independent of the projection source doing the ranking: ESPN prices the
    # players, an imported consensus can price the room.
    adp_source: str
    adp_season: int | None = None
    total_ranked: int
    position: str | None = None
    # Which horizon's value the rows are ordered by.
    horizon: str
    # The date every `age` on this board was computed at. Ages are stored as a number, so the
    # board has to say what date that number is true on, or it means nothing three months
    # from now.
    age_as_of: date
    # 'auto' or 'off'.
    tiers: str
    # How many players were tiered — the top TIER_POOL of the OVERALL ranking, so this does
    # not shrink when a position filter or a small `limit` narrows what is returned. 0 when
    # tiering is off.
    tier_pool: int = 0
    # The tier structure behind the `tier` column, so a client can draw dividers without
    # recomputing them (and without being able to disagree with the board about where they
    # are). Describes the overall board, not the filtered page.
    tier_summary: list[TierSummaryRow] = []
    players: list[BoardRow]


@dataclass(frozen=True)
class RankedEntry:
    """One player, valued and placed — what both the board and the tier endpoint work from."""

    projection: Projection
    player: Player
    adp: AdpEntry | None
    value: PlayerValue
    tier: int | None


@dataclass(frozen=True)
class RankedBoard:
    """The whole board under one horizon: every player in order, and how it was tiered."""

    source: str
    season: int
    adp_source: str
    adp_season: int | None
    horizon: str
    tiers: str
    # Ordered by the selected horizon, every player we can price. Unfiltered and unsliced:
    # `position` and `limit` are presentation, applied by the route after the fact.
    entries: list[RankedEntry]
    # None when tiering is off.
    tiering: Tiering | None


def horizon_value(value: PlayerValue, horizon: str) -> float:
    """The one number a horizon ranks by — and therefore the one tiers are cut from.

    Single-sourced on purpose. The whole point of tiering the board is that the breaks fall in
    the values the board is ordered by; a second expression of "which number is this horizon"
    is a second board waiting to disagree with the first.
    """
    return value.dynasty if horizon == HORIZON_DYNASTY else value.current_year


def ranked_board(
    db: Session,
    *,
    source: str = ESPN_SOURCE,
    season: int | None = None,
    adp_source: str = ESPN_SOURCE,
    adp_season: int | None = None,
    horizon: str = HORIZON_DYNASTY,
    tiers: str = TIERS_AUTO,
) -> RankedBoard:
    """Build the full board: query, value, order by the horizon, and cut it into tiers.

    Everything that decides *what the board is* lives here, so `GET /players/board` and
    `GET /valuation/tiers` are two views of one computation rather than two computations that
    have to be kept in agreement.
    """
    if horizon not in HORIZONS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Unknown horizon {horizon!r}; supported: "
            + ", ".join(repr(name) for name in HORIZONS)
            + ".",
        )
    if tiers not in TIER_MODES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Unknown tiers mode {tiers!r}; supported: "
            + ", ".join(repr(name) for name in TIER_MODES)
            + ".",
        )

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

    # ADP is keyed by season, so the join has to pin one. Without that, a player whose ADP we
    # hold for two seasons joins twice and fans out into duplicate board rows — the board must
    # be exactly one row per player. Newest by default: that is this year's market.
    if adp_season is None:
        adp_season = db.scalar(
            select(AdpEntry.season)
            .where(AdpEntry.source == adp_source)
            .order_by(AdpEntry.season.desc())
            .limit(1)
        )

    adp_match = (AdpEntry.player_id == Projection.player_id) & (AdpEntry.source == adp_source)
    if adp_season is None:
        # No ADP stored for that source at all: nothing to join, and the column reads null.
        adp_match = adp_match & sa_false()
    else:
        adp_match = adp_match & (AdpEntry.season == adp_season)

    rows = db.execute(
        select(Projection, Player, AdpEntry)
        .join(Player, Player.espn_player_id == Projection.player_id)
        # Outer join: a player we can price but the market has no read on still belongs on the
        # board — that is exactly the player we want to find.
        .outerjoin(AdpEntry, adp_match)
        .where(
            Projection.source == source,
            Projection.kind == kind,
            Projection.season == season,
        )
        .order_by(Projection.fantasy_points_per_game.desc(), Player.full_name)
    ).all()

    settings = get_settings()
    # One curve for the whole response: every row on a board must be priced by the same
    # parameters, or the ranking compares numbers that don't mean the same thing.
    curve = settings.dynasty_curve()
    # Per-game is the basis the board ranks on, so that is what the horizons are computed from.
    valued = [
        (
            projection,
            player,
            adp,
            value_player(projection.fantasy_points_per_game, player.age, curve),
        )
        for projection, player, adp in rows
    ]

    if horizon == HORIZON_DYNASTY:
        # Re-rank in Python rather than in SQL: the multiplier depends on `Player.age` through
        # the curve, and pushing a piecewise function into SQLite-and-Postgres-compatible SQL
        # would buy nothing on a ~1k-row pool except a second definition of the curve to keep
        # in step with this one. Same tie-break as the SQL order — full_name — so the board is
        # stable between identical calls.
        valued.sort(key=lambda entry: (-entry[3].dynasty, entry[1].full_name))
    # current_year needs no sort at all: it IS the per-game projection, and the query already
    # came back ordered by exactly that. Leaving the rows untouched is what makes
    # `horizon=current_year` byte-for-byte the board we had before this endpoint had horizons.

    tiering: Tiering | None = None
    assignments: list[int | None] = [None] * len(valued)
    if tiers == TIERS_AUTO:
        # Tier the numbers the board is ALREADY ordered by — never a value recomputed some
        # other way — over the whole ranking, before any position filter. A point guard's tier
        # is his tier on the board, not his tier among point guards.
        tiering = tier_structure(
            [horizon_value(entry[3], horizon) for entry in valued], settings.tier_params()
        )
        assignments = tiering.assignments

    return RankedBoard(
        source=source,
        season=season,
        adp_source=adp_source,
        adp_season=adp_season,
        horizon=horizon,
        tiers=tiers,
        entries=[
            RankedEntry(projection=projection, player=player, adp=adp, value=value, tier=tier)
            for (projection, player, adp, value), tier in zip(valued, assignments, strict=True)
        ],
        tiering=tiering,
    )


@router.get("/board", response_model=BoardResponse)
def player_board(
    db: Session = Depends(get_db),
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=1000, description="How many rows to return"),
    position: str | None = Query(None, description="Filter to one position: PG, SG, SF, PF, or C"),
    source: str = Query(ESPN_SOURCE, description="Projection source to rank by"),
    season: int | None = Query(
        None, description="Projection season; defaults to the newest stored for this source"
    ),
    adp_source: str = Query(ESPN_SOURCE, description="Whose ADP to display in the adp column"),
    adp_season: int | None = Query(
        None, description="ADP season; defaults to the newest stored for that ADP source"
    ),
    horizon: str = Query(
        HORIZON_DYNASTY,
        description=f"Which value horizon ranks the board: {HORIZON_CURRENT_YEAR!r} or "
        f"{HORIZON_DYNASTY!r}",
    ),
    tiers: str = Query(
        TIERS_AUTO,
        description=f"Auto-tier the board by score gaps: {TIERS_AUTO!r} or {TIERS_OFF!r}",
    ),
) -> BoardResponse:
    """Players ranked by projected fantasy points per game under our custom scoring.

    `horizon=current_year` ranks by that number as-is (win-now). `horizon=dynasty`, the default
    because this is a dynasty startup, ranks by the same number through the age/longevity
    curve. Both values are on every row regardless.

    `tiers=auto` (the default) also cuts the top TIER_POOL players into tiers wherever the drop
    to the next player is unusually large — the line between "draft anyone still here" and
    "reach". The tiers are computed over the overall ranking and are therefore identical
    whether you ask for 20 rows or 200; a `position` filter narrows who is shown, never what
    tier they are in. See GET /valuation/tiers for the structure and the gaps behind it.
    """
    board = ranked_board(
        db,
        source=source,
        season=season,
        adp_source=adp_source,
        adp_season=adp_season,
        horizon=horizon,
        tiers=tiers,
    )

    entries = board.entries
    # Positions live in a JSON column, and JSON containment is spelled differently in Postgres
    # and the SQLite the tests run on. The pool is ~1k rows, so filtering here costs nothing
    # and keeps one code path. It happens AFTER tiering, on purpose: see `ranked_board`.
    if position:
        wanted = position.strip().upper()
        entries = [entry for entry in entries if wanted in (entry.player.positions or [])]

    return BoardResponse(
        source=board.source,
        kind=SEASON_PROJECTION_KIND,
        season=board.season,
        adp_source=board.adp_source,
        adp_season=board.adp_season,
        total_ranked=len(entries),
        position=position.strip().upper() if position else None,
        horizon=board.horizon,
        age_as_of=get_settings().resolved_age_as_of(),
        tiers=board.tiers,
        tier_pool=board.tiering.pool_size if board.tiering else 0,
        tier_summary=[
            TierSummaryRow(
                tier=tier.tier,
                size=tier.size,
                value_high=tier.value_high,
                value_low=tier.value_low,
                start_rank=tier.start_index + 1,
                gap=tier.gap,
                gap_ratio=tier.gap_ratio,
            )
            for tier in (board.tiering.tiers if board.tiering else [])
        ],
        players=[
            BoardRow(
                rank=rank,
                espn_player_id=entry.player.espn_player_id,
                name=entry.player.full_name,
                nba_team=entry.player.nba_team,
                positions=entry.player.positions or [],
                age=entry.player.age,
                fantasy_points_per_game=entry.projection.fantasy_points_per_game,
                fantasy_points_total=entry.projection.fantasy_points_total,
                projected_games=entry.projection.projected_games,
                per_game_basis=entry.projection.per_game_basis,
                adp=entry.adp.adp if entry.adp else None,
                auction_value=entry.adp.auction_value if entry.adp else None,
                percent_owned=entry.adp.percent_owned if entry.adp else None,
                current_year_value=entry.value.current_year,
                dynasty_value=entry.value.dynasty,
                age_multiplier=entry.value.multiplier,
                age_adjusted=entry.value.age_adjusted,
                tier=entry.tier,
            )
            for rank, entry in enumerate(entries[:limit], start=1)
        ],
    )


class UnresolvedPlayer(BaseModel):
    """One of our players that a source hasn't been matched to, and why they're missing."""

    espn_player_id: int
    name: str
    nba_team: str | None = None
    positions: list[str] = []
    # True when an alias for the source already exists — so the gap is not a naming problem.
    # For 'age' that means the birthdate fetch hasn't run or came back empty; for 'adp' it
    # means we've imported that source before and this player simply wasn't in the file.
    has_alias: bool
    # Their projected value, so the worklist is ordered by who actually matters.
    fantasy_points_per_game: float | None = None


class UnresolvedResponse(BaseModel):
    """The manual-alias worklist."""

    need: str
    source: str
    # Only meaningful for season-keyed needs ('adp'); null for 'age'.
    season: int | None = None
    total: int
    players: list[UnresolvedPlayer]


def _best_per_game(db: Session) -> dict[int, float]:
    """Each player's best projected fantasy points per game, across every stored projection."""
    best: dict[int, float] = {}
    for player_id, per_game in db.execute(
        select(Projection.player_id, Projection.fantasy_points_per_game)
    ):
        best[player_id] = max(best.get(player_id, 0.0), per_game)
    return best


@router.get("/unresolved", response_model=UnresolvedResponse)
def unresolved_players(
    db: Session = Depends(get_db),
    need: str = Query(NEED_AGE, description=f"What's missing: {NEED_AGE!r} or {NEED_ADP!r}"),
    source: str | None = Query(
        None, description=f"For {NEED_ADP!r}: which ADP source. Defaults to {ESPN_SOURCE!r}."
    ),
    season: int | None = Query(
        None, description=f"For {NEED_ADP!r}: which season. Defaults to the newest stored."
    ),
    limit: int = Query(100, ge=1, le=1000),
) -> UnresolvedResponse:
    """Players a source has nothing for, most valuable first — the list to hand-resolve.

    `need=age`: players with no birthdate. Two kinds of row show up. Most have no `nba_api`
    alias at all: nba.com has never heard of them (a draft-and-stash prospect, a G-League
    call-up, or a rookie newer than the installed `nba_api` roster). A few have an alias but
    no birthdate, meaning the fetch hasn't reached them yet. Fix the first kind with
    `POST /players/{id}/aliases`, the second by re-running the age sync.

    `need=adp`: players on the board (anyone we can price) with no `adp_entry` for that source
    and season. That is the other half of an import's own review list: the importer tells you
    which of *its* names it couldn't place, this tells you which of *our* players ended up with
    nothing — a name spelled so differently the file's row went to review, or a player the
    source genuinely doesn't rank. Same fix, same re-import.
    """
    if need not in (NEED_AGE, NEED_ADP):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Unknown need {need!r}; supported: {NEED_AGE!r}, {NEED_ADP!r}.",
        )

    if need == NEED_AGE:
        alias_source = source or NBA_SOURCE
        aliased = set(
            db.scalars(select(PlayerAlias.player_id).where(PlayerAlias.source == alias_source))
        )
        best = _best_per_game(db)
        candidates = list(db.scalars(select(Player).where(Player.age.is_(None))))
    else:
        alias_source = source or ESPN_SOURCE
        if season is None:
            season = db.scalar(
                select(AdpEntry.season)
                .where(AdpEntry.source == alias_source)
                .order_by(AdpEntry.season.desc())
                .limit(1)
            )
        aliased = set(
            db.scalars(select(PlayerAlias.player_id).where(PlayerAlias.source == alias_source))
        )
        best = _best_per_game(db)
        priced = set(
            db.scalars(
                select(AdpEntry.player_id).where(
                    AdpEntry.source == alias_source, AdpEntry.season == season
                )
            )
        )
        # "On the board" means "we can price him" — a player with no projection has no place on
        # a worklist about the draft board.
        candidates = [
            player
            for player in db.scalars(select(Player).where(Player.espn_player_id.in_(best.keys())))
            if player.espn_player_id not in priced
        ]

    missing = [
        UnresolvedPlayer(
            espn_player_id=player.espn_player_id,
            name=player.full_name,
            nba_team=player.nba_team,
            positions=player.positions or [],
            has_alias=player.espn_player_id in aliased,
            fantasy_points_per_game=best.get(player.espn_player_id),
        )
        for player in candidates
    ]
    # Projected value first, then name, so the same call twice gives the same order.
    missing.sort(key=lambda row: (-(row.fantasy_points_per_game or 0.0), row.name))

    return UnresolvedResponse(
        need=need,
        source=alias_source,
        season=season if need == NEED_ADP else None,
        total=len(missing),
        players=missing[:limit],
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
