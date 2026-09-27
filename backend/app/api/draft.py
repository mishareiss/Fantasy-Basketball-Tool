"""The draft room's endpoints: one live draft, the picks in it, and the plan for my next ones.

`app.draft` is the engine and it knows nothing about HTTP. This module is the other side of
that line: it persists the draft Misha is sitting in, turns the engine's `ValueError`s into
status codes, and builds the one thing the engine deliberately stops short of — the
round-by-round PLAN, which is my board joined to the availability numbers at each of my
upcoming picks.

Nine verbs over one draft, and they are the things that happen in a draft room:

* `POST /draft` — start one. There is ONE (see below); a second is a 409 unless it is told to
  replace the first. The config is SNAPSHOTTED onto the row, so the draft is self-describing
  and replays identically whatever `DRAFT_*` becomes later (`app.db.models.draft`).
* `GET /draft` — where it stands: the log, the rosters, the clock, my remaining picks.
* `POST /draft/picks` — a pick, entered. Mine, or somebody else's typed in off the screen.
* `PUT /draft/picks/{n}` — a pick already made, re-decided: the mis-entry noticed too late
  for undo to be the fix.
* `POST /draft/simulate` — the room, advanced: auto-pick the OPPONENTS up to my next pick and
  commit what they took. A single seeded draw — one live mock, not a distribution.
* `POST /draft/undo` — take the last pick back, auto or manual. The fix for a mis-entry, and
  the way to re-roll a sim advance.
* `POST /draft/reset` — same config, no picks. Start the mock over.
* `PUT /draft/config` — the seat, before a pick is made, and the seats' NAMES at any time.
* `GET /draft/plan` — my board at each of my upcoming picks (below).
* `GET /draft/availability` — the same probability as the plan's, over the WHOLE available
  board at the next pick of mine there is a WAIT before, so a page can put a percentage on any
  name it draws.

ONE ACTIVE DRAFT, deliberately. Not a session per mock: there is one startup and one seat in
it, and a list of saved drafts would need an owner, a name and a picker, none of which exist.
Re-running a mock is `POST /draft/reset`, which is a verb rather than a second row.

MODE IS A PREFERENCE, NOT A CONSTRAINT. 'simulation' and 'manual' are stored so the page comes
back the way it was left, and nothing here enforces either: entering a pick by hand in a
simulated draft (because the room did something the field model wouldn't) and advancing the sim
in a manual one (because the room stalled) are both things that happen. Undo is always there.

THE TWO SIMULATIONS are different animals and the difference is worth keeping straight:
`POST /draft/simulate` is ONE draw that COMMITS picks — a mock draft happening. `GET
/draft/plan`'s availability is a thousand draws that commit nothing — a probability about the
draft that is happening. The first is seeded randomly by default (a re-run should not replay
the same mock); the second is seeded fixedly by default (a percentage someone compares between
two refreshes must not move on its own).

Everything numeric here is the engine's: `simulate_opponents_until` makes the room's picks,
`simulate_availability` produces every percentage, `field_ranks` builds the board they are
computed over, and `build_state` is the only place a stored log becomes a runnable draft. This
module is serialization, status codes, and which of my board's players to ask about.
"""

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from random import Random, randrange

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.api.consensus import HORIZON_DESCRIPTION
from app.config import get_settings
from app.db.models import Player
from app.db.models.draft import DRAFT_MODES, MODE_SIMULATION, Draft, DraftPick
from app.db.models.master_rank import TAG_TARGET
from app.db.models.master_tier import SCOPE_OVERALL
from app.db.session import get_db
from app.draft import (
    DEDICATED_POSITIONS,
    DEFAULT_SIM_SEED,
    DraftConfig,
    DraftState,
    FieldBoard,
    UnknownSources,
    build_state,
    field_ranks,
    positions_for,
    simulate_availability,
    simulate_opponents_until,
)
from app.ranking import UnknownHorizon
from app.ranking.master import load_entries
from app.ranking.tiers import load_cuts, tiers_for

router = APIRouter(prefix="/draft", tags=["draft"])

MODE_DESCRIPTION = (
    "Does the room advance itself? " + ", ".join(repr(mode) for mode in DRAFT_MODES) + ". A "
    "stored UI preference and nothing more: every verb here works in either mode, because "
    "correcting a mis-entered pick in a simulated draft and auto-picking a stalled room in a "
    "manual one both happen. Anything else is a 422."
)

FIELD_SOURCES_DESCRIPTION = (
    "The source ids the OTHER nine teams are assumed to draft off — the chips from "
    "`GET /sources`. Omit for every source the horizon offers, which is the best guess at what "
    "a room of nine strangers collectively believes; name a subset (`['adp:espn']`) for a room "
    "that visibly drafts one board. Snapshotted onto the draft, because it is how this draft "
    "is being MODELLED rather than a per-request preference. An unknown id is a 400."
)

TEAM_NAMES_DESCRIPTION = (
    "What the seats are called, keyed by seat number as a STRING: "
    "`{'1': 'Sam', '4': 'The Process'}`. Seats left out (or given an empty name) render as "
    "'Team {slot}'. Purely cosmetic — no pick, no need, no autopick weight and no "
    "availability number reads a name. A key outside 1..team_count is a 422."
)

NO_DRAFT = (
    "There is no draft. POST /draft starts one — it takes the shape from DRAFT_* and the "
    "field from the consensus, so an empty body is a complete request."
)


# --- what goes over the wire -------------------------------------------------------------------


class DraftPickRow(BaseModel):
    """One pick that happened."""

    # 1-based, in snake order. The log is contiguous 1..N — a draft cannot skip a pick.
    pick_number: int
    round: int
    team_slot: int
    is_mine: bool = False
    espn_player_id: int
    name: str
    positions: list[str] = []
    # The simulated field took him, rather than this being typed in. Display only: undo and
    # replay treat the two identically.
    is_auto: bool = False


