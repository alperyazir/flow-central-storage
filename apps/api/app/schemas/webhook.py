"""Pydantic schemas for webhook subscriptions and delivery logs."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.webhook import WebhookDeliveryStatus, WebhookEventType


class WebhookSubscriptionBase(BaseModel):
    """Shared attributes for webhook subscription operations."""

    url: str = Field(..., max_length=512, description="Webhook endpoint URL")
    secret: str = Field(..., max_length=255, min_length=16, description="Webhook signing secret")
    description: str | None = Field(default=None, max_length=512)
    is_active: bool = Field(default=True)
    event_types: str | None = Field(
        default=None,
        max_length=255,
        description="Comma-separated list of event types (e.g., 'book.created,book.updated'). Empty = all events",
    )


class WebhookSubscriptionCreate(WebhookSubscriptionBase):
    """Payload for creating a new webhook subscription."""

    pass


class WebhookSubscriptionUpdate(BaseModel):
    """Payload for updating an existing webhook subscription."""

    url: str | None = Field(default=None, max_length=512)
    secret: str | None = Field(default=None, max_length=255, min_length=16)
    description: str | None = Field(default=None, max_length=512)
    is_active: bool | None = Field(default=None)
    event_types: str | None = Field(default=None, max_length=255)


class WebhookSubscriptionRead(WebhookSubscriptionBase):
    """Representation returned by the API for persisted webhook subscriptions."""

    id: int
    created_at: datetime
    updated_at: datetime

    # Mask the secret in responses (show only first/last 4 characters)
    @property
    def masked_secret(self) -> str:
        """Return masked secret for display."""
        if len(self.secret) <= 8:
            return "***"
        return f"{self.secret[:4]}***{self.secret[-4:]}"

    model_config = ConfigDict(from_attributes=True)


class WebhookDeliveryLogRead(BaseModel):
    """Representation of a webhook delivery log entry."""

    id: int
    subscription_id: int
    event_type: WebhookEventType
    payload: str
    status: WebhookDeliveryStatus
    response_status: int | None
    response_body: str | None
    error_message: str | None
    attempt_count: int
    created_at: datetime
    delivered_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


# Webhook event payload schemas (what gets sent to the webhook URL)


class WebhookEventBookData(BaseModel):
    """Book data included in webhook events."""

    id: int
    book_name: str
    book_title: str | None
    publisher: str
    language: str
    category: str | None
    status: str
    # Bumped every time the book's content is rewritten; consumers can skip a
    # re-import when it has not moved. Optional for older payloads.
    content_version: int | None = None


class WebhookEventPublisherData(BaseModel):
    """Publisher data included in webhook events."""

    id: int
    name: str
    contact_email: str | None = None
    logo_url: str | None = None


class WebhookEventPayload(BaseModel):
    """Webhook event payload sent to subscribers."""

    event: WebhookEventType
    timestamp: datetime
    data: WebhookEventBookData | WebhookEventPublisherData
