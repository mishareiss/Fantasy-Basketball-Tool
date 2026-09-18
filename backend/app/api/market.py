"""Market lines: see them, add one, change one, delete one.

The `market_line` import kind (`app.ingest.market_line`) can already put a whole book's worth
of props in, and it is the right tool for a hundred rows pasted out of a page. What it cannot
do is the thing this data actually needs, which is the thing no other source needs at all:

* **A market line is a standing SET, not a one-shot file.** An ADP table is imported and
  replaced by next week's table. A player's lines are kept — five props, edited one at a time
  as the book moves the assists number and nothing else.
* **And a line can be WRONG, or gone.** A prop pulled off the board (an injury) is not a line
  with a different number; it is a line that must no longer exist. An importer has no verb for
  that: pasting a shorter file leaves what it doesn't mention exactly where it was.

So this module is the CRUD the set needs, and it is deliberately thin — every number in it
is computed by the same code the importer runs. `resolve_stat` reads the stat cell,
`upsert_market_line` writes the row AND re-derives the player, `derive_market_projections`
re-prices him. Nothing here prices anything, and nothing here knows what a de-vig is.

The one rule worth stating out loud is what a DELETE means, because it is the one place the
import path had no opinion: a player whose LAST line is deleted must leave the source
entirely. See `derive_market_projections` — the projection is removed rather than re-derived
at zero, so he disappears from `GET /sources` and `GET /board/consensus` instead of sitting
there ranked by a number that nothing underlies. Every write here goes through that one
function, which is why an add, an edit and a delete cannot disagree about what he is worth.
"""

from datetime import datetime

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.imports import _resolve_season
from app.config import get_settings
from app.db.models import MarketLine, Player, Projection
from app.db.session import get_db
from app.espn.statsplits import COUNTING_STAT_IDS
from app.ingest.market_line import (
    LINE_FIELD,
    MARKET_PROJECTION_KIND,
    MARKET_SOURCE,
    OVER_FIELD,
    STAT_FIELD,
    UNDER_FIELD,
    UnknownStatError,
    derive_market_projections,
    resolve_stat,
    stored_lines,
    upsert_market_line,
)
from app.ingest.parser import ParsedRow
from app.ingest.registry import ResolvedRow, UpsertContext
from app.scoring import ScoringRulesNotLoaded, load_scoring_engine_for_season, stat_label
from app.scoring.stats import stat_name

router = APIRouter(prefix="/market", tags=["market"])


class MarketStat(BaseModel):
    """A stat our league pays for and a book can post a line on — one option in the picker."""

    stat_id: int
    # 'PTS', 'AST' — the name the line is stored under and the derived stat line uses.
    name: str
    label: str
    # What our scoring gives per unit of it. The reason the list is the LEAGUE's stats and not
    # every stat that exists: a line on something worth nothing here changes no value.
    points: float


class MarketLineRow(BaseModel):
    """One stored line — app/db/models/market_line.py: MarketLine."""

    id: int
    player_id: int
    stat_id: int
    stat: str
    # Per game, always: season-long props are quoted that way and the board ranks on it.
    line: float
    # American odds, null for a side nobody priced. A line with no price derives itself.
    over_odds: int | None = None
    under_odds: int | None = None
    as_of: datetime


class MarketPlayer(BaseModel):
    """One player's whole set of lines, and what they derive to."""

    espn_player_id: int
    name: str
    nba_team: str | None = None
    positions: list[str] = []
    age: int | None = None

    lines: list[MarketLineRow] = []
    # The derived market projection — `Projection(source, 'projected_season', season)`, the
    # row the consensus board actually reads. Null when he has no lines left, which is not
    # the same claim as zero: see `derive_market_projections`.
    fantasy_points_per_game: float | None = None
    fantasy_points_total: float | None = None
    projected_games: float | None = None
    # How many stats his value is built from. PARTIAL by construction — a player with only a
    # points prop is priced on points alone and will rank far below where anyone thinks he
    # belongs, which is honest rather than complete (app.ingest.market_line).
    stats_priced: int = 0


class MarketLinesResponse(BaseModel):
    """Every line stored for one (source, season), grouped by player."""

    source: str
    season: int
    # The stats a line can be entered on here: the counting stats our league scores. Empty
    # when no league sync has stored coefficients yet — nothing can be priced until it has.
    stats: list[MarketStat] = []
    total_players: int
    total_lines: int
    players: list[MarketPlayer] = []


