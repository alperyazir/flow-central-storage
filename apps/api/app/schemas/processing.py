"""Pydantic schemas for AI processing API endpoints."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.services.queue.models import JobPriority, ProcessingJobType, ProcessingStatus


class ProcessingTriggerRequest(BaseModel):
    """Request payload to trigger AI processing for a book."""

    job_type: ProcessingJobType = Field(
        default=ProcessingJobType.UNIFIED,
        description="Type of processing to perform (unified uses single LLM call for better accuracy)",
    )
    priority: JobPriority = Field(
        default=JobPriority.NORMAL,
        description="Job priority level",
    )
    admin_override: bool = Field(
        default=False,
        description="Bypass rate limiting (requires admin)",
    )


class ProcessingJobResponse(BaseModel):
    """Response representing a processing job."""

    job_id: str
    book_id: str | int
    publisher_id: str | int
    job_type: ProcessingJobType
    status: ProcessingStatus
    priority: JobPriority
    progress: int = Field(ge=0, le=100)
    current_step: str
    error_message: str | None = None
    retry_count: int = 0
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class ProcessingStatusResponse(BaseModel):
    """Response for processing status endpoint."""

    job_id: str
    book_id: str | int
    status: ProcessingStatus
    progress: int = Field(ge=0, le=100)
    current_step: str
    error_message: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class QueueStatsResponse(BaseModel):
    """Response for queue statistics."""

    total_jobs: int
    queued_jobs: int
    processing_jobs: int
    completed_jobs: int
    failed_jobs: int
    cancelled_jobs: int


class CleanupStatsResponse(BaseModel):
    """Response for AI data cleanup operation."""

    total_deleted: int = 0
    text_deleted: int = 0
    modules_deleted: int = 0
    audio_deleted: int = 0
    vocabulary_deleted: int = 0
    metadata_deleted: int = 0
    errors: list[str] = Field(default_factory=list)
