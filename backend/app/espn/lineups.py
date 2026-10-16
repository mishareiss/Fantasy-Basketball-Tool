"""Pull lineups from ESPN, day by day: the "sync lineup from ESPN" button.

Separate from the league sync on purpose. A lineup is something this site lets you edit, and
a change made in the ESPN app must not silently overwrite it, so `sync_league` never writes a
lineup. This pull does, and only when asked: it REPLACES every stored row for the days it
covers, manual edits included, with what ESPN has for those days.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.db.models import LINEUP_SOURCE_ESPN, LineupAssignment
from app.espn.client import ESPNClient
from app.espn.league import parse_rosters
from app.espn.sync import _known_player_ids
from app.team.calendar import clamp_period, default_lineup_range


@dataclass(frozen=True)
class LineupPull:
    """What one pull stored: which team, which days, how many rows."""

    team_id: int
    first_sp: int
    last_sp: int
    days: int
    assignments: int


def pull_lineups(
    db: Session,
    client: ESPNClient,
    team_id: int,
    first_sp: int | None = None,
    last_sp: int | None = None,
    *,
    now: datetime | None = None,
) -> LineupPull:
    """Fetch `team_id`'s ESPN lineup for each day in `[first_sp, last_sp]` and store it. Commits.

    Defaults: today's period (Pacific) through the end of the NEXT matchup. Both ends are
    clamped to the fantasy season. Every day is fetched before anything is written, so a
    failure partway leaves the stored lineups as they were.
    """
    season = client.season
    default_first, default_last = default_lineup_range(season, now or datetime.now(UTC))
    first = clamp_period(season, first_sp if first_sp is not None else default_first)
    last = clamp_period(season, last_sp if last_sp is not None else default_last)
    if last < first:
        raise ValueError(f"last_sp {last} is before first_sp {first}")

    by_day: dict[int, dict[int, int]] = {}
    for sp in range(first, last + 1):
        payload = client.fetch_rosters(sp, team_id=team_id)
        by_day[sp] = {
            record.player_id: record.lineup_slot_id
            for record in parse_rosters(payload)
            if record.team_id == team_id
        }

    known = _known_player_ids(db, sorted({pid for day in by_day.values() for pid in day}))
    db.execute(
        delete(LineupAssignment).where(
            LineupAssignment.season == season,
            LineupAssignment.team_id == team_id,
            LineupAssignment.scoring_period_id.between(first, last),
        )
    )
    stamp = datetime.now(UTC)
    written = 0
    for sp, slots in by_day.items():
        for player_id, slot_id in slots.items():
            if player_id not in known:
                continue
            db.add(
                LineupAssignment(
                    season=season,
                    team_id=team_id,
                    scoring_period_id=sp,
                    player_id=player_id,
                    lineup_slot_id=slot_id,
                    source=LINEUP_SOURCE_ESPN,
                    updated_at=stamp,
                )
            )
            written += 1
    db.commit()
    return LineupPull(
        team_id=team_id, first_sp=first, last_sp=last, days=len(by_day), assignments=written
    )
