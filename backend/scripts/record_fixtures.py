"""Record sanitized ESPN fixtures so the test suite never needs live cookies.

    uv run python -m scripts.record_fixtures [--players 60] [--only league|team]

`--only league` re-records the scoring settings and the player pool (what this script always
did); `--only team` records only the in-season files (schedule, teams, rosters, transactions),
so recording those doesn't move the projections every older test is pinned to.

Sanitizing is the point, not a nicety: these files are committed. We keep only the fields the
parsers actually read (scoring coefficients, roster slots, player identity) and drop everything
that identifies the account or the league — cookies, member/owner ids, team names, the league
id, finance and draft blocks. A final scan refuses to write a file that still contains a
credential or the real league id.
"""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.espn.client import ESPNClient, ESPNCredentials, _normalise_swid
from app.espn.league import parse_rosters, parse_teams, resolve_my_team
from app.espn.sync import _roster_period

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
SETTINGS_FIXTURE = FIXTURE_DIR / "espn_msettings.json"
PLAYERS_FIXTURE = FIXTURE_DIR / "espn_player_pool.json"

SCHEDULE_FIXTURE = FIXTURE_DIR / "espn_pro_schedule.json"
TEAMS_FIXTURE = FIXTURE_DIR / "espn_mteam.json"
ROSTERS_FIXTURE = FIXTURE_DIR / "espn_mroster.json"
TRANSACTIONS_FIXTURE = FIXTURE_DIR / "espn_transactions.json"

PLACEHOLDER_LEAGUE_NAME = "Sanitized Test League"

# The SWID every recorded owner list is rewritten to for OUR team. Keep in step with
# `tests.conftest.FAKE_SWID`, which the my-team tests resolve against.
FAKE_MY_SWID = "{11111111-2222-4333-8444-555555555555}"

# The NBA schedule fixture: this many pro teams, these scoring periods.
SCHEDULE_TEAMS = 6
SCHEDULE_PERIODS = ("1", "2", "3")

# The future day the per-day lineup fixture is recorded for (`espn_mroster_day_<N>.json`).
LINEUP_FIXTURE_DAY = 8

_ROSTER_PLAYER_KEEP = (
    "id",
    "fullName",
    "firstName",
    "lastName",
    "defaultPositionId",
    "eligibleSlots",
    "proTeamId",
    "injuryStatus",
    "injured",
)
_ROSTER_ENTRY_KEEP = (
    "playerId",
    "lineupSlotId",
    "acquisitionDate",
    "acquisitionType",
    "injuryStatus",
    "status",
)
_TRANSACTION_KEEP = (
    "id",
    "type",
    "status",
    "teamId",
    "scoringPeriodId",
    "proposedDate",
    "processDate",
)
_TRANSACTION_ITEM_KEEP = (
    "type",
    "playerId",
    "fromTeamId",
    "toTeamId",
    "fromLineupSlotId",
    "toLineupSlotId",
)

# Only these survive from each player object. `stats` (the projected and actual splits) and
# `ownership` (ADP, auction value, roster share) are in because the projection and ADP parsers
# read them; everything else ESPN sends — outlooks, news, draft ranks — is dropped.
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
    "stats",
    "ownership",
)
_ENTRY_KEEP = ("id", "status", "onTeamId")


def sanitize_schedule_settings(schedule: dict[str, Any]) -> dict[str, Any]:
    """The matchup-period map and playoff shape; never the division names (league identity)."""
    keep = (
        "matchupPeriodCount",
        "matchupPeriodLength",
        "matchupPeriods",
        "playoffMatchupPeriodLengthByRound",
        "playoffTeamCount",
    )
    return {key: schedule[key] for key in keep if key in schedule}