class DraftTeamRow(BaseModel):
    """One seat: what it has taken, and what it still starts nobody at.

    Player ids rather than names, on purpose — every one of them is in `log` with his name
    beside it, and a roster panel is a join the page does once rather than a second list here
    that could disagree with the first.
    """

    team_slot: int
    # What this seat is CALLED: the stored name, or "Team {slot}" when it has none. Resolved
    # here rather than on the page so one answer to "what is seat 4 called" exists, and
    # cosmetic all the way down — nothing in the engine reads it.
    name: str
    is_me: bool = False
    # In the order this seat drafted them.
    player_ids: list[int] = []
    # Its unfilled DEDICATED starter positions — its need, as the field model reads it
    # (`app.draft.needs`). Empty once the starting five is set: UT and the bench take anyone,
    # so a team with five starters needs nobody in particular.
    open_needs: list[str] = []


class DraftStateResponse(BaseModel):
    """The whole draft: the shape it is being run under, the log, the rosters, and the clock."""

    # --- the config snapshot, as stored -----------------------------------------------------
    team_count: int
    rounds: int
    my_slot: int
    roster_slots: dict[str, int] = {}
    # Which consensus the simulated room drafts off, and whose. Null sources = all of them.
    field_horizon: str
    field_source_ids: list[str] | None = None
    mode: str

    # --- the board the draft is over --------------------------------------------------------
    # How many players the field ranks at all. `POST /draft/picks` refuses anyone outside it:
    # availability computed over a board the simulation never draws from is a number about
    # nothing (`app.draft.state`).
    universe_size: int
    total_picks: int
    picks_made: int

    # --- the clock --------------------------------------------------------------------------
    # Null once the draft is complete, both of them.
    on_the_clock: int | None = None
    next_pick_number: int | None = None
    current_round: int | None = None
    is_my_pick: bool = False
    is_complete: bool = False
    # Every pick number my seat owns, and the ones still to come. At slot 2 of 10 the first is
    # [2, 19, 22, 39, 42, ...] — the alternating 17-then-3 wait that makes availability a
    # question worth asking.
    my_pick_numbers: list[int] = []
    my_remaining_pick_numbers: list[int] = []

    created_at: datetime
    updated_at: datetime

    log: list[DraftPickRow] = []
    # Every seat, 1..team_count, mine included.
    teams: list[DraftTeamRow] = []


class DraftCreate(BaseModel):
    """How to start the draft. Every field is optional — an empty body is our league."""

    my_slot: int | None = Field(
        None,
        description="My 1-based seat, counted the way round 1 runs. Defaults to DRAFT_MY_SLOT.",
    )
    mode: str | None = Field(None, description=MODE_DESCRIPTION)
    field_horizon: str | None = Field(
        None,
        description="Which consensus the room is assumed to draft off — "
        + HORIZON_DESCRIPTION
        + " Defaults to the horizon our own board is built under (MASTER_SEED_HORIZON), so the "
        "field and my board are being read on the same terms.",
    )
    field_source_ids: list[str] | None = Field(None, description=FIELD_SOURCES_DESCRIPTION)
    roster_slots: dict[str, int] | None = Field(
        None,
        description="ESPN's `lineupSlotCounts` shape, `{'PG': 1, ..., 'UT': 2, 'BE': 13}`. "
        "Only the five dedicated starter slots in it affect anything — UT and the bench take "
        "anyone. Defaults to our startup roster.",
    )
    team_names: dict[str, str] | None = Field(None, description=TEAM_NAMES_DESCRIPTION)


class DraftConfigWrite(BaseModel):
    """What can be changed about a draft without throwing its picks away."""

    my_slot: int | None = Field(
        None,
        description="My 1-based seat. Accepted only while the draft is EMPTY — once a pick "
        "has been made the seat is part of what those picks mean, and changing it is a "
        "reconfigure (POST /draft?reset=true), not an edit.",
    )
    team_names: dict[str, str] | None = Field(
        None,
        description=TEAM_NAMES_DESCRIPTION + " MERGED into what is stored rather than "
        "replacing it, so naming one seat leaves the others alone; an empty string clears a "
        "name back to 'Team {slot}'. Accepted at any point in the draft — a name is cosmetic.",
    )
    field_horizon: str | None = Field(
        None,
        description="Which consensus the room is assumed to draft off — "
        + HORIZON_DESCRIPTION
        + " Accepted only while the draft is EMPTY, for the same reason the seat is: the "
        "field is what every pick already made was made against, so changing it would "
        "re-describe them. An unknown horizon is a 400.",
    )
    field_source_ids: list[str] | None = Field(
        None,
        description=FIELD_SOURCES_DESCRIPTION + " Here, as with `field_horizon`, only while "
        "the draft is EMPTY. An empty list means the same thing as omitting it on create: "
        "every source the horizon offers.",
    )


class DraftPickWrite(BaseModel):
    """A pick, entered by hand."""

    player_id: int = Field(..., description="Our canonical (ESPN) player id")
    team_slot: int | None = Field(
        None,
        description="Whose pick it is. Defaults to whoever is on the clock; pass it and the "
        "pick is refused (422) unless it matches, which is what a typed-in room wants — 'team "
        "7 took Jokic' should fail loudly if the board thinks team 6 is up.",
    )


class DraftPickEdit(BaseModel):
    """A pick already made, re-decided: who it should have taken instead."""

    player_id: int = Field(
        ...,
        description="Our canonical (ESPN) player id. The seat is NOT in this body — the snake "
        "owns which team picks at a pick number, and an edit changes who was taken, never "
        "whose turn it was.",
    )


class DraftSimulateWrite(BaseModel):
    """How to roll the room forward. Every field is optional."""

    count: int | None = Field(
        None,
        ge=1,
        description="Stop after at most this many opponent picks. Omitted, the room runs to "
        "my next pick, which is the usual thing to want; `count: 1` is the step button, for "
        "watching the room one name at a time. It is a CAP and not a target — the advance "
        "still stops at my seat and at the end of the draft, so a count larger than the gap "
        "makes fewer picks than asked and a count while I am already on the clock makes none.",
    )
    seed: int | None = Field(
        None,
        description="The RNG seed for this one advance. Omitted, a fresh random one is drawn "
        "and echoed back, so re-running a mock is a different mock. Pass it to reproduce one.",
    )
    top_k: int | None = Field(
        None, ge=1, description="How many available players the field will consider at all"
    )
    temperature: float | None = Field(
        None,
        gt=0,
        description="The softmax temperature, in PLACES: a player ranked this many spots "
        "better is e times likelier to go. Lower drafts the consensus straighter down.",
    )
    need_mult: float | None = Field(
        None,
        gt=0,
        description="The tilt towards a player filling an open dedicated starter slot. 1.0 "
        "turns the positional nudge off entirely.",
    )


