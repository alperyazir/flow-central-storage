"""Pydantic schemas for publisher metadata payloads."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class PublisherBase(BaseModel):
    """Shared attributes required for publisher metadata operations."""

    name: str = Field(..., max_length=255)
    slug: str | None = Field(default=None, max_length=255)
    display_name: str | None = Field(default=None, max_length=255)
    description: str | None = Field(default=None)
    logo_url: str | None = Field(default=None, max_length=512)
    contact_email: str | None = Field(default=None, max_length=255)
    status: str = Field(default="active", max_length=20)


class PublisherCreate(PublisherBase):
    """Payload for creating a new publisher record."""

    pass


class PublisherUpdate(BaseModel):
    """Payload for updating existing publisher metadata."""

    name: str | None = Field(default=None, max_length=255)
    slug: str | None = Field(default=None, max_length=255)
    display_name: str | None = Field(default=None, max_length=255)
    description: str | None = Field(default=None)
    logo_url: str | None = Field(default=None, max_length=512)
    contact_email: str | None = Field(default=None, max_length=255)
    status: str | None = Field(default=None, max_length=20)


class PublisherRead(PublisherBase):
    """Representation returned by the API for persisted publisher records."""

    id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PublisherWithBooks(PublisherRead):
    """Publisher with nested list of books.

    Note: books field uses Any to avoid circular import with BookRead.
    At runtime, this will contain BookRead-compatible dictionaries.
    """

    books: list[Any] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class PublisherSyncItem(BaseModel):
    """Publisher data for sync/webhook purposes (LMS integration)."""

    id: int
    name: str
    slug: str
    contact_email: str | None = None
    logo_url: str | None = None

    model_config = ConfigDict(from_attributes=True)


class PublisherListItem(PublisherRead):
    """Publisher item in list response with computed logo_url."""

    logo_url: str | None = None


class PublisherListResponse(BaseModel):
    """Paginated publisher list response."""

    items: list[PublisherListItem]
    total: int