def sanitize_settings(payload: dict[str, Any]) -> dict[str, Any]:
    """Keep the scoring formula, roster shape, add/waiver rules and matchup map; drop the rest."""
    settings = payload["settings"]
    return {
        "settings": {
            "name": PLACEHOLDER_LEAGUE_NAME,
            "size": settings.get("size"),
            "acquisitionSettings": settings.get("acquisitionSettings", {}),
            "rosterSettings": settings.get("rosterSettings", {}),
            "scheduleSettings": sanitize_schedule_settings(settings.get("scheduleSettings", {})),
            "scoringSettings": settings.get("scoringSettings", {}),
        }
    }


def sanitize_schedule(payload: dict[str, Any]) -> dict[str, Any]:
    """A few pro teams over a few days, with the placeholder team 0 kept to prove it's skipped.

    The teams are the ones playing on day 1 first (so some games appear under both of their
    teams, which is what the de-duplication needs), then day 2's, up to `SCHEDULE_TEAMS`.
    ESPN had no TBD game when this was written, so the last kept game is marked TBD by hand.
    """
    teams = {team["id"]: team for team in payload["settings"]["proTeams"]}
    chosen: list[int] = []
    for period in SCHEDULE_PERIODS:
        for team_id, team in sorted(teams.items()):
            plays = period in (team.get("proGamesByScoringPeriod") or {})
            if team_id and plays and team_id not in chosen and len(chosen) < SCHEDULE_TEAMS:
                chosen.append(team_id)

    out = []
    for team_id in [0, *sorted(chosen)]:
        team = teams.get(team_id)
        if team is None:
            continue
        games = team.get("proGamesByScoringPeriod") or {}
        out.append(
            {
                "id": team_id,
                "abbrev": team.get("abbrev"),
                "proGamesByScoringPeriod": {
                    period: games[period] for period in SCHEDULE_PERIODS if period in games
                },
            }
        )

    if not any(
        game.get("startTimeTBD")
        for team in out
        for games in team["proGamesByScoringPeriod"].values()
        for game in games
    ):
        last = out[-1]["proGamesByScoringPeriod"]
        tbd_id = last[max(last, key=int)][-1]["id"]
        for team in out:
            for games in team["proGamesByScoringPeriod"].values():
                for game in games:
                    if game["id"] == tbd_id:
                        game["startTimeTBD"] = True
    return {"settings": {"proTeams": out}}


def _fake_swid(index: int) -> str:
    return "{00000000-0000-4000-8000-" + f"{index:012d}" + "}"


def sanitize_teams(payload: dict[str, Any], my_swid: str) -> dict[str, Any]:
    """Teams with names, owners and everything else identifying replaced.

    Every owner SWID becomes a fake GUID; ours becomes `FAKE_MY_SWID`, so the fixture resolves
    to the same team the live league does. Names become "Team N".
    """
    mine = _normalise_swid(my_swid).upper()
    fakes: dict[str, str] = {}
    teams = []
    for team in payload["teams"]:
        owners = []
        for owner in team.get("owners") or []:
            key = _normalise_swid(owner).upper()
            if key == mine:
                owners.append(FAKE_MY_SWID)
            else:
                owners.append(fakes.setdefault(key, _fake_swid(len(fakes) + 1)))
        counter = team.get("transactionCounter") or {}
        teams.append(
            {
                "id": team["id"],
                "name": f"Team {team['id']}",
                "abbrev": f"T{team['id']}",
                "owners": owners,
                "transactionCounter": {
                    key: counter[key]
                    for key in ("acquisitions", "drops", "matchupAcquisitionTotals")
                    if key in counter
                },
            }
        )
    return {"teams": teams}


def sanitize_rosters(payload: dict[str, Any]) -> dict[str, Any]:
    """Every team's roster entries, each reduced to what the roster and player parsers read."""
    teams = []
    for team in payload["teams"]:
        entries = []
        for entry in (team.get("roster") or {}).get("entries") or []:
            player = (entry.get("playerPoolEntry") or {}).get("player") or {}
            entries.append(
                {
                    **{key: entry[key] for key in _ROSTER_ENTRY_KEEP if key in entry},
                    "playerPoolEntry": {
                        "player": {key: player[key] for key in _ROSTER_PLAYER_KEEP if key in player}
                    },
                }
            )
        teams.append({"id": team["id"], "roster": {"entries": entries}})
    return {"scoringPeriodId": payload.get("scoringPeriodId"), "teams": teams}


