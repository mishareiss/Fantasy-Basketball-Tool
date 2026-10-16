"""Pull our league from ESPN and store it: scoring settings first, then the player universe.

Every write is an upsert keyed on a natural key, so running a sync twice is a no-op rather
than a duplicate. That matters because sync is going to run on a schedule and on demand.
"""

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import (
    AdpEntry,
    EspnTransaction,
    FantasyTeam,
    LeagueSettings,
    NbaGame,
    Player,
    Projection,
    RosterEntry,
    ScoringRule,
)
from app.espn.client import ESPNClient
from app.espn.league import (
    parse_pro_schedule,
    parse_rosters,
    parse_teams,
    parse_transactions,
    resolve_my_team,
    waiver_entry_keys,
)
from app.espn.ownership import OwnershipRecord, parse_ownership
from app.espn.players import PlayerRecord, parse_player_pool
from app.espn.statsplits import (
    CURRENT_SEASON_SPLIT_ID,
    LAST_7_SPLIT_ID,
    LAST_15_SPLIT_ID,
    LAST_30_SPLIT_ID,
    ProjectionSplit,
    parse_actuals,
    parse_current_actuals,
    parse_projections,
)
from app.scoring.engine import ScoringEngine
from app.scoring.projections import score_projection
from app.scoring.settings import LeagueScoringSettings, parse_league_settings
from app.team.calendar import UnknownSeasonError, eastern_date, effective_period, period_date

# What we call ourselves in `Projection.source` / `AdpEntry.source`, and the projection horizon
# ESPN publishes. Named so imported sources slot in beside them without touching this module.
ESPN_SOURCE = "espn"
SEASON_PROJECTION_KIND = "projected_season"

# Last season's ACTUAL production, priced under OUR scoring — the same table, the same writer,
# a different `kind`. Named separately from the projection kind because that separation is the
# whole safety argument: every board and consensus query filters `kind == SEASON_PROJECTION_KIND`
# (`app.api.players.ranked_board`, `app.ranking.sources`), so an actual row cannot reach the
# ranking however many of them get stored. What reads it is the two reference columns on the
# master board and the player stat popup, which ask for it by name.
ACTUAL_SEASON_KIND = "actual_season"

# THIS season's actual production so far, one kind per window, for the Team tab's
# Season / L7 / L15 / L30 toggle. Same table, same writer, same safety argument as
# ACTUAL_SEASON_KIND: nothing that ranks players reads any of these kinds.
ACTUAL_CURRENT_KIND = "actual_current"
ACTUAL_LAST_7_KIND = "actual_last_7"
ACTUAL_LAST_15_KIND = "actual_last_15"
ACTUAL_LAST_30_KIND = "actual_last_30"
CURRENT_ACTUAL_KINDS: dict[str, int] = {
    ACTUAL_CURRENT_KIND: CURRENT_SEASON_SPLIT_ID,
    ACTUAL_LAST_7_KIND: LAST_7_SPLIT_ID,
    ACTUAL_LAST_15_KIND: LAST_15_SPLIT_ID,
    ACTUAL_LAST_30_KIND: LAST_30_SPLIT_ID,
}

log = logging.getLogger(__name__)

# Player columns the sync owns. Anything else on the row is left alone so a re-sync never
# clobbers work from other sources.
#
# `birthdate` and `age` are pointedly NOT here. ESPN publishes neither, so this sync would
# only ever write None over them — and once the nba.com age sync has populated them, the next
# `make sync` would silently wipe every age on the board. They belong to `app.ages`; this
# module does not touch them.
_PLAYER_FIELDS = (
    "full_name",
    "first_name",
    "last_name",
    "nba_team",
    "pro_team_id",
    "primary_position",
    "positions",
    "eligible_slots",
    "roster_status",
    "espn_fantasy_team_id",
    "injury_status",
    "injured",
)


