"""Job repository for Redis-based job storage."""

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from redis.asyncio import Redis

from app.services.queue.models import (
    JobAlreadyExistsError,
    JobNotFoundError,
    JobPriority,
    ProcessingJob,
    ProcessingJobType,
    ProcessingStatus,
)

logger = logging.getLogger(__name__)

# Redis key prefixes
JOB_KEY_PREFIX = "dcs:job:"
BOOK_JOB_INDEX_PREFIX = "dcs:book_jobs:"
STATUS_INDEX_PREFIX = "dcs:jobs_by_status:"


class JobRepository:
    """Repository for managing processing jobs in Redis."""

    def __init__(self, redis_client: Redis, job_ttl_seconds: int = 604800):
        """Initialize job repository.

        Args:
            redis_client: Redis client instance
            job_ttl_seconds: TTL for completed/failed jobs (default 7 days)
        """
        self._redis = redis_client
        self._job_ttl = job_ttl_seconds

    def _job_key(self, job_id: str) -> str:
        """Get Redis key for a job."""
        return f"{JOB_KEY_PREFIX}{job_id}"

    def _book_jobs_key(self, book_id: str) -> str:
        """Get Redis key for book's job index."""
        return f"{BOOK_JOB_INDEX_PREFIX}{book_id}"

    def _status_index_key(self, status: ProcessingStatus) -> str:
        """Get Redis key for status index."""
        return f"{STATUS_INDEX_PREFIX}{status.value}"

    def _serialize_job(self, job: ProcessingJob) -> dict:
        """Serialize job to Redis hash format."""
        return {
            "job_id": job.job_id,
            "book_id": job.book_id,
            "publisher_id": job.publisher_id,
            "job_type": job.job_type.value,
            "status": job.status.value,
            "priority": job.priority.value,
            "progress": str(job.progress),
            "current_step": job.current_step,
            "error_message": job.error_message or "",
            "retry_count": str(job.retry_count),
            "created_at": job.created_at.isoformat(),
            "started_at": job.started_at.isoformat() if job.started_at else "",
            "completed_at": job.completed_at.isoformat() if job.completed_at else "",
            "metadata": json.dumps(job.metadata),
        }

    def _deserialize_job(self, data: dict) -> ProcessingJob:
        """Deserialize job from Redis hash format."""
        return ProcessingJob(
            job_id=data["job_id"],
            book_id=data["book_id"],
            publisher_id=data["publisher_id"],
            job_type=ProcessingJobType(data["job_type"]),
            status=ProcessingStatus(data["status"]),
            priority=JobPriority(data["priority"]),
            progress=int(data["progress"]),
            current_step=data["current_step"],
            error_message=data["error_message"] or None,
            retry_count=int(data["retry_count"]),
            created_at=datetime.fromisoformat(data["created_at"]),
            started_at=(datetime.fromisoformat(data["started_at"]) if data["started_at"] else None),
            completed_at=(datetime.fromisoformat(data["completed_at"]) if data["completed_at"] else None),
            metadata=json.loads(data["metadata"]) if data["metadata"] else {},
        )

    async def create_job(
        self,
        job: ProcessingJob,
        check_duplicate: bool = True,
    ) -> ProcessingJob:
        """Create a new job in Redis.

        Args:
            job: The job to create
            check_duplicate: Whether to check for existing active job for book

        Returns:
            The created job

        Raises:
            JobAlreadyExistsError: If active job already exists for book
        """
        if check_duplicate:
            existing = await self.get_active_job_for_book(job.book_id)
            if existing:
                raise JobAlreadyExistsError(job.book_id)

        job_key = self._job_key(job.job_id)
        job_data = self._serialize_job(job)

        # Store job hash
        await self._redis.hset(job_key, mapping=job_data)

        # Add to book's job index
        await self._redis.sadd(self._book_jobs_key(job.book_id), job.job_id)

        # Add to status index
        await self._redis.sadd(self._status_index_key(job.status), job.job_id)

        logger.info("Created job %s for book %s", job.job_id, job.book_id)
        return job

    async def get_job(self, job_id: str) -> ProcessingJob:
        """Retrieve a job by ID.

        Args:
            job_id: The job ID

        Returns:
            The job

        Raises:
            JobNotFoundError: If job not found
        """
        job_key = self._job_key(job_id)
        data = await self._redis.hgetall(job_key)

        if not data:
            raise JobNotFoundError(job_id)

        return self._deserialize_job(data)

    async def get_active_job_for_book(self, book_id: str) -> Optional[ProcessingJob]:
        """Get active (queued/processing) job for a book.

        Args:
            book_id: The book ID

        Returns:
            Active job or None
        """
        job_ids = await self._redis.smembers(self._book_jobs_key(book_id))

        for job_id in job_ids:
            try:
                job = await self.get_job(job_id)
                if job.status in (ProcessingStatus.QUEUED, ProcessingStatus.PROCESSING):
                    return job
            except JobNotFoundError:
                # Job was deleted, clean up index
                await self._redis.srem(self._book_jobs_key(book_id), job_id)

        return None

    async def update_job_status(
        self,
        job_id: str,
        status: ProcessingStatus,
        error_message: Optional[str] = None,
    ) -> ProcessingJob:
        """Update job status.

        Args:
            job_id: The job ID
            status: New status
            error_message: Error message if failed

        Returns:
            Updated job

        Raises:
            JobNotFoundError: If job not found
        """
        job = await self.get_job(job_id)
        old_status = job.status

        # Remove from old status index
        await self._redis.srem(self._status_index_key(old_status), job_id)

        # Update fields
        updates = {"status": status.value}
        now = datetime.now(timezone.utc)

        if status == ProcessingStatus.PROCESSING and job.started_at is None:
            updates["started_at"] = now.isoformat()
        elif status in (
            ProcessingStatus.COMPLETED,
            ProcessingStatus.FAILED,
            ProcessingStatus.PARTIAL,
            ProcessingStatus.CANCELLED,
        ):
            updates["completed_at"] = now.isoformat()

        if error_message is not None:
            updates["error_message"] = error_message

        job_key = self._job_key(job_id)
        await self._redis.hset(job_key, mapping=updates)

        # Add to new status index
        await self._redis.sadd(self._status_index_key(status), job_id)

        # Set TTL for completed/failed jobs
        if status in (
            ProcessingStatus.COMPLETED,
            ProcessingStatus.FAILED,
            ProcessingStatus.CANCELLED,
        ):
            await self._redis.expire(job_key, self._job_ttl)

        logger.info("Updated job %s status: %s -> %s", job_id, old_status.value, status.value)
        return await self.get_job(job_id)

    async def update_job_progress(
        self,
        job_id: str,
        progress: int,
        current_step: Optional[str] = None,
    ) -> ProcessingJob:
        """Update job progress.

        Args:
            job_id: The job ID
            progress: Progress percentage (0-100)
            current_step: Current processing step name

        Returns:
            Updated job

        Raises:
            JobNotFoundError: If job not found
        """
        # Verify job exists
        await self.get_job(job_id)

        updates = {"progress": str(max(0, min(100, progress)))}
        if current_step is not None:
            updates["current_step"] = current_step

        job_key = self._job_key(job_id)
        await self._redis.hset(job_key, mapping=updates)

        logger.debug("Updated job %s progress: %d%%", job_id, progress)
        return await self.get_job(job_id)

    async def increment_retry_count(self, job_id: str) -> ProcessingJob:
        """Increment job retry count.

        Args:
            job_id: The job ID

        Returns:
            Updated job

        Raises:
            JobNotFoundError: If job not found
        """
        job = await self.get_job(job_id)

        job_key = self._job_key(job_id)
        await self._redis.hincrby(job_key, "retry_count", 1)

        logger.info("Incremented retry count for job %s to %d", job_id, job.retry_count + 1)
        return await self.get_job(job_id)

    async def list_jobs(
        self,
        status: Optional[ProcessingStatus] = None,
        book_id: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
        job_types: Optional[set[ProcessingJobType]] = None,
    ) -> list[ProcessingJob]:
        """List jobs with optional filtering.

        Args:
            status: Filter by status
            book_id: Filter by book ID
            limit: Maximum results
            offset: Skip first N results
            job_types: If given, keep only jobs whose ``job_type`` is in this set
                (e.g. AI-processing types, excluding bundle jobs).

        Returns:
            List of jobs, most recent first.
        """
        job_ids: set = set()

        if book_id:
            job_ids = await self._redis.smembers(self._book_jobs_key(book_id))
        elif status:
            job_ids = await self._redis.smembers(self._status_index_key(status))
        else:
            # Get all jobs from all status indices
            for s in ProcessingStatus:
                ids = await self._redis.smembers(self._status_index_key(s))
                job_ids.update(ids)

        # Fetch and filter ALL matching jobs, then sort, then page — so that
        # type/status filtering and "most recent" (limit) compose correctly.
        jobs = []
        for job_id in job_ids:
            try:
                job = await self.get_job(job_id)
            except JobNotFoundError:
                continue
            if status and job.status != status:
                continue
            if job_types is not None and job.job_type not in job_types:
                continue
            jobs.append(job)

        # Sort by created_at descending, then apply offset/limit.
        jobs.sort(key=lambda j: j.created_at, reverse=True)
        return jobs[offset : offset + limit]

    async def delete_job(self, job_id: str) -> bool:
        """Delete a job.

        Args:
            job_id: The job ID

        Returns:
            True if deleted, False if not found
        """
        try:
            job = await self.get_job(job_id)
        except JobNotFoundError:
            return False

        job_key = self._job_key(job_id)

        # Remove from indices
        await self._redis.srem(self._book_jobs_key(job.book_id), job_id)
        await self._redis.srem(self._status_index_key(job.status), job_id)

        # Delete job hash
        await self._redis.delete(job_key)

        logger.info("Deleted job %s", job_id)
        return True

    async def count_jobs_by_status(self) -> dict[ProcessingStatus, int]:
        """Count jobs by status.

        Returns:
            Dict mapping status to count
        """
        counts = {}
        for status in ProcessingStatus:
            count = await self._redis.scard(self._status_index_key(status))
            counts[status] = count
        return counts
