"""Endpoints for application-wide settings."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.auth import require_admin as _require_admin
from app.db import get_db
from app.repositories.app_setting import AppSettingRepository
from app.schemas.setting import DEFAULT_SETTINGS, AppSettingsRead, AppSettingsUpdate

router = APIRouter(prefix="/settings", tags=["Settings"])
_bearer_scheme = HTTPBearer(auto_error=True)
_settings_repository = AppSettingRepository()
logger = logging.getLogger(__name__)


def _current_settings(db: Session) -> AppSettingsRead:
    """Return the full settings object (defaults overlaid with stored values)."""
    merged = {**DEFAULT_SETTINGS, **_settings_repository.get_all(db)}
    return AppSettingsRead(**{key: merged[key] for key in DEFAULT_SETTINGS})


@router.get("", response_model=AppSettingsRead)
def get_app_settings(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> AppSettingsRead:
    """Return all application settings with defaults applied."""
    _require_admin(credentials, db)
    return _current_settings(db)


@router.put("", response_model=AppSettingsRead)
def update_app_settings(
    payload: AppSettingsUpdate,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> AppSettingsRead:
    """Update one or more application settings; omitted fields are unchanged."""
    _require_admin(credentials, db)
    updates = payload.model_dump(exclude_none=True)
    if updates:
        _settings_repository.set_many(db, updates)
    return _current_settings(db)