@dataclass
class SyncSummary:
    """What a sync did — returned by the API and printed by the CLI."""

    league_id: int
    season: int
    league_name: str | None = None
    scoring_type: str | None = None
    team_count: int | None = None

    scoring_rules: int = 0
    scoring_rules_created: int = 0
    scoring_rules_updated: int = 0
    scoring_rules_removed: int = 0

    players_seen: int = 0
    players_created: int = 0
    players_updated: int = 0
    players_unchanged: int = 0

    projections_seen: int = 0
    projections_created: int = 0
    projections_updated: int = 0
    projections_unchanged: int = 0
    # Players ESPN listed but published no projection for — rookies, two-ways, deep bench.
    projections_missing: int = 0
    # The season the stored projections are FOR; see `select_projected_split` for why it can
    # trail the season we synced.
    projection_season: int | None = None

    # Last season's actuals, counted separately from the projections above. They are written
    # to the same table by the same function, so sharing one set of counters would report one
    # pass as the other — see `sync_actuals`.
    actuals_seen: int = 0
    actuals_created: int = 0
    actuals_updated: int = 0
    actuals_unchanged: int = 0
    # Players ESPN listed but has no completed season for — rookies, mostly.
    actuals_missing: int = 0
    # The season the stored actuals are FOR: the season before the one we synced, or the
    # newest one ESPN carries. Never the season being drafted.
    actual_season: int | None = None

    adp_seen: int = 0
    # The season the stored ADP is FOR — always the season we synced, unlike projections.
    adp_season: int | None = None
    adp_created: int = 0
    adp_updated: int = 0
    adp_unchanged: int = 0

    # This season's actuals, one entry per kind (actual_current, actual_last_7, ...), each
    # {seen, created, updated, unchanged, removed}. `removed` is a rolling window a player has
    # dropped out of: no game in the last 7 days means no L7 line, so yesterday's row goes.
    current_actuals: dict[str, dict[str, int]] = field(default_factory=dict)
    # Whether any `actual_current` row exists for the synced season: false until games start.
    actuals_available: bool = False

    # The NBA schedule. `schedule_date_mismatches` counts games whose US/Eastern date is not
    # the date of their scoring period; `schedule_mismatched_games` lists the first few.
    games_seen: int = 0
    games_created: int = 0
    games_updated: int = 0
    games_unchanged: int = 0
    games_removed: int = 0
    schedule_date_mismatches: int = 0
    schedule_mismatched_games: list[str] = field(default_factory=list)

    # The fantasy teams, and which one is ours (None, plus a warning, when we couldn't tell).
    teams_seen: int = 0
    my_team_id: int | None = None
    my_team_warning: str | None = None
    # ESPN's raw per-matchup add counts for our team, as sent.
    my_team_matchup_acquisition_totals: dict[str, Any] | None = None

    # Roster membership as of `roster_scoring_period`. Skipped = a rostered player we have no
    # Player row for (never expected: the pool lists every rostered player).
    roster_entries: int = 0
    roster_entries_skipped: int = 0
    roster_scoring_period: int | None = None

    transactions_seen: int = 0
    transactions_created: int = 0
    transactions_updated: int = 0
    transactions_unchanged: int = 0

    # Sorted keys of any WAIVERS entry in the player pool (and of its `player`), so the first
    # sync that sees someone on waivers shows whether ESPN publishes a clear time after all.
    waiver_entry_keys: dict[str, list[str]] | None = None

    roster_slots: dict[str, int] = field(default_factory=dict)
    points_by_stat: dict[str, float] = field(default_factory=dict)


