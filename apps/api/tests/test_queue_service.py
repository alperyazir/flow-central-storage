"""Tests for AI Processing Queue System."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.queue.models import (
    PROCESSING_STAGES,
    JobAlreadyExistsError,
    JobNotFoundError,
    JobPriority,
    ProcessingJob,
    ProcessingJobType,
    ProcessingStatus,
    QueueConnectionError,
    QueueError,
    QueueStats,
)
from app.services.queue.redis import RedisConnection
from app.services.queue.repository import JobRepository
from app.services.queue.service import ProgressReporter, QueueService

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_redis():
    """Create mock Redis client for testing."""
    storage: dict = {}
    sets: dict = {}

    class MockRedis:
        async def hset(self, key: str, mapping: dict) -> int:
            if key not in storage:
                storage[key] = {}
            storage[key].update(mapping)
            return len(mapping)

        async def hget(self, key: str, field: str) -> str | None:
            return storage.get(key, {}).get(field)

        async def hgetall(self, key: str) -> dict:
            return storage.get(key, {})

        async def hincrby(self, key: str, field: str, amount: int) -> int:
            if key not in storage:
                storage[key] = {}
            current = int(storage[key].get(field, 0))
            storage[key][field] = str(current + amount)
            return current + amount

        async def delete(self, key: str) -> int:
            if key in storage:
                del storage[key]
                return 1
            return 0

        async def expire(self, key: str, seconds: int) -> bool:
            return True

        async def sadd(self, key: str, *values: str) -> int:
            if key not in sets:
                sets[key] = set()
            added = 0
            for v in values:
                if v not in sets[key]:
                    sets[key].add(v)
                    added += 1
            return added

        async def srem(self, key: str, *values: str) -> int:
            if key not in sets:
                return 0
            removed = 0
            for v in values:
                if v in sets[key]:
                    sets[key].discard(v)
                    removed += 1
            return removed

        async def smembers(self, key: str) -> set:
            return sets.get(key, set())

        async def scard(self, key: str) -> int:
            return len(sets.get(key, set()))

        async def ping(self) -> bool:
            return True

        async def aclose(self) -> None:
            pass

        # Expose internal storage for test assertions
        def _get_storage(self) -> dict:
            return storage

        def _get_sets(self) -> dict:
            return sets

        def _clear(self) -> None:
            storage.clear()
            sets.clear()

    return MockRedis()


@pytest.fixture
def job_repository(mock_redis):
    """Create job repository with mock Redis."""
    return JobRepository(redis_client=mock_redis, job_ttl_seconds=86400)


@pytest.fixture
def mock_redis_connection(mock_redis):
    """Create mock Redis connection."""
    connection = MagicMock(spec=RedisConnection)
    connection.client = mock_redis
    connection.is_connected = True
    return connection


@pytest.fixture
def mock_arq_pool():
    """Create mock arq pool."""
    pool = AsyncMock()
    pool.enqueue_job = AsyncMock(return_value=MagicMock(job_id="arq-123"))
    pool.all_job_results = AsyncMock(return_value=[])
    pool.close = AsyncMock()
    return pool


@pytest.fixture
def queue_service(mock_redis_connection, job_repository, mock_arq_pool):
    """Create queue service for testing."""
    return QueueService(
        redis_connection=mock_redis_connection,
        repository=job_repository,
        arq_pool=mock_arq_pool,
    )


@pytest.fixture
def sample_job():
    """Create a sample processing job."""
    return ProcessingJob(
        job_id="test-job-123",
        book_id="book-456",
        publisher_id="pub-789",
        job_type=ProcessingJobType.FULL,
        status=ProcessingStatus.QUEUED,
        priority=JobPriority.NORMAL,
    )


# =============================================================================
# Model Tests
# =============================================================================


class TestProcessingJob:
    """Tests for ProcessingJob dataclass."""

    def test_create_job_with_defaults(self):
        """Test creating a job with default values."""
        job = ProcessingJob(
            job_id="test-123",
            book_id="book-456",
            publisher_id="pub-789",
        )

        assert job.job_id == "test-123"
        assert job.book_id == "book-456"
        assert job.publisher_id == "pub-789"
        assert job.job_type == ProcessingJobType.FULL
        assert job.status == ProcessingStatus.QUEUED
        assert job.priority == JobPriority.NORMAL
        assert job.progress == 0
        assert job.current_step == ""
        assert job.error_message is None
        assert job.retry_count == 0
        assert job.started_at is None
        assert job.completed_at is None
        assert job.metadata == {}

    def test_create_job_with_custom_values(self):
        """Test creating a job with custom values."""
        job = ProcessingJob(
            job_id="test-123",
            book_id="book-456",
            publisher_id="pub-789",
            job_type=ProcessingJobType.TEXT_ONLY,
            status=ProcessingStatus.PROCESSING,
            priority=JobPriority.HIGH,
            progress=50,
            current_step="text_extraction",
            metadata={"source": "upload"},
        )

        assert job.job_type == ProcessingJobType.TEXT_ONLY
        assert job.status == ProcessingStatus.PROCESSING
        assert job.priority == JobPriority.HIGH
        assert job.progress == 50
        assert job.current_step == "text_extraction"
        assert job.metadata == {"source": "upload"}


class TestProcessingStatus:
    """Tests for ProcessingStatus enum."""

    def test_status_values(self):
        """Test status enum values."""
        assert ProcessingStatus.QUEUED.value == "queued"
        assert ProcessingStatus.PROCESSING.value == "processing"
        assert ProcessingStatus.COMPLETED.value == "completed"
        assert ProcessingStatus.FAILED.value == "failed"
        assert ProcessingStatus.PARTIAL.value == "partial"
        assert ProcessingStatus.CANCELLED.value == "cancelled"

    def test_status_from_string(self):
        """Test creating status from string."""
        status = ProcessingStatus("queued")
        assert status == ProcessingStatus.QUEUED


class TestQueueExceptions:
    """Tests for queue exceptions."""

    def test_queue_error(self):
        """Test base QueueError."""
        error = QueueError("Test error", {"key": "value"})
        assert error.message == "Test error"
        assert error.details == {"key": "value"}
        assert str(error) == "Test error"

    def test_job_not_found_error(self):
        """Test JobNotFoundError."""
        error = JobNotFoundError("job-123")
        assert "job-123" in error.message
        assert error.details["job_id"] == "job-123"

    def test_job_already_exists_error(self):
        """Test JobAlreadyExistsError."""
        error = JobAlreadyExistsError("book-456")
        assert "book-456" in error.message
        assert error.details["book_id"] == "book-456"


# =============================================================================
# Repository Tests
# =============================================================================


class TestJobRepository:
    """Tests for JobRepository."""

    @pytest.mark.asyncio
    async def test_create_job(self, job_repository, sample_job):
        """Test creating a job."""
        created = await job_repository.create_job(sample_job)

        assert created.job_id == sample_job.job_id
        assert created.book_id == sample_job.book_id

    @pytest.mark.asyncio
    async def test_create_duplicate_job_fails(self, job_repository, sample_job):
        """Test that duplicate active job creation fails."""
        await job_repository.create_job(sample_job)

        with pytest.raises(JobAlreadyExistsError):
            duplicate = ProcessingJob(
                job_id="different-id",
                book_id=sample_job.book_id,  # Same book
                publisher_id=sample_job.publisher_id,
            )
            await job_repository.create_job(duplicate)

    @pytest.mark.asyncio
    async def test_get_job(self, job_repository, sample_job):
        """Test retrieving a job."""
        await job_repository.create_job(sample_job)

        retrieved = await job_repository.get_job(sample_job.job_id)

        assert retrieved.job_id == sample_job.job_id
        assert retrieved.book_id == sample_job.book_id
        assert retrieved.status == ProcessingStatus.QUEUED

    @pytest.mark.asyncio
    async def test_get_nonexistent_job_fails(self, job_repository):
        """Test that getting nonexistent job raises error."""
        with pytest.raises(JobNotFoundError):
            await job_repository.get_job("nonexistent-id")

    @pytest.mark.asyncio
    async def test_update_job_status_queued_to_processing(self, job_repository, sample_job):
        """Test status transition QUEUED -> PROCESSING."""
        await job_repository.create_job(sample_job)

        updated = await job_repository.update_job_status(sample_job.job_id, ProcessingStatus.PROCESSING)

        assert updated.status == ProcessingStatus.PROCESSING
        assert updated.started_at is not None
        assert updated.completed_at is None

    @pytest.mark.asyncio
    async def test_update_job_status_processing_to_completed(self, job_repository, sample_job):
        """Test status transition PROCESSING -> COMPLETED."""
        await job_repository.create_job(sample_job)
        await job_repository.update_job_status(sample_job.job_id, ProcessingStatus.PROCESSING)

        updated = await job_repository.update_job_status(sample_job.job_id, ProcessingStatus.COMPLETED)

        assert updated.status == ProcessingStatus.COMPLETED
        assert updated.completed_at is not None

    @pytest.mark.asyncio
    async def test_update_job_status_with_error(self, job_repository, sample_job):
        """Test status update with error message."""
        await job_repository.create_job(sample_job)

        updated = await job_repository.update_job_status(
            sample_job.job_id,
            ProcessingStatus.FAILED,
            error_message="Processing failed",
        )

        assert updated.status == ProcessingStatus.FAILED
        assert updated.error_message == "Processing failed"

    @pytest.mark.asyncio
    async def test_update_job_progress(self, job_repository, sample_job):
        """Test updating job progress."""
        await job_repository.create_job(sample_job)

        updated = await job_repository.update_job_progress(sample_job.job_id, 50, current_step="text_extraction")

        assert updated.progress == 50
        assert updated.current_step == "text_extraction"

    @pytest.mark.asyncio
    async def test_update_job_progress_clamps_values(self, job_repository, sample_job):
        """Test that progress is clamped to 0-100."""
        await job_repository.create_job(sample_job)

        # Test upper bound
        updated = await job_repository.update_job_progress(sample_job.job_id, 150)
        assert updated.progress == 100

        # Test lower bound
        updated = await job_repository.update_job_progress(sample_job.job_id, -10)
        assert updated.progress == 0

    @pytest.mark.asyncio
    async def test_increment_retry_count(self, job_repository, sample_job):
        """Test incrementing retry count."""
        await job_repository.create_job(sample_job)

        updated = await job_repository.increment_retry_count(sample_job.job_id)
        assert updated.retry_count == 1

        updated = await job_repository.increment_retry_count(sample_job.job_id)
        assert updated.retry_count == 2

    @pytest.mark.asyncio
    async def test_list_jobs_no_filter(self, job_repository):
        """Test listing all jobs."""
        job1 = ProcessingJob(job_id="job-1", book_id="book-1", publisher_id="pub-1")
        job2 = ProcessingJob(job_id="job-2", book_id="book-2", publisher_id="pub-1")
        await job_repository.create_job(job1)
        await job_repository.create_job(job2)

        jobs = await job_repository.list_jobs()

        assert len(jobs) == 2

    @pytest.mark.asyncio
    async def test_list_jobs_by_status(self, job_repository):
        """Test listing jobs filtered by status."""
        job1 = ProcessingJob(job_id="job-1", book_id="book-1", publisher_id="pub-1")
        job2 = ProcessingJob(job_id="job-2", book_id="book-2", publisher_id="pub-1")
        await job_repository.create_job(job1)
        await job_repository.create_job(job2)
        await job_repository.update_job_status("job-1", ProcessingStatus.PROCESSING)

        queued_jobs = await job_repository.list_jobs(status=ProcessingStatus.QUEUED)
        processing_jobs = await job_repository.list_jobs(status=ProcessingStatus.PROCESSING)

        assert len(queued_jobs) == 1
        assert queued_jobs[0].job_id == "job-2"
        assert len(processing_jobs) == 1
        assert processing_jobs[0].job_id == "job-1"

    @pytest.mark.asyncio
    async def test_list_jobs_by_book_id(self, job_repository):
        """Test listing jobs filtered by book ID."""
        job1 = ProcessingJob(job_id="job-1", book_id="book-1", publisher_id="pub-1")
        # Complete job1 first so we can create job2 for same book
        await job_repository.create_job(job1)
        await job_repository.update_job_status("job-1", ProcessingStatus.COMPLETED)

        job2 = ProcessingJob(job_id="job-2", book_id="book-1", publisher_id="pub-1")
        await job_repository.create_job(job2)

        jobs = await job_repository.list_jobs(book_id="book-1")

        assert len(jobs) == 2

    @pytest.mark.asyncio
    async def test_list_jobs_filters_out_bundle_jobs(self, job_repository):
        """AI status must not be polluted by bundle jobs sharing the book_id."""
        from app.services.queue.models import AI_BOOK_JOB_TYPES

        ai_job = ProcessingJob(
            job_id="ai-1",
            book_id="book-9",
            publisher_id="pub-1",
            job_type=ProcessingJobType.UNIFIED,
        )
        bundle_job = ProcessingJob(
            job_id="auto-bundle-9-win7-8",
            book_id="book-9",
            publisher_id="pub-1",
            job_type=ProcessingJobType.BUNDLE,
        )
        await job_repository.create_job(ai_job)
        # Bundle jobs are created alongside the AI job (same book_id) with the
        # duplicate guard disabled — exactly as the auto-bundle path does.
        await job_repository.create_job(bundle_job, check_duplicate=False)

        # Unfiltered: both jobs are present.
        assert len(await job_repository.list_jobs(book_id="book-9")) == 2

        # AI-only filter: only the AI job, even though the bundle exists.
        ai_only = await job_repository.list_jobs(
            book_id="book-9", job_types=AI_BOOK_JOB_TYPES
        )
        assert [j.job_id for j in ai_only] == ["ai-1"]

    @pytest.mark.asyncio
    async def test_delete_job(self, job_repository, sample_job):
        """Test deleting a job."""
        await job_repository.create_job(sample_job)

        result = await job_repository.delete_job(sample_job.job_id)
        assert result is True

        with pytest.raises(JobNotFoundError):
            await job_repository.get_job(sample_job.job_id)

    @pytest.mark.asyncio
    async def test_delete_nonexistent_job(self, job_repository):
        """Test deleting nonexistent job returns False."""
        result = await job_repository.delete_job("nonexistent")
        assert result is False

    @pytest.mark.asyncio
    async def test_count_jobs_by_status(self, job_repository):
        """Test counting jobs by status."""
        job1 = ProcessingJob(job_id="job-1", book_id="book-1", publisher_id="pub-1")
        job2 = ProcessingJob(job_id="job-2", book_id="book-2", publisher_id="pub-1")
        job3 = ProcessingJob(job_id="job-3", book_id="book-3", publisher_id="pub-1")
        await job_repository.create_job(job1)
        await job_repository.create_job(job2)
        await job_repository.create_job(job3)
        await job_repository.update_job_status("job-1", ProcessingStatus.PROCESSING)
        await job_repository.update_job_status("job-2", ProcessingStatus.COMPLETED)

        counts = await job_repository.count_jobs_by_status()

        assert counts[ProcessingStatus.QUEUED] == 1
        assert counts[ProcessingStatus.PROCESSING] == 1
        assert counts[ProcessingStatus.COMPLETED] == 1


# =============================================================================
# Progress Reporter Tests
# =============================================================================


class TestProgressReporter:
    """Tests for ProgressReporter."""

    @pytest.mark.asyncio
    async def test_report_progress_first_stage(self, job_repository, sample_job):
        """Test reporting progress for first stage."""
        await job_repository.create_job(sample_job)
        reporter = ProgressReporter(job_repository, sample_job.job_id)

        await reporter.report_progress("text_extraction", 50)

        job = await job_repository.get_job(sample_job.job_id)
        # text_extraction is 20% weight, 50% progress = 10% overall
        assert job.progress == 10
        assert job.current_step == "text_extraction"

    @pytest.mark.asyncio
    async def test_report_progress_middle_stage(self, job_repository, sample_job):
        """Test reporting progress for middle stage."""
        await job_repository.create_job(sample_job)
        reporter = ProgressReporter(job_repository, sample_job.job_id)

        # topic_analysis is 3rd stage, after text_extraction (20%) and segmentation (15%)
        # So 35% completed + 50% of 20% = 35 + 10 = 45%
        await reporter.report_progress("topic_analysis", 50)

        job = await job_repository.get_job(sample_job.job_id)
        assert job.progress == 45

    @pytest.mark.asyncio
    async def test_report_step_complete(self, job_repository, sample_job):
        """Test reporting step completion."""
        await job_repository.create_job(sample_job)
        reporter = ProgressReporter(job_repository, sample_job.job_id)

        await reporter.report_step_complete("text_extraction")

        job = await job_repository.get_job(sample_job.job_id)
        # text_extraction complete = 20%
        assert job.progress == 20

    @pytest.mark.asyncio
    async def test_report_substep(self, job_repository, sample_job):
        """Test reporting substep progress."""
        await job_repository.create_job(sample_job)
        reporter = ProgressReporter(job_repository, sample_job.job_id)

        # 2 of 4 substeps = 50% of stage
        await reporter.report_substep("text_extraction", 2, 4)

        job = await job_repository.get_job(sample_job.job_id)
        assert job.progress == 10  # 50% of 20%

    @pytest.mark.asyncio
    async def test_full_pipeline_progress(self, job_repository, sample_job):
        """Test progress through full pipeline."""
        await job_repository.create_job(sample_job)
        reporter = ProgressReporter(job_repository, sample_job.job_id)

        stages = list(PROCESSING_STAGES.keys())
        for stage in stages:
            await reporter.report_step_complete(stage)

        job = await job_repository.get_job(sample_job.job_id)
        assert job.progress == 100


# =============================================================================
# Queue Service Tests
# =============================================================================


class TestQueueService:
    """Tests for QueueService."""

    @pytest.mark.asyncio
    async def test_enqueue_job(self, queue_service, mock_arq_pool):
        """Test enqueueing a new job."""
        job = await queue_service.enqueue_job(
            book_id="book-123",
            publisher_id="pub-456",
            job_type=ProcessingJobType.FULL,
            priority=JobPriority.NORMAL,
        )

        assert job.book_id == "book-123"
        assert job.publisher_id == "pub-456"
        assert job.status == ProcessingStatus.QUEUED
        mock_arq_pool.enqueue_job.assert_called_once()

    @pytest.mark.asyncio
    async def test_enqueue_job_with_priority(self, queue_service, mock_arq_pool):
        """Test enqueueing a job with high priority."""
        job = await queue_service.enqueue_job(
            book_id="book-123",
            publisher_id="pub-456",
            priority=JobPriority.HIGH,
        )

        assert job.priority == JobPriority.HIGH
        # Check arq was called with correct queue name
        call_kwargs = mock_arq_pool.enqueue_job.call_args.kwargs
        assert "high" in call_kwargs["_queue_name"]

    @pytest.mark.asyncio
    async def test_enqueue_duplicate_job_fails(self, queue_service):
        """Test that duplicate active job fails."""
        await queue_service.enqueue_job(
            book_id="book-123",
            publisher_id="pub-456",
        )

        with pytest.raises(JobAlreadyExistsError):
            await queue_service.enqueue_job(
                book_id="book-123",
                publisher_id="pub-456",
            )

    @pytest.mark.asyncio
    async def test_get_job_status(self, queue_service):
        """Test getting job status."""
        created = await queue_service.enqueue_job(
            book_id="book-123",
            publisher_id="pub-456",
        )

        job = await queue_service.get_job_status(created.job_id)

        assert job.job_id == created.job_id
        assert job.status == ProcessingStatus.QUEUED

    @pytest.mark.asyncio
    async def test_cancel_queued_job(self, queue_service):
        """Test cancelling a queued job."""
        created = await queue_service.enqueue_job(
            book_id="book-123",
            publisher_id="pub-456",
        )

        cancelled = await queue_service.cancel_job(created.job_id)

        assert cancelled.status == ProcessingStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_cancel_completed_job_fails(self, queue_service, job_repository):
        """Test that cancelling completed job fails."""
        created = await queue_service.enqueue_job(
            book_id="book-123",
            publisher_id="pub-456",
        )
        await job_repository.update_job_status(created.job_id, ProcessingStatus.COMPLETED)

        with pytest.raises(QueueError) as exc_info:
            await queue_service.cancel_job(created.job_id)
        assert "Cannot cancel" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_retry_failed_job(self, queue_service, job_repository):
        """Test retrying a failed job."""
        created = await queue_service.enqueue_job(
            book_id="book-123",
            publisher_id="pub-456",
        )
        await job_repository.update_job_status(created.job_id, ProcessingStatus.FAILED, error_message="Test failure")

        retried = await queue_service.retry_job(created.job_id)

        assert retried.job_id != created.job_id  # New job
        assert retried.book_id == created.book_id
        assert retried.priority == JobPriority.HIGH  # Default for retries
        assert retried.metadata.get("retry_of") == created.job_id

    @pytest.mark.asyncio
    async def test_retry_queued_job_fails(self, queue_service):
        """Test that retrying queued job fails."""
        created = await queue_service.enqueue_job(
            book_id="book-123",
            publisher_id="pub-456",
        )

        with pytest.raises(QueueError) as exc_info:
            await queue_service.retry_job(created.job_id)
        assert "Cannot retry" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_get_queue_stats(self, queue_service, job_repository):
        """Test getting queue statistics."""
        # Create some jobs in different states
        job1 = await queue_service.enqueue_job(book_id="book-1", publisher_id="pub-1")
        job2 = await queue_service.enqueue_job(book_id="book-2", publisher_id="pub-1")
        await job_repository.update_job_status(job1.job_id, ProcessingStatus.PROCESSING)
        await job_repository.update_job_status(job2.job_id, ProcessingStatus.COMPLETED)

        stats = await queue_service.get_queue_stats()

        assert isinstance(stats, QueueStats)
        assert stats.total_jobs == 2
        assert stats.processing_jobs == 1
        assert stats.completed_jobs == 1

    @pytest.mark.asyncio
    async def test_list_jobs(self, queue_service):
        """Test listing jobs."""
        await queue_service.enqueue_job(book_id="book-1", publisher_id="pub-1")
        await queue_service.enqueue_job(book_id="book-2", publisher_id="pub-1")

        jobs = await queue_service.list_jobs()

        assert len(jobs) == 2

    @pytest.mark.asyncio
    async def test_list_jobs_with_filter(self, queue_service, job_repository):
        """Test listing jobs with status filter."""
        job1 = await queue_service.enqueue_job(book_id="book-1", publisher_id="pub-1")
        await queue_service.enqueue_job(book_id="book-2", publisher_id="pub-1")
        await job_repository.update_job_status(job1.job_id, ProcessingStatus.PROCESSING)

        processing = await queue_service.list_jobs(status=ProcessingStatus.PROCESSING)

        assert len(processing) == 1
        assert processing[0].job_id == job1.job_id

    @pytest.mark.asyncio
    async def test_get_progress_reporter(self, queue_service):
        """Test getting progress reporter."""
        job = await queue_service.enqueue_job(
            book_id="book-123",
            publisher_id="pub-456",
        )

        reporter = queue_service.get_progress_reporter(job.job_id)

        assert isinstance(reporter, ProgressReporter)


# =============================================================================
# Redis Connection Tests
# =============================================================================


class TestRedisConnection:
    """Tests for RedisConnection."""

    @pytest.mark.asyncio
    async def test_connection_health_check(self, mock_redis):
        """Test health check when connected."""
        connection = RedisConnection(url="redis://localhost:6379")
        connection._client = mock_redis

        result = await connection.health_check()

        assert result is True

    @pytest.mark.asyncio
    async def test_health_check_not_connected(self):
        """Test health check when not connected."""
        connection = RedisConnection(url="redis://localhost:6379")

        result = await connection.health_check()

        assert result is False

    def test_client_property_raises_when_not_connected(self):
        """Test client property raises when not connected."""
        connection = RedisConnection(url="redis://localhost:6379")

        with pytest.raises(QueueConnectionError):
            _ = connection.client

    def test_is_connected_false_initially(self):
        """Test is_connected is False initially."""
        connection = RedisConnection(url="redis://localhost:6379")

        assert connection.is_connected is False


# =============================================================================
# Processing Stages Tests
# =============================================================================


class TestProcessingStages:
    """Tests for processing stage configuration."""

    def test_stages_sum_to_100(self):
        """Test that stage weights sum to 100%."""
        total = sum(PROCESSING_STAGES.values())
        assert total == 100

    def test_all_stages_present(self):
        """Test all expected stages are defined."""
        expected_stages = {
            "text_extraction",
            "segmentation",
            "topic_analysis",
            "vocabulary",
            "audio_generation",
        }
        assert set(PROCESSING_STAGES.keys()) == expected_stages
