"""API endpoints for AI processing operations."""

from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import decode_access_token, verify_api_key_from_db
from app.db import SessionLocal, get_db
from app.repositories.book import BookRepository
from app.repositories.publisher import PublisherRepository
from app.repositories.user import UserRepository
from app.schemas.processing import (
    CleanupStatsResponse,
    ProcessingJobResponse,
    ProcessingStatusResponse,
    ProcessingTriggerRequest,
)
from app.services import get_minio_client
from app.services.ai_data import get_ai_data_cleanup_manager, get_ai_data_retrieval_service
from app.services.queue.models import (
    JobAlreadyExistsError,
    JobPriority,
    ProcessingJobType,
    ProcessingStatus,
)
from app.services.queue.service import get_queue_service

router = APIRouter(prefix="/books", tags=["AI Processing"])
dashboard_router = APIRouter(prefix="/processing", tags=["Processing Dashboard"])
_bearer_scheme = HTTPBearer(auto_error=True)
_book_repository = BookRepository()
_publisher_repository = PublisherRepository()
_user_repository = UserRepository()
logger = logging.getLogger(__name__)

# Rate limiting constants
RATE_LIMIT_WINDOW = 3600  # 1 hour in seconds
MAX_JOBS_PER_PUBLISHER = 10


def _require_auth(credentials: HTTPAuthorizationCredentials, db: Session) -> int:
    """Validate JWT token or API key and return user ID or -1 for API key auth."""
    token = credentials.credentials

    # Try JWT first
    try:
        payload = decode_access_token(token, settings=get_settings())
        subject = payload.get("sub")
        if subject is not None:
            try:
                user_id = int(subject)
                user = _user_repository.get(db, user_id)
                if user is not None:
                    return user_id
            except (TypeError, ValueError):
                pass
    except ValueError:
        pass  # JWT failed, try API key

    # Try API key
    api_key_info = verify_api_key_from_db(token, db)
    if api_key_info is not None:
        return -1  # API key authentication

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid token",
    )


async def _check_rate_limit(publisher_id: int) -> tuple[bool, int]:
    """Check if publisher has exceeded rate limit.

    Args:
        publisher_id: Publisher ID to check

    Returns:
        Tuple of (is_allowed, retry_after_seconds)
    """
    settings = get_settings()
    from app.services.queue.redis import get_redis_connection

    redis_conn = await get_redis_connection(url=settings.redis_url)
    key = f"dcs:rate_limit:{publisher_id}"

    current = await redis_conn.client.incr(key)
    if current == 1:
        await redis_conn.client.expire(key, RATE_LIMIT_WINDOW)

    ttl = await redis_conn.client.ttl(key)
    if ttl < 0:
        ttl = RATE_LIMIT_WINDOW

    if current > MAX_JOBS_PER_PUBLISHER:
        return False, ttl
    return True, 0


def _book_has_content(book, publisher_slug: str) -> bool:
    """Check if book has content in MinIO storage."""
    settings = get_settings()
    client = get_minio_client(settings)
    prefix = f"{publisher_slug}/books/{book.book_name}/"

    try:
        objects = list(
            client.list_objects(
                settings.minio_publishers_bucket,
                prefix=prefix,
                recursive=False,
            )
        )
        return len(objects) > 0
    except Exception as e:
        logger.error("Failed to check book content: %s", e)
        return False


