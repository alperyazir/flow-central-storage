"""Database access helpers for book metadata."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.book import Book, BookStatusEnum
from app.models.publisher import Publisher
from app.repositories.base import BaseRepository


class BookRepository(BaseRepository[Book]):
    """Repository for interacting with book metadata records."""

    def __init__(self) -> None:
        super().__init__(model=Book)

    def create(self, session: Session, *, data: dict[str, object]) -> Book:
        book = Book(**data)
        created = self.add(session, book)
        session.commit()
        return created

    def list_all_books(self, session: Session, *, skip: int = 0, limit: int = 50) -> list[Book]:
        """List all books that are not archived (not in trash)."""
        statement = select(Book).where(Book.status != BookStatusEnum.ARCHIVED).offset(skip).limit(limit)
        return list(session.scalars(statement).all())

    def list_by_publisher_id(
        self, session: Session, publisher_id: int, *, skip: int = 0, limit: int = 50
    ) -> list[Book]:
        """List all non-archived books for a specific publisher."""
        statement = (
            select(Book)
            .where(
                Book.publisher_id == publisher_id,
                Book.status != BookStatusEnum.ARCHIVED,
            )
            .offset(skip)
            .limit(limit)
        )
        return list(session.scalars(statement).all())

    def get_by_id(self, session: Session, identifier: int) -> Book | None:
        return self.get(session, identifier)

    def get_by_publisher_id_and_name(self, session: Session, *, publisher_id: int, book_name: str) -> Book | None:
        """Find a book by publisher ID and book name."""
        statement = select(Book).where(
            Book.publisher_id == publisher_id,
            Book.book_name == book_name,
        )
        result = session.execute(statement)
        return result.scalars().first()

    def get_by_publisher_id_and_book_name(self, session: Session, publisher_id: int, book_name: str) -> Book | None:
        """Find a book by publisher ID and book name."""
        statement = select(Book).where(
            Book.publisher_id == publisher_id,
            Book.book_name == book_name,
        )
        return session.scalars(statement).first()

    def get_by_publisher_name_and_book_name(
        self, session: Session, *, publisher_name: str, book_name: str
    ) -> Book | None:
        """Find a book by publisher name and book name (joins publisher table)."""
        statement = (
            select(Book)
            .join(Publisher)
            .where(
                Publisher.name == publisher_name,
                Book.book_name == book_name,
            )
        )
        result = session.execute(statement)
        return result.scalars().first()

    def update(self, session: Session, book: Book, *, data: dict[str, object]) -> Book:
        for field, value in data.items():
            setattr(book, field, value)
        session.flush()
        session.refresh(book)
        session.commit()
        return book

    def archive(self, session: Session, book: Book) -> Book:
        """Mark a book as archived and persist the change."""

        book.status = BookStatusEnum.ARCHIVED
        session.flush()
        session.refresh(book)
        session.commit()
        return book

    def restore(self, session: Session, book: Book) -> Book:
        """Restore an archived book to the published state."""

        if book.status != BookStatusEnum.ARCHIVED:
            raise ValueError("Book is not archived and cannot be restored")

        book.status = BookStatusEnum.PUBLISHED
        session.flush()
        session.refresh(book)
        session.commit()
        return book

    def delete(self, session: Session, book: Book) -> None:
        """Permanently remove a book record from the database."""

        session.delete(book)
        session.commit()
