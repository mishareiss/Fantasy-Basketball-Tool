"""ESPN's `ownership` block: redraft ADP, auction value, and roster share.

These ride along in the same `kona_player_info` payload as player identity and projections, so
they cost nothing extra to collect.

Two things to keep in mind about what these numbers mean:

* They are **redraft** figures from ESPN's public game, not dynasty, and not tuned to our
  custom scoring. We store them raw; the dynasty adjustment happens downstream, where it can
  be tuned and inspected.
* ESPN floors ADP at roughly the last pick of a standard draft (undrafted players all come
  back at ~140), so a high ADP means "nobody drafts them", not "drafted 140th". Storing it
  as-is keeps that visible instead of inventing a null.
"""

from dataclasses import dataclass
from typing import Any

from app.espn.players import player_object


@dataclass(frozen=True)
class OwnershipRecord:
    """One player's ESPN redraft market data, ready to upsert into `adp_entry`."""

    espn_player_id: int
    adp: float | None = None
    auction_value: float | None = None
    percent_owned: float | None = None


def _as_float(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def parse_ownership_entry(entry: dict[str, Any]) -> OwnershipRecord | None:
    """Parse one `players[]` entry's ownership block, or None if it has none.

    A player with an ownership block but no usable numbers still returns a record: "ESPN
    tracks them and has no market for them" is different from "we never looked".
    """
    player = player_object(entry)
    if player is None:
        return None

    player_id = player.get("id", entry.get("id"))
    ownership = player.get("ownership")
    if player_id is None or not isinstance(ownership, dict):
        return None

    return OwnershipRecord(
        espn_player_id=int(player_id),
        adp=_as_float(ownership.get("averageDraftPosition")),
        auction_value=_as_float(ownership.get("auctionValueAverage")),
        percent_owned=_as_float(ownership.get("percentOwned")),
    )


def parse_ownership(entries: list[dict[str, Any]]) -> list[OwnershipRecord]:
    """Parse a whole pool, de-duplicating by ESPN id the way `parse_player_pool` does."""
    records: dict[int, OwnershipRecord] = {}
    for entry in entries:
        record = parse_ownership_entry(entry)
        if record is not None:
            records[record.espn_player_id] = record
    return list(records.values())
