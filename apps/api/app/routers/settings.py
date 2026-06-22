"""Endpoints for application-wide settings."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import decode_access_token, verify_api_key_from_db
from app.db import get_db
from app.repositories.app_setting import AppSettingRepository
from app.repositories.user import UserRepository
from app.schemas.setting import DEFAULT_SETTINGS, AppSettingsRead, AppSettingsUpdate

router = APIRouter(prefix="/settings", tags=["Settings"])
_bearer_scheme = HTTPBearer(auto_error=True)
_settings_repository = AppSettingRepository()
_user_repository = UserRepository()
logger = logging.getLogger(__name__)


def _require_admin(credentials: HTTPAuthorizationCredentials, db: Session) -> int:
    """Validate JWT token or API key and ensure authentication is valid."""

    token = credentials.credentials

    # Try JWT first
    try:
        payload = decode_access_token(token, settings=get_settings())
        subject = payload.get("sub")
        if subject is not None:
            try:
                user_id = int(subject)
                user = _user_repository.get(db, user_id)
                if user is not None:
                    return user_id
            except (TypeError, ValueError):
                pass
    except ValueError:
        pass  # JWT failed, try API key

    # Try API key
    api_key_info = verify_api_key_from_db(token, db)
    if api_key_info is not None:
        return -1

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid token",
    )


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
