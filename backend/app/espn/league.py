"""Parse ESPN's in-season league views: the NBA schedule, teams, rosters and transactions.

Pure functions over the raw JSON, no database, the same split as `app.espn.players` (parse)
and `app.espn.sync` (write). Shapes were checked against the live league for 2027; see
`docs/prompts/27-team-data-backend.md` for what each view returns.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.espn.client import _normalise_swid

# ESPN's placeholder pro team ("FA"), listed in the schedule with no games of its own.
PLACEHOLDER_PRO_TEAM_ID = 0

# The transaction type we never store: the draft is history the draft room already keeps.
DRAFT_TRANSACTION_TYPE = "DRAFT"

# The fields of a transaction item we keep; everything else ESPN sends is noise to us.
_TRANSACTION_ITEM_FIELDS = (
    "type",
    "playerId",
    "fromTeamId",
    "toTeamId",
    "fromLineupSlotId",
    "toLineupSlotId",
)


def epoch_ms_to_utc(value: Any) -> datetime | None:
    """ESPN's epoch-milliseconds timestamps -> aware UTC datetimes. None for anything else."""
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        return None
    return datetime.fromtimestamp(value / 1000.0, UTC)


def _int(value: Any) -> int | None:
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else None


# --- schedule -------------------------------------------------------------------------------


@dataclass(frozen=True)
class GameRecord:
    """One NBA game from `proTeamSchedules_wl`."""

    id: int
    scoring_period_id: int
    starts_at: datetime
    home_pro_team_id: int
    away_pro_team_id: int
    start_time_tbd: bool = False


def parse_pro_schedule(payload: dict[str, Any]) -> list[GameRecord]:
    """Every game in the season schedule, de-duplicated by game id.

    ESPN lists each game under both of its teams, and under a placeholder team 0 that has no
    games. A row missing its id, date or teams is skipped rather than guessed at.
    """
    settings = payload.get("settings") if isinstance(payload, dict) else None
    pro_teams = (settings or {}).get("proTeams") or []

    games: dict[int, GameRecord] = {}
    for team in pro_teams:
        if not isinstance(team, dict) or team.get("id") == PLACEHOLDER_PRO_TEAM_ID:
            continue
        by_period = team.get("proGamesByScoringPeriod") or {}
        for period_games in by_period.values():
            for game in period_games or []:
                record = _parse_game(game)
                if record is not None:
                    games[record.id] = record
    return sorted(games.values(), key=lambda g: (g.starts_at, g.id))


def _parse_game(game: Any) -> GameRecord | None:
    if not isinstance(game, dict):
        return None
    game_id = _int(game.get("id"))
    period = _int(game.get("scoringPeriodId"))
    starts_at = epoch_ms_to_utc(game.get("date"))
    home, away = _int(game.get("homeProTeamId")), _int(game.get("awayProTeamId"))
    if None in (game_id, period, starts_at, home, away):
        return None
    return GameRecord(
        id=game_id,  # type: ignore[arg-type]  # None ruled out above
        scoring_period_id=period,  # type: ignore[arg-type]
        starts_at=starts_at,  # type: ignore[arg-type]
        home_pro_team_id=home,  # type: ignore[arg-type]
        away_pro_team_id=away,  # type: ignore[arg-type]
        start_time_tbd=bool(game.get("startTimeTBD", False)),
    )


# --- fantasy teams --------------------------------------------------------------------------


@dataclass(frozen=True)
class TeamRecord:
    """One fantasy team from `mTeam`."""

    team_id: int
    name: str
    abbrev: str | None = None
    owners: list[str] = field(default_factory=list)
    acquisitions_total: int = 0
    matchup_acquisition_totals: dict[str, Any] = field(default_factory=dict)


def parse_teams(payload: dict[str, Any]) -> list[TeamRecord]:
    """Every team in `mTeam`, in team-id order."""
    out: dict[int, TeamRecord] = {}
    for team in (payload or {}).get("teams") or []:
        if not isinstance(team, dict):
            continue
        team_id = _int(team.get("id"))
        if team_id is None:
            continue
        counter = team.get("transactionCounter") or {}
        totals = counter.get("matchupAcquisitionTotals")
        owners = [owner for owner in team.get("owners") or [] if isinstance(owner, str)]
        out[team_id] = TeamRecord(
            team_id=team_id,
            name=team.get("name") or f"Team {team_id}",
            abbrev=team.get("abbrev"),
            owners=owners,
            acquisitions_total=_int(counter.get("acquisitions")) or 0,
            matchup_acquisition_totals=dict(totals) if isinstance(totals, dict) else {},
        )
    return [out[team_id] for team_id in sorted(out)]


def _swid_key(swid: str) -> str:
    return _normalise_swid(swid).upper()


@dataclass(frozen=True)
class MyTeamResolution:
    team_id: int | None
    warning: str | None = None


