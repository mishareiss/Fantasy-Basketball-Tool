"""The Master Ranking: our own board, and the four things you can do to it.

`GET /board/consensus` is what the field thinks. This is what WE think, and the difference is
not a filter or a weighting — it is that this board has a MEMORY. The order is stored, it was
arranged by hand, and nothing that happens to the sources underneath it moves a player. What
moves is the reference column beside him: `consensus_rank` and `delta`, which is the number
the board is actually for ("I have him fourteen spots above the field, and I still do").

Four endpoints, and the shape of the whole feature is in them:

* `GET /master/board` — the board, complete, reconciled. Complete is the load-bearing word:
  before it answers, it seeds an empty board from the consensus, inserts anyone the sources
  now rank who has no entry yet (flagged `is_new`), and flags anyone whose sources have gone
  away (`is_stale`). It is therefore a GET that writes, which is unusual enough to say out
  loud: the alternative is a board that silently doesn't contain this year's rookies.
* `PUT /master/order` — what a drag-drop saves. The whole non-excluded order, validated as a
  permutation of it.
* `PUT /master/entries/{player_id}` — tag, note, set aside / bring back.
* `POST /master/seed` — start the board over.

Everything the consensus half of that needs is borrowed rather than rebuilt: `_load` and
`_source_info` come from `app.api.consensus`, so the catalog, the shared pool, the 400 on a
bad horizon and the source list are literally the same code path `GET /board/consensus` runs
with no `sources` parameter. Everything the stored half needs lives in `app.ranking.master`.
This module is serialization, status codes, and the one decision neither of them can make:
which horizon means what (below).

THE HORIZON IS A LENS, NOT A BOARD. `?horizon=` picks the consensus the reference column is
computed against — the same player against the dynasty field and against the win-now field are
two genuinely different readings, and flipping between them is the point. It does NOT select a
different set of ranks (there is one board) and it does NOT decide who is on it: membership
comes from `MASTER_SEED_HORIZON`, so looking through the win-now lens cannot quietly admit a
hundred players who are only on a redraft list.
"""

from dataclasses import dataclass
from datetime import date, datetime

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

