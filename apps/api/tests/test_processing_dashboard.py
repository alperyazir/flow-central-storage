"""Unit tests for processing dashboard API endpoints."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.queue.models import ProcessingJob, ProcessingStatus

# =============================================================================
# Mock Fixtures
# =============================================================================


@pytest.fixture
def mock_book():
    """Create a mock book object.

    The mirrored AI columns are explicitly empty — a bare MagicMock is truthy,
    which would make every book look like it had a persisted status.
    """
    book = MagicMock()
    book.id = 1
    book.book_name = "test-book"
    book.book_title = "Test Book"
    book.publisher = "TestPublisher"
    book.publisher_id = 1
    book.ai_processing_status = None
    book.ai_processed_at = None
    return book


@pytest.fixture
def mock_processing_job():
    """Create a mock processing job."""
    job = MagicMock(spec=ProcessingJob)
    job.job_id = "job-123"
    job.book_id = "1"
    job.publisher_id = "1"
    job.status = ProcessingStatus.QUEUED
    job.progress = 0
    job.current_step = "Initializing"
    job.error_message = None
    job.created_at = MagicMock()
    job.created_at.isoformat.return_value = "2024-01-01T00:00:00Z"
    job.started_at = None
    job.completed_at = None
    return job


@pytest.fixture
def mock_books_query(mock_book):
    """DB query stub: every builder call returns the same query object.

    _filtered_books_query chains query.filter/join/order_by, so each step has to
    hand the same mock back or the count/offset stubs below never apply.
    """
    query = MagicMock()
    query.filter.return_value = query
    query.join.return_value = query
    query.order_by.return_value = query
    query.count.return_value = 1
    query.offset.return_value.limit.return_value.all.return_value = [mock_book]
    return query


@pytest.fixture
def mock_failed_job():
    """Create a mock failed processing job."""
    job = MagicMock(spec=ProcessingJob)
    job.job_id = "job-456"
    job.book_id = "1"
    job.publisher_id = "1"
    job.status = ProcessingStatus.FAILED
    job.progress = 50
    job.current_step = "Vocabulary extraction"
    job.error_message = "API rate limit exceeded"
    job.created_at = MagicMock()
    job.created_at.isoformat.return_value = "2024-01-01T00:00:00Z"
    job.started_at = MagicMock()
    job.started_at.isoformat.return_value = "2024-01-01T00:01:00Z"
    job.completed_at = None
    return job


# =============================================================================
# List Books with Processing Status Tests
# =============================================================================


class TestListBooksWithProcessingStatus:
    """Test GET /processing/books endpoint."""

    @pytest.mark.asyncio
    @patch("app.routers.processing.get_queue_service")
    @patch("app.routers.processing.get_ai_data_retrieval_service")
    @patch("app.routers.processing._require_auth")
    async def test_list_books_returns_books_with_status(
        self,
        mock_auth: MagicMock,
        mock_get_retrieval: MagicMock,
        mock_get_queue: MagicMock,
        mock_book: MagicMock,
        mock_books_query: MagicMock,
        mock_processing_job: MagicMock,
    ) -> None:
        """Test listing books returns books with processing status."""
        from app.routers.processing import list_books_with_processing_status

        mock_auth.return_value = 1

        # Mock queue service
        mock_queue = AsyncMock()
        mock_queue.list_jobs.return_value = [mock_processing_job]
        mock_get_queue.return_value = mock_queue

        # Mock retrieval service
        mock_retrieval = MagicMock()
        mock_retrieval.get_metadata.return_value = None
        mock_get_retrieval.return_value = mock_retrieval

        # Mock DB query
        mock_db = MagicMock()
        mock_db.query.return_value = mock_books_query

        mock_credentials = MagicMock()

        result = await list_books_with_processing_status(
            status=None,
            publisher=None,
            search=None,
            page=1,
            page_size=50,
            credentials=mock_credentials,
            db=mock_db,
        )

        assert result.total == 1
        assert len(result.books) == 1
        assert result.books[0].book_id == 1
        assert result.books[0].processing_status == "queued"

    @pytest.mark.asyncio
    @patch("app.routers.processing.get_queue_service")
    @patch("app.routers.processing.get_ai_data_retrieval_service")
    @patch("app.routers.processing._require_auth")
    async def test_list_books_with_completed_status_from_metadata(
        self,
        mock_auth: MagicMock,
        mock_get_retrieval: MagicMock,
        mock_get_queue: MagicMock,
        mock_book: MagicMock,
        mock_books_query: MagicMock,
    ) -> None:
        """Test books with metadata but no active job show as completed."""
        from app.routers.processing import list_books_with_processing_status

        mock_auth.return_value = 1

        # Mock queue service - no jobs
        mock_queue = AsyncMock()
        mock_queue.list_jobs.return_value = []
        mock_get_queue.return_value = mock_queue

        # Mock retrieval service - has metadata (an AIDataMetadata-like object,
        # not a dict: the endpoint reads .processing_status.value off it)
        metadata = MagicMock()
        metadata.processing_status.value = "completed"
        metadata.processing_completed_at = None
        mock_retrieval = MagicMock()
        mock_retrieval.get_metadata.return_value = metadata
        mock_get_retrieval.return_value = mock_retrieval

        # Mock DB query
        mock_db = MagicMock()
        mock_db.query.return_value = mock_books_query

        mock_credentials = MagicMock()

        result = await list_books_with_processing_status(
            status=None,
            publisher=None,
            search=None,
            page=1,
            page_size=50,
            credentials=mock_credentials,
            db=mock_db,
        )

        assert result.books[0].processing_status == "completed"
        assert result.books[0].progress == 100


# =============================================================================
# Processing Queue Tests
# =============================================================================


class TestGetProcessingQueue:
    """Test GET /processing/queue endpoint."""

    @pytest.mark.asyncio
    @patch("app.routers.processing._publisher_repository")
    @patch("app.routers.processing.get_queue_service")
    @patch("app.routers.processing._require_auth")
    @patch("app.routers.processing._book_repository")
    async def test_get_queue_returns_active_jobs(
        self,
        mock_book_repo: MagicMock,
        mock_auth: MagicMock,
        mock_get_queue: MagicMock,
        mock_publisher_repo: MagicMock,
        mock_book: MagicMock,
        mock_processing_job: MagicMock,
    ) -> None:
        """Test getting queue returns active jobs."""
        from app.routers.processing import get_processing_queue

        mock_auth.return_value = 1
        mock_book_repo.get_by_id.return_value = mock_book
        # publisher_name is a str field on the response model. Assigned after
        # construction: MagicMock(name=...) sets the mock's own name, not .name
        publisher = MagicMock()
        publisher.name = "TestPublisher"
        mock_publisher_repo.get.return_value = publisher

        # Mock queue service
        mock_queue = AsyncMock()
        mock_queue.list_jobs.side_effect = [
            [],  # QUEUED jobs
            [mock_processing_job],  # PROCESSING jobs
        ]
        mock_get_queue.return_value = mock_queue

        mock_db = MagicMock()
        mock_credentials = MagicMock()

        result = await get_processing_queue(
            credentials=mock_credentials,
            db=mock_db,
        )

        assert result.total_queued == 0
        assert result.total_processing == 1
        assert len(result.queue) == 1

    @pytest.mark.asyncio
    @patch("app.routers.processing.get_queue_service")
    @patch("app.routers.processing._require_auth")
    async def test_get_queue_empty_when_no_jobs(
        self,
        mock_auth: MagicMock,
        mock_get_queue: MagicMock,
    ) -> None:
        """Test getting queue returns empty when no active jobs."""
        from app.routers.processing import get_processing_queue

        mock_auth.return_value = 1

        # Mock queue service - no jobs
        mock_queue = AsyncMock()
        mock_queue.list_jobs.return_value = []
        mock_get_queue.return_value = mock_queue

        mock_db = MagicMock()
        mock_credentials = MagicMock()

        result = await get_processing_queue(
            credentials=mock_credentials,
            db=mock_db,
        )

        assert result.total_queued == 0
        assert result.total_processing == 0
        assert len(result.queue) == 0


# =============================================================================
# Clear Error Tests
# =============================================================================


class TestClearProcessingError:
    """Test POST /processing/books/{book_id}/clear-error endpoint."""

    @pytest.mark.asyncio
    @patch("app.routers.processing.get_queue_service")
    @patch("app.routers.processing._require_auth")
    @patch("app.routers.processing._book_repository")
    async def test_clear_error_success(
        self,
        mock_book_repo: MagicMock,
        mock_auth: MagicMock,
        mock_get_queue: MagicMock,
        mock_book: MagicMock,
        mock_failed_job: MagicMock,
    ) -> None:
        """Test clearing error for a failed job."""
        from app.routers.processing import clear_processing_error

        mock_auth.return_value = 1
        mock_book_repo.get_by_id.return_value = mock_book

        # Mock queue service
        mock_queue = AsyncMock()
        mock_queue.list_jobs.return_value = [mock_failed_job]
        mock_get_queue.return_value = mock_queue

        mock_db = MagicMock()
        mock_credentials = MagicMock()

        result = await clear_processing_error(
            book_id=1,
            credentials=mock_credentials,
            db=mock_db,
        )

        assert result["message"] == "Processing error cleared successfully"
        # The failed job record is dropped so the book can be queued again.
        mock_queue.delete_job.assert_awaited_once_with("job-456")

    @pytest.mark.asyncio
    @patch("app.routers.processing.get_queue_service")
    @patch("app.routers.processing._require_auth")
    @patch("app.routers.processing._book_repository")
    async def test_clear_error_fails_for_non_failed_job(
        self,
        mock_book_repo: MagicMock,
        mock_auth: MagicMock,
        mock_get_queue: MagicMock,
        mock_book: MagicMock,
        mock_processing_job: MagicMock,
    ) -> None:
        """Test clearing error fails for job not in failed state."""
        from fastapi import HTTPException

        from app.routers.processing import clear_processing_error

        mock_auth.return_value = 1
        mock_book_repo.get_by_id.return_value = mock_book

        # Mock queue service - job is queued, not failed
        mock_queue = AsyncMock()
        mock_queue.list_jobs.return_value = [mock_processing_job]
        mock_get_queue.return_value = mock_queue

        mock_db = MagicMock()
        mock_credentials = MagicMock()

        with pytest.raises(HTTPException) as exc_info:
            await clear_processing_error(
                book_id=1,
                credentials=mock_credentials,
                db=mock_db,
            )

        assert exc_info.value.status_code == 400
        assert "not in failed state" in exc_info.value.detail


# =============================================================================
# Bulk Reprocess Tests
# =============================================================================


class TestBulkReprocess:
    """Test POST /processing/bulk-reprocess endpoint."""

    @pytest.mark.asyncio
    @patch("app.routers.processing._book_has_content")
    @patch("app.routers.processing.get_queue_service")
    @patch("app.routers.processing._require_auth")
    @patch("app.routers.processing._book_repository")
    async def test_bulk_reprocess_success(
        self,
        mock_book_repo: MagicMock,
        mock_auth: MagicMock,
        mock_get_queue: MagicMock,
        mock_has_content: MagicMock,
        mock_book: MagicMock,
    ) -> None:
        """Test bulk reprocessing multiple books."""
        from app.routers.processing import BulkReprocessRequest, bulk_reprocess

        mock_auth.return_value = 1  # User auth, not API key
        mock_book_repo.get_by_id.return_value = mock_book
        mock_has_content.return_value = True

        # Mock queue service
        mock_job = MagicMock()
        mock_job.job_id = "new-job-123"
        mock_queue = AsyncMock()
        mock_queue.enqueue_job.return_value = mock_job
        mock_get_queue.return_value = mock_queue

        mock_db = MagicMock()
        mock_credentials = MagicMock()

        request = BulkReprocessRequest(book_ids=[1, 2, 3])

        result = await bulk_reprocess(
            request=request,
            credentials=mock_credentials,
            db=mock_db,
        )

        assert result.triggered == 3
        assert result.skipped == 0
        assert len(result.job_ids) == 3

    @pytest.mark.asyncio
    @patch("app.routers.processing._require_auth")
    async def test_bulk_reprocess_requires_user_auth(
        self,
        mock_auth: MagicMock,
    ) -> None:
        """Test bulk reprocess requires user authentication (not API key)."""
        from fastapi import HTTPException

        from app.routers.processing import BulkReprocessRequest, bulk_reprocess

        mock_auth.return_value = -1  # API key auth

        mock_db = MagicMock()
        mock_credentials = MagicMock()

        request = BulkReprocessRequest(book_ids=[1, 2])

        with pytest.raises(HTTPException) as exc_info:
            await bulk_reprocess(
                request=request,
                credentials=mock_credentials,
                db=mock_db,
            )

        assert exc_info.value.status_code == 403
        assert "user authentication" in exc_info.value.detail

    @pytest.mark.asyncio
    @patch("app.routers.processing._book_has_content")
    @patch("app.routers.processing.get_queue_service")
    @patch("app.routers.processing._require_auth")
    @patch("app.routers.processing._book_repository")
    async def test_bulk_reprocess_skips_missing_books(
        self,
        mock_book_repo: MagicMock,
        mock_auth: MagicMock,
        mock_get_queue: MagicMock,
        mock_has_content: MagicMock,
    ) -> None:
        """Test bulk reprocess skips books that don't exist."""
        from app.routers.processing import BulkReprocessRequest, bulk_reprocess

        mock_auth.return_value = 1
        mock_book_repo.get_by_id.return_value = None  # Book not found
        mock_has_content.return_value = True

        mock_queue = AsyncMock()
        mock_get_queue.return_value = mock_queue

        mock_db = MagicMock()
        mock_credentials = MagicMock()

        request = BulkReprocessRequest(book_ids=[999])

        result = await bulk_reprocess(
            request=request,
            credentials=mock_credentials,
            db=mock_db,
        )

        assert result.triggered == 0
        assert result.skipped == 1
        assert "not found" in result.errors[0]

    @pytest.mark.asyncio
    @patch("app.routers.processing._book_has_content")
    @patch("app.routers.processing.get_queue_service")
    @patch("app.routers.processing._require_auth")
    @patch("app.routers.processing._filtered_books_query")
    async def test_bulk_reprocess_by_filter_queues_every_match(
        self,
        mock_query_builder: MagicMock,
        mock_auth: MagicMock,
        mock_get_queue: MagicMock,
        mock_has_content: MagicMock,
    ) -> None:
        """Filters queue the whole matching set, not just one dashboard page."""
        from app.routers.processing import (
            BulkReprocessFilters,
            BulkReprocessRequest,
            bulk_reprocess,
        )
        from app.services.queue.models import ProcessingJobType

        mock_auth.return_value = 1
        mock_has_content.return_value = True

        books = []
        for book_id in (1, 2, 3):
            book = MagicMock()
            book.id = book_id
            book.publisher_id = 7
            book.book_name = f"book-{book_id}"
            books.append(book)

        query = MagicMock()
        query.count.return_value = 3
        query.limit.return_value.all.return_value = books
        mock_query_builder.return_value = query

        mock_job = MagicMock()
        mock_job.job_id = "new-job"
        mock_queue = AsyncMock()
        mock_queue.enqueue_job.return_value = mock_job
        mock_get_queue.return_value = mock_queue

        request = BulkReprocessRequest(
            filters=BulkReprocessFilters(status="not_started"),
            job_type="full",
        )

        result = await bulk_reprocess(
            request=request,
            credentials=MagicMock(),
            db=MagicMock(),
        )

        assert result.matched == 3
        assert result.triggered == 3
        assert result.skipped == 0
        assert result.errors == []
        # The filter is handed to the same query builder the dashboard uses.
        assert mock_query_builder.call_args.kwargs["status_filter"] == "not_started"
        # job_type from the request is honoured (not silently forced to unified).
        assert mock_queue.enqueue_job.await_args.kwargs["job_type"] == ProcessingJobType.FULL

    @pytest.mark.asyncio
    @patch("app.routers.processing.BULK_REPROCESS_MAX_BOOKS", 2)
    @patch("app.routers.processing._book_has_content")
    @patch("app.routers.processing.get_queue_service")
    @patch("app.routers.processing._require_auth")
    @patch("app.routers.processing._filtered_books_query")
    async def test_bulk_reprocess_by_filter_reports_cap(
        self,
        mock_query_builder: MagicMock,
        mock_auth: MagicMock,
        mock_get_queue: MagicMock,
        mock_has_content: MagicMock,
    ) -> None:
        """Books beyond the per-request cap are reported, never dropped silently."""
        from app.routers.processing import (
            BulkReprocessFilters,
            BulkReprocessRequest,
            bulk_reprocess,
        )

        mock_auth.return_value = 1
        mock_has_content.return_value = True

        books = []
        for book_id in (1, 2):
            book = MagicMock()
            book.id = book_id
            book.publisher_id = 7
            book.book_name = f"book-{book_id}"
            books.append(book)

        query = MagicMock()
        query.count.return_value = 5  # more matches than the capped page
        query.limit.return_value.all.return_value = books
        mock_query_builder.return_value = query

        mock_job = MagicMock()
        mock_job.job_id = "new-job"
        mock_queue = AsyncMock()
        mock_queue.enqueue_job.return_value = mock_job
        mock_get_queue.return_value = mock_queue

        result = await bulk_reprocess(
            request=BulkReprocessRequest(filters=BulkReprocessFilters(status="failed")),
            credentials=MagicMock(),
            db=MagicMock(),
        )

        query.limit.assert_called_once_with(2)
        assert result.matched == 5
        assert result.triggered == 2
        assert any("first 2 of 5" in e for e in result.errors)

    @pytest.mark.asyncio
    @patch("app.routers.processing._require_auth")
    async def test_bulk_reprocess_requires_ids_or_filters(
        self,
        mock_auth: MagicMock,
    ) -> None:
        """An empty request is rejected instead of silently doing nothing."""
        from fastapi import HTTPException

        from app.routers.processing import BulkReprocessRequest, bulk_reprocess

        mock_auth.return_value = 1

        with pytest.raises(HTTPException) as exc_info:
            await bulk_reprocess(
                request=BulkReprocessRequest(),
                credentials=MagicMock(),
                db=MagicMock(),
            )

        assert exc_info.value.status_code == 400