@router.post(
    "/{book_id}/process-ai",
    response_model=ProcessingJobResponse,
    status_code=status.HTTP_201_CREATED,
)
async def trigger_processing(
    book_id: int,
    payload: ProcessingTriggerRequest,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> ProcessingJobResponse:
    """Trigger AI processing for a book.

    Queues a new processing job for the specified book. The job will extract
    text, segment content, analyze topics, extract vocabulary, and generate
    audio pronunciations.

    Args:
        book_id: ID of the book to process
        payload: Processing options (job_type, priority, admin_override)

    Returns:
        ProcessingJobResponse with job details

    Raises:
        404: Book not found
        400: Book has no content to process
        409: Active processing job already exists for book
        429: Rate limit exceeded
    """
    user_id = _require_auth(credentials, db)

    # Validate book exists
    book = _book_repository.get_by_id(db, book_id)
    if book is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Book not found",
        )

    # Get publisher explicitly (don't rely on lazy loading)
    publisher = _publisher_repository.get(db, book.publisher_id)
    publisher_id = publisher.id if publisher else book.publisher_id
    publisher_slug = publisher.slug if publisher else str(book.publisher_id)

    # Validate book has content
    if not _book_has_content(book, publisher_slug):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Book has no content to process",
        )

    # Check rate limit (skip if admin_override)
    if not payload.admin_override:
        allowed, retry_after = await _check_rate_limit(book.publisher_id)
        if not allowed:
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={"detail": "Rate limit exceeded for publisher"},
                headers={"Retry-After": str(retry_after)},
            )

    # Validate admin_override and priority
    priority = payload.priority
    if payload.admin_override or priority == JobPriority.HIGH:
        # For HIGH priority or admin_override, require authenticated user (not API key)
        if user_id == -1:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admin override and HIGH priority require user authentication",
            )

    # Enqueue processing job
    queue_service = await get_queue_service()
    try:
        job = await queue_service.enqueue_job(
            book_id=str(book.id),
            publisher_id=str(book.publisher_id),
            job_type=payload.job_type,
            priority=priority,
            metadata={"book_name": book.book_name, "publisher_id": publisher_id, "publisher_slug": publisher_slug},
        )
    except JobAlreadyExistsError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Active processing job already exists for this book",
        )

    logger.info(
        "Triggered AI processing for book %s (job_id=%s, type=%s, priority=%s)",
        book_id,
        job.job_id,
        job.job_type.value,
        job.priority.value,
    )

    return ProcessingJobResponse(
        job_id=job.job_id,
        book_id=job.book_id,
        publisher_id=job.publisher_id,
        job_type=job.job_type,
        status=job.status,
        priority=job.priority,
        progress=job.progress,
        current_step=job.current_step,
        error_message=job.error_message,
        retry_count=job.retry_count,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
    )


@router.get(
    "/{book_id}/process-ai/status",
    response_model=ProcessingStatusResponse,
)
async def get_processing_status(
    book_id: int,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> ProcessingStatusResponse:
    """Get the current AI processing status for a book.

    Returns the most recent processing job status for the specified book.

    Args:
        book_id: ID of the book

    Returns:
        ProcessingStatusResponse with job status and progress

    Raises:
        404: Book not found or no processing jobs exist
    """
    _require_auth(credentials, db)

    # Validate book exists
    book = _book_repository.get_by_id(db, book_id)
    if book is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Book not found",
        )

    # Get most recent job for this book
    queue_service = await get_queue_service()
    jobs = await queue_service.list_jobs(book_id=str(book.id), limit=1)

    if not jobs:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No processing jobs found for this book",
        )

    job = jobs[0]
    return ProcessingStatusResponse(
        job_id=job.job_id,
        book_id=job.book_id,
        status=job.status,
        progress=job.progress,
        current_step=job.current_step,
        error_message=job.error_message,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
    )