class DraftAdvanceResponse(BaseModel):
    """What the room did, and where that leaves the draft."""

    # The seed this advance was rolled with — echoed so a mock worth keeping can be re-run.
    seed: int
    # The opponents' picks, in order. Empty when my seat is already on the clock or the draft
    # is over: this never picks for me (`app.draft.autopick`).
    picks: list[DraftPickRow] = []
    state: DraftStateResponse


class PlanPlayerRow(BaseModel):
    """One name on the plan: where I have him, and how likely he is to last."""

    espn_player_id: int
    name: str
    positions: list[str] = []
    # MY board rank and MY tier band (`GET /master/board`). The tier is null when the board's
    # 'overall' dividers have never been read into existence — that seeding belongs to the
    # board endpoint, and a plan request is not going to start writing tiers.
    rank: int | None = None
    tier: int | None = None
    # 'target' | 'fade' | null. Every row in `targets` has 'target' here by construction.
    tag: str | None = None
    note: str | None = None
    # His place on the FIELD's board — the room's opinion, which is what the availability
    # number is actually computed from. Read the two together: my 14 against their 40 is why
    # he is still there at 22.
    field_rank: int | None = None
    # The chance he is still on the board when this pick comes up, in [0, 1]. Non-increasing
    # across my later picks, by construction (`app.draft.availability`).
    availability: float = 0.0
    # Would he cover a dedicated starter slot I still have open?
    fills_need: bool = False


class PlanPickRow(BaseModel):
    """One of my upcoming picks, and who to be thinking about at it."""

    pick_number: int
    round: int
    # How many picks away it is — 0 means I am on the clock now.
    picks_away: int
    # My unfilled dedicated starter positions. The same at every planned pick, because the
    # projection takes nobody for me in between (see `GET /draft/plan`).
    open_needs: list[str] = []
    # The players I tagged 'target' who are still available, in my board order.
    targets: list[PlanPlayerRow] = []
    # The top of my board that is still available, in my board order.
    best_available: list[PlanPlayerRow] = []


class DraftPlanResponse(BaseModel):
    """The round-by-round plan: my board, at each of my upcoming picks, with the odds."""

    # The Monte Carlo behind every `availability` on this response.
    iterations: int
    seed: int
    # How long each list is, at most.
    size: int
    # The field the availability was computed against — the draft's, not the request's.
    field_horizon: str
    field_source_ids: list[str] | None = None
    # How many ranked, non-excluded players on my board are still available. The denominator
    # for "the top of the board" — `best_available` is the first `size` of these.
    available_on_board: int
    # Nothing to plan for: the draft is over. `picks` is empty.
    is_complete: bool = False
    picks: list[PlanPickRow] = []


class DraftAvailabilityResponse(BaseModel):
    """How likely every player still on the board is to last until my next WAITING pick."""

    # Which pick the numbers are about: my next one while I am waiting, the one AFTER this one
    # while I am on the clock (see `get_draft_availability` — everybody is 1.0 at a pick I am
    # already making). Null when there isn't one.
    pick_number: int | None = None
    # Nothing left to be available FOR: the draft is over, I have no pick remaining in it, or I
    # am on the clock at my last one. `availability` is empty in all three.
    is_complete: bool = False
    # player id -> chance in [0, 1] he is still there at `pick_number`. EVERY available
    # player the field ranks, so a page can put a number on any name it draws.
    availability: dict[int, float] = {}


# --- the draft, loaded -------------------------------------------------------------------------


def _current(db: Session) -> Draft | None:
    """The one draft, or None. Ordered by id so a hand-inserted second row is at least stable."""
    return db.scalars(select(Draft).order_by(Draft.id)).first()


def _require(db: Session) -> Draft:
    if (draft := _current(db)) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NO_DRAFT)
    return draft


def _load(db: Session, draft: Draft) -> tuple[DraftState, FieldBoard]:
    """Replay the stored log into an engine state, mapping its two failures to status codes.

    A field selection that no longer resolves is a 400 (the request is against a board that
    cannot be built). A log that no longer replays is a 409: the room being modelled changed
    under a draft in progress — a source was deleted and a player it ranked is no longer in
    the field's universe — and the draft has to be re-created rather than silently read as
    something it isn't.
    """
    try:
        return build_state(db, draft)
    except (UnknownHorizon, UnknownSources) as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error
    except ValueError as error:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"This draft's stored picks no longer replay against its field: {error} The sources "
            "it was created against have changed. POST /draft?reset=true re-creates it.",
        ) from error


def _identities(db: Session, player_ids: set[int]) -> dict[int, Player]:
    """Names and positions for a set of players — one query, however many rows want them."""
    if not player_ids:
        return {}
    return {
        player.espn_player_id: player
        for player in db.scalars(select(Player).where(Player.espn_player_id.in_(player_ids)))
    }


def _team_name(names: dict | None, slot: int) -> str:
    """What a seat is called: its stored name, or "Team {slot}".

    The default is computed rather than stored, so a draft created without names is a NULL
    column and not ten rows of the string the page would have printed anyway.
    """
    stored = (names or {}).get(str(slot))
    if isinstance(stored, str) and stored.strip():
        return stored.strip()
    return f"Team {slot}"


def _clean_names(names: dict[str, str] | None, team_count: int) -> dict[str, str]:
    """Validate a slot->name map and drop the blanks, or 422.

    Keys are seat numbers as strings (JSON has no integer keys); a key that isn't one, or is
    outside 1..team_count, is a 422 rather than a name quietly attached to nobody. A blank
    name is not stored at all — it IS the default, and storing it would be storing the string
    `_team_name` would have produced.
    """
    if not names:
        return {}
    cleaned: dict[str, str] = {}
    for key, value in names.items():
        try:
            slot = int(key)
        except (TypeError, ValueError):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"team_names key {key!r} is not a seat number; keys are 1..{team_count} as "
                "strings, e.g. {'1': 'Sam'}",
            ) from None
        if not 1 <= slot <= team_count:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"team_names has a name for seat {slot}, which is outside 1..{team_count}",
            )
        trimmed = (value or "").strip()
        if trimmed:
            cleaned[str(slot)] = trimmed
    return cleaned


