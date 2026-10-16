"""Auto-detect: mark a planned move done when ESPN's transaction log shows it was made.

Runs at the end of every league sync, after transactions land. Idempotent: a transaction
already credited to a move is never credited again, and only `planned` moves are looked at,
so a move marked done or skipped by hand is never touched. Only our own team's transactions
are read.

A match is an EXECUTED free-agent or waiver transaction of ours that ADDs one of the move's
candidates, effective no earlier than a day before the move was created (a move planned just
after making it still counts). There is no upper bound and no date match against the move's
day, so an after-tip-off move made in the evening of its day (already the next day in UTC and
in ESPN's Eastern filing) matches like any other. One that also DROPs the planned drop wins;
otherwise the earliest. The move becomes `done`, resolved to the candidate actually added,
and every move chained to it follows automatically, because chains resolve through
`resolve_drop`.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import PLAN_DONE, PLAN_PLANNED, EspnTransaction, PlanMove
from app.team.plan import resolve_drop
from app.team.plan_store import engine_move, load_moves
from app.team.waivers import ADD_ITEM, DROP_ITEM, EXECUTED

PENDING = "PENDING"
PICKUP_TRANSACTION_TYPES = ("FREEAGENT", "WAIVER")
# How long before a move was created a transaction can be and still be that move.
DETECT_WINDOW = timedelta(days=1)


@dataclass
class DetectResult:
    detected: int = 0
    # Of those, how many dropped someone other than the planned drop.
    different_drop: int = 0
    claims_pending: int = 0


def _players(tx: EspnTransaction, item_type: str, team_id: int) -> list[int]:
    """The players `tx` moves onto (ADD) or off (DROP) `team_id`, in item order."""
    side = "toTeamId" if item_type == ADD_ITEM else "fromTeamId"
    return [
        item["playerId"]
        for item in tx.items or []
        if item.get("type") == item_type
        and item.get("playerId") is not None
        and item.get(side) in (team_id, None)
    ]


def _team_transactions(
    db: Session, season: int, team_id: int, status: str
) -> list[EspnTransaction]:
    return list(
        db.scalars(
            select(EspnTransaction)
            .where(
                EspnTransaction.season == season,
                EspnTransaction.team_id == team_id,
                EspnTransaction.status == status,
                EspnTransaction.type.in_(PICKUP_TRANSACTION_TYPES),
            )
            .order_by(EspnTransaction.id)
        )
    )


def _in_window(tx: EspnTransaction, move: PlanMove) -> bool:
    when = tx.effective_at
    return when is not None and when >= move.created_at - DETECT_WINDOW


def detect_plan_moves(db: Session, season: int, team_id: int) -> DetectResult:
    """Mark every planned move of `team_id` that ESPN shows as made. Flushes, doesn't commit."""
    result = DetectResult()
    rows = load_moves(db, season, team_id)
    if not any(row.status == PLAN_PLANNED for row in rows):
        return result

    executed = _team_transactions(db, season, team_id, EXECUTED)
    claimed = {row.espn_transaction_id for row in rows if row.espn_transaction_id}
    engine = {row.id: engine_move(row) for row in rows}

    for row in rows:
        if row.status != PLAN_PLANNED:
            continue
        candidates = [c.player_id for c in sorted(row.candidates, key=lambda c: c.rank)]
        planned_drop = resolve_drop(engine[row.id], engine).player_id

        matches = []
        for tx in executed:
            if tx.id in claimed or not _in_window(tx, row):
                continue
            added = _players(tx, ADD_ITEM, team_id)
            hit = next((pid for pid in candidates if pid in added), None)
            if hit is not None:
                matches.append((tx, hit))
        if not matches:
            continue

        # A transaction that also drops the planned drop first, then the earliest.
        tx, added = min(
            matches,
            key=lambda match: (
                planned_drop not in _players(match[0], DROP_ITEM, team_id),
                match[0].effective_at,
                match[0].id,
            ),
        )
        dropped = _players(tx, DROP_ITEM, team_id)
        row.status = PLAN_DONE
        row.resolved_player_id = added
        row.espn_transaction_id = tx.id
        mismatch = planned_drop not in dropped if planned_drop is not None else bool(dropped)
        row.drop_mismatch = mismatch
        row.detected_drop_player_id = (dropped[0] if dropped else None) if mismatch else None
        claimed.add(tx.id)
        engine[row.id] = engine_move(row)
        result.detected += 1
        result.different_drop += mismatch

    db.flush()
    result.claims_pending = len(pending_claims(db, season, team_id, rows))
    return result


def pending_claims(db: Session, season: int, team_id: int, rows: Iterable[PlanMove]) -> set[int]:
    """Ids of planned moves with a PENDING waiver claim of ours on one of their candidates.
    Read-time only: a pending claim changes nothing about the move. One query."""
    planned = [row for row in rows if row.status == PLAN_PLANNED]
    if not planned:
        return set()
    pending = _team_transactions(db, season, team_id, PENDING)
    out = set()
    for row in planned:
        candidates = {c.player_id for c in row.candidates}
        if any(
            _in_window(tx, row) and candidates & set(_players(tx, ADD_ITEM, team_id))
            for tx in pending
        ):
            out.add(row.id)
    return out