class MarketLineWrite(BaseModel):
    """Add or change ONE line. The same body either way — the key decides which it is."""

    source: str = Field(MARKET_SOURCE, min_length=1, description="Which book published it")
    season: int | None = Field(None, description="Defaults to ESPN_SEASON, as an import does")
    player_id: int = Field(..., description="Our canonical (ESPN) player id")
    stat: str = Field(
        ...,
        min_length=1,
        description="The stat, in any spelling the importer accepts: 'AST', 'assists', 'apg', "
        "or the bare ESPN stat id",
    )
    line: float = Field(..., description="The over/under itself, PER GAME")
    over_odds: int | None = Field(None, description="American odds on the over, e.g. -135")
    under_odds: int | None = Field(None, description="American odds on the under, e.g. 110")


class MarketLineWriteResponse(BaseModel):
    """What was written, and what it did to the player it belongs to."""

    source: str
    season: int
    # False when the line already existed and was updated in place.
    created: bool
    line: MarketLineRow
    # The player as he is NOW: every line of his, and the re-derived value.
    player: MarketPlayer


class MarketDeleteResponse(BaseModel):
    """What was removed, and what the player is worth without it."""

    source: str
    season: int
    deleted: int
    # The player as he is NOW. `lines` empty with a null value means he is gone from this
    # source altogether — his derived projection was removed, not zeroed.
    player: MarketPlayer
    # True when that is what happened. The one thing a client can't infer from an empty list
    # without knowing the rule.
    player_removed: bool


def _engine(db: Session, season: int):
    """The league's scoring engine, or a 409 — the same translation the importer does.

    409, not 500: nothing is broken and nothing was written. A market line can't be priced
    until a league sync has stored our coefficients, and that is one call away.
    """
    try:
        return load_scoring_engine_for_season(
            db, season, espn_league_id=get_settings().espn_league_id
        )
    except ScoringRulesNotLoaded as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error


def _stat_options(db: Session, season: int) -> list[MarketStat]:
    """The counting stats this league scores, biggest mover first.

    Scoped twice, and both cuts matter. To the league's OWN coefficients, because a line on a
    stat we don't score derives nothing; and to `COUNTING_STAT_IDS`, because a rate (FG%) has
    no coefficient that can be multiplied by a per-game number — the same rule `resolve_stat`
    enforces on a pasted cell.
    """
    try:
        engine = load_scoring_engine_for_season(
            db, season, espn_league_id=get_settings().espn_league_id
        )
    except ScoringRulesNotLoaded:
        # Not an error here: the lines are still worth showing, and the empty list is what
        # tells the page why it can't offer a stat to add one on.
        return []

    options = {
        coefficient.stat_name: MarketStat(
            stat_id=coefficient.stat_id,
            name=coefficient.stat_name,
            label=stat_label(coefficient.stat_name),
            points=coefficient.points,
        )
        for coefficient in engine.coefficients
        if coefficient.stat_id in COUNTING_STAT_IDS
    }
    # The stats that move a value most, first — that is the order someone entering props by
    # hand works in. Name as the tie-break, so the list is stable between calls.
    return sorted(options.values(), key=lambda stat: (-abs(stat.points), stat.name))


def _line_row(row: MarketLine) -> MarketLineRow:
    return MarketLineRow(
        id=row.id,
        player_id=row.player_id,
        stat_id=row.stat_id,
        stat=row.stat_name,
        line=row.line,
        over_odds=row.over_odds,
        under_odds=row.under_odds,
        as_of=row.as_of,
    )


def _player_entry(
    player: Player, lines: list[MarketLine], projection: Projection | None
) -> MarketPlayer:
    """One player's group: his identity, his lines in a stable order, and his derived value."""
    return MarketPlayer(
        espn_player_id=player.espn_player_id,
        name=player.full_name,
        nba_team=player.nba_team,
        positions=list(player.positions or []),
        age=player.age,
        # By stat id, so a player's rows don't reshuffle when one is edited.
        lines=[_line_row(row) for row in sorted(lines, key=lambda row: row.stat_id)],
        fantasy_points_per_game=projection.fantasy_points_per_game if projection else None,
        fantasy_points_total=projection.fantasy_points_total if projection else None,
        projected_games=projection.projected_games if projection else None,
        stats_priced=len(lines),
    )


