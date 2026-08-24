"""Turn ESPN's `kona_player_info` entries into canonical player records."""

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from espn_api.basketball.constant import POSITION_MAP, PRO_TEAM_MAP

# Real basketball positions, in the order we want them listed. Everything else ESPN reports as
# an "eligible slot" (G, F, G/F, UT, BE, IR) is a lineup construct, not a position.
ATOMIC_POSITIONS: tuple[str, ...] = ("PG", "SG", "SF", "PF", "C")

# Keys ESPN has used for a birthdate in other endpoints. The fantasy API returns none of them
# today, which is why ages come from nba.com instead (`app.ages`) — and why the sync does not
# list `birthdate`/`age` among the columns it owns: writing this parser's None over a real
# birthdate would wipe the ages the board depends on. Parsed here only so we'd notice if ESPN
# ever started publishing them.
_BIRTHDATE_KEYS = ("dateOfBirth", "birthDate", "birthdate")


@dataclass(frozen=True)
class PlayerRecord:
    """A parsed player, ready to upsert into the `player` table."""

    espn_player_id: int
    full_name: str
    first_name: str | None = None
    last_name: str | None = None
    nba_team: str | None = None
    pro_team_id: int | None = None
    primary_position: str | None = None
    positions: list[str] = field(default_factory=list)
    roster_status: str | None = None
    espn_fantasy_team_id: int | None = None
    injury_status: str | None = None
    injured: bool = False
    birthdate: date | None = None
    age: int | None = None


def _positions(eligible_slots: list[int] | None) -> list[str]:
    """Atomic positions from ESPN's eligible-slot ids, in PG->C order."""
    names = {POSITION_MAP.get(slot_id, "") for slot_id in (eligible_slots or [])}
    return [position for position in ATOMIC_POSITIONS if position in names]


def _primary_position(default_position_id: int | None) -> str | None:
    """ESPN's `defaultPositionId` is 1-based over the same map espn-api indexes from zero."""
    if default_position_id is None:
        return None
    return POSITION_MAP.get(default_position_id - 1) or None


def _parse_birthdate(player: dict[str, Any]) -> date | None:
    for key in _BIRTHDATE_KEYS:
        raw = player.get(key)
        if not raw:
            continue
        if isinstance(raw, int):  # epoch milliseconds, ESPN's usual timestamp shape
            return datetime.fromtimestamp(raw / 1000.0).date()
        if isinstance(raw, str):
            try:
                return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
            except ValueError:
                continue
    return None


def _age_from(birthdate: date | None, today: date | None = None) -> int | None:
    if birthdate is None:
        return None
    today = today or date.today()
    had_birthday = (today.month, today.day) >= (birthdate.month, birthdate.day)
    return today.year - birthdate.year - (0 if had_birthday else 1)


def player_object(entry: dict[str, Any]) -> dict[str, Any] | None:
    """The `player` object inside one `players[]` entry, whichever shape ESPN used.

    Shared with the projection and ownership parsers, which read different blocks of the same
    object out of the same payload.
    """
    player = entry.get("player") or entry.get("playerPoolEntry", {}).get("player")
    return player if isinstance(player, dict) else None


def parse_player_entry(entry: dict[str, Any]) -> PlayerRecord | None:
    """Parse one `players[]` entry. Returns None for entries with no usable player object."""
    player = player_object(entry)
    if player is None:
        return None

    player_id = player.get("id", entry.get("id"))
    full_name = player.get("fullName")
    if player_id is None or not full_name:
        return None

    pro_team_id = player.get("proTeamId")
    birthdate = _parse_birthdate(player)
    on_team_id = entry.get("onTeamId")

    return PlayerRecord(
        espn_player_id=int(player_id),
        full_name=full_name,
        first_name=player.get("firstName"),
        last_name=player.get("lastName"),
        nba_team=PRO_TEAM_MAP.get(pro_team_id) if pro_team_id is not None else None,
        pro_team_id=pro_team_id,
        primary_position=_primary_position(player.get("defaultPositionId")),
        positions=_positions(player.get("eligibleSlots")),
        roster_status=entry.get("status"),
        # ESPN uses team 0 for "nobody"; store None so "rostered" is a simple NOT NULL check.
        espn_fantasy_team_id=on_team_id or None,
        injury_status=player.get("injuryStatus"),
        injured=bool(player.get("injured", False)),
        birthdate=birthdate,
        age=_age_from(birthdate),
    )


def parse_player_pool(entries: list[dict[str, Any]]) -> list[PlayerRecord]:
    """Parse a whole pool, de-duplicating by ESPN id (paged responses can overlap).

    Later entries win, so a player who shows up on a roster page after a free-agent page ends
    up with the roster status.
    """
    records: dict[int, PlayerRecord] = {}
    for entry in entries:
        record = parse_player_entry(entry)
        if record is not None:
            records[record.espn_player_id] = record
    return list(records.values())