def _needs(open_dedicated: frozenset[str]) -> list[str]:
    """Open dedicated slots, in the order a lineup card prints them rather than alphabetically."""
    return [position for position in DEDICATED_POSITIONS if position in open_dedicated]


def _pick_row(pick: DraftPick, state: DraftState, players: dict[int, Player]) -> DraftPickRow:
    player = players.get(pick.player_id)
    return DraftPickRow(
        pick_number=pick.pick_number,
        round=state.config.round_of(pick.pick_number),
        team_slot=pick.team_slot,
        is_mine=pick.team_slot == state.config.my_slot,
        espn_player_id=pick.player_id,
        # A player whose identity row has gone would have taken his pick with him (the FK
        # cascades), so this is unreachable and cheaper than a 500 if it ever isn't.
        name=player.full_name if player else f"player {pick.player_id}",
        positions=list(player.positions or []) if player else [],
        is_auto=pick.is_auto,
    )


def _state_response(db: Session, draft: Draft, state: DraftState) -> DraftStateResponse:
    """Render the draft. One identity query for the whole log, not one per pick."""
    log = list(
        db.scalars(
            select(DraftPick).where(DraftPick.draft_id == draft.id).order_by(DraftPick.pick_number)
        )
    )
    players = _identities(db, {pick.player_id for pick in log})
    config = state.config
    next_number = state.next_pick_number
    return DraftStateResponse(
        team_count=config.team_count,
        rounds=config.rounds,
        my_slot=config.my_slot,
        roster_slots=dict(config.roster_slots),
        field_horizon=draft.field_horizon,
        field_source_ids=list(draft.field_source_ids) if draft.field_source_ids else None,
        mode=draft.mode,
        universe_size=len(state.universe),
        total_picks=config.total_picks,
        picks_made=len(state.selections),
        on_the_clock=state.on_the_clock,
        next_pick_number=next_number,
        current_round=config.round_of(next_number) if next_number else None,
        is_my_pick=state.is_my_pick,
        is_complete=state.is_complete,
        my_pick_numbers=config.my_pick_numbers,
        my_remaining_pick_numbers=_remaining(state),
        created_at=draft.created_at,
        updated_at=draft.updated_at,
        log=[_pick_row(pick, state, players) for pick in log],
        teams=[
            DraftTeamRow(
                team_slot=slot,
                name=_team_name(draft.team_names, slot),
                is_me=slot == config.my_slot,
                player_ids=state.roster(slot),
                open_needs=_needs(state.open_dedicated(slot)),
            )
            for slot in range(1, config.team_count + 1)
        ],
    )


def _stop_after(limit: int) -> Callable[[DraftState], bool]:
    """The `count` cap, as the stop predicate `simulate_opponents_until` takes.

    A predicate rather than a loop of one-pick advances, because re-rolling would draw from a
    fresh `Random` each time and ten steps would not be the same mock as one advance of ten.
    It reads the state's own pick count, so there is no counter to get out of step with what
    was actually applied.
    """

    def reached(current: DraftState) -> bool:
        return len(current.picks) >= limit

    return reached


def _remaining(state: DraftState) -> list[int]:
    """My pick numbers that are still to come. Empty once the draft is complete."""
    start = state.next_pick_number
    if start is None:
        return []
    return [number for number in state.config.my_pick_numbers if number >= start]


# --- the draft's lifecycle ---------------------------------------------------------------------


@router.post("", response_model=DraftStateResponse, status_code=status.HTTP_201_CREATED)
def post_draft(
    payload: DraftCreate = Body(default_factory=DraftCreate),
    db: Session = Depends(get_db),
    reset: bool = Query(
        False,
        description="Replace the existing draft. Required to start a second one — every pick "
        "made in the first goes with it.",
    ),
) -> DraftStateResponse:
    """Start the draft, with its config SNAPSHOTTED onto the row.

    The snapshot is the whole design of this endpoint (`app.db.models.draft`): a draft is a log
    of pick numbers, and a pick number only means something under one shape. Copying
    `team_count` / `rounds` / `my_slot` / `roster_slots` / the field selection in means the
    draft replays to exactly the state it was in whatever `DRAFT_*` becomes later — a seat
    changed for next year's startup cannot re-label picks already made in this one.

    ONE draft at a time. A second is a 409 rather than a second row, guarded the way `POST
    /master/seed` is, because the picks on the first are a record of something that happened
    and nothing else can reconstruct them. `reset=true` replaces it; `POST /draft/reset` keeps
    the config and only drops the picks.

    `team_count` and `rounds` are not in the body on purpose: they are what the league IS, they
    come from `DRAFT_TEAM_COUNT` / `DRAFT_ROUNDS`, and a mock of a different-sized league is a
    setting rather than a request.
    """
    settings = get_settings()
    existing = _current(db)
    if existing is not None and not reset:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "A draft already exists. Pass reset=true to replace it — every pick in it goes. "
            "POST /draft/reset starts the same draft over without reconfiguring it, and "
            "POST /draft/undo takes back one pick.",
        )

    mode = payload.mode or MODE_SIMULATION
    if mode not in DRAFT_MODES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Unknown mode {mode!r}; supported: " + ", ".join(repr(name) for name in DRAFT_MODES),
        )

    # Built before anything is written, so a nonsense seat or roster is a 422 against an
    # untouched database rather than a draft nobody can use. `from_settings` is where the
    # defaults live (the engine's own), so this endpoint doesn't carry a second copy of them.
    try:
        config = DraftConfig.from_settings(settings, roster_slots=payload.roster_slots)
        if payload.my_slot is not None:
            config = replace(config, my_slot=payload.my_slot)
    except ValueError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error

    horizon = payload.field_horizon or settings.master_seed_horizon
    source_ids = payload.field_source_ids
    # The field is resolved BEFORE anything is written, so an unknown horizon or source id is a
    # 400 against an untouched database rather than a half-created draft. It is also the
    # universe the state is built over, which is why this is the request's one consensus load
    # rather than `build_state` doing it again on a draft that has no picks to replay yet.
    try:
        ranks = field_ranks(db, horizon, source_ids)
    except (UnknownHorizon, UnknownSources) as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error
    state = DraftState(config, ranks, positions_for(db, ranks.keys()))

    draft = Draft(
        team_count=config.team_count,
        rounds=config.rounds,
        my_slot=config.my_slot,
        roster_slots=dict(config.roster_slots),
        field_horizon=horizon,
        field_source_ids=list(source_ids) if source_ids else None,
        mode=mode,
        # Empty stays NULL: "nobody is named" is the column's own default, not a map of ten
        # blanks. Validated before anything is written, like the seat above it.
        team_names=_clean_names(payload.team_names, config.team_count) or None,
    )

    if existing is not None:
        # The picks go with it: the FK cascades on Postgres, and the ORM cascade does it on the
        # SQLite the tests run on, where foreign keys are off by default.
        db.delete(existing)
        db.flush()
    db.add(draft)
    db.flush()

    response = _state_response(db, draft, state)
    db.commit()
    return response


