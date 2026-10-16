"""The league's waiver-clear rule, on its own. Pure: no database, no ESPN, no clock.

Split out of `app.team.waivers` so the waiver plan's pure engine (`app.team.plan`) can ask
when a player it plans to drop would clear, without importing the database layer.
`app.team.waivers` re-exports it, so existing callers are unchanged.
"""

from datetime import datetime, timedelta, tzinfo
from zoneinfo import ZoneInfo


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