def sync_scoring_settings(
    db: Session,
    *,
    espn_league_id: int,
    season: int,
    parsed: LeagueScoringSettings,
    summary: SyncSummary,
) -> LeagueSettings:
    """Upsert the LeagueSettings row and its ScoringRules for one (league, season)."""
    settings_row = db.scalar(
        select(LeagueSettings).where(
            LeagueSettings.espn_league_id == espn_league_id,
            LeagueSettings.season == season,
        )
    )
    if settings_row is None:
        settings_row = LeagueSettings(espn_league_id=espn_league_id, season=season)
        db.add(settings_row)

    settings_row.name = parsed.name
    settings_row.scoring_type = parsed.scoring_type
    settings_row.team_count = parsed.team_count
    settings_row.roster_slots = dict(parsed.roster_slots)
    settings_row.acquisition_settings = dict(parsed.acquisition.raw) or None
    db.flush()  # assigns settings_row.id for brand-new leagues

    existing = {rule.stat_id: rule for rule in settings_row.scoring_rules}
    incoming_ids = set()

    for coefficient in parsed.coefficients:
        incoming_ids.add(coefficient.stat_id)
        rule = existing.get(coefficient.stat_id)
        if rule is None:
            settings_row.scoring_rules.append(
                ScoringRule(
                    stat_id=coefficient.stat_id,
                    stat_name=coefficient.stat_name,
                    points=coefficient.points,
                    is_reverse=coefficient.is_reverse,
                )
            )
            summary.scoring_rules_created += 1
        elif (rule.stat_name, rule.points, rule.is_reverse) != (
            coefficient.stat_name,
            coefficient.points,
            coefficient.is_reverse,
        ):
            rule.stat_name = coefficient.stat_name
            rule.points = coefficient.points
            rule.is_reverse = coefficient.is_reverse
            summary.scoring_rules_updated += 1

    # A stat that stopped being scored must lose its row, or projections keep paying for it.
    for stat_id, rule in existing.items():
        if stat_id not in incoming_ids:
            settings_row.scoring_rules.remove(rule)
            summary.scoring_rules_removed += 1

    db.flush()

    summary.league_name = parsed.name
    summary.scoring_type = parsed.scoring_type
    summary.team_count = parsed.team_count
    summary.roster_slots = dict(parsed.roster_slots)
    summary.scoring_rules = len(parsed.coefficients)
    summary.points_by_stat = parsed.as_points_map()
    return settings_row


def sync_players(db: Session, records: list[PlayerRecord], summary: SyncSummary) -> None:
    """Upsert players by ESPN id. Players ESPN no longer lists are kept, not deleted.

    Keeping them matters for a dynasty tool: history and aliases reference these rows, and a
    player dropping off ESPN's active list (injury, G-League, retirement) shouldn't erase them.
    """
    summary.players_seen = len(records)
    if not records:
        return

    existing = {
        player.espn_player_id: player
        for player in db.scalars(
            select(Player).where(
                Player.espn_player_id.in_([record.espn_player_id for record in records])
            )
        )
    }

    for record in records:
        player = existing.get(record.espn_player_id)
        if player is None:
            db.add(Player(espn_player_id=record.espn_player_id, **_record_fields(record)))
            summary.players_created += 1
            continue

        changes = {
            name: value
            for name, value in _record_fields(record).items()
            if getattr(player, name) != value
        }
        if changes:
            for name, value in changes.items():
                setattr(player, name, value)
            summary.players_updated += 1
        else:
            summary.players_unchanged += 1

    db.flush()


def _record_fields(record: PlayerRecord) -> dict[str, object]:
    return {name: getattr(record, name) for name in _PLAYER_FIELDS}


def _known_player_ids(db: Session, player_ids: list[int]) -> set[int]:
    """Which of these ids actually have a Player row.

    Projections and ADP hang off `player`, so a row for an unknown id would be a foreign-key
    error mid-sync. In a full sync every id is known (players are upserted first); this keeps
    the partial-sync and test paths honest.
    """
    if not player_ids:
        return set()
    return set(
        db.scalars(select(Player.espn_player_id).where(Player.espn_player_id.in_(player_ids)))
    )