@router.get("", response_model=DraftStateResponse)
def get_draft(db: Session = Depends(get_db)) -> DraftStateResponse:
    """Where the draft stands: the log, every team's roster, the clock, and my remaining picks.

    Nothing is recomputed from a snapshot — the state is REPLAYED from the pick rows through
    the same `apply_pick` that made them (`app.draft.session`), so what comes back cannot
    disagree with the log it came from.
    """
    draft = _require(db)
    state, _ = _load(db, draft)
    return _state_response(db, draft, state)


@router.post("/reset", response_model=DraftStateResponse)
def post_draft_reset(db: Session = Depends(get_db)) -> DraftStateResponse:
    """Throw the picks away and keep the config: the same draft, from pick 1.

    What re-running a mock is. `POST /draft?reset=true` is the other reset — it replaces the
    config too, which is what you want when the seat or the field selection was wrong rather
    than when the mock simply went badly.
    """
    draft = _require(db)
    db.execute(delete(DraftPick).where(DraftPick.draft_id == draft.id))
    _touch(draft)
    db.flush()
    state, _ = _load(db, draft)
    response = _state_response(db, draft, state)
    db.commit()
    return response


@router.put("/config", response_model=DraftStateResponse)
def put_draft_config(
    payload: DraftConfigWrite = Body(...),
    db: Session = Depends(get_db),
) -> DraftStateResponse:
    """Change the seat or the field (before a pick is made), or the names (at any point).

    TWO KINDS OF FIELD WITH DIFFERENT RULES, and the difference is the whole endpoint. A NAME is
    cosmetic: nothing in the engine reads it, so it can be edited at pick 1 or pick 141 and
    nothing that has happened means anything different afterwards. THE SEAT is not: a pick
    number only means something under one shape (`app.db.models.draft`), and moving my seat
    after pick 19 would silently re-label every pick already made as somebody else's. So the
    seat moves only while the draft is EMPTY — which is the case that actually happens, the
    "I set it to 2 and I'm actually at 7" noticed before the room starts — and a started
    draft answers 422 pointing at the reconfigure that can do it, by throwing the picks away.

    THE FIELD IS THE SEAT'S RULE AGAIN. `field_horizon` and `field_source_ids` are how this
    draft is being MODELLED — whose board the other nine seats are assumed to be reading — so
    every pick already in the log was made against them, and moving them afterwards would
    re-describe picks that have happened. Editable while the draft is empty (which is when the
    question "what does this room actually draft off?" gets answered), a 422 after that. An
    unknown horizon or source id is a 400, validated through the same `field_ranks` call
    `POST /draft` runs. Sending the field this draft already has is a no-op at any point, so a
    panel that always submits its current selection never trips the 422.

    Sitting between `POST /draft?reset=true` (replace everything) and nothing at all: an
    empty draft has no picks to protect, so making the seat correctable without a reset is a
    button that loses nothing.

    Names MERGE rather than replace, so naming one seat leaves the other nine alone; an empty
    string clears one back to "Team {slot}".
    """
    draft = _require(db)
    state, _ = _load(db, draft)

    if payload.my_slot is not None and payload.my_slot != draft.my_slot:
        if not 1 <= payload.my_slot <= draft.team_count:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"my_slot {payload.my_slot} is outside 1..{draft.team_count}",
            )
        if len(state.selections) > 0:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"this draft is {len(state.selections)} pick"
                f"{'' if len(state.selections) == 1 else 's'} in, so the seat is part of what "
                "those picks mean — reconfigure to change a started draft's seat "
                "(POST /draft?reset=true), which throws them away. Names can still be "
                "changed here.",
            )
        draft.my_slot = payload.my_slot

    # The FIELD, under exactly the seat's rule and for exactly the seat's reason: every pick
    # already made was made against this board, so changing it under a started draft would
    # silently re-describe them. Resolved through `field_ranks` before anything is written, so
    # an unknown horizon or source id is a 400 against an untouched draft — the same
    # validation `POST /draft` runs, because it is the same call.
    if payload.field_horizon is not None or payload.field_source_ids is not None:
        horizon = payload.field_horizon or draft.field_horizon
        stored_ids = list(draft.field_source_ids) if draft.field_source_ids else None
        # An empty list is "all of them", the way create reads it — so ticking every box and
        # ticking none of them cannot mean two different rooms.
        wanted_ids = (
            stored_ids if payload.field_source_ids is None else (payload.field_source_ids or None)
        )

        changed = horizon != draft.field_horizon or sorted(wanted_ids or ()) != sorted(
            stored_ids or ()
        )
        if changed and len(state.selections) > 0:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"this draft is {len(state.selections)} pick"
                f"{'' if len(state.selections) == 1 else 's'} in, so the field is what those "
                "picks were made against — reconfigure to change a started draft's field "
                "(POST /draft?reset=true), which throws them away.",
            )
        if changed:
            try:
                field_ranks(db, horizon, wanted_ids)
            except (UnknownHorizon, UnknownSources) as error:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error
            draft.field_horizon = horizon
            draft.field_source_ids = list(wanted_ids) if wanted_ids else None

    if payload.team_names is not None:
        merged = dict(draft.team_names or {})
        merged.update(_clean_names(payload.team_names, draft.team_count))
        # A name submitted blank is a name removed: `_clean_names` drops it from the update,
        # so it has to be taken out of the merge explicitly rather than surviving it.
        for key, value in payload.team_names.items():
            if not (value or "").strip():
                merged.pop(str(int(key)), None)
        draft.team_names = merged or None

    _touch(draft)
    db.flush()
    # Re-replayed rather than patched: a new seat changes whose picks the ones already in the
    # log were, which is `is_mine` on every row and the whole `teams` block.
    state, _ = _load(db, draft)
    response = _state_response(db, draft, state)
    db.commit()
    return response


