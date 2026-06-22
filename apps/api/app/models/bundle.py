"""ORM model for the bundle index.

Mirrors the standalone-app bundles stored in R2 so the panel can list them
without scanning object storage (one ``stat_object`` per bundle) on every
request. R2 remains the source of truth; this table is a queryable index kept
in sync on create/delete and via an explicit reconcile against R2.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Bundle(Base):
    """A single standalone-app bundle (one platform of one book/group)."""

    __tablename__ = "bundles"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # Full R2 object path; this is the bundle's identity (unique).
    object_name: Mapped[str] = mapped_column(String(1024), nullable=False, unique=True)

    # Path-derived descriptors (kept denormalized so reconcile from R2 needs no
    # DB joins, and the panel keys bundles by publisher_slug/book_name).
    publisher_slug: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    book_name: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    platform: Mapped[str] = mapped_column(String(20), nullable=False)

    file_name: Mapped[str] = mapped_column(String(512), nullable=False)
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    # App version stamped on the bundle (from the template it was built with).
    app_version: Mapped[str | None] = mapped_column(String(50), nullable=True, default=None)

    # Convenience back-references. Nullable so reconcile-discovered rows (which
    # only know the R2 path) still insert. ``group_id`` is wired to a FK in the
    # book-groups phase; kept as a plain nullable column for now.
    book_id: Mapped[int | None] = mapped_column(
        ForeignKey("books.id", ondelete="SET NULL"), nullable=True, index=True, default=None
    )
    group_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True, default=None)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