def ensure_lineup_differs(day: dict[str, Any], today: dict[str, Any], team_id: int) -> None:
    """If ESPN stored no future change for this day, swap a starter with a bench player.

    The per-day fixture exists to prove a future day can differ from today, so it has to.
    """
    now = {r.player_id: r.lineup_slot_id for r in parse_rosters(today) if r.team_id == team_id}
    entries = day["teams"][0]["roster"]["entries"]
    if any(now.get(e["playerId"]) != e["lineupSlotId"] for e in entries):
        return
    starter = next(e for e in entries if e["lineupSlotId"] not in (12, 13))
    bench = next(e for e in entries if e["lineupSlotId"] == 12)
    starter["lineupSlotId"], bench["lineupSlotId"] = 12, starter["lineupSlotId"]


def sanitize_transactions(payload: dict[str, Any]) -> dict[str, Any]:
    """A trimmed log: every add/drop/waiver row, a little of the lineup noise, two draft rows.

    Keeps every non-lineup, non-draft row (FREEAGENT / WAIVER / TRADE, whatever their status:
    that's where the ADD->DROP pair, the PENDING and the FAILED rows live), plus two
    FUTURE_ROSTER, one ROSTER and two DRAFT rows so their handling is covered too.
    """
    rows = payload["transactions"]
    kept = [r for r in rows if r.get("type") not in ("DRAFT", "FUTURE_ROSTER", "ROSTER")]
    for kind, count in (("FUTURE_ROSTER", 2), ("ROSTER", 1), ("DRAFT", 2)):
        kept += [r for r in rows if r.get("type") == kind][:count]
    return {
        "transactions": [
            {
                **{key: row[key] for key in _TRANSACTION_KEEP if key in row},
                "items": [
                    {key: item.get(key) for key in _TRANSACTION_ITEM_KEEP}
                    for item in row.get("items") or []
                ],
            }
            for row in sorted(kept, key=lambda r: (r.get("proposedDate") or 0, r["id"]))
        ]
    }


def sanitize_players(
    entries: list[dict[str, Any]], limit: int, tail: int = 0
) -> list[dict[str, Any]]:
    """Keep `limit` entries, reduced to the fields the parsers read.

    ESPN hands the pool back sorted by roster share, so the head is stars and the tail is
    players nobody owns. Taking `tail` entries from the bottom as well as the top is
    deliberate: ESPN publishes projections for roughly the top third of the pool, so without a
    tail sample the fixtures would have no player who *lacks* a projection — and that path
    needs the same coverage as the happy one.
    """
    head = max(limit - tail, 0)
    kept = entries[:head] + (entries[-tail:] if tail else [])

    out: list[dict[str, Any]] = []
    for entry in kept:
        player = entry.get("player") or {}
        out.append(
            {
                **{key: entry[key] for key in _ENTRY_KEEP if key in entry},
                "player": {key: player[key] for key in _PLAYER_KEEP if key in player},
            }
        )
    return out