def resolve_my_team(
    teams: list[TeamRecord], *, swid: str | None, override: int | None
) -> MyTeamResolution:
    """Which team is ours: the ESPN_MY_TEAM_ID override if set, else the one our SWID owns.

    SWIDs are compared braces-normalised and case-insensitively, because ESPN's owner list and
    a pasted cookie disagree on both. No match is a warning, not an error: everything else the
    sync stores is still worth storing.
    """
    team_ids = {team.team_id for team in teams}
    if override is not None:
        if override in team_ids:
            return MyTeamResolution(override)
        return MyTeamResolution(
            None, f"ESPN_MY_TEAM_ID={override} is not a team in this league ({sorted(team_ids)})"
        )

    if not swid:
        return MyTeamResolution(None, "No SWID configured, so no team could be marked as ours")

    wanted = _swid_key(swid)
    matches = [team.team_id for team in teams if wanted in {_swid_key(o) for o in team.owners}]
    if not matches:
        return MyTeamResolution(
            None,
            "No team in this league lists our SWID as an owner. Set ESPN_MY_TEAM_ID to pick one.",
        )
    if len(matches) > 1:
        return MyTeamResolution(
            matches[0],
            f"Our SWID owns several teams {matches}; using {matches[0]}. "
            "Set ESPN_MY_TEAM_ID to choose.",
        )
    return MyTeamResolution(matches[0])


# --- rosters --------------------------------------------------------------------------------


@dataclass(frozen=True)
class RosterRecord:
    """One player on one fantasy roster, on the scoring period the view was fetched for."""

    team_id: int
    player_id: int
    lineup_slot_id: int
    acquisition_date: datetime | None = None
    acquisition_type: str | None = None


def parse_rosters(payload: dict[str, Any]) -> list[RosterRecord]:
    """Every roster entry in an `mRoster` response (all teams, or the one `forTeamId` asked for).

    De-duplicated by player id, last one wins: a player is on one roster at a time.
    """
    out: dict[int, RosterRecord] = {}
    for team in (payload or {}).get("teams") or []:
        if not isinstance(team, dict):
            continue
        team_id = _int(team.get("id"))
        entries = (team.get("roster") or {}).get("entries") or []
        if team_id is None:
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            player_id, slot = _int(entry.get("playerId")), _int(entry.get("lineupSlotId"))
            if player_id is None or slot is None:
                continue
            out[player_id] = RosterRecord(
                team_id=team_id,
                player_id=player_id,
                lineup_slot_id=slot,
                acquisition_date=epoch_ms_to_utc(entry.get("acquisitionDate")),
                acquisition_type=entry.get("acquisitionType"),
            )
    return list(out.values())


def roster_player_entries(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """The `playerPoolEntry` blocks inside an `mRoster` response, shaped for `parse_player_pool`.

    `app.espn.players.player_object` already reads the `playerPoolEntry.player` shape, so a
    roster entry can be parsed as a player directly.
    """
    return [
        entry
        for team in (payload or {}).get("teams") or []
        for entry in (team.get("roster") or {}).get("entries") or []
        if isinstance(entry, dict) and isinstance(entry.get("playerPoolEntry"), dict)
    ]


# --- transactions ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TransactionRecord:
    """One `mTransactions2` row, minus the draft."""

    id: str
    type: str
    status: str
    team_id: int | None
    scoring_period_id: int | None
    proposed_at: datetime | None
    processed_at: datetime | None
    items: list[dict[str, Any]] = field(default_factory=list)


def parse_transactions(payload: dict[str, Any]) -> list[TransactionRecord]:
    """Every non-DRAFT transaction, with each item trimmed to the fields we read."""
    out: dict[str, TransactionRecord] = {}
    for row in (payload or {}).get("transactions") or []:
        if not isinstance(row, dict):
            continue
        tx_id, tx_type, status = row.get("id"), row.get("type"), row.get("status")
        if not (isinstance(tx_id, str) and tx_type and status):
            continue
        if tx_type == DRAFT_TRANSACTION_TYPE:
            continue
        items = [
            {key: item.get(key) for key in _TRANSACTION_ITEM_FIELDS}
            for item in row.get("items") or []
            if isinstance(item, dict)
        ]
        out[tx_id] = TransactionRecord(
            id=tx_id,
            type=str(tx_type),
            status=str(status),
            team_id=_int(row.get("teamId")),
            scoring_period_id=_int(row.get("scoringPeriodId")),
            proposed_at=epoch_ms_to_utc(row.get("proposedDate")),
            processed_at=epoch_ms_to_utc(row.get("processDate")),
            items=items,
        )
    return list(out.values())


# --- the waiver probe -----------------------------------------------------------------------

WAIVERS_STATUS = "WAIVERS"


def waiver_entry_keys(entries: list[dict[str, Any]]) -> dict[str, list[str]] | None:
    """Sorted top-level keys of every WAIVERS entry and of its `player` object, or None.

    ESPN publishes no "clears at" time that we know of, but no player was on waivers when the
    payload was last inspected. This is how the first sync that sees one tells us what the
    entry actually carries.
    """
    waivers = [e for e in entries if isinstance(e, dict) and e.get("status") == WAIVERS_STATUS]
    if not waivers:
        return None
    entry_keys: set[str] = set()
    player_keys: set[str] = set()
    for entry in waivers:
        entry_keys.update(entry.keys())
        player = entry.get("player") or (entry.get("playerPoolEntry") or {}).get("player")
        if isinstance(player, dict):
            player_keys.update(player.keys())
    return {"entry": sorted(entry_keys), "player": sorted(player_keys)}