def sync_projections(
    db: Session,
    splits: list[ProjectionSplit],
    engine: ScoringEngine,
    summary: SyncSummary,
    *,
    source: str = ESPN_SOURCE,
    kind: str = SEASON_PROJECTION_KIND,
) -> None:
    """Price each projected split under our scoring and upsert it.

    Keyed on (player, source, kind, season), so a re-sync overwrites rather than accumulating,
    and ESPN publishing next season's projections adds rows beside this season's instead of
    destroying them.
    """
    summary.projections_seen = len(splits)
    summary.projections_missing = max(summary.players_seen - len(splits), 0)
    if not splits:
        return

    summary.projection_season = max(split.season for split in splits)

    known = _known_player_ids(db, [split.espn_player_id for split in splits])
    existing = {
        (row.player_id, row.season): row
        for row in db.scalars(
            select(Projection).where(
                Projection.source == source,
                Projection.kind == kind,
                Projection.player_id.in_(known),
            )
        )
    }
    now = datetime.now(UTC)

    for split in splits:
        if split.espn_player_id not in known:
            continue

        scored = score_projection(
            engine,
            split.stats,
            per_game_stats=split.average_stats,
            projected_games=split.projected_games,
        )
        values = {
            "raw_stats": split.stats,
            "per_game_stats": split.average_stats or None,
            "projected_games": split.projected_games,
            "fantasy_points_total": scored.fantasy_points_total,
            "fantasy_points_per_game": scored.fantasy_points_per_game,
            "per_game_basis": scored.per_game_basis,
            "source_fantasy_points_total": split.espn_applied_total,
        }

        row = existing.get((split.espn_player_id, split.season))
        if row is None:
            db.add(
                Projection(
                    player_id=split.espn_player_id,
                    source=source,
                    kind=kind,
                    season=split.season,
                    as_of=now,
                    **values,
                )
            )
            summary.projections_created += 1
            continue

        if any(getattr(row, name) != value for name, value in values.items()):
            for name, value in values.items():
                setattr(row, name, value)
            row.as_of = now
            summary.projections_updated += 1
        else:
            summary.projections_unchanged += 1

    db.flush()


def sync_actuals(
    db: Session,
    splits: list[ProjectionSplit],
    engine: ScoringEngine,
    summary: SyncSummary,
    *,
    source: str = ESPN_SOURCE,
) -> None:
    """Store what each player ACTUALLY did last season, priced under OUR scoring.

    The one interesting word is *our*: ESPN's own applied totals for a completed season were
    computed under whatever scoring that league ran, and what a dynasty startup wants to know
    is what last year's production would have been worth HERE. So the same engine that prices
    the projections prices these, and the number on a row is directly comparable to the
    projection beside it.

    Mechanically this is `sync_projections` under `ACTUAL_SEASON_KIND` — one writer, because
    an actual full-season split and a projected one are the same shape and the one thing we do
    with either is score it (`app.scoring.projections.score_projection` is source-agnostic on
    purpose). The scratch summary is how the two passes keep separate counters without teaching
    `sync_projections` about a second set of fields it would have to choose between.
    """
    scratch = SyncSummary(league_id=summary.league_id, season=summary.season)
    # Carried over so `projections_missing`'s arithmetic — players seen minus splits parsed —
    # means the same thing here: how many of the pool have never completed a season.
    scratch.players_seen = summary.players_seen

    sync_projections(db, splits, engine, scratch, source=source, kind=ACTUAL_SEASON_KIND)

    summary.actuals_seen = scratch.projections_seen
    summary.actuals_created = scratch.projections_created
    summary.actuals_updated = scratch.projections_updated
    summary.actuals_unchanged = scratch.projections_unchanged
    summary.actuals_missing = scratch.projections_missing
    summary.actual_season = scratch.projection_season


def sync_adp(
    db: Session,
    records: list[OwnershipRecord],
    summary: SyncSummary,
    *,
    season: int,
    source: str = ESPN_SOURCE,
) -> None:
    """Upsert one ADP row per player for this source and season. Values stored exactly as sent.

    `season` is required rather than defaulted: an ADP row without one is a number nobody can
    interpret next August, and the season is always to hand at the call site (it is the season
    the client is pointed at). Keyed on (player, source, season), so next season's ESPN sync
    adds rows beside this season's instead of overwriting the history the dynasty trend needs.
    """
    summary.adp_seen = len(records)
    summary.adp_season = season
    if not records:
        return

    known = _known_player_ids(db, [record.espn_player_id for record in records])
    existing = {
        row.player_id: row
        for row in db.scalars(
            select(AdpEntry).where(
                AdpEntry.source == source,
                AdpEntry.season == season,
                AdpEntry.player_id.in_(known),
            )
        )
    }
    now = datetime.now(UTC)

    for record in records:
        if record.espn_player_id not in known:
            continue

        values = {
            "adp": record.adp,
            "auction_value": record.auction_value,
            "percent_owned": record.percent_owned,
        }

        row = existing.get(record.espn_player_id)
        if row is None:
            db.add(
                AdpEntry(
                    player_id=record.espn_player_id,
                    source=source,
                    season=season,
                    as_of=now,
                    **values,
                )
            )
            summary.adp_created += 1
            continue

        if any(getattr(row, name) != value for name, value in values.items()):
            for name, value in values.items():
                setattr(row, name, value)
            row.as_of = now
            summary.adp_updated += 1
        else:
            summary.adp_unchanged += 1

    db.flush()


