"""Column types shared by the models."""

from datetime import UTC, datetime

from sqlalchemy import DateTime
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator):
    """A timezone-aware UTC datetime on every backend.

    `DateTime(timezone=True)` round-trips aware on Postgres but comes back NAIVE from SQLite,
    which the tests run on. A naive value never compares equal to the aware one a sync is about
    to write, so every "unchanged?" check would report a change. This stores UTC and always
    hands back an aware UTC datetime, so the comparison means the same thing on both.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("UTCDateTime refuses a naive datetime; attach a timezone first")
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
