"""Webhook service for sending webhook events to subscribers."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy.orm import Session

from app.models.book import Book
from app.models.publisher import Publisher
from app.models.webhook import WebhookDeliveryStatus, WebhookEventType
from app.repositories.webhook import WebhookDeliveryLogRepository, WebhookSubscriptionRepository
from app.schemas.webhook import WebhookEventBookData, WebhookEventPayload, WebhookEventPublisherData

logger = logging.getLogger(__name__)


class WebhookService:
    """Service for managing and delivering webhook events."""

    def __init__(self) -> None:
        self.subscription_repo = WebhookSubscriptionRepository()
        self.delivery_log_repo = WebhookDeliveryLogRepository()

    def generate_signature(self, payload: str, secret: str) -> str:
        """
        Generate HMAC-SHA256 signature for webhook payload.

        Args:
            payload: JSON string of the webhook payload
            secret: Webhook secret key

        Returns:
            Signature in format "sha256=<hex_digest>"
        """
        signature = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
        return f"sha256={signature}"

    async def send_webhook(self, url: str, payload: str, signature: str, timeout: int = 30) -> tuple[int, str]:
        """
        Send webhook HTTP POST request.

        Args:
            url: Webhook endpoint URL
            payload: Webhook payload as JSON string
            signature: HMAC signature
            timeout: Request timeout in seconds

        Returns:
            Tuple of (status_code, response_body)

        Raises:
            Exception: If HTTP request fails
        """
        headers = {
            "Content-Type": "application/json",
            "X-Webhook-Signature": signature,
            "User-Agent": "Dream-Central-Storage-Webhook/1.0",
        }

        logger.debug(f"[WEBHOOK] Sending request to {url} with headers: {headers}")
        logger.debug(f"[WEBHOOK] Signature: {signature}")

        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, content=payload, headers=headers)
            logger.info(f"[WEBHOOK] Response from {url}: {response.status_code}, headers: {dict(response.headers)}")
            return response.status_code, response.text

    async def deliver_webhook(
        self, session: Session, subscription_id: int, event_type: WebhookEventType, book: Book
    ) -> None:
        """
        Deliver a webhook event to a specific subscription with retry logic.

        Args:
            session: Database session
            subscription_id: ID of webhook subscription
            event_type: Type of webhook event
            book: Book model instance

        Returns:
            None (logs delivery status to database)
        """
        logger.info(f"[WEBHOOK] deliver_webhook called with event_type={event_type.value}")
        subscription = self.subscription_repo.get_by_id(session, subscription_id)
        if not subscription or not subscription.is_active:
            logger.warning(f"[WEBHOOK] Skipping inactive subscription {subscription_id}")
            return

        # Check if subscription wants this event type
        if subscription.event_types:
            subscribed_events = [e.strip() for e in subscription.event_types.split(",")]
            if event_type.value not in subscribed_events:
                logger.info(
                    f"[WEBHOOK] Subscription {subscription_id} ({subscription.url}) not interested in {event_type.value} (subscribed to: {subscription.event_types})"
                )
                return

        # Build webhook payload
        event_data = WebhookEventBookData(
            id=book.id,
            book_name=book.book_name,
            book_title=book.book_title or book.book_name,
            publisher=book.publisher,
            language=book.language,
            category=book.category or "",
            status=book.status.value,
            content_version=book.content_version if isinstance(book.content_version, int) else None,
        )

        webhook_payload = WebhookEventPayload(event=event_type, timestamp=datetime.now(timezone.utc), data=event_data)

        payload_json = webhook_payload.model_dump_json()

        # Generate signature
        signature = self.generate_signature(payload_json, subscription.secret)

        # Create delivery log
        # Use .value to get string representation (columns are now String type, not Enum)
        logger.info(f"[WEBHOOK] Creating delivery log with event_type={event_type.value}, status=pending")
        log = self.delivery_log_repo.create(
            session,
            data={
                "subscription_id": subscription_id,
                "event_type": event_type.value,  # Use .value to get "book.created" not "BOOK_CREATED"
                "payload": payload_json,
                "status": WebhookDeliveryStatus.PENDING.value,  # Use .value to get "pending"
                "attempt_count": 0,
            },
        )

        # Attempt delivery with retries
        max_retries = 3
        backoff_seconds = [1, 2, 4]  # Exponential backoff

        for attempt in range(max_retries):
            try:
                log.attempt_count = attempt + 1
                session.commit()

                logger.info(
                    f"[WEBHOOK] Sending webhook {log.id} to {subscription.url} (attempt {attempt + 1}/{max_retries})"
                )
                logger.debug(f"[WEBHOOK] Payload: {payload_json[:500]}")

                status_code, response_body = await self.send_webhook(subscription.url, payload_json, signature)

                # Update log with response
                log.response_status = status_code
                log.response_body = response_body[:1000]  # Limit stored response size
                log.delivered_at = datetime.now(timezone.utc)

                if 200 <= status_code < 300:
                    log.status = WebhookDeliveryStatus.SUCCESS.value
                    session.commit()
                    logger.info(
                        f"[WEBHOOK] Webhook {log.id} delivered successfully to {subscription.url} (status {status_code})"
                    )
                    return
                else:
                    error_msg = f"HTTP {status_code}: {response_body[:200]}"
                    log.error_message = error_msg
                    logger.warning(
                        f"[WEBHOOK] Webhook {log.id} failed with status {status_code}, response: {response_body[:200]}"
                    )

            except Exception as e:
                error_msg = f"Exception: {str(e)}"
                log.error_message = error_msg
                logger.error(f"[WEBHOOK] Webhook {log.id} delivery error to {subscription.url}: {e}", exc_info=True)

            # Retry with backoff if not last attempt
            if attempt < max_retries - 1:
                logger.info(f"[WEBHOOK] Retrying webhook {log.id} in {backoff_seconds[attempt]}s...")
                await asyncio.sleep(backoff_seconds[attempt])
            else:
                # Mark as failed after all retries
                log.status = WebhookDeliveryStatus.FAILED.value
                session.commit()
                logger.error(
                    f"[WEBHOOK] Webhook {log.id} permanently failed after {max_retries} attempts to {subscription.url}"
                )

    async def broadcast_event(self, session: Session, event_type: WebhookEventType, book: Book) -> None:
        """
        Broadcast a webhook event to all active subscriptions.

        Args:
            session: Database session
            event_type: Type of webhook event
            book: Book model instance

        Returns:
            None (triggers async delivery tasks)
        """
        logger.info(f"[WEBHOOK] Fetching active webhook subscriptions for event {event_type.value}")
        subscriptions = self.subscription_repo.list_active(session)

        if not subscriptions:
            logger.warning(
                f"[WEBHOOK] No active webhook subscriptions found for {event_type.value} - no webhooks will be sent"
            )
            return

        logger.info(
            f"[WEBHOOK] Broadcasting {event_type.value} for book {book.id} ('{book.book_name}') to {len(subscriptions)} subscriptions"
        )

        for sub in subscriptions:
            logger.info(f"[WEBHOOK] - Subscription {sub.id}: {sub.url} (events: {sub.event_types})")

        # Deliver to all subscriptions concurrently
        tasks = [self.deliver_webhook(session, sub.id, event_type, book) for sub in subscriptions]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Log any exceptions from delivery tasks
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"[WEBHOOK] Delivery task {i} raised exception: {result}")

    async def deliver_publisher_webhook(
        self, session: Session, subscription_id: int, event_type: WebhookEventType, publisher: Publisher
    ) -> None:
        """
        Deliver a publisher webhook event to a specific subscription with retry logic.

        Args:
            session: Database session
            subscription_id: ID of webhook subscription
            event_type: Type of webhook event
            publisher: Publisher model instance

        Returns:
            None (logs delivery status to database)
        """
        logger.info(f"[WEBHOOK] deliver_publisher_webhook called with event_type={event_type.value}")
        subscription = self.subscription_repo.get_by_id(session, subscription_id)
        if not subscription or not subscription.is_active:
            logger.warning(f"[WEBHOOK] Skipping inactive subscription {subscription_id}")
            return

        # Check if subscription wants this event type
        if subscription.event_types:
            subscribed_events = [e.strip() for e in subscription.event_types.split(",")]
            if event_type.value not in subscribed_events:
                logger.info(
                    f"[WEBHOOK] Subscription {subscription_id} ({subscription.url}) not interested in {event_type.value} (subscribed to: {subscription.event_types})"
                )
                return

        # Build webhook payload
        logo_url = f"/publishers/{publisher.id}/logo" if publisher.id else None
        event_data = WebhookEventPublisherData(
            id=publisher.id,
            name=publisher.name,
            contact_email=publisher.contact_email,
            logo_url=logo_url,
        )

        webhook_payload = WebhookEventPayload(event=event_type, timestamp=datetime.now(timezone.utc), data=event_data)

        payload_json = webhook_payload.model_dump_json()

        # Generate signature
        signature = self.generate_signature(payload_json, subscription.secret)

        # Create delivery log
        # Use .value to get string representation (columns are now String type, not Enum)
        logger.info(f"[WEBHOOK] Creating delivery log with event_type={event_type.value}, status=pending")
        log = self.delivery_log_repo.create(
            session,
            data={
                "subscription_id": subscription_id,
                "event_type": event_type.value,  # Use .value to get "book.created" not "BOOK_CREATED"
                "payload": payload_json,
                "status": WebhookDeliveryStatus.PENDING.value,  # Use .value to get "pending"
                "attempt_count": 0,
            },
        )

        # Attempt delivery with retries
        max_retries = 3
        backoff_seconds = [1, 2, 4]  # Exponential backoff

        for attempt in range(max_retries):
            try:
                log.attempt_count = attempt + 1
                session.commit()

                logger.info(
                    f"[WEBHOOK] Sending publisher webhook {log.id} to {subscription.url} (attempt {attempt + 1}/{max_retries})"
                )
                logger.debug(f"[WEBHOOK] Payload: {payload_json[:500]}")

                status_code, response_body = await self.send_webhook(subscription.url, payload_json, signature)

                # Update log with response
                log.response_status = status_code
                log.response_body = response_body[:1000]  # Limit stored response size
                log.delivered_at = datetime.now(timezone.utc)

                if 200 <= status_code < 300:
                    log.status = WebhookDeliveryStatus.SUCCESS.value
                    session.commit()
                    logger.info(
                        f"[WEBHOOK] Publisher webhook {log.id} delivered successfully to {subscription.url} (status {status_code})"
                    )
                    return
                else:
                    error_msg = f"HTTP {status_code}: {response_body[:200]}"
                    log.error_message = error_msg
                    logger.warning(
                        f"[WEBHOOK] Publisher webhook {log.id} failed with status {status_code}, response: {response_body[:200]}"
                    )

            except Exception as e:
                error_msg = f"Exception: {str(e)}"
                log.error_message = error_msg
                logger.error(
                    f"[WEBHOOK] Publisher webhook {log.id} delivery error to {subscription.url}: {e}", exc_info=True
                )

            # Retry with backoff if not last attempt
            if attempt < max_retries - 1:
                logger.info(f"[WEBHOOK] Retrying publisher webhook {log.id} in {backoff_seconds[attempt]}s...")
                await asyncio.sleep(backoff_seconds[attempt])
            else:
                # Mark as failed after all retries
                log.status = WebhookDeliveryStatus.FAILED.value
                session.commit()
                logger.error(
                    f"[WEBHOOK] Publisher webhook {log.id} permanently failed after {max_retries} attempts to {subscription.url}"
                )

    async def broadcast_publisher_event(
        self, session: Session, event_type: WebhookEventType, publisher: Publisher
    ) -> None:
        """
        Broadcast a publisher webhook event to all active subscriptions.

        Args:
            session: Database session
            event_type: Type of webhook event
            publisher: Publisher model instance

        Returns:
            None (triggers async delivery tasks)
        """
        logger.info(f"[WEBHOOK] Fetching active webhook subscriptions for event {event_type.value}")
        subscriptions = self.subscription_repo.list_active(session)

        if not subscriptions:
            logger.warning(
                f"[WEBHOOK] No active webhook subscriptions found for {event_type.value} - no webhooks will be sent"
            )
            return

        logger.info(
            f"[WEBHOOK] Broadcasting {event_type.value} for publisher {publisher.id} ('{publisher.name}') to {len(subscriptions)} subscriptions"
        )

        for sub in subscriptions:
            logger.info(f"[WEBHOOK] - Subscription {sub.id}: {sub.url} (events: {sub.event_types})")

        # Deliver to all subscriptions concurrently
        tasks = [self.deliver_publisher_webhook(session, sub.id, event_type, publisher) for sub in subscriptions]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Log any exceptions from delivery tasks
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"[WEBHOOK] Publisher delivery task {i} raised exception: {result}")