def sync_current_actuals(
    db: Session,
    entries: list[dict[str, Any]],
    engine: ScoringEngine,
    summary: SyncSummary,
    *,
    season: int,
    source: str = ESPN_SOURCE,
) -> None:
    """Store THIS season's actual lines (season so far, last 7 / 15 / 30 days), priced by us.

    Four more passes of `sync_projections`, one per kind, with the same scratch-summary trick
    as `sync_actuals`. Two differences from last season's actuals:

    * The split must be for exactly `season`; there is no fallback to an older one (see
      `select_current_split`).
    * Stale rows are deleted. A rolling window is a statement about now: a player who had an
      L7 line yesterday and hasn't played in a week has none today, and the upsert-only writer
      would keep yesterday's forever. So any row of these kinds, for this season, whose player
      this sync parsed no split for is removed. An empty pool removes nothing, since that's an
      ESPN failure, not a week with no games.
    """
    for kind, split_type_id in CURRENT_ACTUAL_KINDS.items():
        splits = parse_current_actuals(entries, season, split_type_id)
        scratch = SyncSummary(league_id=summary.league_id, season=summary.season)
        scratch.players_seen = summary.players_seen
        sync_projections(db, splits, engine, scratch, source=source, kind=kind)

        removed = 0
        if entries:
            keep = {split.espn_player_id for split in splits}
            for row in db.scalars(
                select(Projection).where(
                    Projection.source == source,
                    Projection.kind == kind,
                    Projection.season == season,
                    Projection.player_id.not_in(keep),
                )
            ):
                db.delete(row)
                removed += 1
            db.flush()

        summary.current_actuals[kind] = {
            "seen": scratch.projections_seen,
            "created": scratch.projections_created,
            "updated": scratch.projections_updated,
            "unchanged": scratch.projections_unchanged,
            "removed": removed,
        }

    summary.actuals_available = (
        db.scalar(
            select(Projection.id)
            .where(
                Projection.source == source,
                Projection.kind == ACTUAL_CURRENT_KIND,
                Projection.season == season,
            )
            .limit(1)
        )
        is not None
    )


# How many mismatched games the summary lists by name; the count covers all of them.
_MISMATCH_SAMPLE = 20


def sync_schedule(db: Session, payload: dict[str, Any], season: int, summary: SyncSummary) -> None:
    """Upsert the season's NBA games and delete the ones ESPN no longer lists.

    A postponed game moves (new period and date) or disappears, so the stored season is made to
    match the payload exactly. An empty payload deletes nothing: that is ESPN failing, not the
    NBA cancelling a season.

    Every game's US/Eastern date is checked against its scoring period's date. A mismatch is
    counted (and the first few listed), never fatal: the schedule is still the best we have.
    """
    games = parse_pro_schedule(payload)
    summary.games_seen = len(games)

    try:
        for game in games:
            expected = period_date(season, game.scoring_period_id)
            actual = eastern_date(game.starts_at)
            if actual != expected:
                summary.schedule_date_mismatches += 1
                if len(summary.schedule_mismatched_games) < _MISMATCH_SAMPLE:
                    summary.schedule_mismatched_games.append(
                        f"game {game.id}: period {game.scoring_period_id} is {expected}, "
                        f"but it starts {actual} ET ({game.starts_at.isoformat()})"
                    )
    except UnknownSeasonError:
        log.warning("No calendar for season %s; schedule dates not checked", season)

    existing = {
        game.id: game for game in db.scalars(select(NbaGame).where(NbaGame.season == season))
    }
    for game in games:
        values = {
            "scoring_period_id": game.scoring_period_id,
            "starts_at": game.starts_at,
            "home_pro_team_id": game.home_pro_team_id,
            "away_pro_team_id": game.away_pro_team_id,
            "start_time_tbd": game.start_time_tbd,
        }
        row = existing.pop(game.id, None)
        if row is None:
            db.add(NbaGame(id=game.id, season=season, **values))
            summary.games_created += 1
        elif any(getattr(row, name) != value for name, value in values.items()):
            for name, value in values.items():
                setattr(row, name, value)
            summary.games_updated += 1
        else:
            summary.games_unchanged += 1

    if games:
        for row in existing.values():
            db.delete(row)
            summary.games_removed += 1
    db.flush()