# --- making picks ------------------------------------------------------------------------------


@router.post("/picks", response_model=DraftStateResponse)
def post_draft_pick(
    payload: DraftPickWrite = Body(...),
    db: Session = Depends(get_db),
) -> DraftStateResponse:
    """Enter a pick: mine, or a rival's read off the screen.

    Strictly in pick order, because the draft has one clock — a state that let pick 22 be
    entered before 19 could not say who was available when. Everything it can refuse, the
    engine refuses (`DraftState.apply_pick`), and all of it comes back as a 422 carrying the
    engine's own message: a player already drafted, a player the field doesn't rank (and so
    the simulation can never draw from), a `team_slot` that isn't the one on the clock, or a
    draft that is already complete.

    Stored `is_auto=false`, which is the only thing that distinguishes it from a pick the sim
    made. Undo treats the two identically.
    """
    draft = _require(db)
    state, _ = _load(db, draft)
    try:
        pick = state.apply_pick(payload.player_id, team_slot=payload.team_slot)
    except ValueError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error

    db.add(
        DraftPick(
            draft_id=draft.id,
            pick_number=pick.pick_number,
            team_slot=pick.team_slot,
            player_id=pick.player_id,
            is_auto=False,
        )
    )
    _touch(draft)
    db.flush()
    response = _state_response(db, draft, state)
    db.commit()
    return response


@router.put("/picks/{pick_number}", response_model=DraftStateResponse)
def put_draft_pick(
    pick_number: int,
    payload: DraftPickEdit = Body(...),
    db: Session = Depends(get_db),
) -> DraftStateResponse:
    """Change who a pick took. The fix for a mis-entry deeper than the last pick, and the
    manual override for a room pick the sim got wrong.

    Undo is the fix for the pick that just happened; this is the fix for pick 7 noticed at
    pick 30, which undo can only reach by throwing away twenty-three picks that were right.
    It edits IN PLACE: the `team_slot` is untouched (the snake owns it, and a pick number that
    changed hands would desynchronise every later one), and the player who was there goes back
    on the board for anyone to take.

    Stored `is_auto=false` whatever it was, because an edited pick is a decision somebody made
    — including the one case worth having, an auto-picked opponent corrected to what the room
    really did.

    REPLAY STAYS VALID, which is what makes this safe at all: `build_state` walks the log by
    `pick_number` and applies whoever is on each row, so an edited row is simply a different
    player applied at the same slot. The one thing that could break it — the new player being
    taken at another pick — is the 422 below.
    """
    draft = _require(db)
    state, _ = _load(db, draft)

    made = len(state.selections)
    if not 1 <= pick_number <= made:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"pick {pick_number} has not been made; this draft is {made} pick"
            f"{'' if made == 1 else 's'} in. Only a pick that happened can be edited — "
            "POST /draft/picks makes the next one.",
        )
    if payload.player_id not in state.universe:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"player {payload.player_id} is not in this draft's universe "
            f"({len(state.universe)} players); nobody ranks him",
        )

    row = db.scalars(
        select(DraftPick).where(
            DraftPick.draft_id == draft.id, DraftPick.pick_number == pick_number
        )
    ).one()
    if row.player_id == payload.player_id:
        # Already who he is. A no-op 200 rather than a 422: re-submitting the same name is not
        # a mistake worth refusing, and the caller wants the state either way.
        return _state_response(db, draft, state)

    conflict = db.scalars(
        select(DraftPick).where(
            DraftPick.draft_id == draft.id,
            DraftPick.player_id == payload.player_id,
            DraftPick.pick_number != pick_number,
        )
    ).first()
    if conflict is not None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"player {payload.player_id} was already drafted at pick {conflict.pick_number} "
            f"by team slot {conflict.team_slot}; edit or undo that pick first",
        )

    row.player_id = payload.player_id
    row.is_auto = False
    _touch(draft)
    db.flush()
    # Re-replayed rather than patched: the edit changes who is available from this pick
    # onwards, and the honest way to say that is to run the log again.
    state, _ = _load(db, draft)
    response = _state_response(db, draft, state)
    db.commit()
    return response


@router.post("/simulate", response_model=DraftAdvanceResponse)
def post_draft_simulate(
    payload: DraftSimulateWrite = Body(default_factory=DraftSimulateWrite),
    db: Session = Depends(get_db),
) -> DraftAdvanceResponse:
    """Let the room draft up to my next pick, and commit what it took.

    ONE seeded draw, not a distribution: this is a mock draft happening, and the picks it makes
    are as real as typed ones (`is_auto=true` marks where they came from). The seed is random
    by default and echoed back, because re-running a mock should be a different mock — pass
    `seed` to reproduce one exactly.

    It STOPS AT MY SEAT and never picks for me — the engine's rule, not a courtesy here
    (`app.draft.autopick`). So an advance while I am already on the clock is an empty list and
    a 200, not an error; the room has nothing to do until I take somebody. `POST /draft/undo`
    re-rolls an advance one pick at a time.

    `count` caps how many picks the room makes, and caps ONLY — the two stops above still
    apply first. That is the step button: `count: 1` is one opponent pick, and watching the
    room name by name is the same draft as letting it run, because the cap is a predicate
    inside the one seeded roll rather than a series of separate ones.
    """
    settings = get_settings()
    draft = _require(db)
    state, board = _load(db, draft)

    seed = payload.seed if payload.seed is not None else randrange(2**32)
    stop = _stop_after(len(state.picks) + payload.count) if payload.count is not None else None
    made = simulate_opponents_until(
        state,
        board,
        Random(seed),
        stop=stop,
        top_k=payload.top_k or settings.draft_autopick_topk,
        temperature=payload.temperature or settings.draft_autopick_temperature,
        need_mult=payload.need_mult or settings.draft_autopick_need_mult,
    )
    for pick in made:
        assert pick.player_id is not None  # the field never passes; only my seat is stepped over
        db.add(
            DraftPick(
                draft_id=draft.id,
                pick_number=pick.pick_number,
                team_slot=pick.team_slot,
                player_id=pick.player_id,
                is_auto=True,
            )
        )
    if made:
        _touch(draft)
    db.flush()

    players = _identities(db, {pick.player_id for pick in made if pick.player_id})
    response = DraftAdvanceResponse(
        seed=seed,
        picks=[
            DraftPickRow(
                pick_number=pick.pick_number,
                round=state.config.round_of(pick.pick_number),
                team_slot=pick.team_slot,
                is_mine=False,
                espn_player_id=pick.player_id,
                name=players[pick.player_id].full_name
                if pick.player_id in players
                else f"player {pick.player_id}",
                positions=list(players[pick.player_id].positions or [])
                if pick.player_id in players
                else [],
                is_auto=True,
            )
            for pick in made
            if pick.player_id is not None
        ],
        state=_state_response(db, draft, state),
    )
    db.commit()
    return response


