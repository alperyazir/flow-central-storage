"""ORM model for book metadata."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, Enum, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.publisher import Publisher


class BookStatusEnum(str, enum.Enum):
    """Lifecycle states for book metadata records."""

    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class Book(Base):
    """Represents a book metadata record persisted in PostgreSQL."""

    __tablename__ = "books"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    book_name: Mapped[str] = mapped_column(String(255), nullable=False)
    book_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    book_cover: Mapped[str | None] = mapped_column(String(512), nullable=True)
    activity_count: Mapped[int | None] = mapped_column(nullable=True)
    activity_details: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    total_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    language: Mapped[str] = mapped_column(String(64), nullable=False)
    category: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # TODO [PERF-H5]: Add database index on `status` column.
    #   Requires Alembic migration:
    #     op.create_index("ix_books_status", "books", ["status"])
    #   This will speed up the common WHERE status != 'archived' filter.
    status: Mapped[BookStatusEnum] = mapped_column(
        Enum(BookStatusEnum, name="book_status", native_enum=False),
        nullable=False,
        default=BookStatusEnum.DRAFT,
        server_default=BookStatusEnum.DRAFT.value,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Foreign key to publishers table (required)
    publisher_id: Mapped[int] = mapped_column(ForeignKey("publishers.id"), nullable=False)

    # Relationship to Publisher model
    publisher_rel: Mapped["Publisher"] = relationship("Publisher", back_populates="books")

    @property
    def publisher(self) -> str:
        """Get publisher name from relationship."""
        return self.publisher_rel.name

    @property
    def publisher_slug(self) -> str:
        """Get publisher slug from relationship."""
        return self.publisher_rel.slug