def sync_teams(
    db: Session,
    payload: dict[str, Any],
    season: int,
    summary: SyncSummary,
    *,
    swid: str | None,
    my_team_override: int | None = None,
) -> None:
    """Upsert the season's fantasy teams and mark exactly one of them as ours (or none).

    Which one is ours: `my_team_override` (ESPN_MY_TEAM_ID) if set, else the team whose owners
    include `swid`. If neither finds a team the sync carries on, with no team marked and a
    warning in the summary.
    """
    teams = parse_teams(payload)
    summary.teams_seen = len(teams)
    resolution = resolve_my_team(teams, swid=swid, override=my_team_override)
    summary.my_team_id = resolution.team_id
    summary.my_team_warning = resolution.warning
    if resolution.warning:
        log.warning("my team: %s", resolution.warning)

    now = datetime.now(UTC)
    existing = {
        team.team_id: team
        for team in db.scalars(select(FantasyTeam).where(FantasyTeam.season == season))
    }
    for team in teams:
        row = existing.pop(team.team_id, None)
        if row is None:
            row = FantasyTeam(season=season, team_id=team.team_id)
            db.add(row)
        row.name = team.name
        row.abbrev = team.abbrev
        row.owners = list(team.owners)
        row.is_mine = team.team_id == resolution.team_id
        row.acquisitions_total = team.acquisitions_total
        row.matchup_acquisition_totals = dict(team.matchup_acquisition_totals)
        row.synced_at = now
        if row.is_mine:
            summary.my_team_matchup_acquisition_totals = dict(team.matchup_acquisition_totals)

    # A team that left the league; and in any case nothing left over may still claim to be ours.
    for row in existing.values():
        if teams:
            db.delete(row)
        else:
            row.is_mine = False
    db.flush()


def sync_rosters(db: Session, payload: dict[str, Any], season: int, summary: SyncSummary) -> None:
    """Replace the season's roster membership with what `mRoster` says, all teams at once.

    Delete-then-insert inside the sync's transaction: membership is a snapshot, and a player
    who was dropped simply isn't in it. Lineups (`LineupAssignment`) are never touched here.
    """
    records = parse_rosters(payload)
    summary.roster_scoring_period = payload.get("scoringPeriodId") if payload else None
    if not records:
        return

    known = _known_player_ids(db, [record.player_id for record in records])
    db.execute(delete(RosterEntry).where(RosterEntry.season == season))
    for record in records:
        if record.player_id not in known:
            summary.roster_entries_skipped += 1
            continue
        db.add(
            RosterEntry(
                season=season,
                team_id=record.team_id,
                player_id=record.player_id,
                lineup_slot_id=record.lineup_slot_id,
                acquisition_date=record.acquisition_date,
                acquisition_type=record.acquisition_type,
            )
        )
        summary.roster_entries += 1
    db.flush()


