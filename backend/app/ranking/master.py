"""Our own board: seeded from the consensus once, then owned — and kept correct as data moves.

`consensus` recomputes an order from other people's opinions on every request. This module is
what happens after somebody disagrees with it: `MasterRankEntry` stores OUR order, and nothing
in here ever re-sorts it. What it does instead is keep a stored order *complete* while the
world underneath it changes, which is the one hard problem a hand-made board has.

**Reconcile-on-read**, the whole rule, in three cases:

* a player in the consensus pool with an entry — left exactly where he was put. This is the
  case that is 99% of the board and it is the one the module exists to protect: a projection
  landing, a ranking re-imported, a market line moved, all of it changes the REFERENCE column
  and none of it changes a rank.
* a player in the pool with NO entry — a rookie, a signing, a name someone just imported. He
  is inserted at the slot the consensus implies (below), persisted, and flagged `is_new` on
  that one response so the page can highlight him. A stable board that couldn't admit this
  year's rookies would be a stale board.
* an entry whose player has dropped OUT of the pool — kept, at his rank, flagged `is_stale`.
  Nobody currently ranks him; that is a fact about the sources, not a reason to delete a
  decision. (He is not "excluded": excluded is something you chose.)

**The consensus-implied slot** is "below every player the field rates above him, and above
everyone else" — a count, not a search for a neighbour (`_slot_for` says why the two obvious
scanning rules both blow up on a hand-worked board). On a board still in consensus order that
is exactly his consensus rank, which is what makes the seed and an insert the same arithmetic;
on a re-ordered one it is the closest thing to an answer the consensus can give about a board
it didn't write. Either way it disturbs nobody else's rank.

**Seeding is not a special case.** An empty board is a board where every pool player is
missing, so the insert rule above produces rank == consensus rank for all of them and the seed
falls out of the same code. The only thing the caller is told separately is that it HAPPENED
(`seeded`), because a hundred rows arriving at once is not a hundred new players to highlight.

**`rank IS NULL` iff `excluded`** is the invariant, maintained here and nowhere else. Excluding
takes a player out of the order and reflows the rest; restoring him puts him back through the
same insert rule as a new player, flagged the same way. Ranks are always contiguous 1..N when
this module is done, so nothing downstream ever has to renumber.

Nothing in here computes a consensus. `consensus_positions` is the only bridge, and it is
`app.ranking.consensus.consensus_board` at method='rank' over whatever sources it is handed —
the same call `GET /board/consensus` makes with no `sources` parameter, which is what keeps the
seed, the reference column and the public consensus board from ever disagreeing.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import MasterRankEntry
from app.db.models.master_rank import MASTER_TAGS
from app.ranking.consensus import METHOD_RANK, consensus_board
from app.ranking.sources import RankingSource

# The one place the reference/seed consensus is specified. Ranks, not percentiles: the board
# is an ORDER, the thing next to a rank has to be a rank, and over one shared pool the two
# methods agree about the order anyway (see `app.ranking.sources`).
BOARD_METHOD = METHOD_RANK


class OrderMismatch(ValueError):
    """A submitted order that isn't a permutation of the board. Routes turn this into a 422.

    Carries what was wrong rather than just saying so: a drag-drop client that posts a stale
    list needs to know WHICH player it is missing (the rookie that got reconciled in under it)
    or has spare (the one someone set aside in another tab), because the fix is to refresh, and
    "invalid order" doesn't tell it that.
    """

    def __init__(
        self,
        *,
        missing: Sequence[int] = (),
        extra: Sequence[int] = (),
        duplicates: Sequence[int] = (),
    ) -> None:
        self.missing = tuple(missing)
        self.extra = tuple(extra)
        self.duplicates = tuple(duplicates)
        parts = []
        if self.missing:
            parts.append(f"missing {list(self.missing)}")
        if self.extra:
            parts.append(f"not on the ranked board {list(self.extra)}")
        if self.duplicates:
            parts.append(f"listed twice {list(self.duplicates)}")
        super().__init__(
            "`ordered_player_ids` must be exactly the board's non-excluded players, once each: "
            + "; ".join(parts)
            + ". GET /master/board for the current set — a player set aside is not in it, and a "
            "newly-reconciled one is."
        )


class UnknownTag(ValueError):
    """A tag outside MASTER_TAGS. Routes turn this into a 422."""


@dataclass(frozen=True)
class BoardRow:
    """One stored entry, plus what this request found out about it."""

    entry: MasterRankEntry
    # Placed into the order by THIS request: a player who had no entry, or one just restored
    # from `set_aside`. Deliberately not a stored column — "new" is a fact about a response,
    # and a persisted bit would need something to clear it that nothing would ever call.
    is_new: bool = False
    # Has an entry, but no source currently ranks him. His rank stands; nothing backs it.
    is_stale: bool = False

    @property
    def player_id(self) -> int:
        return self.entry.player_id


@dataclass(frozen=True)
class MasterBoard:
    """The whole board after a reconcile: the order, the parked players, and what changed."""

    ranked: tuple[BoardRow, ...]
    set_aside: tuple[BoardRow, ...]
    # True when this request found an empty board and seeded it from the consensus.
    seeded: bool = False
    # Players this request placed into the order (new or restored). Empty on a seed.
    inserted: tuple[int, ...] = field(default_factory=tuple)

    @property
    def stale(self) -> tuple[int, ...]:
        return tuple(row.player_id for row in (*self.ranked, *self.set_aside) if row.is_stale)


def consensus_positions(
    sources: Sequence[RankingSource], names: Mapping[int, str] | None = None
) -> dict[int, int]:
    """player id -> his 1-based place on the consensus of these sources.

    The same board `GET /board/consensus` returns with no `sources` parameter, read as an order
    instead of as rows. One call, so the seed, the reference column and the public consensus
    board cannot drift apart.
    """
    return {
        row.player_id: place
        for place, row in enumerate(consensus_board(sources, BOARD_METHOD, names=names), start=1)
    }


def load_entries(db: Session) -> list[MasterRankEntry]:
    """Every entry on the board, ranked ones first in rank order.

    `player_id` breaks the tie so two entries that somehow share a rank (nothing here writes
    that, but a hand-edited database can hold it) come out in a stable order rather than
    whatever the database felt like.
    """
    return list(
        db.scalars(
            select(MasterRankEntry).order_by(
                MasterRankEntry.rank.is_(None),
                MasterRankEntry.rank,
                MasterRankEntry.player_id,
            )
        )
    )


def _slot_for(
    order: Sequence[MasterRankEntry], positions: Mapping[int, int], position: int | None
) -> int:
    """Where a player belongs: below everyone the field rates above him, above everyone else.

    So it is a COUNT, not a scan for a neighbour — which matters on a board that has been
    worked over by hand. The two scanning rules both have a failure that this one doesn't: "put
    him above the first player rated below him" sends every future arrival to rank 1 as soon as
    one sleeper is promoted to the top, and "put him below the last player rated above him"
    sends them all to the bottom as soon as one bust is buried at 300. Counting makes each of
    those a one-slot error instead of a three-hundred-slot one, and on a board still in
    consensus order it is exactly his consensus rank — which is why the seed and an insert are
    the same arithmetic.

    Entries the consensus has no opinion about (the stale ones) are not counted: they are not
    evidence about where he goes. `position` None — nobody ranks HIM — puts him at the bottom,
    because there is no opinion to place him by and guessing the middle would invent one.
    """
    if position is None:
        return len(order)
    return sum(
        1
        for other in order
        if (rated := positions.get(other.player_id)) is not None and rated < position
    )


def _renumber(order: Sequence[MasterRankEntry]) -> None:
    """Make the ranks contiguous 1..N, touching only the rows that actually move.

    The `!=` matters: assigning the same rank back would mark the row dirty and push
    `updated_at` forward, so every GET would claim the whole board was edited.
    """
    for place, entry in enumerate(order, start=1):
        if entry.rank != place:
            entry.rank = place


def reconcile(db: Session, positions: Mapping[int, int]) -> MasterBoard:
    """Bring the stored board up to date with the pool, without re-ordering what is there.

    The one function every endpoint runs before it answers, because a board that is read
    without it is a board that is missing this week's rookies. It flushes but does NOT commit:
    the caller owns the transaction, and a GET that reconciles is still one unit of work with
    whatever else it is doing.
    """
    entries = load_entries(db)
    seeded = not entries

    for entry in entries:
        # The invariant, repaired rather than trusted: a row with `excluded` set and a rank
        # still on it would otherwise sit in the order AND in `set_aside`.
        if entry.excluded and entry.rank is not None:
            entry.rank = None

    order = [entry for entry in entries if not entry.excluded and entry.rank is not None]
    known = {entry.player_id for entry in entries}

    # Everyone the consensus ranks who has no row yet, best first — so a batch of arrivals
    # lands in consensus order relative to each other as well as to the board.
    arrivals: list[MasterRankEntry] = []
    for player_id in sorted(set(positions) - known, key=lambda player_id: positions[player_id]):
        entry = MasterRankEntry(player_id=player_id, excluded=False)
        db.add(entry)
        arrivals.append(entry)

    # Restored players (excluded cleared, so no rank) are placed by the same rule as arrivals;
    # to this function the two cases are identical, which is why restore needs no code of its
    # own. Both are ordered by consensus position before being inserted.
    unplaced = [entry for entry in entries if not entry.excluded and entry.rank is None]

    def by_consensus(entry: MasterRankEntry) -> tuple[bool, int]:
        position = positions.get(entry.player_id)
        # Unranked last, and by position among the rest — the order they are inserted in is
        # the order they end up in relative to each other.
        return (position is None, position or 0)

    for entry in sorted([*unplaced, *arrivals], key=by_consensus):
        order.insert(_slot_for(order, positions, positions.get(entry.player_id)), entry)

    _renumber(order)
    db.flush()

    # A seed is not a hundred new players: the whole board arriving at once is the board, and
    # flagging every row would ask the page to highlight everything.
    inserted = () if seeded else tuple(entry.player_id for entry in (*unplaced, *arrivals))
    return _board(order, entries + arrivals, positions, inserted=inserted, seeded=seeded)


def _board(
    order: Sequence[MasterRankEntry],
    entries: Iterable[MasterRankEntry],
    positions: Mapping[int, int],
    *,
    inserted: tuple[int, ...],
    seeded: bool,
) -> MasterBoard:
    """Shape the reconciled entries into the two lists a client reads."""

    def row(entry: MasterRankEntry) -> BoardRow:
        return BoardRow(
            entry=entry,
            is_new=entry.player_id in inserted,
            is_stale=entry.player_id not in positions,
        )

    return MasterBoard(
        ranked=tuple(row(entry) for entry in order),
        # Parked players have no rank to sort by, so they are shown in the order the field has
        # them — the useful order when the question is "should any of these come back?".
        set_aside=tuple(
            row(entry)
            for entry in sorted(
                (entry for entry in entries if entry.excluded),
                key=lambda entry: (positions.get(entry.player_id, len(positions) + 1),),
            )
        ),
        seeded=seeded,
        inserted=inserted,
    )


def reorder(
    db: Session, ordered_player_ids: Sequence[int], positions: Mapping[int, int]
) -> MasterBoard:
    """Persist a drag-drop: rank = position in the submitted list, for the whole ranked board.

    Deliberately all-or-nothing. A partial order ("just these ten, in this order") has no
    answer to where the other 900 go, and a client that sends a stale list is a client whose
    board is out of date — so the mismatch is a 422 naming the difference rather than a
    best-effort write that silently loses a player.
    """
    # Reconciled FIRST, deliberately: the set a client is held to is the set a GET would have
    # just handed it, rookies included. Sending an order that predates an arrival is then a
    # 422 naming him rather than a write that quietly drops him off the board.
    board = reconcile(db, positions)
    current = {row.player_id: row.entry for row in board.ranked}

    counts: dict[int, int] = {}
    for player_id in ordered_player_ids:
        counts[player_id] = counts.get(player_id, 0) + 1
    duplicates = [player_id for player_id, count in counts.items() if count > 1]
    missing = [player_id for player_id in current if player_id not in counts]
    extra = [player_id for player_id in counts if player_id not in current]
    if missing or extra or duplicates:
        raise OrderMismatch(missing=missing, extra=extra, duplicates=duplicates)

    order = [current[player_id] for player_id in ordered_player_ids]
    _renumber(order)
    db.flush()
    # Re-shaped rather than re-reconciled: the entries are the same rows in a new order, and a
    # second reconcile would forget that this request also inserted an arrival.
    return _board(
        order,
        [row.entry for row in (*board.ranked, *board.set_aside)],
        positions,
        inserted=board.inserted,
        seeded=board.seeded,
    )


def upsert_entry(
    db: Session,
    player_id: int,
    positions: Mapping[int, int],
    *,
    fields: Mapping[str, object],
) -> tuple[MasterRankEntry, MasterBoard]:
    """Set tag / note / excluded on one player, then let the reconcile do the arithmetic.

    Only the keys actually sent are applied, so `{"note": "..."}` cannot quietly clear a tag
    and an explicit `null` still means "clear this one". Everything that follows from the write
    — dropping him out of the order, reflowing the rest, putting him back at the slot the
    consensus implies — is `reconcile`'s job, which is how an exclude and a restore stay exact
    inverses of each other.
    """
    if "tag" in fields and fields["tag"] is not None and fields["tag"] not in MASTER_TAGS:
        raise UnknownTag(
            f"Unknown tag {fields['tag']!r}; supported: "
            + ", ".join(repr(name) for name in MASTER_TAGS)
            + ", or null to clear it."
        )

    entry = db.scalars(
        select(MasterRankEntry).where(MasterRankEntry.player_id == player_id)
    ).one_or_none()
    if entry is None:
        # A player nobody ranks can still be given a note and a place — that is half of what a
        # personal board is for.
        entry = MasterRankEntry(player_id=player_id, excluded=False)
        db.add(entry)

    for name in ("tag", "note", "excluded"):
        if name in fields:
            setattr(entry, name, fields[name])
    if entry.excluded:
        entry.rank = None
    db.flush()

    return entry, reconcile(db, positions)


def reseed(db: Session, positions: Mapping[int, int]) -> MasterBoard:
    """Throw the board away and rebuild it from the consensus. Every rank, tag and note goes.

    The escape hatch, not a maintenance step: the reconcile above is what keeps a board
    correct, and this is what you call when you want to start the season over.
    """
    for entry in load_entries(db):
        db.delete(entry)
    db.flush()
    return reconcile(db, positions)
