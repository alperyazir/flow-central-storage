"""ORM model for application-wide key/value settings."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AppSetting(Base):
    """A single application setting stored as a JSON-encoded key/value pair.

    Settings are a flat key/value store rather than a typed columns table so
    new toggles can be added without a migration. The known keys and their
    defaults live in ``app.schemas.setting.DEFAULT_SETTINGS``; rows here are
    only the stored *overrides* of those defaults.
    """

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
