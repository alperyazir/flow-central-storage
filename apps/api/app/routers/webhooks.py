"""Webhook subscription management endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import decode_access_token, verify_api_key_from_db
from app.db import get_db
from app.repositories.user import UserRepository
from app.repositories.webhook import WebhookDeliveryLogRepository, WebhookSubscriptionRepository
from app.schemas.webhook import (
    WebhookDeliveryLogRead,
    WebhookSubscriptionCreate,
    WebhookSubscriptionRead,
    WebhookSubscriptionUpdate,
)

router = APIRouter(prefix="/webhooks", tags=["Webhooks"])
_bearer_scheme = HTTPBearer(auto_error=True)
_user_repository = UserRepository()
_subscription_repository = WebhookSubscriptionRepository()
_delivery_log_repository = WebhookDeliveryLogRepository()


def _require_auth(credentials: HTTPAuthorizationCredentials, db: Session) -> int:
    """Validate JWT or API key. Returns user_id or -1 for API key auth."""

    token = credentials.credentials

    # Try JWT first
    try:
        payload = decode_access_token(token, settings=get_settings())
        subject = payload.get("sub")
        if subject is not None:
            user_id = int(subject)
            user = _user_repository.get(db, user_id)
            if user is not None:
                return user_id
    except (ValueError, TypeError):
        pass

    # Try API key
    api_key_info = verify_api_key_from_db(token, db)
    if api_key_info is not None:
        return -1

    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")


@router.post("/", response_model=WebhookSubscriptionRead, status_code=status.HTTP_201_CREATED)
def create_webhook_subscription(
    payload: WebhookSubscriptionCreate,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> WebhookSubscriptionRead:
    """Create a new webhook subscription."""

    _require_auth(credentials, db)

    subscription = _subscription_repository.create(db, data=payload.model_dump())
    return WebhookSubscriptionRead.model_validate(subscription)


@router.get("/", response_model=list[WebhookSubscriptionRead])
def list_webhook_subscriptions(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> list[WebhookSubscriptionRead]:
    """List all webhook subscriptions."""

    _require_auth(credentials, db)

    subscriptions = _subscription_repository.list_all(db)
    return [WebhookSubscriptionRead.model_validate(sub) for sub in subscriptions]


@router.get("/{subscription_id}", response_model=WebhookSubscriptionRead)
def get_webhook_subscription(
    subscription_id: int,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> WebhookSubscriptionRead:
    """Get a webhook subscription by ID."""

    _require_auth(credentials, db)

    subscription = _subscription_repository.get_by_id(db, subscription_id)
    if subscription is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Webhook subscription not found")

    return WebhookSubscriptionRead.model_validate(subscription)


@router.put("/{subscription_id}", response_model=WebhookSubscriptionRead)
def update_webhook_subscription(
    subscription_id: int,
    payload: WebhookSubscriptionUpdate,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> WebhookSubscriptionRead:
    """Update a webhook subscription."""

    _require_auth(credentials, db)

    subscription = _subscription_repository.get_by_id(db, subscription_id)
    if subscription is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Webhook subscription not found")

    update_data = payload.model_dump(exclude_unset=True)
    if not update_data:
        return WebhookSubscriptionRead.model_validate(subscription)

    updated = _subscription_repository.update(db, subscription, data=update_data)
    return WebhookSubscriptionRead.model_validate(updated)


@router.delete("/{subscription_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def delete_webhook_subscription(
    subscription_id: int,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
):
    """Delete a webhook subscription."""

    _require_auth(credentials, db)

    subscription = _subscription_repository.get_by_id(db, subscription_id)
    if subscription is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Webhook subscription not found")

    _subscription_repository.delete(db, subscription)


@router.get("/{subscription_id}/logs", response_model=list[WebhookDeliveryLogRead])
def list_webhook_delivery_logs(
    subscription_id: int,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> list[WebhookDeliveryLogRead]:
    """List delivery logs for a webhook subscription."""

    _require_auth(credentials, db)

    # Verify subscription exists
    subscription = _subscription_repository.get_by_id(db, subscription_id)
    if subscription is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Webhook subscription not found")

    logs = _delivery_log_repository.list_by_subscription(db, subscription_id)
    return [WebhookDeliveryLogRead.model_validate(log) for log in logs]
