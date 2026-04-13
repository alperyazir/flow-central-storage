"""ORM model for publisher metadata."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.book import Book


class PublisherStatusEnum(str, enum.Enum):
    """Lifecycle states for publisher records."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    SUSPENDED = "suspended"


class ProcessingPriorityEnum(str, enum.Enum):
    """Processing priority levels for AI jobs."""

    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"


class Publisher(Base):
    """Represents a publisher entity persisted in PostgreSQL."""

    __tablename__ = "publishers"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    slug: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    logo_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    contact_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active", server_default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # AI Processing Settings (nullable = use global default)
    ai_auto_process_enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=None)
    ai_processing_priority: Mapped[str | None] = mapped_column(String(20), nullable=True, default=None)
    ai_audio_languages: Mapped[str | None] = mapped_column(String(100), nullable=True, default=None)

    # Relationship to books (cascade delete: when publisher is deleted, all books are deleted too)
    books: Mapped[list["Book"]] = relationship("Book", back_populates="publisher_rel", cascade="all, delete-orphan")