def sync_transactions(
    db: Session, payload: dict[str, Any], season: int, summary: SyncSummary
) -> None:
    """Upsert every non-DRAFT transaction by id. Never deletes: the log only grows.

    Upsert rather than insert-once because a row's status moves (PENDING -> EXECUTED).
    """
    records = parse_transactions(payload)
    summary.transactions_seen = len(records)
    if not records:
        return

    existing = {
        row.id: row
        for row in db.scalars(
            select(EspnTransaction).where(EspnTransaction.id.in_([r.id for r in records]))
        )
    }
    for record in records:
        values = {
            "season": season,
            "type": record.type,
            "status": record.status,
            "team_id": record.team_id,
            "scoring_period_id": record.scoring_period_id,
            "proposed_at": record.proposed_at,
            "processed_at": record.processed_at,
            "items": record.items,
        }
        row = existing.get(record.id)
        if row is None:
            db.add(EspnTransaction(id=record.id, **values))
            summary.transactions_created += 1
        elif any(getattr(row, name) != value for name, value in values.items()):
            for name, value in values.items():
                setattr(row, name, value)
            summary.transactions_updated += 1
        else:
            summary.transactions_unchanged += 1
    db.flush()


def _roster_period(season: int, now: datetime) -> int | None:
    """The scoring period to read rosters at: today's (Pacific), clamped into the season.

    None for a season the calendar doesn't know, which leaves the choice to ESPN (it answers
    with its own current period).
    """
    try:
        return effective_period(season, now)
    except UnknownSeasonError:
        return None


def sync_league(
    db: Session, client: ESPNClient | None = None, *, now: datetime | None = None
) -> SyncSummary:
    """Full league sync. Commits once, at the end.

    Settings (scoring + acquisition rules) -> players (+ eligible slots) -> projections ->
    last season's actuals -> this season's actuals -> ADP -> NBA schedule -> fantasy teams
    (+ which is ours) -> rosters -> transactions.

    Order matters. The scoring rules have to land before projections, because pricing a
    projection needs them; players have to land before everything that hangs off `player`.
    One `kona_player_info` fetch feeds all the player passes.

    It does NOT write lineups. A lineup changed in the ESPN app must not silently overwrite one
    set on this site; only the explicit pull (`app.espn.lineups.pull_lineups`) does that.

    Every ESPN request is made before anything is written, so `ESPNCredentialsError` (cookies
    missing or rejected) or a failed request leaves the last good data intact.
    """
    client = client or ESPNClient.from_settings()
    now = now or datetime.now(UTC)
    season = client.season
    summary = SyncSummary(league_id=client.league_id, season=season)

    parsed = parse_league_settings(client.fetch_settings_view())
    entries = client.fetch_player_pool_pages()
    schedule_payload = client.fetch_pro_schedule()
    teams_payload = client.fetch_teams()
    rosters_payload = client.fetch_rosters(_roster_period(season, now))
    transactions_payload = client.fetch_transactions()

    summary.waiver_entry_keys = waiver_entry_keys(entries)
    if summary.waiver_entry_keys:
        log.warning("WAIVERS entries in the player pool carry keys: %s", summary.waiver_entry_keys)

    settings_row = sync_scoring_settings(
        db,
        espn_league_id=client.league_id,
        season=client.season,
        parsed=parsed,
        summary=summary,
    )
    sync_players(db, parse_player_pool(entries), summary)

    # Score against the rules we just stored, so a mid-season scoring change is reflected in
    # the same sync that picked it up.
    engine = ScoringEngine(settings_row.scoring_rules)
    sync_projections(db, parse_projections(entries, client.season), engine, summary)
    # The same payload's OTHER full-season split: what actually happened last year, priced
    # under the same engine. Stored under its own `kind`, so it sits beside the projection
    # rather than anywhere near the board's ranking.
    sync_actuals(db, parse_actuals(entries, client.season), engine, summary)
    # THIS season so far, by window. Exact-season only, stale windows removed.
    sync_current_actuals(db, entries, engine, summary, season=season)
    sync_adp(db, parse_ownership(entries), summary, season=client.season)

    sync_schedule(db, schedule_payload, season, summary)
    sync_teams(
        db,
        teams_payload,
        season,
        summary,
        swid=client.credentials.swid,
        my_team_override=get_settings().espn_my_team_id,
    )
    sync_rosters(db, rosters_payload, season, summary)
    sync_transactions(db, transactions_payload, season, summary)

    db.commit()
    return summary
