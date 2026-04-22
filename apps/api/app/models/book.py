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


class BookTypeEnum(str, enum.Enum):
    """Kind of content stored for a book record."""

    STANDARD = "standard"
    PDF = "pdf"


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

    # Child-book support: a book may be attached to a parent book as an
    # additional resource (flowbook child or raw PDF). ``book_type``
    # controls the content pipeline: ``standard`` keeps the existing ZIP
    # flow, ``pdf`` stores a single PDF under ``raw/`` and skips bundles.
    parent_book_id: Mapped[int | None] = mapped_column(
        ForeignKey("books.id", ondelete="CASCADE"), nullable=True, index=True
    )
    book_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default=BookTypeEnum.STANDARD.value, server_default=BookTypeEnum.STANDARD.value
    )

    # Relationship to Publisher model
    publisher_rel: Mapped["Publisher"] = relationship("Publisher", back_populates="books")

    parent_rel: Mapped["Book | None"] = relationship(
        "Book", remote_side="Book.id", back_populates="children_rel"
    )
    children_rel: Mapped[list["Book"]] = relationship(
        "Book",
        back_populates="parent_rel",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    @property
    def publisher(self) -> str:
        """Get publisher name from relationship."""
        return self.publisher_rel.name

    @property
    def publisher_slug(self) -> str:
        """Get publisher slug from relationship."""
        return self.publisher_rel.slug

    @property
    def r2_prefix(self) -> str:
        """R2 object prefix for this book's content in the publishers bucket.

        Top-level books live at ``{publisher_slug}/books/{book_name}/``.
        Child books (``parent_book_id`` set) are nested under their parent:
        ``{publisher_slug}/books/{parent.book_name}/additional-resources/{book_name}/``.
        The nested layout lets sync-with-R2 reconstruct parent links from
        path alone and makes parent bundle exclusion straightforward
        (see ``should_skip_bundled_path`` filtering ``additional-resources/``).

        Requires an attached session — accesses ``publisher_rel`` and,
        for children, ``parent_rel``.
        """
        slug = self.publisher_rel.slug
        if self.parent_book_id is None:
            return f"{slug}/books/{self.book_name}/"
        parent = self.parent_rel
        if parent is None:
            # Defensive: child with a dangling FK — fall back to flat path.
            return f"{slug}/books/{self.book_name}/"
        return f"{slug}/books/{parent.book_name}/additional-resources/{self.book_name}/"
