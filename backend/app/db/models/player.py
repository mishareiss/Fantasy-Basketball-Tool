"""Canonical player identity: every projection, ranking, and valuation row points here."""

from datetime import date, datetime

from sqlalchemy import JSON, Date, DateTime, Float, ForeignKey, String, UniqueConstraint, func
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

    # ESPN publishes neither of these, so they are owned by the nba.com age sync
    # (`app.ages`), never by the ESPN sync. Birthdate is the source of truth; `age` is derived
    # from it at `Settings.age_as_of` — a fixed date, not "today" — so a stored age is
    # reproducible and correct for draft day. Both stay nullable: a player nba.com has never
    # heard of simply has no age.
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

    Written by whoever resolved the match — `app.matching` for automatic ones, the manual
    alias endpoint for the long tail — and read back by `app.matching` before it guesses
    anything, so a name is resolved once and never re-guessed.

    `confidence` and `match_method` record *how* the match was made. That is the difference
    between a row you can trust and one worth a second look: a 0.89 fuzzy hit and a hand-made
    alias are both just a player id without them.
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

    # 1.0 for an exact, normalized, or hand-made match; the similarity score for a fuzzy one.
    confidence: Mapped[float | None] = mapped_column(Float)
    # One of app.matching's methods: alias / exact / normalized / fuzzy, or 'manual'.
    match_method: Mapped[str | None] = mapped_column(String(24))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    player: Mapped[Player] = relationship(back_populates="aliases")

    def __repr__(self) -> str:
        return f"PlayerAlias(source={self.source!r}, source_name={self.source_name!r})"
