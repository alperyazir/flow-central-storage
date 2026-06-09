"""Queue infrastructure models for AI processing jobs."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


def _utc_now() -> datetime:
    """Get current UTC time as timezone-aware datetime."""
    return datetime.now(timezone.utc)


class ProcessingStatus(str, Enum):
    """Status of a processing job."""

    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    PARTIAL = "partial"  # Some steps succeeded, others failed
    CANCELLED = "cancelled"


class ProcessingJobType(str, Enum):
    """Type of processing job.

    Main 4 options for UI (Books):
    - FULL: Complete pipeline (text + LLM + audio)
    - TEXT_ONLY: Just PDF extraction
    - LLM_ONLY: AI analysis only (requires text extraction done)
    - AUDIO_ONLY: Audio generation only (requires LLM analysis done)

    Material options (Teacher Materials):
    - MATERIAL_FULL: Full material processing (text + LLM + audio)
    - MATERIAL_TEXT_ONLY: Extract text only
    - MATERIAL_LLM_ONLY: AI analysis only

    Bundle options:
    - BUNDLE: Create standalone app bundle
    """

    # Main 4 options for books
    FULL = "full"  # Complete pipeline: text extraction + LLM analysis + audio
    TEXT_ONLY = "text_only"  # PDF extraction only
    LLM_ONLY = "llm_only"  # AI analysis only (chunked approach)
    AUDIO_ONLY = "audio_only"  # Audio generation only

    # Material processing options
    MATERIAL_FULL = "material_full"  # Complete material pipeline
    MATERIAL_TEXT_ONLY = "material_text_only"  # Material text extraction only
    MATERIAL_LLM_ONLY = "material_llm_only"  # Material AI analysis only

    # Bundle creation
    BUNDLE = "bundle"  # Create standalone app bundle

    # Legacy/advanced options (kept for backwards compatibility)
    UNIFIED = "unified"  # Legacy: single LLM call approach
    ANALYSIS_ONLY = "analysis_only"  # Legacy: text + LLM, no audio
    VOCABULARY_ONLY = "vocabulary_only"  # Legacy: vocabulary extraction only


# AI book-processing job types — everything that contributes to a book's AI
# processing state. Deliberately EXCLUDES BUNDLE (and material) jobs, which
# share the same book_id but must not pollute the book's AI status.
AI_BOOK_JOB_TYPES: set["ProcessingJobType"] = {
    ProcessingJobType.FULL,
    ProcessingJobType.TEXT_ONLY,
    ProcessingJobType.LLM_ONLY,
    ProcessingJobType.AUDIO_ONLY,
    ProcessingJobType.UNIFIED,
    ProcessingJobType.ANALYSIS_ONLY,
    ProcessingJobType.VOCABULARY_ONLY,
}


class JobPriority(str, Enum):
    """Priority level for job execution."""

    HIGH = "high"  # Admin re-processing, urgent
    NORMAL = "normal"  # Standard auto-processing
    LOW = "low"  # Bulk/batch processing


@dataclass
class ProcessingJob:
    """Represents an AI processing job for a book."""

    job_id: str
    book_id: str
    publisher_id: str
    job_type: ProcessingJobType = ProcessingJobType.FULL
    status: ProcessingStatus = ProcessingStatus.QUEUED
    priority: JobPriority = JobPriority.NORMAL
    progress: int = 0  # 0-100 percentage
    current_step: str = ""  # Current processing step
    error_message: str | None = None
    retry_count: int = 0
    created_at: datetime = field(default_factory=_utc_now)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    metadata: dict = field(default_factory=dict)  # Additional job context


@dataclass
class QueueStats:
    """Statistics about the processing queue."""

    total_jobs: int
    queued_jobs: int
    processing_jobs: int
    completed_jobs: int
    failed_jobs: int
    active_workers: int


# Processing stages with weight percentages
PROCESSING_STAGES = {
    "text_extraction": 20,  # PDF -> text files
    "segmentation": 15,  # Text -> modules
    "topic_analysis": 20,  # AI topic extraction
    "vocabulary": 20,  # AI vocabulary extraction
    "audio_generation": 25,  # TTS for vocabulary
}

# Full processing stages (chunked LLM approach - recommended)
FULL_PROCESSING_STAGES = {
    "text_extraction": 20,  # PDF -> text files
    "chunked_analysis": 55,  # Two-phase: detect modules + extract vocabulary per module
    "audio_generation": 25,  # TTS for vocabulary
}

# LLM-only stages (chunked analysis, no text extraction or audio)
LLM_ONLY_STAGES = {
    "chunked_analysis": 100,  # Two-phase chunked analysis
}

# Unified processing stages (single LLM call for analysis - legacy)
UNIFIED_PROCESSING_STAGES = {
    "text_extraction": 25,  # PDF -> text files
    "unified_analysis": 50,  # Single LLM call: modules + topics + vocabulary
    "audio_generation": 25,  # TTS for vocabulary
}

# Analysis-only stages (no audio generation - legacy)
ANALYSIS_ONLY_STAGES = {
    "text_extraction": 40,  # PDF -> text files
    "unified_analysis": 60,  # Single LLM call: modules + topics + vocabulary
}

# Material processing stages (full pipeline)
MATERIAL_FULL_STAGES = {
    "material_text_extraction": 25,  # Extract text from PDF/TXT/DOCX
    "material_analysis": 50,  # AI analysis of content
    "material_audio": 25,  # Audio generation for vocabulary
}

# Material text extraction only
MATERIAL_TEXT_ONLY_STAGES = {
    "material_text_extraction": 100,
}

# Material LLM analysis only (requires text extraction done)
MATERIAL_LLM_ONLY_STAGES = {
    "material_analysis": 100,
}

# Bundle creation stages
BUNDLE_STAGES = {
    "download_template": 10,  # Download template from MinIO
    "extract_template": 10,  # Extract template zip
    "download_assets": 40,  # Download book assets from MinIO
    "create_bundle": 30,  # Create bundle zip
    "upload_bundle": 10,  # Upload bundle to MinIO
}


class QueueError(Exception):
    """Base exception for queue errors."""

    def __init__(self, message: str, details: dict | None = None):
        self.message = message
        self.details = details or {}
        super().__init__(message)


class JobNotFoundError(QueueError):
    """Raised when a job is not found in the queue."""

    def __init__(self, job_id: str):
        super().__init__(f"Job not found: {job_id}", {"job_id": job_id})


class JobAlreadyExistsError(QueueError):
    """Raised when trying to create a duplicate job."""

    def __init__(self, book_id: str):
        super().__init__(f"Job already exists for book: {book_id}", {"book_id": book_id})


class QueueConnectionError(QueueError):
    """Raised when Redis connection fails."""

    pass