@router.delete(
    "/{book_id}/ai-data",
    response_model=CleanupStatsResponse,
)
async def delete_ai_data(
    book_id: int,
    reprocess: bool = Query(
        default=False,
        description="Trigger reprocessing after cleanup",
    ),
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> CleanupStatsResponse:
    """Delete AI-generated data for a book.

    Removes all data under /ai-data/ for the specified book, including
    extracted text, modules, vocabulary, and audio files.

    Optionally triggers reprocessing after cleanup.

    Args:
        book_id: ID of the book
        reprocess: If True, queue a new processing job after cleanup

    Returns:
        CleanupStatsResponse with deletion statistics

    Raises:
        404: Book not found
    """
    _require_auth(credentials, db)

    # Validate book exists and capture needed data
    book = _book_repository.get_by_id(db, book_id)
    if book is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Book not found",
        )

    # Get publisher explicitly (don't rely on lazy loading)
    publisher = _publisher_repository.get(db, book.publisher_id)
    publisher_id = publisher.id if publisher else book.publisher_id
    publisher_slug = publisher.slug if publisher else str(book.publisher_id)
    book_name = book.book_name
    book_publisher_id = book.publisher_id

    # Release DB connection before slow storage operation
    db.close()

    # Cleanup AI data (use publisher slug for correct storage path)
    cleanup_manager = get_ai_data_cleanup_manager()
    stats = cleanup_manager.cleanup_all(
        publisher_id=publisher_slug,
        book_id=str(book_id),
        book_name=book_name,
    )

    logger.info(
        "Deleted AI data for book %s: %d files removed",
        book_id,
        stats.total_deleted,
    )

    # Optionally trigger reprocessing
    if reprocess:
        # Re-open DB to check book content
        db = SessionLocal()
        try:
            book = _book_repository.get_by_id(db, book_id)
            if book and _book_has_content(book, publisher_slug):
                queue_service = await get_queue_service()
                try:
                    job = await queue_service.enqueue_job(
                        book_id=str(book_id),
                        publisher_id=str(book_publisher_id),
                        metadata={"book_name": book_name, "publisher_id": publisher_id, "publisher_slug": publisher_slug},
                    )
                    logger.info(
                        "Triggered reprocessing for book %s (job_id=%s)",
                        book_id,
                        job.job_id,
                    )
                except JobAlreadyExistsError:
                    logger.warning(
                        "Skipped reprocessing for book %s: active job exists",
                        book_id,
                    )
        finally:
            db.close()

    return CleanupStatsResponse(
        total_deleted=stats.total_deleted,
        text_deleted=stats.text_deleted,
        modules_deleted=stats.modules_deleted,
        audio_deleted=stats.audio_deleted,
        vocabulary_deleted=stats.vocabulary_deleted,
        metadata_deleted=stats.metadata_deleted,
        errors=stats.errors,
    )


# =============================================================================
# Processing Dashboard Endpoints
# =============================================================================


class BookWithProcessingStatus(BaseModel):
    """Book with its processing status."""

    book_id: int
    book_name: str
    book_title: str
    publisher_id: int
    publisher_name: str
    processing_status: str  # 'not_started', 'queued', 'processing', 'completed', 'failed', 'partial'
    progress: int
    current_step: Optional[str]
    error_message: Optional[str]
    job_id: Optional[str]
    last_processed_at: Optional[str]


class BooksWithProcessingStatusResponse(BaseModel):
    """Response for list of books with processing status."""

    books: List[BookWithProcessingStatus]
    total: int
    page: int
    page_size: int


class ProcessingQueueItem(BaseModel):
    """Item in the processing queue."""

    job_id: str
    book_id: int
    book_name: str
    book_title: str
    publisher_name: str
    status: str
    progress: int
    current_step: str
    position: int
    created_at: str
    started_at: Optional[str]


class ProcessingQueueResponse(BaseModel):
    """Response for processing queue."""

    queue: List[ProcessingQueueItem]
    total_queued: int
    total_processing: int


class BulkReprocessRequest(BaseModel):
    """Request for bulk reprocessing."""

    book_ids: List[int]
    job_type: Optional[str] = "unified"  # unified uses single LLM call for better accuracy
    priority: Optional[str] = "normal"


class BulkReprocessResponse(BaseModel):
    """Response for bulk reprocessing."""

    triggered: int
    skipped: int
    errors: List[str]
    job_ids: List[str]


