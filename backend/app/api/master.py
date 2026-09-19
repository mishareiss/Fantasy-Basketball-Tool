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
* `PUT /master/tiers` — where the board breaks into tiers, per scope. What a dragged divider
  saves. `POST /master/tiers/reseed` throws a scope's dividers away and re-derives them from
  the value gaps.

Everything the consensus half of that needs is borrowed rather than rebuilt: `_load` and
`_source_info` come from `app.api.consensus`, so the catalog, the shared pool, the 400 on a
bad horizon and the source list are literally the same code path `GET /board/consensus` runs
with no `sources` parameter. Everything the stored half needs lives in `app.ranking.master`.
This module is serialization, status codes, and the one decision neither of them can make:
which horizon means what (below).

TIERS ARE BANDS OVER THE ORDER, and `?position=` is a view of it. A tier is stored as cut
ranks — "a new tier starts at rank 12" — so dragging a player up into the tier-1 band makes him
tier 1 with nothing written; see `app.ranking.tiers`, which owns all of that arithmetic. Every
row carries both its overall tier and its tier among the players at its position, because those
are two different questions and a board that answered only the first can't tell you whether the
centre you are about to reach for is the last of his tier. `?position=PG` narrows WHO IS SHOWN
and nothing else: the ranks on the rows are still overall board ranks, because a point guard's
place on our board is his place on our board.

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
from app.api.players import TIERS_OFF, ranked_board
from app.config import get_settings
from app.db.models import Player
from app.db.models.master_rank import MASTER_TAGS
from app.db.models.master_tier import SCOPE_OVERALL, TIER_SCOPES
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
from app.ranking.tiers import (
    POSITIONS,
    BadCutRanks,
    UnknownScope,
    clear_cuts,
    ensure_cuts,
    require_scope,
    save_cuts,
    scope_orders,
    tiers_for,
    validate_cuts,
)
from app.valuation import HORIZON_DYNASTY

router = APIRouter(prefix="/master", tags=["master"])

POSITION_DESCRIPTION = (
    "Show only players listed at one position: " + ", ".join(POSITIONS) + ". Narrows WHO is "
    "returned and nothing else — the ranks stay overall board ranks and the tiers stay the "
    "tiers of the whole board. Anything else is a 422."
)

SCOPE_DESCRIPTION = (
    "Which order the cut ranks are over: " + ", ".join(repr(scope) for scope in TIER_SCOPES) + ". "
    f"{SCOPE_OVERALL!r} is the whole board; a position is that position's sub-order, so its cut "
    "ranks count point guards rather than board ranks."
)

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

    # --- tiers: which band of the board he is in, overall and among his position -----------
    # 1 is the top tier. Null only for a player in `set_aside` — he has no rank, and a tier
    # here is a band over the ranks. Never null for a ranked player: the bands cover the whole
    # board, so a player below the auto-tiered pool is in the bottom band rather than untiered.
    overall_tier: int | None = None
    # His tier among the players at `position_scope` — the requested `?position=` when there is
    # one, otherwise the first position he is listed at. Null when we have no position for him,
    # or when that position has too few players on the board to tier at all.
    position_tier: int | None = None
    # Which position `position_tier` is counted in, so the number is never ambiguous for a
    # player listed at two.
    position_scope: str | None = None

    # When we last touched this row — moved him, tagged him, wrote the note.
    updated_at: datetime


class TierScopeRow(BaseModel):
    """One scope's tier structure — enough for the UI to draw dividers and label bands."""

    # 'overall' | 'PG' | 'SG' | 'SF' | 'PF' | 'C'.
    scope: str
    # How many players are in this scope's order — the board, or that position's slice of it.
    # `cut_ranks` are ranks within THIS, so for a position they count point guards.
    size: int
    # The rank each tier starts at, ascending, always beginning with 1. `[1, 4, 12]` is three
    # tiers: 1-3, 4-11, 12-size. This is exactly what `PUT /master/tiers` takes back.
    cut_ranks: list[int] = []
    tier_count: int = 0


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

    # The `?position=` this response was narrowed to, or null for the whole board.
    position: str | None = None
    # Every scope's tier structure, including the ones this response isn't showing — the UI
    # draws dividers from these rather than inferring them from the rows, so it cannot end up
    # disagreeing with the board about where a tier starts. Seeded from the value gaps the
    # first time a scope is read and stored from then on.
    tiers: list[TierScopeRow] = []

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


