"""nba.com as our birthdate source, via `nba_api`.

Two very different halves, deliberately separated:

* **The roster** comes from `nba_api.stats.static.players`, a list bundled inside the package.
  No network, no rate limit, no failure mode — so building the name index and matching it to
  our players is free and works on a plane.
* **The birthdates** come from the `CommonPlayerInfo` endpoint, which is one HTTP call to
  nba.com *per player*. It is undocumented, unversioned, occasionally blocks datacentre IPs,
  and has rate limits nobody publishes. Everything here treats it accordingly: always a
  timeout, a couple of retries with backoff, and a delay between calls left to the caller.

The one caveat worth knowing: the static roster ships with the installed `nba_api` version, so
the newest draft class can be missing until the package is updated. Those players match
nothing, land on the unresolved worklist, and get a hand-made alias — which is exactly the
escape hatch that list exists for.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime

# What we call ourselves in `PlayerAlias.source`.
NBA_SOURCE = "nba_api"

# nba.com is somebody else's server and we are a hobby tool: never hammer it.
DEFAULT_TIMEOUT = 30.0
DEFAULT_DELAY = 0.7
DEFAULT_RETRIES = 2
DEFAULT_BACKOFF = 2.0

# The column CommonPlayerInfo returns the birthdate in, as an ISO timestamp.
BIRTHDATE_COLUMN = "BIRTHDATE"


class NbaApiError(RuntimeError):
    """nba.com didn't answer, or answered with something we can't use."""


@dataclass(frozen=True)
class NbaPlayer:
    """One player from the offline static roster."""

    nba_id: int
    full_name: str
    first_name: str | None = None
    last_name: str | None = None
    is_active: bool = False

    @property
    def source_id(self) -> str:
        return str(self.nba_id)


def nba_api_available() -> bool:
    """Whether `nba_api` can be imported — lets the live tests skip instead of erroring."""
    try:
        import nba_api  # noqa: F401
    except ImportError:
        return False
    return True


def static_players() -> list[NbaPlayer]:
    """Every player nba.com knows about, from the bundled list. Offline and instant."""
    from nba_api.stats.static import players as static

    return [
        NbaPlayer(
            nba_id=int(entry["id"]),
            full_name=entry["full_name"],
            first_name=entry.get("first_name"),
            last_name=entry.get("last_name"),
            is_active=bool(entry.get("is_active")),
        )
        for entry in static.get_players()
        if entry.get("id") is not None and entry.get("full_name")
    ]


def parse_birthdate(raw: object) -> date | None:
    """Parse the BIRTHDATE column: "1997-07-18T00:00:00" -> date(1997, 7, 18)."""
    if isinstance(raw, str) and raw.strip():
        try:
            return datetime.fromisoformat(raw.strip().replace("Z", "+00:00")).date()
        except ValueError:
            return None
    return None


def birthdate_from_payload(payload: dict) -> date | None:
    """Pull the birthdate out of a normalized CommonPlayerInfo response.

    Takes the already-normalized dict rather than the endpoint object so the same function
    parses a live response and a recorded fixture.
    """
    rows = (payload or {}).get("CommonPlayerInfo") or []
    return parse_birthdate(rows[0].get(BIRTHDATE_COLUMN)) if rows else None


def fetch_common_player_info(
    nba_id: int,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
    backoff: float = DEFAULT_BACKOFF,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    """One CommonPlayerInfo call, with a timeout and exponential backoff on failure.

    Raises `NbaApiError` after the last attempt rather than returning something empty, so a
    caller can tell "nba.com is refusing us" (stop) from "this player has no birthdate on
    file" (carry on).
    """
    from nba_api.stats.endpoints import commonplayerinfo

    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return commonplayerinfo.CommonPlayerInfo(
                player_id=nba_id, timeout=timeout
            ).get_normalized_dict()
        except Exception as exc:  # noqa: BLE001  # the library raises requests/JSON errors alike
            last_error = exc
            if attempt < retries:
                sleep(backoff * (2**attempt))

    raise NbaApiError(
        f"CommonPlayerInfo({nba_id}) failed after {retries + 1} attempts: {last_error}"
    )


def fetch_birthdate(
    nba_id: int,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
    backoff: float = DEFAULT_BACKOFF,
    sleep: Callable[[float], None] = time.sleep,
) -> date | None:
    """The birthdate nba.com has for one player, or None if it has none on file."""
    payload = fetch_common_player_info(
        nba_id, timeout=timeout, retries=retries, backoff=backoff, sleep=sleep
    )
    return birthdate_from_payload(payload)