@dashboard_router.get(
    "/books",
    response_model=BooksWithProcessingStatusResponse,
)
async def list_books_with_processing_status(
    status: Optional[str] = Query(None, description="Filter by processing status"),
    publisher: Optional[str] = Query(None, description="Filter by publisher name"),
    search: Optional[str] = Query(None, description="Search by book title or name"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=100, description="Page size"),
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> BooksWithProcessingStatusResponse:
    """List all books with their processing status.

    Returns paginated list of books with their current AI processing status.
    Supports filtering by status, publisher, and search query.
    """
    _require_auth(credentials, db)

    # Get all books with optional filters
    query = db.query(_book_repository.model)

    if publisher:
        from app.models.publisher import Publisher

        query = query.join(Publisher).filter(Publisher.name.ilike(f"%{publisher}%"))

    if search:
        from app.models.book import Book

        query = query.filter((Book.book_name.ilike(f"%{search}%")) | (Book.book_title.ilike(f"%{search}%")))

    # Get total count before pagination
    total = query.count()

    # Apply pagination
    books = query.offset((page - 1) * page_size).limit(page_size).all()

    # Build publisher lookup (to avoid lazy loading issues)
    publisher_ids = list(set(book.publisher_id for book in books))
    publishers_by_id = (
        {
            p.id: p
            for p in db.query(_publisher_repository.model)
            .filter(_publisher_repository.model.id.in_(publisher_ids))
            .all()
        }
        if publisher_ids
        else {}
    )

    # Get processing status for each book
    queue_service = await get_queue_service()
    retrieval_service = get_ai_data_retrieval_service()

    result_books = []
    for book in books:
        pub = publishers_by_id.get(book.publisher_id)
        publisher_name = pub.name if pub else ""
        pub_id = pub.id if pub else book.publisher_id
        # Get most recent job for this book
        jobs = await queue_service.list_jobs(book_id=str(book.id), limit=1)

        processing_status = "not_started"
        progress = 0
        current_step = None
        error_message = None
        job_id = None
        last_processed_at = None

        if jobs:
            job = jobs[0]
            processing_status = job.status.value if hasattr(job.status, "value") else str(job.status)
            progress = job.progress
            current_step = job.current_step
            error_message = job.error_message
            job_id = job.job_id
            if job.completed_at:
                last_processed_at = (
                    job.completed_at.isoformat() if hasattr(job.completed_at, "isoformat") else str(job.completed_at)
                )
        else:
            # Check if metadata exists (means it was processed at some point)
            metadata = retrieval_service.get_metadata(pub_id, str(book.id), book.book_name)
            if metadata:
                processing_status = "completed"
                progress = 100
                if metadata.processing_completed_at:
                    last_processed_at = (
                        metadata.processing_completed_at.isoformat()
                        if hasattr(metadata.processing_completed_at, "isoformat")
                        else str(metadata.processing_completed_at)
                    )

        # Apply status filter
        if status and processing_status != status:
            continue

        result_books.append(
            BookWithProcessingStatus(
                book_id=book.id,
                book_name=book.book_name,
                book_title=book.book_title or book.book_name,
                publisher_id=book.publisher_id,
                publisher_name=publisher_name,
                processing_status=processing_status,
                progress=progress,
                current_step=current_step,
                error_message=error_message,
                job_id=job_id,
                last_processed_at=last_processed_at,
            )
        )

    return BooksWithProcessingStatusResponse(
        books=result_books,
        total=len(result_books) if status else total,
        page=page,
        page_size=page_size,
    )


@dashboard_router.get(
    "/queue",
    response_model=ProcessingQueueResponse,
)
async def get_processing_queue(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> ProcessingQueueResponse:
    """Get current processing queue.

    Returns list of jobs currently queued or processing.
    """
    _require_auth(credentials, db)

    queue_service = await get_queue_service()

    # Get queued and processing jobs
    queued_jobs = await queue_service.list_jobs(status=ProcessingStatus.QUEUED, limit=100)
    processing_jobs = await queue_service.list_jobs(status=ProcessingStatus.PROCESSING, limit=100)

    all_jobs = processing_jobs + queued_jobs  # Processing first, then queued

    queue_items = []
    for idx, job in enumerate(all_jobs):
        # Get book info
        book = _book_repository.get_by_id(db, int(job.book_id))
        if book:
            # Get publisher name explicitly
            publisher = _publisher_repository.get(db, book.publisher_id)
            publisher_name = publisher.name if publisher else ""
            queue_items.append(
                ProcessingQueueItem(
                    job_id=job.job_id,
                    book_id=int(job.book_id),
                    book_name=book.book_name,
                    book_title=book.book_title or book.book_name,
                    publisher_name=publisher_name,
                    status=job.status.value if hasattr(job.status, "value") else str(job.status),
                    progress=job.progress,
                    current_step=job.current_step or "",
                    position=idx + 1,
                    created_at=job.created_at.isoformat()
                    if hasattr(job.created_at, "isoformat")
                    else str(job.created_at),
                    started_at=job.started_at.isoformat()
                    if job.started_at and hasattr(job.started_at, "isoformat")
                    else None,
                )
            )

    return ProcessingQueueResponse(
        queue=queue_items,
        total_queued=len(queued_jobs),
        total_processing=len(processing_jobs),
    )


@dashboard_router.post(
    "/books/{book_id}/clear-error",
)
async def clear_processing_error(
    book_id: int,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> dict:
    """Clear processing error for a book.

    Resets the processing status by removing the failed job,
    allowing the book to be reprocessed.
    """
    _require_auth(credentials, db)

    # Validate book exists
    book = _book_repository.get_by_id(db, book_id)
    if book is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Book not found",
        )

    # Get the most recent failed job
    queue_service = await get_queue_service()
    jobs = await queue_service.list_jobs(book_id=str(book.id), limit=1)

    if not jobs:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No processing jobs found for this book",
        )

    job = jobs[0]
    if job.status != ProcessingStatus.FAILED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Job is not in failed state (current: {job.status.value})",
        )

    # Delete the failed job
    await queue_service.delete_job(job.job_id)

    logger.info("Cleared processing error for book %s (job_id=%s)", book_id, job.job_id)

    return {"message": "Processing error cleared successfully"}


