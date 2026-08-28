"""Pull our league from ESPN and store it: scoring settings first, then the player universe.

Every write is an upsert keyed on a natural key, so running a sync twice is a no-op rather
than a duplicate. That matters because sync is going to run on a schedule and on demand.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AdpEntry, LeagueSettings, Player, Projection, ScoringRule
from app.espn.client import ESPNClient
from app.espn.ownership import OwnershipRecord, parse_ownership
from app.espn.players import PlayerRecord, parse_player_pool
from app.espn.statsplits import ProjectionSplit, parse_projections
from app.scoring.engine import ScoringEngine
from app.scoring.projections import score_projection
from app.scoring.settings import LeagueScoringSettings, parse_league_settings

# What we call ourselves in `Projection.source` / `AdpEntry.source`, and the projection horizon
# ESPN publishes. Named so imported sources slot in beside them without touching this module.
ESPN_SOURCE = "espn"
SEASON_PROJECTION_KIND = "projected_season"

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

    adp_seen: int = 0
    # The season the stored ADP is FOR — always the season we synced, unlike projections.
    adp_season: int | None = None
    adp_created: int = 0
    adp_updated: int = 0
    adp_unchanged: int = 0

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


def sync_league(db: Session, client: ESPNClient | None = None) -> SyncSummary:
    """Full league sync: scoring settings, players, projections, ADP. Commits on success.

    Order matters. The scoring rules have to land before projections, because pricing a
    projection needs them; players have to land before projections and ADP, because both hang
    off `player`. One `kona_player_info` fetch feeds all three player passes.

    Raises `ESPNCredentialsError` if cookies are missing or rejected — nothing is written in
    that case, so a stale-cookie sync leaves the last good data intact.
    """
    client = client or ESPNClient.from_settings()
    summary = SyncSummary(league_id=client.league_id, season=client.season)

    parsed = parse_league_settings(client.fetch_settings_view())
    entries = client.fetch_player_pool_pages()

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
    sync_adp(db, parse_ownership(entries), summary, season=client.season)

    db.commit()
    return summary
