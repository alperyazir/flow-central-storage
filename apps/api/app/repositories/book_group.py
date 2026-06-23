"""Database access helpers for book groups."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.book import Book
from app.models.book_group import BookGroup
from app.repositories.base import BaseRepository


class BookGroupRepository(BaseRepository[BookGroup]):
    """Repository for book group records and their membership."""

    def __init__(self) -> None:
        super().__init__(model=BookGroup)

    def list_all(self, session: Session, *, publisher_id: int | None = None) -> list[BookGroup]:
        """List groups (optionally for one publisher), with books eager-loaded
        so ``book_count`` is available without an extra query per group."""
        statement = select(BookGroup).options(selectinload(BookGroup.books)).order_by(BookGroup.name)
        if publisher_id is not None:
            statement = statement.where(BookGroup.publisher_id == publisher_id)
        return list(session.scalars(statement).all())

    def get(self, session: Session, group_id: int) -> BookGroup | None:
        """Fetch a group by id (no eager loading)."""
        return session.get(BookGroup, group_id)

    def get_with_books(self, session: Session, group_id: int) -> BookGroup | None:
        """Fetch a group with member books + each book's publisher/parent
        eager-loaded (needed by BookRead's ``r2_prefix``/``publisher_slug``)."""
        statement = (
            select(BookGroup)
            .options(
                selectinload(BookGroup.books).selectinload(Book.publisher_rel),
                selectinload(BookGroup.books).selectinload(Book.parent_rel),
            )
            .where(BookGroup.id == group_id)
        )
        return session.scalars(statement).first()

    def create(self, session: Session, *, name: str, publisher_id: int) -> BookGroup:
        """Create a new group."""
        group = BookGroup(name=name, publisher_id=publisher_id)
        session.add(group)
        session.commit()
        session.refresh(group)
        return group

    def update(self, session: Session, group: BookGroup, *, name: str) -> BookGroup:
        """Rename a group."""
        group.name = name
        session.commit()
        session.refresh(group)
        return group

    def delete(self, session: Session, group: BookGroup) -> None:
        """Delete a group; member books are detached via FK ondelete=SET NULL."""
        session.delete(group)
        session.commit()

    def add_books(self, session: Session, group: BookGroup, book_ids: list[int]) -> list[Book]:
        """Attach books to the group. Only books of the group's publisher are
        added; books from other publishers are silently skipped. Returns the
        books that were added."""
        statement = select(Book).where(
            Book.id.in_(book_ids), Book.publisher_id == group.publisher_id
        )
        books = list(session.scalars(statement).all())
        for book in books:
            book.group_id = group.id
        session.commit()
        return books

    def remove_book(self, session: Session, group_id: int, book_id: int) -> bool:
        """Detach a single book from the group. Returns True if it was a member."""
        book = session.get(Book, book_id)
        if book is None or book.group_id != group_id:
            return False
        book.group_id = None
        session.commit()
        return True