# The consensus half of this module, borrowed whole rather than reimplemented: one catalog
# loader, one source shaping, one 400 on an unknown horizon. Private names on purpose — they
# are internals of the consensus API that this module is a second view over, the same way
# `app.api.market` reaches for `app.api.imports._resolve_season`.
from app.api.consensus import HORIZON_DESCRIPTION, SourceInfo, _load, _source_info
from app.config import get_settings
from app.db.models import Player
from app.db.models.master_rank import MASTER_TAGS
from app.db.session import get_db
from app.ranking.master import (
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

router = APIRouter(prefix="/master", tags=["master"])

TAG_DESCRIPTION = (
    "What he is to us beyond his place: " + ", ".join(repr(tag) for tag in MASTER_TAGS) + ", or "
    "null for neither. Send the key to change it; leave it out to keep what is there."
)


class MasterPlayerRow(BaseModel):
    """One player on our board: where WE have him, and where the field does."""

    # OUR place, 1-based and contiguous. Null only for a player in `set_aside`.
    rank: int | None = None
    espn_player_id: int
    name: str
    nba_team: str | None = None
    positions: list[str] = []
    age: int | None = None

    # 'target' | 'fade' | null, and whatever we've written about him.
    tag: str | None = None
    note: str | None = None
    excluded: bool = False

    # Placed into the order by THIS request — a player nobody had an entry for, or one just
    # restored from `set_aside`. A flag about the response, not a stored bit: it is here so
    # the page can highlight the rookie that just appeared, and it is gone on the next GET
    # because by then he is simply on the board.
    is_new: bool = False
    # He has an entry but no source ranks him any more: a player who fell off every list. His
    # rank stands (it was a decision) and his reference column is empty (nothing backs it).
    is_stale: bool = False

    # --- the reference: what the field says, under the requested horizon --------------------
    # His place on the consensus of every available source, or null if it doesn't rank him.
    consensus_rank: int | None = None
    # rank - consensus_rank. POSITIVE means we have him HIGHER than the field (our 10 against
    # their 25 is +15), which is the direction that reads correctly as "how far out on a limb
    # are we". Null when either half is missing.
    delta: int | None = None

    # When we last touched this row — moved him, tagged him, wrote the note.
    updated_at: datetime


class MasterBoardResponse(BaseModel):
    """Our board, its set-aside pile, and the consensus it is being read against."""

    # The horizon the REFERENCE column was computed under — the lens, not the board.
    horizon: str
    ranking_horizon: str
    # The horizon that decides who belongs on the board at all (MASTER_SEED_HORIZON). Equal to
    # `horizon` by default; when they differ, the board is the dynasty pool being read against
    # the win-now field.
    seed_horizon: str
    # The shared draftable pool the reference consensus was computed over.
    pool_size: int
    total_ranked: int
    # This request found an empty board and seeded it from the consensus. Once, ever.
    seeded: bool = False
    # Players this request inserted into the order (the `is_new` ones), and how many entries
    # nobody currently ranks. Both are summaries of the rows, hoisted so a client can say
    # "3 new players since you last looked" without scanning the board.
    added: int = 0
    stale: int = 0
    # The date every `age` here was computed at — an age without one means nothing later.
    age_as_of: date
    # What the reference consensus is averaged from: every available source, equal weight.
    sources: list[SourceInfo] = []

    players: list[MasterPlayerRow] = []
    # The players we've set aside, best-by-consensus first. Off the order, not off the board:
    # their tags and notes are intact and restoring one is a single write.
    set_aside: list[MasterPlayerRow] = []


class MasterOrderWrite(BaseModel):
    """A drag-drop, saved: the whole non-excluded board, in the order it should be in."""

    ordered_player_ids: list[int] = Field(
        ...,
        description="Every non-excluded player on the board, exactly once, best first. Not a "
        "partial order: a list that is missing or has spare players is a 422 naming them.",
    )


class MasterEntryWrite(BaseModel):
    """Tag, note, set aside — any subset. Keys left out are left alone."""

    tag: str | None = Field(None, description=TAG_DESCRIPTION)
    note: str | None = Field(None, description="Free text, or null to clear it")
    excluded: bool | None = Field(
        None,
        description="True sets him aside: out of the order, the rest reflow up, his tag and "
        "note survive. False brings him back at the slot the consensus implies.",
    )


@dataclass(frozen=True)
class _Reference:
    """The two consensus readings a board response needs, and the identities to render it."""

    # The requested horizon and its catalog — the reference column.
    horizon: str
    positions: dict[int, int]
    sources: list[SourceInfo]
    pool_size: int
    ranking_horizon: str
    # The seed horizon's consensus: who BELONGS on the board, and where an arrival goes.
    seed_horizon: str
    membership: dict[int, int]


def _reference(db: Session, horizon: str) -> _Reference:
    """Compute both consensus readings for a request: the lens, and the membership.

    They are the same computation and usually the same call — `MASTER_SEED_HORIZON` is dynasty
    and so is the default lens. The second load happens only when someone asks to read the
    dynasty board against the win-now field, and it is what stops that view from being able to
    change what the board contains.
    """
    loaded = _load(db, horizon)
    names = {player_id: player.full_name for player_id, player in loaded.players.items()}
    positions = consensus_positions(loaded.catalog.sources, names)

    seed_horizon = get_settings().master_seed_horizon
    if seed_horizon == horizon:
        membership = positions
    else:
        seed_loaded = _load(db, seed_horizon)
        membership = consensus_positions(
            seed_loaded.catalog.sources,
            {player_id: player.full_name for player_id, player in seed_loaded.players.items()},
        )

    return _Reference(
        horizon=loaded.catalog.horizon,
        positions=positions,
        sources=[_source_info(source) for source in loaded.catalog.sources],
        pool_size=loaded.catalog.pool_size,
        ranking_horizon=loaded.catalog.ranking_horizon,
        seed_horizon=seed_horizon,
        membership=membership,
    )


def _identities(db: Session, board: MasterBoard) -> dict[int, Player]:
    """Names, teams, positions and ages for everyone on the board.

    Read off the board rather than off the pool, because the two are not the same set: a stale
    player is on the board and in nobody's pool, and he still has to render.
    """
    player_ids = [row.player_id for row in (*board.ranked, *board.set_aside)]
    if not player_ids:
        return {}
    return {
        player.espn_player_id: player
        for player in db.scalars(select(Player).where(Player.espn_player_id.in_(player_ids)))
    }


def _row(row: BoardRow, player: Player, positions: dict[int, int]) -> MasterPlayerRow:
    entry = row.entry
    consensus_rank = positions.get(entry.player_id)
    return MasterPlayerRow(
        rank=entry.rank,
        espn_player_id=entry.player_id,
        name=player.full_name,
        nba_team=player.nba_team,
        positions=list(player.positions or []),
        age=player.age,
        tag=entry.tag,
        note=entry.note,
        excluded=entry.excluded,
        is_new=row.is_new,
        is_stale=row.is_stale,
        consensus_rank=consensus_rank,
        # Only when we have both: a player the field doesn't rank has no gap to the field, and
        # a 0 there would read as agreement.
        delta=(entry.rank - consensus_rank) if entry.rank and consensus_rank else None,
        updated_at=entry.updated_at,
    )


def _response(db: Session, board: MasterBoard, reference: _Reference) -> MasterBoardResponse:
    players = _identities(db, board)
    return MasterBoardResponse(
        horizon=reference.horizon,
        ranking_horizon=reference.ranking_horizon,
        seed_horizon=reference.seed_horizon,
        pool_size=reference.pool_size,
        total_ranked=len(board.ranked),
        seeded=board.seeded,
        added=len(board.inserted),
        stale=len(board.stale),
        age_as_of=get_settings().resolved_age_as_of(),
        sources=reference.sources,
        players=[
            _row(row, players[row.player_id], reference.positions)
            for row in board.ranked
            # Unreachable while the FK cascades — a deleted player takes his entry with him —
            # and cheaper than a 500 if it ever isn't.
            if row.player_id in players
        ],
        set_aside=[
            _row(row, players[row.player_id], reference.positions)
            for row in board.set_aside
            if row.player_id in players
        ],
    )


@router.get("/board", response_model=MasterBoardResponse)
def master_board(
    db: Session = Depends(get_db),
    horizon: str | None = Query(
        None,
        description="Which consensus the REFERENCE column is computed against — "
        + HORIZON_DESCRIPTION
        + " It does not change the board's order or who is on it.",
    ),
) -> MasterBoardResponse:
    """Our board, complete and up to date, with the field's opinion beside each row.

    The order is ours and it is STABLE: a projection landing or a list being re-imported moves
    `consensus_rank` and `delta` and moves nobody's rank. The only thing that changes the board
    here is a player the sources now rank who has no entry yet — he is inserted at the slot the
    consensus implies, persisted, and flagged `is_new` on this one response, because a board
    that couldn't admit a rookie would need re-seeding every October and that would throw away
    every decision on it.

    An empty board seeds itself from the consensus on the first call, in consensus order. That
    is the only time this endpoint invents ranks; from then on it only ever fills gaps.

    A cold database — nothing synced, nothing imported — is an empty board and a clean 200, not
    a 404: "I haven't ranked anyone yet" is the honest answer and the starting state of the
    feature.
    """
    reference = _reference(db, horizon or get_settings().master_seed_horizon)
    board = reconcile(db, reference.membership)
    # A GET that commits. What it persists is exactly what it would have had to invent again
    # on the next call — the seed, and the slot a new player was given — and leaving that
    # uncommitted would hand a different board to two identical requests.
    db.commit()
    return _response(db, board, reference)


@router.put("/order", response_model=MasterBoardResponse)
def put_master_order(
    payload: MasterOrderWrite = Body(...),
    db: Session = Depends(get_db),
    horizon: str | None = Query(
        None, description="Which horizon's reference column to answer with"
    ),
) -> MasterBoardResponse:
    """Save a reorder: rank = place in the submitted list, for the whole non-excluded board.

    All-or-nothing, and a permutation or a 422. The reason is the drag-drop it backs: a client
    that sends a list which predates a reconciled-in rookie would otherwise silently drop him
    off the board, and "you are missing player 4593127" is a message it can act on by
    refreshing. Excluded players are not in the list and must not be sent.
    """
    reference = _reference(db, horizon or get_settings().master_seed_horizon)
    try:
        board = reorder(db, payload.ordered_player_ids, reference.membership)
    except OrderMismatch as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    db.commit()
    return _response(db, board, reference)


@router.put("/entries/{player_id}", response_model=MasterBoardResponse)
def put_master_entry(
    player_id: int = Path(..., description="Our canonical (ESPN) player id"),
    payload: MasterEntryWrite = Body(...),
    db: Session = Depends(get_db),
    horizon: str | None = Query(
        None, description="Which horizon's reference column to answer with"
    ),
) -> MasterBoardResponse:
    """Tag him, write a note about him, set him aside or bring him back.

    Only the fields actually sent are written, so a note edit can't clear a tag and an explicit
    `null` still means "clear this one".

    Setting him aside takes him out of the order and reflows everyone below up a place; his tag
    and note stay exactly where they are, because parking a player is not forgetting what you
    thought of him. Bringing him back re-inserts him at the slot the consensus implies and
    flags him like a new arrival — deliberately NOT at the rank he used to have, which the rest
    of the board has since moved past.

    Returns the whole refreshed board rather than the entry: a write that reflows every rank
    below it can't honestly answer with one row.
    """
    if db.get(Player, player_id) is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"No player {player_id}. The board is keyed by OUR canonical (ESPN) player id — "
            "GET /master/board lists what is on it.",
        )

    reference = _reference(db, horizon or get_settings().master_seed_horizon)
    try:
        _, board = upsert_entry(
            db,
            player_id,
            reference.membership,
            # Only the keys the caller actually sent: `exclude_unset` is what makes "leave it
            # alone" and "set it to null" two different requests.
            fields=payload.model_dump(exclude_unset=True),
        )
    except UnknownTag as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    db.commit()
    return _response(db, board, reference)


@router.post("/seed", response_model=MasterBoardResponse)
def post_master_seed(
    db: Session = Depends(get_db),
    reset: bool = Query(
        False,
        description="Delete the existing board first. Required to re-seed one that isn't empty "
        "— every rank, tag and note goes with it.",
    ),
    horizon: str | None = Query(
        None, description="Which horizon's reference column to answer with"
    ),
) -> MasterBoardResponse:
    """Build the board from the consensus. Guarded, because it is the one destructive verb here.

    `GET /master/board` already seeds an empty board, so this exists for the other case:
    starting the season over. Without `reset=true` it refuses to touch a board that has
    anything on it (409) rather than quietly replacing a draft's worth of decisions.
    """
    reference = _reference(db, horizon or get_settings().master_seed_horizon)
    if load_entries(db) and not reset:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "The board already has entries. Pass reset=true to throw them away and re-seed "
            "from the consensus — every rank, tag and note on it goes. GET /master/board keeps "
            "an existing board up to date without this.",
        )

    board = reseed(db, reference.membership) if reset else reconcile(db, reference.membership)
    db.commit()
    return _response(db, board, reference)
