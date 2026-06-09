"""Queue service for managing AI processing jobs with arq."""

import logging
import uuid
from typing import Optional

from arq import ArqRedis, create_pool
from arq.connections import RedisSettings
from arq.jobs import Job

from app.core.config import get_settings
from app.services.queue.models import (
    PROCESSING_STAGES,
    JobPriority,
    ProcessingJob,
    ProcessingJobType,
    ProcessingStatus,
    QueueError,
    QueueStats,
)
from app.services.queue.redis import RedisConnection, get_redis_connection
from app.services.queue.repository import JobRepository

logger = logging.getLogger(__name__)


class ProgressReporter:
    """Reports progress for processing jobs."""

    def __init__(self, repository: JobRepository, job_id: str):
        """Initialize progress reporter.

        Args:
            repository: Job repository
            job_id: Job ID to report progress for
        """
        self._repository = repository
        self._job_id = job_id
        self._current_stage: Optional[str] = None
        self._stage_order = list(PROCESSING_STAGES.keys())

    def _calculate_overall_progress(self, stage: str, stage_progress: int) -> int:
        """Calculate overall progress based on stage and stage progress.

        Args:
            stage: Current processing stage
            stage_progress: Progress within stage (0-100)

        Returns:
            Overall progress (0-100)
        """
        if stage not in PROCESSING_STAGES:
            return 0

        stage_weight = PROCESSING_STAGES[stage]
        stage_index = self._stage_order.index(stage)

        # Sum weights of completed stages
        stages_before = sum(PROCESSING_STAGES[s] for s in self._stage_order[:stage_index])

        # Add progress within current stage
        stage_contribution = stage_progress * stage_weight // 100
        return min(100, stages_before + stage_contribution)

    async def report_progress(
        self,
        stage: str,
        stage_progress: int,
        step_detail: str | None = None,
    ) -> None:
        """Update job progress based on stage and stage progress.

        Args:
            stage: Current processing stage
            stage_progress: Progress within stage (0-100)
            step_detail: Optional detailed description of current step
        """
        overall_progress = self._calculate_overall_progress(stage, stage_progress)
        self._current_stage = stage

        # Use step_detail if provided, otherwise use stage name
        current_step = step_detail if step_detail else stage

        await self._repository.update_job_progress(
            self._job_id,
            overall_progress,
            current_step=current_step,
        )
        logger.debug(
            "Job %s progress: stage=%s, stage_progress=%d%%, overall=%d%%, detail=%s",
            self._job_id,
            stage,
            stage_progress,
            overall_progress,
            step_detail or stage,
        )

    async def report_step_complete(self, stage: str) -> None:
        """Mark a processing stage as complete.

        Args:
            stage: Stage that completed
        """
        await self.report_progress(stage, 100)
        logger.info("Job %s completed stage: %s", self._job_id, stage)

    async def report_substep(
        self,
        stage: str,
        substep: int,
        total_substeps: int,
    ) -> None:
        """Report progress on a substep within a stage.

        Args:
            stage: Current processing stage
            substep: Current substep number (1-based)
            total_substeps: Total number of substeps
        """
        stage_progress = (substep * 100) // total_substeps
        await self.report_progress(stage, stage_progress)


