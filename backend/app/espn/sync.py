"""Pull our league from ESPN and store it: scoring settings first, then the player universe.

Every write is an upsert keyed on a natural key, so running a sync twice is a no-op rather
than a duplicate. That matters because sync is going to run on a schedule and on demand.
"""

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import LeagueSettings, Player, ScoringRule
from app.espn.client import ESPNClient
from app.espn.players import PlayerRecord, parse_player_pool
from app.scoring.settings import LeagueScoringSettings, parse_league_settings

# Player columns the sync owns. Anything else on the row (aliases, later-derived fields) is
# left alone so a re-sync never clobbers work from other sources.
_PLAYER_FIELDS = (
    "full_name",
    "first_name",
    "last_name",
    "nba_team",
    "pro_team_id",
    "primary_position",
    "positions",
    "roster_status",
    "espn_fantasy_team_id",
    "injury_status",
    "injured",
    "birthdate",
    "age",
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


def sync_league(db: Session, client: ESPNClient | None = None) -> SyncSummary:
    """Full league sync: scoring settings, then the player pool. Commits on success.

    Raises `ESPNCredentialsError` if cookies are missing or rejected — nothing is written in
    that case, so a stale-cookie sync leaves the last good data intact.
    """
    client = client or ESPNClient.from_settings()
    summary = SyncSummary(league_id=client.league_id, season=client.season)

    parsed = parse_league_settings(client.fetch_settings_view())
    records = parse_player_pool(client.fetch_player_pool_pages())

    sync_scoring_settings(
        db,
        espn_league_id=client.league_id,
        season=client.season,
        parsed=parsed,
        summary=summary,
    )
    sync_players(db, records, summary)

    db.commit()
    return summary
