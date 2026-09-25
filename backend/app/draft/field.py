"""The one module here that opens a database: turning stored opinion into the field's board.

Everything else in `app.draft` runs on plain dicts. This is where those dicts come from — and
it is a thin adapter over the consensus path that already exists, not a second ranking. The
whole body is `load_catalog` -> `catalog.select` -> `consensus_positions`, which is precisely
what `GET /board/consensus` and the master board's reference column do. If the consensus
changes, the field changes with it, and there is nothing here to keep in step.

WHOSE CONSENSUS the field drafts from is a parameter, on purpose. The default is every source
available under the horizon, which is the best guess at what a room of nine strangers
collectively believes. But a league that visibly drafts off ADP is better simulated by passing
`['adp:espn']`, and one where everyone reads the same dynasty list by passing that list's id.
Task 20's route exposes it; here it is a list of source ids, validated, and a `ValueError`
naming the ones nobody has heard of.

Note what the field is NOT: my master board. `app.ranking.master` is my opinion, and
simulating a room that shares it would answer the wrong question entirely — the players I am
highest on relative to the field are exactly the ones whose availability I most need to be
told the truth about.

This module deliberately does not reuse `app.api.consensus._load`: that helper raises
`HTTPException`, which is the right thing for a route and the wrong thing for a domain
adapter. It mirrors its six lines instead and raises `ValueError` subclasses, the way
`UnknownHorizon`, `UnknownMethod` and `UnknownTag` already do — Task 20's route turns them
into 400s at the edge, where the HTTP vocabulary belongs.
"""

from collections.abc import Iterable, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Player
from app.ranking import consensus_positions, load_catalog


class UnknownSources(ValueError):
    """Source ids that aren't available under this horizon. Routes turn this into a 400."""


def field_ranks(
    db: Session, horizon: str, source_ids: Sequence[str] | None = None
) -> dict[int, int]:
    """player id -> his 1-based place on the field's consensus board. Lower is better.

    The mapping `app.draft.autopick` drafts the room off. Identical to the order
    `GET /board/consensus` returns for the same horizon and source selection — same call, read
    as an order rather than as rows, so the board I look at and the board the simulation
    assumes cannot drift apart.

    `source_ids` selects a subset; omit it for every source the horizon offers. An unknown id
    raises rather than being skipped, because quietly averaging two sources when three were
    asked for produces a field that is wrong in a way nothing downstream can detect.

    Raises `UnknownHorizon` for a horizon nobody defined, and returns an empty mapping when
    the database holds no sources at all — a cold checkout, not an error.
    """
    catalog = load_catalog(db, horizon)
    if source_ids is None:
        selected = list(catalog.sources)
    else:
        selected, unknown = catalog.select(list(source_ids))
        if unknown:
            raise UnknownSources(
                f"Unknown source id(s) {unknown} for horizon {horizon!r}; available: "
                f"{[source.id for source in catalog.sources]}."
            )
    if not selected:
        return {}

    # Same tie-break the public board and the master seed use: a name, so two players the
    # sources agree exactly about don't swap places between two identical requests.
    names = _names(db, catalog.pool)
    return consensus_positions(selected, names=names)


def positions_for(db: Session, player_ids: Iterable[int] | None = None) -> dict[int, list[str]]:
    """player id -> ESPN's eligibility list, for `DraftState`'s position map.

    Here so Task 20 does not re-query what the draft already needs. A player with no stored
    positions comes back with an empty list rather than being omitted: he is draftable, he
    just fills no dedicated starter slot (`app.draft.needs`).
    """
    statement = select(Player.espn_player_id, Player.positions)
    if player_ids is not None:
        wanted = list(player_ids)
        if not wanted:
            return {}
        statement = statement.where(Player.espn_player_id.in_(wanted))
    return {
        player_id: list(positions or []) for player_id, positions in db.execute(statement).all()
    }


def _names(db: Session, pool: Iterable[int]) -> dict[int, str]:
    """Pool player id -> full name. Mirrors `app.api.consensus._load`'s identity lookup."""
    wanted = list(pool)
    if not wanted:
        return {}
    return {
        player_id: full_name
        for player_id, full_name in db.execute(
            select(Player.espn_player_id, Player.full_name).where(Player.espn_player_id.in_(wanted))
        ).all()
    }