@router.post("/undo", response_model=DraftStateResponse)
def post_draft_undo(db: Session = Depends(get_db)) -> DraftStateResponse:
    """Take back the last pick, whoever or whatever made it.

    The fix for a mis-entry, and the way to re-roll a sim advance — call it once per pick the
    advance made. It removes the HIGHEST `pick_number` and nothing else: undoing pick 12 of 30
    would leave a hole in a log that has to be replayable in order, so there is deliberately no
    way to ask for it.

    A draft with no picks in it is a 409 rather than a silent no-op: "there was nothing to
    undo" is worth hearing.
    """
    draft = _require(db)
    last = db.scalars(
        select(DraftPick)
        .where(DraftPick.draft_id == draft.id)
        .order_by(DraftPick.pick_number.desc())
        .limit(1)
    ).first()
    if last is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This draft has no picks; there is nothing to undo. POST /draft/picks enters one.",
        )

    db.delete(last)
    _touch(draft)
    db.flush()
    state, _ = _load(db, draft)
    response = _state_response(db, draft, state)
    db.commit()
    return response


def _touch(draft: Draft) -> None:
    """Mark the draft as having moved.

    `onupdate` only fires when a column on the row itself changes, and a pick is a row in
    another table — so `updated_at` would otherwise say "created" forever on a draft that has
    been running for thirty picks. Written as `now()` rather than a Python timestamp so the
    clock is the database's on both dialects, the same one `created_at` was stamped by.
    """
    draft.updated_at = func.now()


# --- the plan ----------------------------------------------------------------------------------


@router.get("/plan", response_model=DraftPlanResponse)
def get_draft_plan(
    db: Session = Depends(get_db),
    iterations: int | None = Query(
        None,
        ge=1,
        description="Monte-Carlo iterations behind every availability. Defaults to "
        "DRAFT_SIM_ITERATIONS. More is finer and slower; the standard error on a 50% answer "
        "is about 1.6 points at 1,000.",
    ),
    seed: int = Query(
        DEFAULT_SIM_SEED,
        description="The RNG seed. Fixed by default, deliberately: an availability percentage "
        "is a number someone compares between two refreshes, so the same committed state has "
        "to give the same answer twice.",
    ),
    picks: int | None = Query(
        None,
        ge=1,
        description="How many of my upcoming picks to plan for. Defaults to all of them, which "
        "from pick 1 means simulating nearly the whole draft a thousand times — pass 3 or 4 "
        "while a clock is running.",
    ),
    size: int | None = Query(
        None, ge=1, description="How many names in each list. Defaults to DRAFT_PLAN_SIZE."
    ),
) -> DraftPlanResponse:
    """My board, at each of my upcoming picks, with the chance each name is still there.

    This is what the whole draft engine is for. Two lists per pick, because they answer two
    different questions:

    * `targets` — the players I tagged `target` on my board and have not lost yet. "Can I still
      get the guy I wanted?"
    * `best_available` — the top of my board, still there. "And if not, who is actually next?"

    Both are MY order (`app.ranking.master`), never the consensus — the players I am highest on
    relative to the field are exactly the ones whose availability I most need the truth about.
    The `availability` beside each name is the field's opinion applied to my list: the fraction
    of a thousand simulated rooms in which he was still on the board when that pick came up.

    TWO THINGS TO READ CORRECTLY. Availability at my NEXT pick is exact given the picks made —
    nothing of mine intervenes. Availability at a LATER pick is computed as though I take
    nobody in between, which can only overstate who survives, and by exactly the players I was
    about to take anyway (`app.draft.availability` argues this at length). It is also why
    `open_needs` is the same on every planned pick: the projection never fills a slot for me.

    A player on my board that the FIELD doesn't rank is absent from both lists. He is not in
    the draft's universe, so the simulation can never take him and no pick can be entered for
    him either — there is no availability to report, rather than a 100% that means nothing.

    The availability run is bounded to the names actually on the lists, so its cost is the
    picks it has to simulate and not the size of the board.
    """
    settings = get_settings()
    draft = _require(db)
    state, board = _load(db, draft)
    limit = size or settings.draft_plan_size
    wanted_iterations = iterations or settings.draft_sim_iterations

    planned = _remaining(state)[: picks or len(state.config.my_pick_numbers)]
    board_entries = load_entries(db)
    # A ranked, non-excluded board entry that the field also has a rank for: the set aside pile
    # is not a plan, and a player nobody ranks cannot be drafted here at all (see the docstring).
    entries = [
        entry
        for entry in board_entries
        if entry.rank is not None and not entry.excluded and state.is_available(entry.player_id)
    ]
    best = entries[:limit]
    targets = [entry for entry in entries if entry.tag == TAG_TARGET][:limit]

    if not planned or not entries:
        return DraftPlanResponse(
            iterations=wanted_iterations,
            seed=seed,
            size=limit,
            field_horizon=draft.field_horizon,
            field_source_ids=list(draft.field_source_ids) if draft.field_source_ids else None,
            available_on_board=len(entries),
            is_complete=state.is_complete,
        )

    # The two lists are the same players at every planned pick — what changes down the page is
    # the percentage, not the names — so this is the whole candidate set, and bounding the run
    # to it keeps the Monte Carlo about the picks it simulates rather than about the board.
    candidates = {entry.player_id for entry in (*best, *targets)}
    availability = simulate_availability(
        state,
        board,
        planned,
        iterations=wanted_iterations,
        seed=seed,
        top_k=settings.draft_autopick_topk,
        temperature=settings.draft_autopick_temperature,
        need_mult=settings.draft_autopick_need_mult,
        candidates=candidates,
    )

    players = _identities(db, candidates)
    tiers = _board_tiers(db, board_entries)
    needs = _needs(state.open_dedicated(state.config.my_slot))
    next_number = state.next_pick_number
    assert next_number is not None  # `planned` is empty once the draft is complete

    def rows(entries_for_list, pick_number: int) -> list[PlanPlayerRow]:
        return [
            PlanPlayerRow(
                espn_player_id=entry.player_id,
                name=players[entry.player_id].full_name,
                positions=list(players[entry.player_id].positions or []),
                rank=entry.rank,
                tier=tiers.get(entry.rank),
                tag=entry.tag,
                note=entry.note,
                field_rank=board.ranks.get(entry.player_id),
                availability=availability.get(entry.player_id, {}).get(pick_number, 0.0),
                fills_need=state.fills_need(state.config.my_slot, entry.player_id),
            )
            for entry in entries_for_list
            if entry.player_id in players
        ]

    return DraftPlanResponse(
        iterations=wanted_iterations,
        seed=seed,
        size=limit,
        field_horizon=draft.field_horizon,
        field_source_ids=list(draft.field_source_ids) if draft.field_source_ids else None,
        available_on_board=len(entries),
        is_complete=state.is_complete,
        picks=[
            PlanPickRow(
                pick_number=number,
                round=state.config.round_of(number),
                picks_away=number - next_number,
                open_needs=needs,
                targets=rows(targets, number),
                best_available=rows(best, number),
            )
            for number in planned
        ],
    )