@dashboard_router.post(
    "/bulk-reprocess",
    response_model=BulkReprocessResponse,
)
async def bulk_reprocess(
    request: BulkReprocessRequest,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> BulkReprocessResponse:
    """Bulk reprocess multiple books.

    Queues processing jobs for multiple books at once.
    Skips books that already have active processing jobs.
    """
    user_id = _require_auth(credentials, db)

    # Require user auth (not API key) for bulk operations
    if user_id == -1:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Bulk reprocess requires user authentication",
        )

    queue_service = await get_queue_service()
    triggered = 0
    skipped = 0
    errors = []
    job_ids = []

    # Parse job type and priority
    job_type = ProcessingJobType.UNIFIED
    if request.job_type:
        try:
            job_type = ProcessingJobType(request.job_type)
        except ValueError:
            job_type = ProcessingJobType.UNIFIED

    priority = JobPriority.NORMAL
    if request.priority:
        try:
            priority = JobPriority(request.priority)
        except ValueError:
            priority = JobPriority.NORMAL

    for book_id in request.book_ids:
        book = _book_repository.get_by_id(db, book_id)
        if book is None:
            errors.append(f"Book {book_id} not found")
            skipped += 1
            continue

        # Get publisher explicitly
        publisher = _publisher_repository.get(db, book.publisher_id)
        publisher_id = publisher.id if publisher else book.publisher_id
        publisher_slug = publisher.slug if publisher else str(book.publisher_id)

        if not _book_has_content(book, publisher_slug):
            errors.append(f"Book {book_id} has no content")
            skipped += 1
            continue

        try:
            job = await queue_service.enqueue_job(
                book_id=str(book.id),
                publisher_id=str(book.publisher_id),
                job_type=job_type,
                priority=priority,
                metadata={"book_name": book.book_name, "publisher_id": publisher_id, "publisher_slug": publisher_slug},
            )
            job_ids.append(job.job_id)
            triggered += 1
        except JobAlreadyExistsError:
            skipped += 1
        except Exception as e:
            errors.append(f"Book {book_id}: {str(e)}")
            skipped += 1

    logger.info(
        "Bulk reprocess completed: %d triggered, %d skipped, %d errors",
        triggered,
        skipped,
        len(errors),
    )

    return BulkReprocessResponse(
        triggered=triggered,
        skipped=skipped,
        errors=errors,
        job_ids=job_ids,
    )


