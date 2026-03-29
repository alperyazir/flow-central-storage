"""Authentication endpoints for the Flow Central API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import create_access_token, decode_access_token, verify_password
from app.db import get_db
from app.repositories.user import UserRepository
from app.schemas.auth import LoginRequest, SessionResponse, TokenResponse

router = APIRouter(prefix="/auth", tags=["Auth"])
_user_repository = UserRepository()
_bearer_scheme = HTTPBearer(auto_error=True)


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    """Authenticate an administrator and return a JWT access token."""

    email = payload.email.strip().lower()
    user = _user_repository.get_by_email(db, email)
    if not user or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    settings = get_settings()
    token = create_access_token(subject=str(user.id), settings=settings)
    return TokenResponse(access_token=token)


@router.get("/session", response_model=SessionResponse)
def read_session(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> SessionResponse:
    """Validate the provided access token and return session details."""

    try:
        payload = decode_access_token(credentials.credentials, settings=get_settings())
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
        ) from exc

    subject = payload.get("sub")
    try:
        user_id = int(subject)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
        ) from exc

    user = _user_repository.get(db, user_id)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    return SessionResponse(user_id=user.id, email=user.email)