def assert_clean(
    text: str,
    credentials: ESPNCredentials,
    *,
    real_swids: frozenset[str] = frozenset(),
    allow: tuple[str, ...] = (),
) -> None:
    """Refuse to write anything still carrying a secret, a real SWID or the real league id.

    `real_swids` are every member SWID the league payloads named: the team fixture is allowed
    an `owners` block (`allow`), but only one made of fakes.
    """
    secrets = {
        "espn_s2": credentials.espn_s2,
        "SWID": credentials.swid,
        "SWID (bare)": credentials.swid.strip("{}"),
        "league id": str(credentials.league_id),
    }
    leaked = [label for label, value in secrets.items() if value and value in text]
    upper = text.upper()
    leaked += [f"member SWID {s[:5]}..." for s in real_swids if s.strip("{}").upper() in upper]
    if leaked:
        raise SystemExit(f"Refusing to write fixture: it still contains {', '.join(leaked)}")

    for banned in (
        "members",
        "owners",
        "financeSettings",
        "draftDetail",
        "primaryOwner",
        "memberId",
        "divisions",
    ):
        if banned not in allow and f'"{banned}"' in text:
            raise SystemExit(f"Refusing to write fixture: it still contains a `{banned}` block")


def write_fixture(
    path: Path,
    data: Any,
    credentials: ESPNCredentials,
    *,
    real_swids: frozenset[str] = frozenset(),
    allow: tuple[str, ...] = (),
) -> None:
    text = json.dumps(data, indent=2, sort_keys=True) + "\n"
    assert_clean(text, credentials, real_swids=real_swids, allow=allow)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    print(f"wrote {path.relative_to(Path.cwd())} ({len(text):,} bytes)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--players", type=int, default=60, help="how many players to keep (default: 60)"
    )
    parser.add_argument(
        "--tail",
        type=int,
        default=10,
        help="how many of those to take from the unowned end of the pool (default: 10)",
    )
    parser.add_argument(
        "--only",
        choices=("all", "league", "team"),
        default="all",
        help="league = settings + player pool; team = schedule, teams, rosters, transactions",
    )
    args = parser.parse_args()

    client = ESPNClient.from_settings()
    credentials = client.credentials

    if args.only in ("all", "league"):
        write_fixture(
            SETTINGS_FIXTURE, sanitize_settings(client.fetch_settings_view()), credentials
        )
        # The whole pool, not just a first page: the tail sample has to come from the real
        # bottom.
        entries = client.fetch_player_pool_pages()
        write_fixture(
            PLAYERS_FIXTURE, sanitize_players(entries, args.players, args.tail), credentials
        )

    if args.only in ("all", "team"):
        record_team_fixtures(client)
    return 0


def record_team_fixtures(client: ESPNClient) -> None:
    """The in-season fixtures: schedule, teams, today's rosters, one future day, transactions."""
    credentials = client.credentials
    teams_payload = client.fetch_teams()
    real_swids = frozenset(
        owner for team in teams_payload["teams"] for owner in team.get("owners") or []
    ) | frozenset(member.get("id", "") for member in teams_payload.get("members") or [])
    clean = {"real_swids": real_swids}

    write_fixture(SCHEDULE_FIXTURE, sanitize_schedule(client.fetch_pro_schedule()), credentials)
    write_fixture(
        TEAMS_FIXTURE,
        sanitize_teams(teams_payload, credentials.swid),
        credentials,
        allow=("owners",),
        **clean,
    )

    today_period = _roster_period(client.season, datetime.now(UTC))
    rosters = sanitize_rosters(client.fetch_rosters(today_period))
    write_fixture(ROSTERS_FIXTURE, rosters, credentials, **clean)

    my_team = resolve_my_team(
        parse_teams(teams_payload), swid=credentials.swid, override=None
    ).team_id
    if my_team is None:
        raise SystemExit("Could not tell which team is ours; can't record the per-day lineup")
    day = sanitize_rosters(client.fetch_rosters(LINEUP_FIXTURE_DAY, team_id=my_team))
    ensure_lineup_differs(day, rosters, my_team)
    write_fixture(
        FIXTURE_DIR / f"espn_mroster_day_{LINEUP_FIXTURE_DAY}.json", day, credentials, **clean
    )

    write_fixture(
        TRANSACTIONS_FIXTURE,
        sanitize_transactions(client.fetch_transactions()),
        credentials,
        **clean,
    )


if __name__ == "__main__":
    raise SystemExit(main())