class MasterTierWrite(BaseModel):
    """Where one scope's tiers start, as a dragged divider leaves them."""

    scope: str = Field(SCOPE_OVERALL, description=SCOPE_DESCRIPTION)
    cut_ranks: list[int] = Field(
        ...,
        description="The rank each tier starts at, within this scope's order: sorted, unique, "
        "inside 1..size, and beginning with 1 because tier 1 starts at the top. Send the list "
        "`GET /master/board` gave you with a divider added, moved or removed.",
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


@dataclass(frozen=True)
class _Tiers:
    """Every scope's bands, resolved down to "what tier is this player in".

    Built once per response and handed to every row, because the alternative — each row
    working out its own tier — would recount the point guards a thousand times and could
    disagree with the structure the same response publishes.
    """

    # scope -> its cut ranks (tier start ranks, leading 1 included).
    cuts: dict[str, list[int]]
    # scope -> how many players are in that scope's order.
    sizes: dict[str, int]
    # scope -> {player id: his tier in that scope}.
    assigned: dict[str, dict[int, int]]

    def overall(self, player_id: int) -> int | None:
        return self.assigned.get(SCOPE_OVERALL, {}).get(player_id)

    def position(self, player: Player, wanted: str | None) -> tuple[int | None, str | None]:
        """His tier among his position, and which position that was counted in.

        `wanted` — the `?position=` filter — wins when there is one, because on a view of the
        point guards the only interesting positional tier is the point-guard one. Without a
        filter a player listed at two positions is reported at the FIRST, which is how ESPN
        lists a primary position; the full structure for every other scope is on the response
        beside him, so nothing is hidden by the choice.
        """
        scopes = (
            [wanted]
            if wanted
            else [position for position in (player.positions or ()) if position in POSITIONS]
        )
        for scope in scopes:
            tier = self.assigned.get(scope, {}).get(player.espn_player_id)
            if tier is not None:
                return tier, scope
        return None, None

    def rows(self) -> list[TierScopeRow]:
        return [
            TierScopeRow(
                scope=scope,
                size=self.sizes.get(scope, 0),
                cut_ranks=list(self.cuts.get(scope, ())),
                tier_count=len(self.cuts.get(scope, ())),
            )
            for scope in TIER_SCOPES
        ]


def _dynasty_values(db: Session) -> dict[int, float]:
    """player id -> his dynasty value, from the ONE value path there is.

    Straight through `ranked_board`, so the number the tiers are seeded from is literally the
    number `GET /players/board?horizon=dynasty` ranks by — same projections, same age curve,
    same settings. Tiering is off on this call: what we want is the values, and the value
    board's own tiers are cut over a different order (its own) than this board's.

    A cold database has no projections and `ranked_board` 404s on that, which is the right
    answer for the value board and the wrong one here — an unpriced board is still a board.
    Everyone comes back valueless, and the carry rule in `app.ranking.tiers` already knows what
    to do with that: one flat tier.
    """
    try:
        board = ranked_board(db, horizon=HORIZON_DYNASTY, tiers=TIERS_OFF)
    except HTTPException:
        return {}
    return {entry.player.espn_player_id: entry.value.dynasty for entry in board.entries}


def _tiers(db: Session, board: MasterBoard, players: dict[int, Player]) -> _Tiers:
    """Seed (once) and resolve every scope's bands over the board as it currently stands.

    The seed-on-read half of the tier feature, and the mirror of `reconcile`: a scope with no
    stored cuts gets them derived from the value gaps and PERSISTED, so the board comes back
    tiered on the very first look and the boundaries are a stable thing to edit rather than
    something recomputed under the editor. A scope that is already stored is read, never
    re-derived — which is what makes a reorder reflow the bands without touching them.
    """
    ranked = [
        (row.player_id, list(players[row.player_id].positions or ()))
        for row in board.ranked
        if row.player_id in players
    ]
    orders = scope_orders(ranked)
    # The value path is passed unevaluated: on the overwhelmingly common read — every scope
    # already stored — nothing has to be priced at all.
    cuts = ensure_cuts(db, orders, lambda: _dynasty_values(db), get_settings().tier_params())

    assigned: dict[str, dict[int, int]] = {}
    for scope, order in orders.items():
        scope_cuts = cuts.get(scope)
        if not scope_cuts or not order:
            continue
        assigned[scope] = dict(zip(order, tiers_for(scope_cuts, len(order)), strict=True))

    return _Tiers(
        cuts={scope: cuts.get(scope, []) for scope in TIER_SCOPES},
        sizes={scope: len(order) for scope, order in orders.items()},
        assigned=assigned,
    )


def _row(
    row: BoardRow,
    player: Player,
    positions: dict[int, int],
    tiers: _Tiers,
    wanted: str | None,
) -> MasterPlayerRow:
    entry = row.entry
    consensus_rank = positions.get(entry.player_id)
    position_tier, position_scope = tiers.position(player, wanted)
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
        # A set-aside player has no rank, so no band contains him and his tiers are null.
        overall_tier=tiers.overall(entry.player_id) if entry.rank else None,
        position_tier=position_tier if entry.rank else None,
        position_scope=position_scope if entry.rank else None,
        updated_at=entry.updated_at,
    )


