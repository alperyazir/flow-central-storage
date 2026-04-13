"""Database access helpers for publisher entities."""

from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.slugify import slugify
from app.models.publisher import Publisher
from app.repositories.base import BaseRepository

logger = logging.getLogger(__name__)


class PublisherRepository(BaseRepository[Publisher]):
    """Repository for interacting with publisher records."""

    def __init__(self) -> None:
        super().__init__(model=Publisher)

    def list_all(self, session: Session) -> list[Publisher]:
        """Return all publishers."""
        statement = select(Publisher)
        return list(session.scalars(statement).all())

    def list_paginated(self, session: Session, skip: int = 0, limit: int = 100) -> list[Publisher]:
        """Return publishers with pagination."""
        statement = select(Publisher).offset(skip).limit(limit)
        return list(session.scalars(statement).all())

    def count(self, session: Session) -> int:
        """Return total count of publishers."""
        statement = select(func.count()).select_from(Publisher)
        return session.execute(statement).scalar() or 0

    def list_active(self, session: Session, skip: int = 0, limit: int = 100) -> list[Publisher]:
        """Return only active publishers with pagination."""
        statement = select(Publisher).where(Publisher.status == "active").offset(skip).limit(limit)
        return list(session.scalars(statement).all())

    def count_active(self, session: Session) -> int:
        """Return count of active publishers."""
        statement = select(func.count()).select_from(Publisher).where(Publisher.status == "active")
        return session.execute(statement).scalar() or 0

    def list_archived(self, session: Session, skip: int = 0, limit: int = 100) -> list[Publisher]:
        """Return archived (trashed) publishers with pagination."""
        statement = select(Publisher).where(Publisher.status == "inactive").offset(skip).limit(limit)
        return list(session.scalars(statement).all())

    def count_archived(self, session: Session) -> int:
        """Return count of archived publishers."""
        statement = select(func.count()).select_from(Publisher).where(Publisher.status == "inactive")
        return session.execute(statement).scalar() or 0

    def get_by_name(self, session: Session, name: str) -> Publisher | None:
        """Fetch a publisher by unique name."""
        statement = select(Publisher).where(Publisher.name == name)
        result = session.execute(statement)
        return result.scalars().first()

    def get_by_slug(self, session: Session, slug: str) -> Publisher | None:
        """Fetch a publisher by slug."""
        statement = select(Publisher).where(Publisher.slug == slug)
        return session.scalars(statement).first()

    def _generate_unique_slug(self, session: Session, name: str, exclude_id: int | None = None) -> str:
        """Generate a unique slug from publisher name."""
        base_slug = slugify(name)
        slug = base_slug
        counter = 2
        while True:
            stmt = select(Publisher).where(Publisher.slug == slug)
            if exclude_id is not None:
                stmt = stmt.where(Publisher.id != exclude_id)
            if session.scalars(stmt).first() is None:
                return slug
            slug = f"{base_slug}-{counter}"
            counter += 1

    def get_or_create_by_name(self, session: Session, name: str) -> Publisher:
        """Get existing publisher by name or create a new one."""
        publisher = self.get_by_name(session, name)
        if publisher is not None:
            return publisher

        # Create new publisher with name as display_name
        slug = self._generate_unique_slug(session, name)
        publisher = Publisher(name=name, display_name=name, slug=slug)
        return self.add(session, publisher)

    def get_with_books(self, session: Session, publisher_id: int) -> Publisher | None:
        """Fetch a publisher with books eager-loaded to avoid N+1 queries."""
        statement = select(Publisher).options(selectinload(Publisher.books)).where(Publisher.id == publisher_id)
        return session.scalars(statement).first()

    def create(self, session: Session, *, data: dict[str, object]) -> Publisher:
        """Create a new publisher record. Auto-generates slug from name if not provided."""
        if not data.get("slug"):
            data["slug"] = self._generate_unique_slug(session, str(data["name"]))
        publisher = Publisher(**data)
        created = self.add(session, publisher)
        session.commit()
        return created

    def update(self, session: Session, publisher: Publisher, *, data: dict[str, object]) -> Publisher:
        """Update an existing publisher."""
        for field, value in data.items():
            setattr(publisher, field, value)
        session.flush()
        session.refresh(publisher)
        session.commit()
        return publisher

    def delete(self, session: Session, publisher: Publisher) -> None:
        """Permanently remove a publisher record from the database.

        This will also delete all books associated with the publisher due to
        cascade delete configured on the relationship.
        """
        # Load books to ensure cascade delete works properly
        session.refresh(publisher)
        book_count = len(publisher.books)

        logger.info(f"Deleting publisher '{publisher.name}' (ID: {publisher.id}) and {book_count} associated books")

        # Delete publisher (cascade will delete all books)
        session.delete(publisher)
        session.commit()

        logger.info(f"Successfully deleted publisher '{publisher.name}' and {book_count} books")

    def get_ai_settings(self, publisher: Publisher) -> dict[str, object]:
        """Get AI processing settings for a publisher.

        Returns a dict with ai_auto_process_enabled, ai_processing_priority, ai_audio_languages.
        None values indicate "use global default".
        """
        return {
            "ai_auto_process_enabled": publisher.ai_auto_process_enabled,
            "ai_processing_priority": publisher.ai_processing_priority,
            "ai_audio_languages": publisher.ai_audio_languages,
        }

    def update_ai_settings(
        self,
        session: Session,
        publisher: Publisher,
        *,
        ai_auto_process_enabled: bool | None = None,
        ai_processing_priority: str | None = None,
        ai_audio_languages: str | None = None,
    ) -> Publisher:
        """Update AI processing settings for a publisher.

        Pass None to reset a setting to "use global default".
        """
        publisher.ai_auto_process_enabled = ai_auto_process_enabled
        publisher.ai_processing_priority = ai_processing_priority
        publisher.ai_audio_languages = ai_audio_languages
        session.flush()
        session.refresh(publisher)
        session.commit()
        return publisher
