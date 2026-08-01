"""Auto-processing service for triggering AI processing on book upload."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.core.config import get_settings
from app.services.ai_data import get_ai_data_retrieval_service
from app.services.queue import get_queue_service
from app.services.queue.models import JobPriority, ProcessingJobType

# Use UNIFIED by default for better accuracy and lower cost
DEFAULT_JOB_TYPE = ProcessingJobType.UNIFIED

if TYPE_CHECKING:
    from app.core.config import Settings
    from app.services.queue.models import ProcessingJob

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PublisherOverrides:
    """Per-publisher AI processing settings.

    ``None`` means "use the global default" — the same convention the settings
    endpoints expose (GET /processing/publishers/{id}/settings).
    """

    auto_process_enabled: bool | None = None
    priority: JobPriority | None = None


class AutoProcessingService:
    """
    Service for automatically triggering AI processing when books are uploaded.

    Handles the logic for determining whether to trigger processing and
    coordinating with the queue service.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        """
        Initialize auto-processing service.

        Args:
            settings: Application settings. Uses global settings if not provided.
        """
        self.settings = settings or get_settings()

    def is_auto_processing_enabled(self) -> bool:
        """
        Check if auto-processing is enabled globally.

        Returns:
            True if auto-processing on upload is enabled.
        """
        return self.settings.ai_auto_process_on_upload

    def get_publisher_overrides(self, publisher_id: int | str) -> PublisherOverrides:
        """Load a publisher's AI processing overrides.

        Best-effort: a missing publisher or a DB hiccup falls back to the global
        defaults rather than blocking an upload. Blocking DB call — callers on
        the event loop must run it in a thread.
        """
        from app.db import SessionLocal
        from app.models.publisher import Publisher

        try:
            with SessionLocal() as session:
                publisher = session.get(Publisher, int(publisher_id))
                if publisher is None:
                    return PublisherOverrides()

                priority: JobPriority | None = None
                if publisher.ai_processing_priority:
                    try:
                        priority = JobPriority(publisher.ai_processing_priority)
                    except ValueError:
                        logger.warning(
                            "Publisher %s has unknown ai_processing_priority %r, using default",
                            publisher_id,
                            publisher.ai_processing_priority,
                        )

                return PublisherOverrides(
                    auto_process_enabled=publisher.ai_auto_process_enabled,
                    priority=priority,
                )
        except Exception as exc:  # pragma: no cover - best effort
            logger.warning(
                "Failed to load AI overrides for publisher %s: %s", publisher_id, exc
            )
            return PublisherOverrides()

    def should_skip_existing(self) -> bool:
        """
        Check if already-processed books should be skipped.

        Returns:
            True if existing processed books should be skipped.
        """
        return self.settings.ai_auto_process_skip_existing

    def is_already_processed(
        self,
        publisher_slug: str,
        book_id: str,
        book_name: str,
    ) -> bool:
        """
        Check if a book has already been processed.

        Args:
            publisher_slug: Publisher slug (the object-storage prefix).
            book_id: Book identifier.
            book_name: Book folder name.

        Returns:
            True if metadata.json exists for the book.
        """
        retrieval_service = get_ai_data_retrieval_service()
        metadata = retrieval_service.get_metadata(publisher_slug, book_id, book_name)
        return metadata is not None

    def should_auto_process(
        self,
        publisher_slug: str,
        book_id: str,
        book_name: str,
        force: bool = False,
        auto_process_enabled: bool | None = None,
    ) -> bool:
        """
        Determine if auto-processing should be triggered for a book.

        Args:
            publisher_slug: Publisher slug; ai-data is stored under it, so the
                numeric id would make the already-processed check always miss.
            book_id: Book identifier.
            book_name: Book folder name.
            force: If True, ignore skip_existing setting.
            auto_process_enabled: Publisher override; None uses the global flag.

        Returns:
            True if processing should be triggered.
        """
        # The publisher override wins in both directions: an opted-in publisher
        # is processed even when the global default is off, and an opted-out one
        # is skipped even when it is on.
        enabled = (
            self.is_auto_processing_enabled()
            if auto_process_enabled is None
            else auto_process_enabled
        )
        if not enabled:
            logger.debug(
                "Auto-processing disabled for publisher %s, skipping book %s",
                publisher_slug,
                book_id,
            )
            return False

        # Check if we should skip already-processed books
        if not force and self.should_skip_existing():
            if self.is_already_processed(publisher_slug, book_id, book_name):
                logger.info(
                    "Book %s already processed, skipping auto-processing",
                    book_id,
                )
                return False

        return True

    async def trigger_processing(
        self,
        book_id: int,
        publisher_id: int,
        publisher_slug: str,
        book_name: str,
        force: bool = False,
        priority: JobPriority = JobPriority.NORMAL,
        job_type: ProcessingJobType | None = None,
    ) -> ProcessingJob | None:
        """
        Trigger AI processing for a book if appropriate.

        Args:
            book_id: Book database ID.
            publisher_id: Publisher ID (used to load the publisher's overrides).
            publisher_slug: Publisher slug (the object-storage prefix).
            book_name: Book folder name.
            force: If True, process even if already processed.
            priority: Job priority level. A publisher override takes precedence.
            job_type: Processing job type. Defaults to UNIFIED for single LLM call.

        Returns:
            ProcessingJob if enqueued, None if skipped.
        """
        # Publisher settings (Processing dashboard) override the globals.
        overrides = await asyncio.to_thread(self.get_publisher_overrides, publisher_id)

        # Check if we should process this book
        if not self.should_auto_process(
            publisher_slug,
            str(book_id),
            book_name,
            force,
            auto_process_enabled=overrides.auto_process_enabled,
        ):
            return None

        # Use UNIFIED by default for better accuracy and lower cost
        actual_job_type = job_type or DEFAULT_JOB_TYPE
        actual_priority = overrides.priority or priority

        try:
            queue_service = await get_queue_service()
            job = await queue_service.enqueue_job(
                book_id=str(book_id),
                publisher_id=publisher_id,
                job_type=actual_job_type,
                priority=actual_priority,
                metadata={
                    "book_name": book_name,
                    "publisher_id": publisher_id,
                    "publisher_slug": publisher_slug,
                    "auto_triggered": True,
                    "force_reprocess": force,
                },
            )

            from app.services.ai_processing.book_status import set_book_ai_status

            await asyncio.to_thread(set_book_ai_status, book_id, "queued")

            logger.info(
                "Auto-triggered processing job %s for book %s (publisher: %s, priority: %s)",
                job.job_id,
                book_id,
                publisher_id,
                actual_priority.value,
            )
            return job

        except Exception as e:
            # Log error but don't fail the upload
            logger.error(
                "Failed to auto-trigger processing for book %s: %s",
                book_id,
                e,
            )
            return None


# Singleton instance
_auto_processing_service: AutoProcessingService | None = None


def get_auto_processing_service() -> AutoProcessingService:
    """Get or create the global auto-processing service instance."""
    global _auto_processing_service
    if _auto_processing_service is None:
        _auto_processing_service = AutoProcessingService()
    return _auto_processing_service


async def trigger_auto_processing(
    book_id: int,
    publisher_id: int,
    publisher_slug: str,
    book_name: str,
    force: bool = False,
) -> None:
    """
    Convenience function to trigger auto-processing.

    This is designed to be called from BackgroundTasks.
    """
    service = get_auto_processing_service()
    await service.trigger_processing(
        book_id=book_id,
        publisher_id=publisher_id,
        publisher_slug=publisher_slug,
        book_name=book_name,
        force=force,
    )
