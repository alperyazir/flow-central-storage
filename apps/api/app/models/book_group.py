"""ORM model for book groups.

A group ties together sibling books that should be presented and bundled as a
unit (e.g. a Student's Book + Workbook). Membership is an unordered set: a book
points at its group via ``Book.group_id`` (at most one group per book). The
template app / Learn viewer handle ordering and transitions, so no order or
role columns are stored here.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.book import Book
    from app.models.publisher import Publisher


class BookGroup(Base):
    """A named set of books belonging to one publisher."""

    __tablename__ = "book_groups"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Group is publisher-scoped; deleting the publisher cascades its books and
    # groups together.
    publisher_id: Mapped[int] = mapped_column(
        ForeignKey("publishers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    publisher_rel: Mapped["Publisher"] = relationship("Publisher")
    # Deleting a group detaches its books (FK ondelete=SET NULL) rather than
    # deleting them; passive_deletes lets the DB do the SET NULL in one statement.
    books: Mapped[list["Book"]] = relationship(
        "Book", back_populates="group_rel", passive_deletes=True
    )