def _wanted_position(position: str | None) -> str | None:
    """Normalize `?position=`, or 422. 'pg' is a point guard; 'guard' is a misunderstanding.

    A 422 rather than an empty board, deliberately, and this is the one place this repo's two
    position filters differ: `GET /players/board?position=` has always answered a nonsense
    position with zero rows, and it still does — that endpoint is frozen by the guard test.
    Here the filter drives a tier scope as well as a row filter, and "no such scope" is a
    typo in the client, not a board with nobody on it.
    """
    if position is None:
        return None
    wanted = position.strip().upper()
    if wanted not in POSITIONS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Unknown position {position!r}; supported: " + ", ".join(POSITIONS) + ".",
        )
    return wanted


def _response(
    db: Session, board: MasterBoard, reference: _Reference, position: str | None = None
) -> MasterBoardResponse:
    players = _identities(db, board)
    tiers = _tiers(db, board, players)

    def shown(rows) -> list[MasterPlayerRow]:
        """The rows this response carries: every one we can name, narrowed by `?position=`.

        The `in players` guard is unreachable while the FK cascades — a deleted player takes
        his entry with him — and cheaper than a 500 if it ever isn't.
        """
        return [
            _row(row, players[row.player_id], reference.positions, tiers, position)
            for row in rows
            if row.player_id in players
            and (position is None or position in (players[row.player_id].positions or ()))
        ]

    ranked = shown(board.ranked)
    return MasterBoardResponse(
        horizon=reference.horizon,
        ranking_horizon=reference.ranking_horizon,
        seed_horizon=reference.seed_horizon,
        pool_size=reference.pool_size,
        # What this response actually returned. Unfiltered that is the whole board; under a
        # `?position=` it is that position's count — the full board size is still on the
        # response, as the 'overall' scope's `size`.
        total_ranked=len(ranked),
        seeded=board.seeded,
        added=len(board.inserted),
        stale=len(board.stale),
        age_as_of=get_settings().resolved_age_as_of(),
        sources=reference.sources,
        position=position,
        tiers=tiers.rows(),
        players=ranked,
        # Narrowed by the same filter: a point-guard view whose set-aside tray was full of
        # centres would be one list answering a different question than the one above it.
        set_aside=shown(board.set_aside),
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
    position: str | None = Query(None, description=POSITION_DESCRIPTION),
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

    Every row comes back TIERED, and the tiers are bands over the ranks: `overall_tier` is the
    band his board rank falls in and `position_tier` is the band his rank among his position
    falls in. Like the board itself they seed themselves on the first read — from the gaps in
    the dynasty values, so the starting split is where the value actually cliffs — and are
    stored from then on. Reordering the board reflows them for free and rewrites nothing: a
    player dragged up into the tier-1 band is tier 1 on the very next read.

    `?position=PG` narrows WHO is returned to the players listed there, in board order, with
    their overall ranks and overall tiers intact — a point guard's place on our board does not
    change because we are looking at the guards. The whole tier structure for every scope is on
    the response either way.

    A cold database — nothing synced, nothing imported — is an empty board and a clean 200, not
    a 404: "I haven't ranked anyone yet" is the honest answer and the starting state of the
    feature.
    """
    wanted = _wanted_position(position)
    reference = _reference(db, horizon or get_settings().master_seed_horizon)
    board = reconcile(db, reference.membership)
    response = _response(db, board, reference, wanted)
    # A GET that commits. What it persists is exactly what it would have had to invent again
    # on the next call — the seed, the slot a new player was given, and the first cut of the
    # tiers — and leaving that uncommitted would hand a different board to two identical
    # requests. Built before the commit, so nothing on the response can be a re-read.
    db.commit()
    return response


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
    response = _response(db, board, reference)
    db.commit()
    return response


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
    response = _response(db, board, reference)
    db.commit()
    return response


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

    if reset:
        # The dividers go with the board. They are bands over ranks that are about to stop
        # existing, and a rebuilt board is a different list of players — keeping a cut at rank
        # 12 across that would be keeping a number, not a decision. The next read re-derives
        # them from the value gaps.
        for scope in TIER_SCOPES:
            clear_cuts(db, scope)
    board = reseed(db, reference.membership) if reset else reconcile(db, reference.membership)
    response = _response(db, board, reference)
    db.commit()
    return response


@router.put("/tiers", response_model=MasterBoardResponse)
def put_master_tiers(
    payload: MasterTierWrite = Body(...),
    db: Session = Depends(get_db),
    horizon: str | None = Query(
        None, description="Which horizon's reference column to answer with"
    ),
) -> MasterBoardResponse:
    """Save where one scope's tiers start — what a dragged divider writes.

    All-or-nothing per scope, like `PUT /master/order` and for the same reason: the client has
    the whole list of dividers, not a delta, and a delta applied to a board that has since
    reflowed is a race with nothing to gain. Every other scope is untouched, so adjusting the
    point guards cannot move the overall dividers.

    It writes cut RANKS, so it does not name a player and cannot move one. The order, the
    ranks, the tags and the notes are all exactly what they were; what changes is where the
    lines between the bands are drawn, and therefore which band each rank falls in.

    From here on the scope is Misha's — `GET /master/board` reads these cuts and never
    re-derives them. `POST /master/tiers/reseed` is how to get the automatic split back.
    """
    try:
        scope = require_scope(payload.scope)
    except UnknownScope as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error

    reference = _reference(db, horizon or get_settings().master_seed_horizon)
    board = reconcile(db, reference.membership)
    # Validated against the board as it is RIGHT NOW, reconciled — the same set the client was
    # last handed, rookies included. A divider at rank 300 of a 280-player board is a stale
    # client, and saying so is more use than silently dropping it.
    players = _identities(db, board)
    order = scope_orders(
        [
            (row.player_id, list(players[row.player_id].positions or ()))
            for row in board.ranked
            if row.player_id in players
        ]
    )[scope]
    try:
        save_cuts(db, scope, validate_cuts(payload.cut_ranks, len(order)))
    except BadCutRanks as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error

    response = _response(db, board, reference)
    db.commit()
    return response


@router.post("/tiers/reseed", response_model=MasterBoardResponse)
def post_master_tier_reseed(
    db: Session = Depends(get_db),
    scope: str = Query(SCOPE_OVERALL, description=SCOPE_DESCRIPTION),
    horizon: str | None = Query(
        None, description="Which horizon's reference column to answer with"
    ),
) -> MasterBoardResponse:
    """Throw one scope's dividers away and let the value gaps cut it again.

    The undo for a set of hand-moved boundaries, and the only destructive verb tiers have. It
    forgets the cuts rather than recomputing them in place, so the re-derivation goes through
    exactly the seed-on-read path a never-tiered scope does — one tierer, one answer.

    Scoped, deliberately: reseeding the centres must not throw away the overall dividers, which
    is the whole reason `scope` is a parameter rather than this being a board-wide reset.
    Nothing about the order, the ranks, the tags or the notes is touched.
    """
    try:
        wanted = require_scope(scope)
    except UnknownScope as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error

    reference = _reference(db, horizon or get_settings().master_seed_horizon)
    board = reconcile(db, reference.membership)
    clear_cuts(db, wanted)
    # `_response` runs the seed-on-read, which now finds this scope empty and re-derives it.
    response = _response(db, board, reference)
    db.commit()
    return response
