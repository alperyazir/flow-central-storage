"""Database access helpers for the bundle index.

Methods flush but do NOT commit, so callers control transaction boundaries
(create/delete commit immediately; reconcile batches many writes into one
commit).
"""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.bundle import Bundle
from app.repositories.base import BaseRepository


class BundleRepository(BaseRepository[Bundle]):
    """Repository for the bundle index table."""

    def __init__(self) -> None:
        super().__init__(model=Bundle)

    def list_all(self, session: Session) -> list[Bundle]:
        """Return every indexed bundle, ordered for stable display."""
        statement = select(Bundle).order_by(Bundle.publisher_slug, Bundle.book_name, Bundle.platform)
        return list(session.scalars(statement).all())

    def get_by_object_name(self, session: Session, object_name: str) -> Bundle | None:
        """Fetch a bundle row by its unique R2 object path."""
        return session.scalars(select(Bundle).where(Bundle.object_name == object_name)).first()

    def upsert(
        self,
        session: Session,
        *,
        object_name: str,
        publisher_slug: str,
        book_name: str,
        platform: str,
        file_name: str,
        file_size: int,
        app_version: str | None = None,
        book_id: int | None = None,
        group_id: int | None = None,
    ) -> Bundle:
        """Insert or update the row keyed by ``object_name`` (flush, no commit).

        ``book_id``/``group_id`` are only overwritten when a non-None value is
        provided, so reconcile (which doesn't know them) won't wipe links set by
        ``create_bundle_task``.
        """
        row = self.get_by_object_name(session, object_name)
        if row is None:
            row = Bundle(
                object_name=object_name,
                publisher_slug=publisher_slug,
                book_name=book_name,
                platform=platform,
                file_name=file_name,
                file_size=file_size,
                app_version=app_version,
                book_id=book_id,
                group_id=group_id,
            )
            session.add(row)
        else:
            row.publisher_slug = publisher_slug
            row.book_name = book_name
            row.platform = platform
            row.file_name = file_name
            row.file_size = file_size
            row.app_version = app_version
            if book_id is not None:
                row.book_id = book_id
            if group_id is not None:
                row.group_id = group_id
        session.flush()
        return row

    def delete_by_object_name(self, session: Session, object_name: str) -> int:
        """Delete the row for ``object_name``; return rows deleted (flush, no commit)."""
        result = session.execute(delete(Bundle).where(Bundle.object_name == object_name))
        session.flush()
        return result.rowcount or 0

    def delete_by_prefix(self, session: Session, prefix: str) -> int:
        """Delete all rows whose ``object_name`` starts with ``prefix``.

        Used when a book (and its R2 ``bundles/{slug}/{book}/`` folder) is
        deleted. ``autoescape=True`` escapes ``%``/``_`` in the prefix so book
        names containing underscores (e.g. ``Countdown_2_Sb`` from
        ``normalize_book_name``) don't act as LIKE wildcards and over-delete
        sibling books' rows. Returns rows removed (flush, no commit).
        """
        result = session.execute(
            delete(Bundle).where(Bundle.object_name.startswith(prefix, autoescape=True))
        )
        session.flush()
        return result.rowcount or 0