@router.get("/availability", response_model=DraftAvailabilityResponse)
def get_draft_availability(
    db: Session = Depends(get_db),
    iterations: int | None = Query(
        None,
        ge=1,
        description="Monte-Carlo iterations. Defaults to DRAFT_SIM_ITERATIONS. The standard "
        "error on a 50% answer is about 1.6 points at 1,000.",
    ),
    seed: int = Query(
        DEFAULT_SIM_SEED,
        description="The RNG seed. Fixed by default, deliberately: a percentage someone "
        "compares between two refreshes must not move on its own.",
    ),
) -> DraftAvailabilityResponse:
    """The whole available board's chance of lasting until my next pick.

    `GET /draft/plan` answers the same question about a SHORTLIST — the top of my board and
    my targets, at each of my upcoming picks. This answers it about EVERYONE, at one pick.
    That is the difference worth knowing: a page that draws a rankings column two hundred
    names deep, or a sidebar the search can reach the whole board through, needs a number for
    any name it might put on screen, and asking the plan for a `size` big enough to cover
    that is asking the wrong endpoint a bigger question.

    IT IS CHEAP, which is the fact that makes this endpoint possible at all. The cost of a
    Monte Carlo here is the opponent picks it has to SIMULATE, not the players it tracks —
    `simulate_availability` counts from the taken side, so tracking two hundred names and
    tracking eight cost within a rounding error of each other (`app.draft.availability`).
    One target pick rather than four also makes it the cheapest availability run on the site.

    ONE PICK, AND IT IS ALWAYS A FUTURE ONE. Availability at a pick nothing of mine
    intervenes before is the exact number given the committed state, so it needs none of the
    "as though I take nobody in between" caveat the plan's later picks carry. Which pick that
    is depends on the clock, and the distinction is the difference between a useful number and
    a column of 100%:

    * WAITING (somebody else is on the clock): my NEXT pick, `_remaining[0]`. The wait between
      now and it is exactly the opponent picks the Monte Carlo has to draw.
    * ON THE CLOCK: the pick AFTER this one, `_remaining[1]`. `_remaining[0]` is the pick I am
      making right now, and nothing happens between now and it — everybody would be 1.0, which
      answers no question anybody has. "Can I wait on him?" is a question about the NEXT time
      round, so that is the pick the number is about.

    Either way `pick_number` says which pick it is, because a percentage about pick 19 read as
    a percentage about pick 2 is worse than no percentage at all.

    ON THE CLOCK AT MY LAST PICK there is no next time round, so there is nothing to wait for:
    that reads as complete, the same shape a finished draft answers with.

    Keyed by player id, over every still-available player the FIELD ranks — a player nobody
    ranks is not in the draft's universe and has no availability to report, so he is absent
    rather than carrying a 100% that means nothing.
    """
    settings = get_settings()
    draft = _require(db)
    state, board = _load(db, draft)

    remaining = _remaining(state)
    # Nothing to be available for — no pick of mine left at all, or, on the clock, no pick of
    # mine AFTER this one. Reported as complete rather than as an empty map with a pick
    # number, so the caller doesn't have to tell "no answer" from "0% for all".
    wanted = 1 if state.is_my_pick else 0
    if state.is_complete or len(remaining) <= wanted:
        return DraftAvailabilityResponse(pick_number=None, is_complete=True, availability={})

    number = remaining[wanted]
    computed = simulate_availability(
        state,
        board,
        [number],
        iterations=iterations or settings.draft_sim_iterations,
        seed=seed,
        top_k=settings.draft_autopick_topk,
        temperature=settings.draft_autopick_temperature,
        need_mult=settings.draft_autopick_need_mult,
        # The whole available field-ranked board. See the docstring: this is the cheap axis.
        candidates=None,
    )
    return DraftAvailabilityResponse(
        pick_number=number,
        is_complete=False,
        availability={
            player_id: values[number] for player_id, values in computed.items() if number in values
        },
    )


def _board_tiers(db: Session, entries: list) -> dict[int, int]:
    """board rank -> its tier band, from the dividers the board has STORED.

    Read-only, unlike `GET /master/board`'s `_tiers`: that endpoint owns seeding a scope's cuts
    from the value gaps, and a plan request is not the place to start writing tiers. A board
    whose overall dividers have never been read into existence gets no tiers on the plan, which
    is a missing column rather than a wrong one.
    """
    cuts = load_cuts(db).get(SCOPE_OVERALL)
    ranked = sum(1 for entry in entries if entry.rank is not None and not entry.excluded)
    if not cuts or not ranked:
        return {}
    return dict(enumerate(tiers_for(cuts, ranked), start=1))
