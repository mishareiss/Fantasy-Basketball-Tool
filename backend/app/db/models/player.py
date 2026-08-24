"""Canonical player identity: every projection, ranking, and valuation row points here."""

from datetime import date, datetime

from sqlalchemy import JSON, Date, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Player(Base):
    """One NBA player, keyed by ESPN's player id — the identity every source resolves to.

    ESPN's fantasy API is the only source that gives us a stable id for free, so it is the
    primary key rather than a surrogate: sync upserts by it, and external sources reach it
    through `PlayerAlias`.
    """

    __tablename__ = "player"

    espn_player_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    full_name: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    first_name: Mapped[str | None] = mapped_column(String(60))
    last_name: Mapped[str | None] = mapped_column(String(60))

    # Abbreviation from espn-api's PRO_TEAM_MAP ('FA' when ESPN has no team for them).
    nba_team: Mapped[str | None] = mapped_column(String(4))
    pro_team_id: Mapped[int | None] = mapped_column()

    # Default position plus every atomic position ESPN says they're eligible at (PG/SG/SF/PF/C).
    primary_position: Mapped[str | None] = mapped_column(String(8))
    positions: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)

    # FREEAGENT / WAIVERS / ONTEAM in our league, and the fantasy team holding them if rostered.
    roster_status: Mapped[str | None] = mapped_column(String(16))
    espn_fantasy_team_id: Mapped[int | None] = mapped_column()

    injury_status: Mapped[str | None] = mapped_column(String(24))
    injured: Mapped[bool] = mapped_column(nullable=False, default=False)

    # ESPN's fantasy API does not currently expose birthdates; the authoritative age source
    # lands later (it drives the dynasty age curve), so both stay nullable.
    birthdate: Mapped[date | None] = mapped_column(Date)
    age: Mapped[int | None] = mapped_column()

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    aliases: Mapped[list["PlayerAlias"]] = relationship(
        back_populates="player", cascade="all, delete-orphan"
    )
    projections: Mapped[list["Projection"]] = relationship(  # noqa: F821  # app.db.models.projection
        back_populates="player", cascade="all, delete-orphan"
    )
    adp_entries: Mapped[list["AdpEntry"]] = relationship(  # noqa: F821  # app.db.models.adp
        back_populates="player", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"Player(espn_player_id={self.espn_player_id!r}, full_name={self.full_name!r})"


class PlayerAlias(Base):
    """How one external source names a player, so imported rows resolve to a canonical Player.

    Empty until the import pipeline lands; it exists now so ADP/projection imports have
    somewhere to record a resolved match (fuzzy or hand-made) instead of re-guessing each run.
    """

    __tablename__ = "player_alias"
    __table_args__ = (
        UniqueConstraint("source", "source_name", name="uq_player_alias_source_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(
        ForeignKey("player.espn_player_id", ondelete="CASCADE"), nullable=False, index=True
    )

    # e.g. 'fantasypros', 'hashtag', 'balldontlie', 'manual'
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    source_name: Mapped[str] = mapped_column(String(120), nullable=False)
    source_id: Mapped[str | None] = mapped_column(String(64))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    player: Mapped[Player] = relationship(back_populates="aliases")

    def __repr__(self) -> str:
        return f"PlayerAlias(source={self.source!r}, source_name={self.source_name!r})"
