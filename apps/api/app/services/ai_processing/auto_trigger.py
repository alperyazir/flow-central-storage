"""Auto-processing service for triggering AI processing on book upload."""

from __future__ import annotations

import logging
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

    def should_skip_existing(self) -> bool:
        """
        Check if already-processed books should be skipped.

        Returns:
            True if existing processed books should be skipped.
        """
        return self.settings.ai_auto_process_skip_existing

    def is_already_processed(
        self,
        publisher_id: int,
        book_id: str,
        book_name: str,
    ) -> bool:
        """
        Check if a book has already been processed.

        Args:
            publisher_id: Publisher ID.
            book_id: Book identifier.
            book_name: Book folder name.

        Returns:
            True if metadata.json exists for the book.
        """
        retrieval_service = get_ai_data_retrieval_service()
        metadata = retrieval_service.get_metadata(publisher_id, book_id, book_name)
        return metadata is not None

    def should_auto_process(
        self,
        publisher_id: int,
        book_id: str,
        book_name: str,
        force: bool = False,
    ) -> bool:
        """
        Determine if auto-processing should be triggered for a book.

        Args:
            publisher_id: Publisher ID.
            book_id: Book identifier.
            book_name: Book folder name.
            force: If True, ignore skip_existing setting.

        Returns:
            True if processing should be triggered.
        """
        # Check if auto-processing is enabled globally
        if not self.is_auto_processing_enabled():
            logger.debug(
                "Auto-processing disabled globally, skipping book %s",
                book_id,
            )
            return False

        # Check if we should skip already-processed books
        if not force and self.should_skip_existing():
            if self.is_already_processed(publisher_id, book_id, book_name):
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
            publisher_id: Publisher ID.
            book_name: Book folder name.
            force: If True, process even if already processed.
            priority: Job priority level.
            job_type: Processing job type. Defaults to UNIFIED for single LLM call.

        Returns:
            ProcessingJob if enqueued, None if skipped.
        """
        # Check if we should process this book
        if not self.should_auto_process(publisher_id, str(book_id), book_name, force):
            return None

        # Use UNIFIED by default for better accuracy and lower cost
        actual_job_type = job_type or DEFAULT_JOB_TYPE

        try:
            queue_service = await get_queue_service()
            job = await queue_service.enqueue_job(
                book_id=str(book_id),
                publisher_id=publisher_id,
                job_type=actual_job_type,
                priority=priority,
                metadata={
                    "book_name": book_name,
                    "publisher_id": publisher_id,
                    "publisher_slug": publisher_slug,
                    "auto_triggered": True,
                    "force_reprocess": force,
                },
            )

            logger.info(
                "Auto-triggered processing job %s for book %s (publisher: %s)",
                job.job_id,
                book_id,
                publisher_id,
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