def _market_projections(
    db: Session, player_ids: list[int], *, source: str, season: int
) -> dict[int, Projection]:
    """The derived market projection per player — what the board reads, read back."""
    if not player_ids:
        return {}
    return {
        row.player_id: row
        for row in db.scalars(
            select(Projection).where(
                Projection.source == source,
                Projection.kind == MARKET_PROJECTION_KIND,
                Projection.season == season,
                Projection.player_id.in_(player_ids),
            )
        )
    }


def _one_player(db: Session, player: Player, *, source: str, season: int) -> MarketPlayer:
    """Re-read one player after a write: his remaining lines and his re-derived value."""
    lines = list(
        db.scalars(
            select(MarketLine).where(
                MarketLine.source == source,
                MarketLine.season == season,
                MarketLine.player_id == player.espn_player_id,
            )
        )
    )
    projections = _market_projections(db, [player.espn_player_id], source=source, season=season)
    return _player_entry(player, lines, projections.get(player.espn_player_id))


def _player_or_404(db: Session, player_id: int) -> Player:
    player = db.get(Player, player_id)
    if player is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"No player {player_id}. Lines are keyed by OUR canonical (ESPN) player id — "
            "resolve a name with the importer's preview, or POST /players/{id}/aliases.",
        )
    return player


@router.get("/lines", response_model=MarketLinesResponse)
def list_market_lines(
    db: Session = Depends(get_db),
    source: str = Query(MARKET_SOURCE, description="Which book's lines to list"),
    season: int | None = Query(None, description="Defaults to ESPN_SEASON, as an import does"),
) -> MarketLinesResponse:
    """Every stored line for one (source, season), grouped by the player it belongs to.

    Grouped rather than listed flat because a player is what the board ranks and what an
    editor works on: five props on one man are one decision about him, and his derived value
    is a property of the set, not of any row in it.

    An empty list is a clean 200, not a 404. "No lines for this book yet" is the starting
    state of every source and the answer to a perfectly reasonable question.
    """
    resolved = _resolve_season(season)

    rows = list(
        db.scalars(
            select(MarketLine).where(MarketLine.source == source, MarketLine.season == resolved)
        )
    )
    grouped: dict[int, list[MarketLine]] = {}
    for row in rows:
        grouped.setdefault(row.player_id, []).append(row)

    players = {
        player.espn_player_id: player
        for player in db.scalars(select(Player).where(Player.espn_player_id.in_(list(grouped))))
    }
    projections = _market_projections(db, list(grouped), source=source, season=resolved)

    entries = [
        _player_entry(players[player_id], lines, projections.get(player_id))
        for player_id, lines in grouped.items()
        # A line whose player was deleted out from under it has nothing to render; the FK
        # cascade makes this unreachable, and skipping beats a 500 if it ever isn't.
        if player_id in players
    ]
    # Most valuable first, then by name — the same convention the board uses, and stable
    # between calls. A player with no derived value yet sorts last rather than first.
    entries.sort(key=lambda entry: (-(entry.fantasy_points_per_game or 0.0), entry.name))

    return MarketLinesResponse(
        source=source,
        season=resolved,
        stats=_stat_options(db, resolved),
        total_players=len(entries),
        total_lines=len(rows),
        players=entries,
    )


@router.put("/lines", response_model=MarketLineWriteResponse)
def put_market_line(
    payload: MarketLineWrite = Body(...),
    db: Session = Depends(get_db),
) -> MarketLineWriteResponse:
    """Store ONE line, and re-price the player it belongs to.

    Deliberately the same write the importer makes: the body is turned into the one row a
    pasted file would have produced and handed to `upsert_market_line`, which upserts on
    (source, season, player, stat) and re-derives that player and nobody else. Adding a line
    and pasting a file containing it are therefore the same operation, and can't drift.

    Idempotent, and an edit rather than a duplicate: PUT because (source, season, player,
    stat) is the key, and sending the same stat twice moves the number rather than adding a
    second line for it.
    """
    resolved = _resolve_season(payload.season)
    player = _player_or_404(db, payload.player_id)

    try:
        stat_id = resolve_stat(payload.stat)
    except UnknownStatError as error:
        # 422, not 500 and not 400: the request is well-formed, its *content* names something
        # we can't price. Identical treatment to the same cell in a pasted file, with the same
        # message naming what we do understand.
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error

    resolved_row = ResolvedRow(
        player_id=player.espn_player_id,
        row=ParsedRow(
            line=1,
            name=player.full_name,
            values={
                # The canonical name, so the handler's own `resolve_stat` is a no-op rather
                # than a second chance to disagree with the one above.
                STAT_FIELD: stat_name(stat_id),
                LINE_FIELD: float(payload.line),
                OVER_FIELD: payload.over_odds,
                UNDER_FIELD: payload.under_odds,
            },
            index=1,
        ),
    )

    context = UpsertContext(source=payload.source, season=resolved, dry_run=False)
    try:
        counts = upsert_market_line(db, [resolved_row], context)
    except ScoringRulesNotLoaded as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    db.commit()

    entry = _one_player(db, player, source=payload.source, season=resolved)
    written = next(row for row in entry.lines if row.stat_id == stat_id)
    return MarketLineWriteResponse(
        source=payload.source,
        season=resolved,
        created=counts.created > 0,
        line=written,
        player=entry,
    )


