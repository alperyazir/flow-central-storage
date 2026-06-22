"""Database access helpers for application settings."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.app_setting import AppSetting
from app.repositories.base import BaseRepository


class AppSettingRepository(BaseRepository[AppSetting]):
    """Repository for the flat key/value application settings store."""

    def __init__(self) -> None:
        super().__init__(model=AppSetting)

    def get_all(self, session: Session) -> dict[str, Any]:
        """Return all stored setting overrides as a plain ``{key: value}`` dict."""
        rows = session.scalars(select(AppSetting)).all()
        return {row.key: row.value for row in rows}

    def get_value(self, session: Session, key: str, default: Any = None) -> Any:
        """Return the stored value for ``key`` or ``default`` when unset."""
        row = session.get(AppSetting, key)
        return row.value if row is not None else default

    def set_value(self, session: Session, key: str, value: Any) -> None:
        """Upsert a single setting (does not commit)."""
        row = session.get(AppSetting, key)
        if row is None:
            session.add(AppSetting(key=key, value=value))
        else:
            row.value = value
        session.flush()

    def set_many(self, session: Session, values: dict[str, Any]) -> None:
        """Upsert several settings and commit."""
        for key, value in values.items():
            self.set_value(session, key, value)
        session.commit()
