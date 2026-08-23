"""Record sanitized ESPN fixtures so the test suite never needs live cookies.

    uv run python -m scripts.record_fixtures [--players 60]

Sanitizing is the point, not a nicety: these files are committed. We keep only the fields the
parsers actually read (scoring coefficients, roster slots, player identity) and drop everything
that identifies the account or the league — cookies, member/owner ids, team names, the league
id, finance and draft blocks. A final scan refuses to write a file that still contains a
credential or the real league id.
"""

import argparse
import json
from pathlib import Path
from typing import Any

from app.espn.client import ESPNClient, ESPNCredentials

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
SETTINGS_FIXTURE = FIXTURE_DIR / "espn_msettings.json"
PLAYERS_FIXTURE = FIXTURE_DIR / "espn_player_pool.json"

PLACEHOLDER_LEAGUE_NAME = "Sanitized Test League"

# Only these survive from each player object; everything else (ownership, stat splits,
# outlooks, draft ranks) is either bulky or beyond what task 2 parses.
_PLAYER_KEEP = (
    "id",
    "fullName",
    "firstName",
    "lastName",
    "defaultPositionId",
    "eligibleSlots",
    "proTeamId",
    "injuryStatus",
    "injured",
    "active",
    "droppable",
    "jersey",
)
_ENTRY_KEEP = ("id", "status", "onTeamId")


def sanitize_settings(payload: dict[str, Any]) -> dict[str, Any]:
    """Keep the scoring formula and roster shape; drop league identity and everything else."""
    settings = payload["settings"]
    return {
        "settings": {
            "name": PLACEHOLDER_LEAGUE_NAME,
            "size": settings.get("size"),
            "rosterSettings": settings.get("rosterSettings", {}),
            "scoringSettings": settings.get("scoringSettings", {}),
        }
    }


def sanitize_players(entries: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """Keep the first `limit` entries, reduced to the fields the parser reads."""
    out: list[dict[str, Any]] = []
    for entry in entries[:limit]:
        player = entry.get("player") or {}
        out.append(
            {
                **{key: entry[key] for key in _ENTRY_KEEP if key in entry},
                "player": {key: player[key] for key in _PLAYER_KEEP if key in player},
            }
        )
    return out


def assert_clean(text: str, credentials: ESPNCredentials) -> None:
    """Refuse to write anything still carrying a secret or the real league id."""
    secrets = {
        "espn_s2": credentials.espn_s2,
        "SWID": credentials.swid,
        "SWID (bare)": credentials.swid.strip("{}"),
        "league id": str(credentials.league_id),
    }
    leaked = [label for label, value in secrets.items() if value and value in text]
    if leaked:
        raise SystemExit(f"Refusing to write fixture: it still contains {', '.join(leaked)}")

    for banned in ("members", "owners", "financeSettings", "draftDetail", "primaryOwner"):
        if f'"{banned}"' in text:
            raise SystemExit(f"Refusing to write fixture: it still contains a `{banned}` block")


def write_fixture(path: Path, data: Any, credentials: ESPNCredentials) -> None:
    text = json.dumps(data, indent=2, sort_keys=True) + "\n"
    assert_clean(text, credentials)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    print(f"wrote {path.relative_to(Path.cwd())} ({len(text):,} bytes)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--players", type=int, default=60, help="how many players to keep (default: 60)"
    )
    args = parser.parse_args()

    client = ESPNClient.from_settings()
    credentials = client.credentials

    write_fixture(SETTINGS_FIXTURE, sanitize_settings(client.fetch_settings_view()), credentials)

    entries = client.fetch_player_pool_pages(page_size=500, max_players=args.players + 500)
    write_fixture(PLAYERS_FIXTURE, sanitize_players(entries, args.players), credentials)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
