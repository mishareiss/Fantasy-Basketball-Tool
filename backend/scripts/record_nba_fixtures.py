"""Record sanitized nba.com fixtures so the age tests never touch the network.

    uv run python -m scripts.record_nba_fixtures [--extra "Gary Payton II"]

Mirrors `record_fixtures.py`, but the thing being snapshotted is different in kind: nba.com
publishes no private data about us, so there is nothing to sanitize for secrecy. What we do
instead is *narrow* — keep only the handful of fields the parsers read, and only for the
players in the ESPN fixture pool. A committed copy of nba.com's 5,000-name roster would be a
megabyte of noise that goes stale and tells the tests nothing.

Two files come out:

* `nba_static_players.json` — the offline roster entries for our fixture players, exactly the
  shape `nba_api.stats.static.players.get_players()` returns.
* `nba_common_player_info.json` — the birthdate half of one `CommonPlayerInfo` response per
  player, keyed by nba id.

The second one is the only part that hits the network: one paced call per player.
"""

import argparse
import json
import time
from pathlib import Path
from typing import Any

from app.ages.nba_source import (
    BIRTHDATE_COLUMN,
    DEFAULT_DELAY,
    NbaApiError,
    fetch_common_player_info,
    static_players,
)
from app.matching import normalize_name

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
ESPN_POOL_FIXTURE = FIXTURE_DIR / "espn_player_pool.json"
STATIC_FIXTURE = FIXTURE_DIR / "nba_static_players.json"
INFO_FIXTURE = FIXTURE_DIR / "nba_common_player_info.json"

# The only CommonPlayerInfo columns we keep. BIRTHDATE is the one we read; the other two are
# there so a human reading the fixture can tell whose birthdate it is.
_INFO_KEEP = ("PERSON_ID", "DISPLAY_FIRST_LAST", BIRTHDATE_COLUMN)


def espn_fixture_names() -> list[str]:
    """The player names in the recorded ESPN pool — what the age fixtures have to cover."""
    entries = json.loads(ESPN_POOL_FIXTURE.read_text())
    return [entry["player"]["fullName"] for entry in entries if entry.get("player")]


def select_roster(wanted: list[str], extras: list[str]) -> list[dict[str, Any]]:
    """Static-roster entries whose normalized name matches one we want.

    Matching on the normalized name rather than the exact one is the point: nba.com writes
    "Nikola Jokić" where ESPN writes "Nikola Jokic", and keeping that difference in the
    fixture is what gives the accent-folding tests something real to chew on.
    """
    keys = {normalize_name(name) for name in wanted + extras}
    return [
        {
            "id": player.nba_id,
            "full_name": player.full_name,
            "first_name": player.first_name,
            "last_name": player.last_name,
            "is_active": player.is_active,
        }
        for player in static_players()
        if normalize_name(player.full_name) in keys
    ]


def fetch_infos(roster: list[dict[str, Any]], delay: float) -> dict[str, Any]:
    """One paced CommonPlayerInfo call per player, reduced to the columns we read."""
    infos: dict[str, Any] = {}
    for index, entry in enumerate(roster):
        if index:
            time.sleep(delay)
        try:
            payload = fetch_common_player_info(int(entry["id"]))
        except NbaApiError as exc:
            print(f"  ! {entry['full_name']}: {exc}")
            continue

        rows = payload.get("CommonPlayerInfo") or []
        if not rows:
            print(f"  ! {entry['full_name']}: no CommonPlayerInfo row")
            continue

        infos[str(entry["id"])] = {
            "CommonPlayerInfo": [{key: rows[0].get(key) for key in _INFO_KEEP}]
        }
        print(f"  {entry['full_name']}: {rows[0].get(BIRTHDATE_COLUMN)}")
    return infos


def write_fixture(path: Path, data: Any) -> None:
    text = json.dumps(data, indent=2, sort_keys=True) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    print(f"wrote {path.name} ({len(text):,} bytes)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--extra",
        action="append",
        default=[],
        help="an extra player name to include (repeatable) — for covering a tricky match",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY,
        help=f"seconds between nba.com calls (default: {DEFAULT_DELAY})",
    )
    args = parser.parse_args()

    roster = select_roster(espn_fixture_names(), args.extra)
    print(f"matched {len(roster)} nba.com players to the ESPN fixture pool")
    write_fixture(STATIC_FIXTURE, roster)
    write_fixture(INFO_FIXTURE, fetch_infos(roster, args.delay))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
