"""SQLAlchemy declarative base for Flow Central Storage models."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base class for all ORM models."""

    pass


metadata = Base.metadata