# =============================================================================
# Processing Settings Endpoints
# =============================================================================


class GlobalProcessingSettings(BaseModel):
    """Global AI processing settings from environment."""

    ai_auto_process_on_upload: bool
    ai_auto_process_skip_existing: bool
    llm_primary_provider: str
    llm_fallback_provider: str
    tts_primary_provider: str
    tts_fallback_provider: str
    queue_max_concurrency: int
    vocabulary_max_words_per_module: int
    audio_generation_languages: str
    audio_generation_concurrency: int


class GlobalProcessingSettingsUpdate(BaseModel):
    """Request to update global AI processing settings."""

    ai_auto_process_on_upload: Optional[bool] = None
    ai_auto_process_skip_existing: Optional[bool] = None
    llm_primary_provider: Optional[str] = None
    llm_fallback_provider: Optional[str] = None
    tts_primary_provider: Optional[str] = None
    tts_fallback_provider: Optional[str] = None
    queue_max_concurrency: Optional[int] = None
    vocabulary_max_words_per_module: Optional[int] = None
    audio_generation_languages: Optional[str] = None
    audio_generation_concurrency: Optional[int] = None


class PublisherProcessingSettings(BaseModel):
    """Publisher-specific AI processing settings."""

    publisher_id: int
    publisher_name: str
    ai_auto_process_enabled: Optional[bool] = None  # None = use global
    ai_processing_priority: Optional[str] = None  # high, normal, low
    ai_audio_languages: Optional[str] = None  # Override languages


class PublisherProcessingSettingsUpdate(BaseModel):
    """Request to update publisher AI processing settings."""

    ai_auto_process_enabled: Optional[bool] = None
    ai_processing_priority: Optional[str] = None
    ai_audio_languages: Optional[str] = None


