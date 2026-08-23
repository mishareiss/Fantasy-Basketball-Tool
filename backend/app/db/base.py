"""The SQLAlchemy declarative base every model inherits from."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base class for every ORM model."""
