"""When a dropped player clears waivers.

ESPN publishes no "clears at" time, so it is computed from our league's rule. A player held
MORE than `waiverHours` and then dropped goes on waivers until the first midnight (Pacific)
after drop + waiverHours. One held for `waiverHours` or less goes straight to free agency.
Checked against real data: a drop at 2026-10-13T22:54:39Z had cleared by the 2026-10-15
nightly run, and the rule says 2026-10-15 00:00 PT = 07:00Z.

Whether a player is on waivers NOW is not computed at all: ESPN's own status
(`Player.roster_status == 'WAIVERS'`) is authoritative. Only the clear time is ours.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import EspnTransaction, Player, RosterEntry
from app.espn.league import WAIVERS_STATUS
from app.scoring.settings import AcquisitionSettings
from app.team.store import league_settings_row

EXECUTED = "EXECUTED"
ADD_ITEM = "ADD"
DROP_ITEM = "DROP"


def waiver_clears_at(
    dropped_at: datetime,
    acquired_at: datetime | None,
    waiver_hours: int,
    tz: tzinfo | str,
) -> datetime | None:
    """When a player dropped at `dropped_at` clears waivers, in UTC. None = straight to FA.

    `acquired_at` None means "held long enough": with no record of when he arrived, he was
    there before anything we can see (the draft, usually). Held exactly `waiver_hours` counts
    as not long enough. Pure, so the waiver plan can ask about a drop that hasn't happened.
    """
    if dropped_at.tzinfo is None or (acquired_at is not None and acquired_at.tzinfo is None):
        raise ValueError("waiver_clears_at needs timezone-aware datetimes")
    hours = timedelta(hours=waiver_hours)
    if acquired_at is not None and dropped_at - acquired_at <= hours:
        return None

    zone = ZoneInfo(tz) if isinstance(tz, str) else tz
    local = (dropped_at + hours).astimezone(zone)
    # The first local midnight strictly after: tomorrow's date at 00:00, even when `local` is
    # itself exactly midnight. Midnight exists on both DST days in US zones (they switch at 2am).
    midnight = datetime.combine(local.date() + timedelta(days=1), datetime.min.time(), zone)
    return midnight.astimezone(ZoneInfo("UTC"))


@dataclass(frozen=True)
class WaiverStatus:
    on_waivers: bool
    clears_at: datetime | None = None
    # False when he is on waivers but we can't say until when: no drop on record, or no
    # waiver rule stored. True otherwise, including "not on waivers, nothing to clear".
    clears_at_known: bool = True


def acquisition_settings(db: Session, season: int) -> AcquisitionSettings:
    """The stored add/waiver rules for `season` (our configured league's row, if several)."""
    row = league_settings_row(db, season)
    return AcquisitionSettings.from_raw(row.acquisition_settings if row else None)


def waiver_statuses(db: Session, season: int, player_ids: Iterable[int]) -> dict[int, WaiverStatus]:
    """`waiver_status` for many players at once: one pass over the season's transactions."""
    wanted = set(player_ids)
    if not wanted:
        return {}
    players = {
        p.espn_player_id: p
        for p in db.scalars(select(Player).where(Player.espn_player_id.in_(wanted)))
    }
    on_waivers = {pid for pid, p in players.items() if p.roster_status == WAIVERS_STATUS}
    out = {pid: WaiverStatus(on_waivers=False) for pid in wanted}
    if not on_waivers:
        return out

    # {player: [(effective time, item type)]}, executed moves only, oldest first.
    moves: dict[int, list[tuple[datetime, str]]] = {pid: [] for pid in on_waivers}
    for tx in db.scalars(
        select(EspnTransaction).where(
            EspnTransaction.season == season, EspnTransaction.status == EXECUTED
        )
    ):
        when = tx.effective_at
        if when is None:
            continue
        for item in tx.items or []:
            pid = item.get("playerId")
            if pid in moves and item.get("type") in (ADD_ITEM, DROP_ITEM):
                moves[pid].append((when, item["type"]))

    rule = acquisition_settings(db, season)
    tz = get_settings().waiver_clear_tz
    roster_dates = {
        entry.player_id: entry.acquisition_date
        for entry in db.scalars(
            select(RosterEntry).where(
                RosterEntry.season == season, RosterEntry.player_id.in_(on_waivers)
            )
        )
    }

    for pid in on_waivers:
        history = sorted(moves[pid])
        drops = [when for when, kind in history if kind == DROP_ITEM]
        if not drops or rule.waiver_hours is None:
            out[pid] = WaiverStatus(on_waivers=True, clears_at=None, clears_at_known=False)
            continue
        dropped_at = drops[-1]
        adds = [when for when, kind in history if kind == ADD_ITEM and when < dropped_at]
        acquired_at = adds[-1] if adds else None
        if acquired_at is None:
            roster_date = roster_dates.get(pid)
            if roster_date is not None and roster_date < dropped_at:
                acquired_at = roster_date
        clears_at = waiver_clears_at(dropped_at, acquired_at, rule.waiver_hours, tz)
        # None here means our rule says he went straight to free agency while ESPN says he is
        # on waivers. ESPN wins on the status; we just can't say when he clears.
        out[pid] = WaiverStatus(
            on_waivers=True, clears_at=clears_at, clears_at_known=clears_at is not None
        )
    return out


def waiver_status(db: Session, season: int, player_id: int) -> WaiverStatus:
    """Is he on waivers (ESPN's word), and when does he clear (our rule)?

    The clear time comes from his most recent executed DROP, with "acquired" being his most
    recent executed ADD before it, else his roster entry's acquisition date, else unknown,
    which counts as held long enough. On waivers with no drop on record: `clears_at=None`,
    `clears_at_known=False`.
    """
    return waiver_statuses(db, season, [player_id])[player_id]