def _rederive(db: Session, player: Player, *, source: str, season: int) -> None:
    """Re-price one player from whatever lines he has LEFT, including none at all.

    The whole cleanup, in one call, because `stored_lines` returns an empty mapping for a
    player with nothing stored rather than omitting him — which is exactly the case
    `derive_market_projections` removes the projection for.
    """
    engine = _engine(db, season)
    derive_market_projections(
        db,
        stored_lines(db, [player.espn_player_id], source=source, season=season),
        UpsertContext(source=source, season=season, dry_run=False),
        engine,
    )


def _deleted(db: Session, player: Player, *, source: str, season: int, count: int):
    """Commit a delete, re-read the player, and say whether he left the source."""
    entry = _one_player(db, player, source=source, season=season)
    return MarketDeleteResponse(
        source=source,
        season=season,
        deleted=count,
        player=entry,
        player_removed=not entry.lines,
    )


@router.delete("/lines/{line_id}", response_model=MarketDeleteResponse)
def delete_market_line(
    line_id: int = Path(..., description="The stored line's id, from GET /market/lines"),
    db: Session = Depends(get_db),
) -> MarketDeleteResponse:
    """Delete ONE line, then re-price the player from what is left.

    The re-price is the whole point, and the case that matters is the last one: with no lines
    left there is nothing to derive a market opinion from, so his derived projection is
    REMOVED rather than rewritten as a zero. He leaves `GET /sources`' player_count and the
    consensus board in the same breath, instead of being ranked last by a number nothing
    underlies (see `derive_market_projections`).
    """
    row = db.get(MarketLine, line_id)
    if row is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"No market line {line_id}. GET /market/lines lists what is stored — an id that "
            "was deleted by an earlier call is gone rather than empty.",
        )

    source, season, player_id = row.source, row.season, row.player_id
    player = _player_or_404(db, player_id)
    # The engine is loaded BEFORE the delete: a 409 here must leave the row where it was
    # rather than delete it and fail to re-price what is left.
    _engine(db, season)

    db.delete(row)
    db.flush()
    _rederive(db, player, source=source, season=season)
    db.commit()

    return _deleted(db, player, source=source, season=season, count=1)


@router.delete("/lines", response_model=MarketDeleteResponse)
def delete_player_market_lines(
    db: Session = Depends(get_db),
    source: str = Query(MARKET_SOURCE, description="Which book's lines to clear"),
    season: int | None = Query(None, description="Defaults to ESPN_SEASON, as an import does"),
    player_id: int = Query(..., description="Clear this player's whole set for that source"),
) -> MarketDeleteResponse:
    """Clear one player's entire set of lines for a (source, season) — and drop him with it.

    The convenience form of the above: "this player is off the board" is one decision, not
    five, and doing it a line at a time re-derives him four times on the way to removing him.
    """
    resolved = _resolve_season(season)
    player = _player_or_404(db, player_id)
    _engine(db, resolved)

    rows = list(
        db.scalars(
            select(MarketLine).where(
                MarketLine.source == source,
                MarketLine.season == resolved,
                MarketLine.player_id == player_id,
            )
        )
    )
    for row in rows:
        db.delete(row)
    db.flush()
    _rederive(db, player, source=source, season=resolved)
    db.commit()

    return _deleted(db, player, source=source, season=resolved, count=len(rows))
