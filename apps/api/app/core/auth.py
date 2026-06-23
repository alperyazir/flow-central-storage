"""Shared admin authentication used by router endpoints.

Replaces the ``_require_admin`` helper that was copy-pasted into every CRUD
router (with subtle drift — some checked that the JWT's user still exists,
some didn't). This is the single canonical version: it validates the JWT's
user against the DB, falling back to an API key.
"""

from __future__ import annotations

from fastapi import HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import decode_access_token, verify_api_key_from_db
from app.repositories.user import UserRepository

_user_repository = UserRepository()


def require_admin(credentials: HTTPAuthorizationCredentials, db: Session) -> int:
    """Validate a JWT token or API key.

    Returns the authenticated user's id, or ``-1`` when authenticated via API
    key. Raises ``401`` if neither a valid (existing-user) JWT nor a valid API
    key is presented.
    """
    token = credentials.credentials

    # Try JWT first.
    try:
        payload = decode_access_token(token, settings=get_settings())
        subject = payload.get("sub")
        if subject is not None:
            try:
                user_id = int(subject)
                if _user_repository.get(db, user_id) is not None:
                    return user_id
            except (TypeError, ValueError):
                pass
    except ValueError:
        pass  # JWT failed, try API key.

    # Fall back to API key.
    if verify_api_key_from_db(token, db) is not None:
        return -1

    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