@dashboard_router.get(
    "/settings",
    response_model=GlobalProcessingSettings,
)
async def get_processing_settings(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> GlobalProcessingSettings:
    """Get global AI processing settings.

    Returns current environment-based settings for AI processing.
    """
    _require_auth(credentials, db)

    settings = get_settings()

    return GlobalProcessingSettings(
        ai_auto_process_on_upload=settings.ai_auto_process_on_upload,
        ai_auto_process_skip_existing=settings.ai_auto_process_skip_existing,
        llm_primary_provider=settings.llm_primary_provider,
        llm_fallback_provider=settings.llm_fallback_provider,
        tts_primary_provider=settings.tts_primary_provider,
        tts_fallback_provider=settings.tts_fallback_provider,
        queue_max_concurrency=settings.queue_max_concurrency,
        vocabulary_max_words_per_module=settings.vocabulary_max_words_per_module,
        audio_generation_languages=settings.audio_generation_languages,
        audio_generation_concurrency=settings.audio_generation_concurrency,
    )


@dashboard_router.put(
    "/settings",
    response_model=GlobalProcessingSettings,
)
async def update_processing_settings(
    request: GlobalProcessingSettingsUpdate,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> GlobalProcessingSettings:
    """Update global AI processing settings.

    Note: These settings are environment-based. This endpoint updates the
    runtime settings but changes are not persisted across restarts.
    For persistent changes, update environment variables.

    Requires user authentication (not API key).
    """
    user_id = _require_auth(credentials, db)

    if user_id == -1:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Settings update requires user authentication",
        )

    settings = get_settings()

    # Update runtime settings (note: these won't persist across restarts)
    if request.ai_auto_process_on_upload is not None:
        settings.ai_auto_process_on_upload = request.ai_auto_process_on_upload
    if request.ai_auto_process_skip_existing is not None:
        settings.ai_auto_process_skip_existing = request.ai_auto_process_skip_existing
    if request.llm_primary_provider is not None:
        settings.llm_primary_provider = request.llm_primary_provider
    if request.llm_fallback_provider is not None:
        settings.llm_fallback_provider = request.llm_fallback_provider
    if request.tts_primary_provider is not None:
        settings.tts_primary_provider = request.tts_primary_provider
    if request.tts_fallback_provider is not None:
        settings.tts_fallback_provider = request.tts_fallback_provider
    if request.queue_max_concurrency is not None:
        settings.queue_max_concurrency = request.queue_max_concurrency
    if request.vocabulary_max_words_per_module is not None:
        settings.vocabulary_max_words_per_module = request.vocabulary_max_words_per_module
    if request.audio_generation_languages is not None:
        settings.audio_generation_languages = request.audio_generation_languages
    if request.audio_generation_concurrency is not None:
        settings.audio_generation_concurrency = request.audio_generation_concurrency

    logger.info("Updated global processing settings by user %s", user_id)

    return GlobalProcessingSettings(
        ai_auto_process_on_upload=settings.ai_auto_process_on_upload,
        ai_auto_process_skip_existing=settings.ai_auto_process_skip_existing,
        llm_primary_provider=settings.llm_primary_provider,
        llm_fallback_provider=settings.llm_fallback_provider,
        tts_primary_provider=settings.tts_primary_provider,
        tts_fallback_provider=settings.tts_fallback_provider,
        queue_max_concurrency=settings.queue_max_concurrency,
        vocabulary_max_words_per_module=settings.vocabulary_max_words_per_module,
        audio_generation_languages=settings.audio_generation_languages,
        audio_generation_concurrency=settings.audio_generation_concurrency,
    )


@dashboard_router.get(
    "/publishers/{publisher_id}/settings",
    response_model=PublisherProcessingSettings,
)
async def get_publisher_processing_settings(
    publisher_id: int,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> PublisherProcessingSettings:
    """Get AI processing settings for a specific publisher.

    Returns publisher-specific overrides for AI processing.
    Null values indicate "use global default".
    """
    _require_auth(credentials, db)

    publisher = _publisher_repository.get(db, publisher_id)
    if publisher is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Publisher not found",
        )

    return PublisherProcessingSettings(
        publisher_id=publisher.id,
        publisher_name=publisher.name,
        ai_auto_process_enabled=publisher.ai_auto_process_enabled,
        ai_processing_priority=publisher.ai_processing_priority,
        ai_audio_languages=publisher.ai_audio_languages,
    )


@dashboard_router.put(
    "/publishers/{publisher_id}/settings",
    response_model=PublisherProcessingSettings,
)
async def update_publisher_processing_settings(
    publisher_id: int,
    request: PublisherProcessingSettingsUpdate,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> PublisherProcessingSettings:
    """Update AI processing settings for a specific publisher.

    Set values to null to reset to "use global default".
    Requires user authentication (not API key).
    """
    user_id = _require_auth(credentials, db)

    if user_id == -1:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Settings update requires user authentication",
        )

    publisher = _publisher_repository.get(db, publisher_id)
    if publisher is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Publisher not found",
        )

    # Validate priority if provided
    if request.ai_processing_priority is not None:
        valid_priorities = ["high", "normal", "low"]
        if request.ai_processing_priority not in valid_priorities:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid priority. Must be one of: {', '.join(valid_priorities)}",
            )

    # Update publisher settings
    _publisher_repository.update_ai_settings(
        db,
        publisher,
        ai_auto_process_enabled=request.ai_auto_process_enabled,
        ai_processing_priority=request.ai_processing_priority,
        ai_audio_languages=request.ai_audio_languages,
    )

    logger.info("Updated processing settings for publisher %s by user %s", publisher_id, user_id)

    return PublisherProcessingSettings(
        publisher_id=publisher.id,
        publisher_name=publisher.name,
        ai_auto_process_enabled=publisher.ai_auto_process_enabled,
        ai_processing_priority=publisher.ai_processing_priority,
        ai_audio_languages=publisher.ai_audio_languages,
    )
