"""Pydantic schemas for book group payloads."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.book import BookRead


class BookGroupBase(BaseModel):
    """Shared attributes for book group operations."""

    name: str = Field(..., min_length=1, max_length=255)


class BookGroupCreate(BookGroupBase):
    """Payload for creating a book group."""

    publisher_id: int = Field(..., description="Publisher the group belongs to")


class BookGroupUpdate(BaseModel):
    """Payload for updating a book group (rename)."""

    name: str | None = Field(default=None, min_length=1, max_length=255)


class BookGroupAddBooks(BaseModel):
    """Payload for adding books to a group."""

    book_ids: list[int] = Field(..., min_length=1)


class BookGroupRead(BookGroupBase):
    """Group metadata returned by the API (without member books)."""

    id: int
    publisher_id: int
    book_count: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class BookGroupWithBooks(BookGroupRead):
    """Group plus its member books — consumed by the Learn online viewer to
    offer auto-transition between sibling books."""

    books: list[BookRead] = Field(default_factory=list)


class BookGroupListResponse(BaseModel):
    """List of book groups."""

    groups: list[BookGroupRead] = Field(default_factory=list)