# =============================================================================
# Cancel Tests
# =============================================================================


class TestCancelProcessing:
    """Test POST /processing/books/{book_id}/cancel endpoint."""

    @pytest.mark.asyncio
    @patch("app.routers.processing.set_book_ai_status")
    @patch("app.routers.processing.get_queue_service")
    @patch("app.routers.processing._require_auth")
    @patch("app.routers.processing._book_repository")
    async def test_cancel_marks_the_book_cancelled(
        self,
        mock_book_repo: MagicMock,
        mock_auth: MagicMock,
        mock_get_queue: MagicMock,
        mock_set_status: MagicMock,
        mock_book: MagicMock,
        mock_processing_job: MagicMock,
    ) -> None:
        """Cancelling a queued job also clears the book's "queued" badge."""
        from app.routers.processing import cancel_processing
        from app.services.queue.models import ProcessingStatus

        mock_auth.return_value = 1
        mock_book_repo.get_by_id.return_value = mock_book

        cancelled_job = MagicMock()
        cancelled_job.job_id = "job-123"
        cancelled_job.status = ProcessingStatus.CANCELLED

        mock_queue = AsyncMock()
        mock_queue.list_jobs.return_value = [mock_processing_job]
        mock_queue.cancel_queued_job.return_value = cancelled_job
        mock_get_queue.return_value = mock_queue

        result = await cancel_processing(
            book_id=1,
            credentials=MagicMock(),
            db=MagicMock(),
        )

        assert result["status"] == "cancelled"
        mock_queue.cancel_queued_job.assert_awaited_once_with("job-123")
        assert mock_set_status.call_args.args == (mock_book.id, "cancelled")

    @pytest.mark.asyncio
    @patch("app.routers.processing.set_book_ai_status")
    @patch("app.routers.processing.get_queue_service")
    @patch("app.routers.processing._require_auth")
    @patch("app.routers.processing._book_repository")
    async def test_cancel_running_job_returns_409(
        self,
        mock_book_repo: MagicMock,
        mock_auth: MagicMock,
        mock_get_queue: MagicMock,
        mock_set_status: MagicMock,
        mock_book: MagicMock,
        mock_processing_job: MagicMock,
    ) -> None:
        """A running job is refused, and the book status is left alone."""
        from fastapi import HTTPException

        from app.routers.processing import cancel_processing
        from app.services.queue.models import QueueError

        mock_auth.return_value = 1
        mock_book_repo.get_by_id.return_value = mock_book

        mock_queue = AsyncMock()
        mock_queue.list_jobs.return_value = [mock_processing_job]
        mock_queue.cancel_queued_job.side_effect = QueueError(
            "Cannot cancel job with status processing"
        )
        mock_get_queue.return_value = mock_queue

        with pytest.raises(HTTPException) as exc_info:
            await cancel_processing(
                book_id=1,
                credentials=MagicMock(),
                db=MagicMock(),
            )

        assert exc_info.value.status_code == 409
        mock_set_status.assert_not_called()
