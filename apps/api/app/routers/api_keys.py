"""API key management endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import (
    decode_access_token,
    generate_api_key,
    get_api_key_prefix,
    hash_api_key,
    invalidate_api_key_cache,
)
from app.db import get_db
from app.repositories.api_key import ApiKeyRepository
from app.repositories.user import UserRepository
from app.schemas.api_key import ApiKeyCreate, ApiKeyCreated, ApiKeyListResponse, ApiKeyRead

router = APIRouter(prefix="/api-keys", tags=["API Keys"])
_bearer_scheme = HTTPBearer(auto_error=True)
_api_key_repository = ApiKeyRepository()
_user_repository = UserRepository()


def _require_admin(credentials: HTTPAuthorizationCredentials, db: Session) -> int:
    """Validate JWT token and ensure the referenced administrator exists."""

    token = credentials.credentials
    try:
        payload = decode_access_token(token, settings=get_settings())
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        ) from exc

    subject = payload.get("sub")
    if subject is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    try:
        user_id = int(subject)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from exc

    user = _user_repository.get(db, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    return user_id


@router.post("/", response_model=ApiKeyCreated, status_code=status.HTTP_201_CREATED)
def create_api_key(
    payload: ApiKeyCreate,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> ApiKeyCreated:
    """Create a new API key (admin only)."""

    _require_admin(credentials, db)

    # Generate the API key
    # Extract environment and service from the name (default to 'prod' and 'service')
    # Simple heuristic: if name contains 'dev' or 'test', use that environment
    name_lower = payload.name.lower()
    if "dev" in name_lower or "development" in name_lower:
        environment = "dev"
    elif "test" in name_lower or "staging" in name_lower:
        environment = "test"
    else:
        environment = "prod"

    # Extract service name from the API key name (simplified version)
    service = "".join(c for c in payload.name.lower().split()[0] if c.isalnum())[:10]
    if not service:
        service = "service"

    api_key = generate_api_key(environment, service)
    key_hash = hash_api_key(api_key)
    key_prefix = get_api_key_prefix(api_key)

    # Create the API key record
    api_key_data = {
        "key_hash": key_hash,
        "key_prefix": key_prefix,
        "name": payload.name,
        "description": payload.description,
        "expires_at": payload.expires_at,
        "rate_limit": payload.rate_limit,
        "is_active": True,
    }

    created_key = _api_key_repository.create(db, api_key_data)
    db.commit()
    invalidate_api_key_cache()

    return ApiKeyCreated(
        id=created_key.id,
        key=api_key,  # ONLY shown here, never again
        name=created_key.name,
        created_at=created_key.created_at,
        expires_at=created_key.expires_at,
        is_active=created_key.is_active,
    )


@router.get("/", response_model=ApiKeyListResponse)
def list_api_keys(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> ApiKeyListResponse:
    """List all API keys (admin only)."""

    _require_admin(credentials, db)

    api_keys = _api_key_repository.list_all_keys(db)

    return ApiKeyListResponse(
        api_keys=[
            ApiKeyRead(
                id=key.id,
                key_prefix=key.key_prefix + "...",
                name=key.name,
                created_at=key.created_at,
                last_used_at=key.last_used_at,
                expires_at=key.expires_at,
                is_active=key.is_active,
                rate_limit=key.rate_limit,
            )
            for key in api_keys
        ]
    )


@router.delete("/{key_id}")
def revoke_api_key(
    key_id: int,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
):
    """Revoke an API key (admin only)."""

    _require_admin(credentials, db)

    api_key = _api_key_repository.get(db, key_id)
    if api_key is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="API key not found",
        )

    _api_key_repository.revoke(db, api_key)
    db.commit()
    invalidate_api_key_cache()

    return {"status": "revoked", "id": key_id}
