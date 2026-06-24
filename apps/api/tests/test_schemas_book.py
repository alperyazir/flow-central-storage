"""Validation tests for book Pydantic schemas."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.models.book import Book, BookStatusEnum, BookTypeEnum
from app.models.publisher import Publisher
from app.schemas.book import BookCreate, BookRead, BookUpdate


def test_book_create_defaults() -> None:
    payload = {
        "publisher": "Dream Press",
        "book_name": "Midnight Stories",
        "language": "en",
        "category": "fiction",
    }
    schema = BookCreate(**payload)
    assert schema.status is BookStatusEnum.DRAFT


def test_book_update_supports_partial_mutation() -> None:
    schema = BookUpdate(status=BookStatusEnum.PUBLISHED)
    assert schema.status is BookStatusEnum.PUBLISHED
    assert schema.publisher is None


def test_book_update_rejects_invalid_status() -> None:
    with pytest.raises(ValueError):
        BookUpdate(status="invalid")  # type: ignore[arg-type]


def test_book_read_serializes_from_orm() -> None:
    # Create a real Publisher object for the relationship
    publisher = Publisher(
        id=1,
        name="Dream Press",
        display_name="Dream Press",
        status="active",
    )

    book = Book(
        id=1,
        publisher_id=1,
        book_name="Midnight Stories",
        language="en",
        category="fiction",
        status=BookStatusEnum.DRAFT,
        book_type=BookTypeEnum.STANDARD,
    )
    # Set the relationship manually for testing (bypass SQLAlchemy instrumentation)
    object.__setattr__(book, "publisher_rel", publisher)
    now = datetime.now(timezone.utc)
    book.created_at = now
    book.updated_at = now
    # Persisted books always have an int here (column server_default=1); set it
    # explicitly on this transient instance since the column default only
    # applies on flush.
    book.content_version = 1

    schema = BookRead.model_validate(book)
    assert schema.id == 1
    assert schema.publisher == "Dream Press"
    assert schema.created_at == now
    assert schema.status is BookStatusEnum.DRAFT
