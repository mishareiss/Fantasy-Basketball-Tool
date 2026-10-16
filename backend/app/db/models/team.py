"""In-season league state from ESPN: the NBA schedule, the fantasy teams, who is on which
roster, each day's lineup, and the league's transaction log.

All keyed by `season` (ESPN's label: the year the season ends), so next season's sync adds rows
beside this one's instead of overwriting them. Times are stored UTC and timezone-aware; the
Pacific and Eastern views of them live in `app.team.calendar`, never in a column.
"""

from datetime import datetime

from sqlalchemy import JSON, BigInteger, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import UTCDateTime

# Where a stored lineup came from: pulled from ESPN, or set on this site. Only the explicit
# "sync lineup from ESPN" pull ever writes 'espn' rows, and it overwrites 'manual' ones in the
# days it covers. The general league sync never touches lineups at all.
LINEUP_SOURCE_ESPN = "espn"
LINEUP_SOURCE_MANUAL = "manual"
LINEUP_SOURCES = (LINEUP_SOURCE_ESPN, LINEUP_SOURCE_MANUAL)


class NbaGame(Base):
    """One NBA game, keyed by ESPN's game id.

    `scoring_period_id` is ESPN's day number for it, and that day is the game's US/Eastern date
    (`app.team.calendar.period_date`). Re-synced wholesale per season, so a postponed game that
    moves updates in place and one that vanishes is deleted.
    """

    __tablename__ = "nba_game"
    __table_args__ = (Index("ix_nba_game_season_period", "season", "scoring_period_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    season: Mapped[int] = mapped_column(nullable=False)
    scoring_period_id: Mapped[int] = mapped_column(nullable=False)
    starts_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    home_pro_team_id: Mapped[int] = mapped_column(nullable=False)
    away_pro_team_id: Mapped[int] = mapped_column(nullable=False)
    # ESPN's flag for a game whose tip time isn't set yet. `starts_at` is then a placeholder
    # time on the right day.
    start_time_tbd: Mapped[bool] = mapped_column(nullable=False, default=False)

    def __repr__(self) -> str:
        return (
            f"NbaGame(id={self.id!r}, sp={self.scoring_period_id!r}, "
            f"{self.away_pro_team_id!r}@{self.home_pro_team_id!r})"
        )


class FantasyTeam(Base):
    """One team in our league for one season, and whether it is ours.

    `is_mine` is true on exactly one row per season when the sync can tell which team is ours
    (ESPN_MY_TEAM_ID, else the team whose owners include our SWID), and on none when it can't.
    """

    __tablename__ = "fantasy_team"

    season: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    team_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    abbrev: Mapped[str | None] = mapped_column(String(16))
    # ESPN member SWIDs, braces and all, exactly as ESPN sends them.
    owners: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    is_mine: Mapped[bool] = mapped_column(nullable=False, default=False)

    # `transactionCounter.acquisitions`: this season's adds, all told.
    acquisitions_total: Mapped[int] = mapped_column(nullable=False, default=0)
    # `transactionCounter.matchupAcquisitionTotals`, stored raw: ESPN's own adds-per-matchup
    # count. Empty before the season, so its key format is not confirmed yet.
    matchup_acquisition_totals: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    synced_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"FantasyTeam(season={self.season!r}, team_id={self.team_id!r}, name={self.name!r})"


class RosterEntry(Base):
    """A player on a fantasy roster, as of the last league sync.

    Membership only. `lineup_slot_id` is the slot ESPN had him in on the day of the sync; the
    day-by-day lineup lives in `LineupAssignment`. Replaced wholesale by every sync, and a
    player is on at most one roster per season.
    """

    __tablename__ = "roster_entry"
    __table_args__ = (
        UniqueConstraint("season", "player_id", name="uq_roster_entry_season_player"),
        Index("ix_roster_entry_season_team", "season", "team_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    season: Mapped[int] = mapped_column(nullable=False)
    team_id: Mapped[int] = mapped_column(nullable=False)
    player_id: Mapped[int] = mapped_column(
        ForeignKey("player.espn_player_id", ondelete="CASCADE"), nullable=False
    )
    # ESPN slot id (POSITION_MAP): PG 0, SG 1, SF 2, PF 3, C 4, UT 11, BE 12, IR 13.
    lineup_slot_id: Mapped[int] = mapped_column(nullable=False)
    acquisition_date: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # DRAFT / ADD / TRADE ...
    acquisition_type: Mapped[str | None] = mapped_column(String(24))

    def __repr__(self) -> str:
        return f"RosterEntry(team_id={self.team_id!r}, player_id={self.player_id!r})"


class LineupAssignment(Base):
    """Which slot a player is in on one day (scoring period), for one team.

    Kept apart from roster membership because a lineup is a plan: ESPN stores future days, and
    so will this site. Only the explicit pull (`app.espn.lineups.pull_lineups`) writes 'espn'
    rows; `app.team.lineup.resolve_lineup` fills the days nobody stored.
    """

    __tablename__ = "lineup_assignment"
    __table_args__ = (
        UniqueConstraint(
            "season",
            "team_id",
            "scoring_period_id",
            "player_id",
            name="uq_lineup_assignment_day_player",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    season: Mapped[int] = mapped_column(nullable=False)
    team_id: Mapped[int] = mapped_column(nullable=False)
    scoring_period_id: Mapped[int] = mapped_column(nullable=False)
    player_id: Mapped[int] = mapped_column(
        ForeignKey("player.espn_player_id", ondelete="CASCADE"), nullable=False
    )
    lineup_slot_id: Mapped[int] = mapped_column(nullable=False)
    # One of LINEUP_SOURCES.
    source: Mapped[str] = mapped_column(String(8), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"LineupAssignment(team_id={self.team_id!r}, sp={self.scoring_period_id!r}, "
            f"player_id={self.player_id!r}, slot={self.lineup_slot_id!r})"
        )


class EspnTransaction(Base):
    """One row of ESPN's `mTransactions2` log (every type except DRAFT).

    Upserted by id on every sync, because a row's status moves (PENDING -> EXECUTED). `items`
    is ESPN's item list, trimmed to the fields we read: type (ADD / DROP / LINEUP ...),
    playerId, fromTeamId, toTeamId, fromLineupSlotId, toLineupSlotId.
    """

    __tablename__ = "espn_transaction"
    __table_args__ = (Index("ix_espn_transaction_season_type", "season", "type"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    season: Mapped[int] = mapped_column(nullable=False)
    type: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    team_id: Mapped[int | None] = mapped_column()
    scoring_period_id: Mapped[int | None] = mapped_column()
    proposed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # ESPN only sends `processDate` on processed waiver claims. A free-agent pickup executes
    # the moment it is proposed and carries no process date.
    processed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    items: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    @property
    def effective_at(self) -> datetime | None:
        """When it took effect: the process time if ESPN gave one, else the proposal time."""
        return self.processed_at or self.proposed_at

    def __repr__(self) -> str:
        return f"EspnTransaction(id={self.id!r}, type={self.type!r}, status={self.status!r})"