class QueueService:
    """Service for managing the AI processing queue."""

    def __init__(
        self,
        redis_connection: RedisConnection,
        repository: JobRepository,
        arq_pool: Optional[ArqRedis] = None,
    ):
        """Initialize queue service.

        Args:
            redis_connection: Redis connection manager
            repository: Job repository
            arq_pool: Optional arq Redis pool (created on demand if not provided)
        """
        self._redis_conn = redis_connection
        self._repository = repository
        self._arq_pool = arq_pool
        self._settings = get_settings()

    async def _get_arq_pool(self) -> ArqRedis:
        """Get or create arq Redis pool."""
        if self._arq_pool is None:
            redis_settings = RedisSettings.from_dsn(self._settings.redis_url)
            self._arq_pool = await create_pool(redis_settings)
        return self._arq_pool

    async def enqueue_job(
        self,
        book_id: str,
        publisher_id: str,
        job_type: ProcessingJobType = ProcessingJobType.FULL,
        priority: JobPriority = JobPriority.NORMAL,
        metadata: Optional[dict] = None,
    ) -> ProcessingJob:
        """Add a new processing job to the queue.

        Args:
            book_id: Book ID to process
            publisher_id: Publisher ID
            job_type: Type of processing
            priority: Job priority
            metadata: Additional job metadata

        Returns:
            Created processing job

        Raises:
            JobAlreadyExistsError: If active job exists for book
            QueueError: If enqueue fails
        """
        job_id = str(uuid.uuid4())

        # Create job record
        job = ProcessingJob(
            job_id=job_id,
            book_id=book_id,
            publisher_id=publisher_id,
            job_type=job_type,
            priority=priority,
            metadata=metadata or {},
        )

        # Store in repository (checks for duplicates)
        await self._repository.create_job(job)

        try:
            # Enqueue in arq
            pool = await self._get_arq_pool()

            # Map priority to arq queue name
            queue_name = f"{self._settings.queue_name}:{priority.value}"

            await pool.enqueue_job(
                "process_book_task",
                job_id=job_id,
                book_id=book_id,
                publisher_id=publisher_id,
                job_type=job_type.value,
                metadata=metadata,
                _queue_name=queue_name,
            )

            logger.info(
                "Enqueued job %s for book %s with priority %s",
                job_id,
                book_id,
                priority.value,
            )
            return job

        except Exception as e:
            # Rollback job creation on enqueue failure
            await self._repository.delete_job(job_id)
            logger.error("Failed to enqueue job %s: %s", job_id, e)
            raise QueueError(f"Failed to enqueue job: {e}") from e

    async def get_job_status(self, job_id: str) -> ProcessingJob:
        """Get current job status.

        Args:
            job_id: Job ID

        Returns:
            Job with current status

        Raises:
            JobNotFoundError: If job not found
        """
        return await self._repository.get_job(job_id)

    async def cancel_job(self, job_id: str) -> ProcessingJob:
        """Cancel a queued or in-progress job.

        Args:
            job_id: Job ID

        Returns:
            Cancelled job

        Raises:
            JobNotFoundError: If job not found
            QueueError: If job cannot be cancelled
        """
        job = await self._repository.get_job(job_id)

        if job.status not in (ProcessingStatus.QUEUED, ProcessingStatus.PROCESSING):
            raise QueueError(
                f"Cannot cancel job with status {job.status.value}",
                {"job_id": job_id, "status": job.status.value},
            )

        # Try to abort arq job
        try:
            pool = await self._get_arq_pool()
            arq_job = Job(job_id, pool)
            await arq_job.abort()
        except Exception as e:
            logger.warning("Could not abort arq job %s: %s", job_id, e)

        # Update status
        return await self._repository.update_job_status(
            job_id,
            ProcessingStatus.CANCELLED,
        )

    async def delete_job(self, job_id: str) -> bool:
        """Delete a job from the queue.

        Used to clear failed jobs so they can be reprocessed.

        Args:
            job_id: Job ID to delete

        Returns:
            True if deleted, False if not found
        """
        return await self._repository.delete_job(job_id)

    async def retry_job(
        self,
        job_id: str,
        priority: Optional[JobPriority] = None,
    ) -> ProcessingJob:
        """Re-queue a failed job.

        Args:
            job_id: Job ID to retry
            priority: Optional new priority (defaults to HIGH for retries)

        Returns:
            New job for retry

        Raises:
            JobNotFoundError: If job not found
            QueueError: If job cannot be retried
        """
        job = await self._repository.get_job(job_id)

        if job.status not in (ProcessingStatus.FAILED, ProcessingStatus.PARTIAL):
            raise QueueError(
                f"Cannot retry job with status {job.status.value}",
                {"job_id": job_id, "status": job.status.value},
            )

        # Use HIGH priority for retries by default
        retry_priority = priority or JobPriority.HIGH

        # Create new job with same parameters
        return await self.enqueue_job(
            book_id=job.book_id,
            publisher_id=job.publisher_id,
            job_type=job.job_type,
            priority=retry_priority,
            metadata={
                **job.metadata,
                "retry_of": job_id,
                "retry_count": job.retry_count + 1,
            },
        )

    async def get_queue_stats(self) -> QueueStats:
        """Get queue statistics.

        Returns:
            Queue statistics
        """
        counts = await self._repository.count_jobs_by_status()

        # Get active worker count from arq
        active_workers = 0
        try:
            pool = await self._get_arq_pool()
            # arq stores worker info in Redis
            workers = await pool.all_job_results()
            active_workers = len([w for w in workers if w])
        except Exception as e:
            logger.warning("Could not get worker count: %s", e)

        return QueueStats(
            total_jobs=sum(counts.values()),
            queued_jobs=counts.get(ProcessingStatus.QUEUED, 0),
            processing_jobs=counts.get(ProcessingStatus.PROCESSING, 0),
            completed_jobs=counts.get(ProcessingStatus.COMPLETED, 0),
            failed_jobs=counts.get(ProcessingStatus.FAILED, 0),
            active_workers=active_workers,
        )

    async def list_jobs(
        self,
        status: Optional[ProcessingStatus] = None,
        book_id: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
        job_types: Optional[set[ProcessingJobType]] = None,
    ) -> list[ProcessingJob]:
        """List jobs with filtering.

        Args:
            status: Filter by status
            book_id: Filter by book ID
            limit: Maximum results
            offset: Skip first N results
            job_types: Keep only these job types (e.g. AI types, excluding bundles)

        Returns:
            List of jobs
        """
        return await self._repository.list_jobs(
            status=status,
            book_id=book_id,
            limit=limit,
            offset=offset,
            job_types=job_types,
        )

    def get_progress_reporter(self, job_id: str) -> ProgressReporter:
        """Get a progress reporter for a job.

        Args:
            job_id: Job ID

        Returns:
            ProgressReporter instance
        """
        return ProgressReporter(self._repository, job_id)

    async def close(self) -> None:
        """Close the queue service and release resources."""
        if self._arq_pool:
            await self._arq_pool.close()
            self._arq_pool = None


# Global service instance
_queue_service: Optional[QueueService] = None


async def get_queue_service() -> QueueService:
    """Get or create the global queue service instance.

    Returns:
        QueueService instance
    """
    global _queue_service

    if _queue_service is None:
        settings = get_settings()
        redis_conn = await get_redis_connection(url=settings.redis_url)
        repository = JobRepository(
            redis_client=redis_conn.client,
            job_ttl_seconds=settings.queue_job_ttl_seconds,
        )
        _queue_service = QueueService(redis_conn, repository)

    return _queue_service


async def close_queue_service() -> None:
    """Close the global queue service."""
    global _queue_service

    if _queue_service:
        await _queue_service.close()
        _queue_service = None
