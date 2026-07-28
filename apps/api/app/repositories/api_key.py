"""Repository for API key database operations."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.api_key import ApiKey
from app.repositories.base import BaseRepository


class ApiKeyRepository(BaseRepository[ApiKey]):
    """Manages API key persistence and retrieval."""

    def __init__(self):
        super().__init__(ApiKey)

    def create(self, session: Session, data: dict) -> ApiKey:
        """Create a new API key record."""
        api_key = ApiKey(**data)
        return self.add(session, api_key)

    def get_by_hash(self, session: Session, key_hash: str) -> ApiKey | None:
        """Retrieve an API key by its hash."""
        stmt = select(ApiKey).where(ApiKey.key_hash == key_hash)
        result = session.execute(stmt)
        return result.scalar_one_or_none()

    def list_all_keys(self, session: Session) -> list[ApiKey]:
        """Return all API keys."""
        return self.list_all(session)

    def list_active_by_prefix(self, session: Session, key_prefix: str) -> list[ApiKey]:
        """Return active API keys whose stored prefix matches ``key_prefix``.

        Authentication uses this to narrow the candidate set to (normally) a
        single row, so only one bcrypt comparison is needed per request instead
        of one per key in the table.
        """
        stmt = select(ApiKey).where(
            ApiKey.key_prefix == key_prefix,
            ApiKey.is_active.is_(True),
        )
        return list(session.execute(stmt).scalars().all())

    def list_active_keys(self, session: Session) -> list[ApiKey]:
        """Return every active API key."""
        stmt = select(ApiKey).where(ApiKey.is_active.is_(True))
        return list(session.execute(stmt).scalars().all())

    def update_last_used(self, session: Session, api_key: ApiKey) -> ApiKey:
        """Update the last_used_at timestamp for an API key.

        No ``refresh`` here on purpose: the caller already holds the value it
        needs, and a refresh costs an extra round-trip on the hot auth path.
        """
        api_key.last_used_at = datetime.now(timezone.utc)
        session.flush()
        return api_key

    def revoke(self, session: Session, api_key: ApiKey) -> ApiKey:
        """Revoke an API key by setting is_active to False."""
        api_key.is_active = False
        session.flush()
        session.refresh(api_key)
        return api_key
