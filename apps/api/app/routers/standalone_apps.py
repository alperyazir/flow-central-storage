"""API endpoints for standalone app templates and bundling."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Response, UploadFile, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import decode_access_token, verify_api_key_from_db
from app.db import get_db
from app.repositories.book import BookRepository
from app.repositories.publisher import PublisherRepository
from app.schemas.standalone_app import (
    AsyncBundleRequest,
    AsyncBundleResponse,
    BundleInfo,
    BundleJobListResponse,
    BundleJobResult,
    BundleJobStatus,
    BundleListResponse,
    BundleRequest,
    BundleResponse,
    TemplateInfo,
    TemplateListResponse,
    TemplateUploadResponse,
)
from app.services import get_minio_client, get_minio_client_external
from app.services.standalone_apps import (
    PRESIGNED_URL_EXPIRY_SECONDS,
    BundleCreationError,
    BundleNotFoundError,
    InvalidPlatformError,
    TemplateNotFoundError,
    create_bundle,
    delete_bundle,
    delete_template,
    get_template_download_url,
    list_bundles,
    list_templates,
    template_exists,
    upload_template,
)

router = APIRouter(prefix="/standalone-apps", tags=["Standalone Apps"])
_bearer_scheme = HTTPBearer(auto_error=True)
_book_repository = BookRepository()
_publisher_repository = PublisherRepository()

logger = logging.getLogger(__name__)


def _require_admin(credentials: HTTPAuthorizationCredentials, db: Session) -> int:
    """Validate JWT token or API key."""
    token = credentials.credentials

    try:
        payload = decode_access_token(token, settings=get_settings())
        subject = payload.get("sub")
        if subject is not None:
            try:
                return int(subject)
            except (TypeError, ValueError):
                pass
    except ValueError:
        pass

    api_key_info = verify_api_key_from_db(token, db)
    if api_key_info is not None:
        return -1

    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")


def _require_api_key_or_admin(credentials: HTTPAuthorizationCredentials, db: Session) -> int:
    """Validate JWT token or API key for access.

    Returns:
        User ID if authenticated via JWT, -1 if via API key

    Raises:
        HTTPException: If authentication fails
    """
    token = credentials.credentials

    # Try JWT first
    try:
        payload = decode_access_token(token, settings=get_settings())
        subject = payload.get("sub")
        if subject is not None:
            return int(subject)
    except (ValueError, TypeError):
        pass

    # Try API key
    api_key_info = verify_api_key_from_db(token, db)
    if api_key_info is not None:
        return -1

    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token or API key")


@router.get("", response_model=TemplateListResponse)
def list_all_templates(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> TemplateListResponse:
    """List all uploaded standalone app templates.

    Requires admin authentication.
    """
    _require_admin(credentials, db)

    settings = get_settings()
    client = get_minio_client(settings)
    external_client = get_minio_client_external(settings)

    templates_meta = list_templates(
        client=client,
        external_client=external_client,
        bucket=settings.minio_apps_bucket,
    )

    templates = []
    for meta in templates_meta:
        try:
            download_url = get_template_download_url(
                external_client=external_client,
                bucket=settings.minio_apps_bucket,
                platform=meta.platform,
            )
            templates.append(
                TemplateInfo(
                    platform=meta.platform,
                    file_name=meta.file_name,
                    file_size=meta.file_size,
                    uploaded_at=meta.uploaded_at,
                    download_url=download_url,
                )
            )
        except Exception as exc:
            logger.warning("Failed to get download URL for %s: %s", meta.platform, exc)

    return TemplateListResponse(templates=templates)


@router.post("/{platform}/upload", response_model=TemplateUploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_template_endpoint(
    platform: str,
    file: UploadFile,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> TemplateUploadResponse:
    """Upload a standalone app template for a specific platform.

    Requires admin authentication.

    - **platform**: Target platform (mac, win, linux)
    - **file**: Zip file containing the app template
    """
    import asyncio

    _require_admin(credentials, db)
    db.commit()  # Release DB connection before S3 upload

    if not file.filename or not file.filename.endswith(".zip"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File must be a zip archive",
        )

    settings = get_settings()
    client = get_minio_client(settings)

    file_data = await file.read()
    if len(file_data) > settings.standalone_app_max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds max size ({settings.standalone_app_max_bytes // 1024 // 1024}MB)",
        )

    try:
        metadata = await asyncio.to_thread(
            upload_template,
            client=client,
            bucket=settings.minio_apps_bucket,
            platform=platform,
            file_data=file_data,
            file_name=file.filename,
        )
    except InvalidPlatformError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("Failed to upload template: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to upload template",
        ) from exc

    return TemplateUploadResponse(
        platform=metadata.platform,
        file_name=metadata.file_name,
        file_size=metadata.file_size,
    )


@router.delete("/{platform}", status_code=status.HTTP_204_NO_CONTENT)
def delete_template_endpoint(
    platform: str,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> Response:
    """Delete a standalone app template for a specific platform.

    Requires admin authentication.

    - **platform**: Platform to delete template for (mac, win, linux)
    """
    _require_admin(credentials, db)

    settings = get_settings()
    client = get_minio_client(settings)

    try:
        delete_template(
            client=client,
            bucket=settings.minio_apps_bucket,
            platform=platform,
        )
    except InvalidPlatformError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except TemplateNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("Failed to delete template: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to delete template",
        ) from exc

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{platform}/download")
def download_template(
    platform: str,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> dict:
    """Get a presigned download URL for a standalone app template.

    Requires admin authentication.

    - **platform**: Platform to download template for (mac, win, linux)

    Returns a JSON object with the download URL.
    """
    _require_admin(credentials, db)

    settings = get_settings()
    external_client = get_minio_client_external(settings)

    try:
        download_url = get_template_download_url(
            external_client=external_client,
            bucket=settings.minio_apps_bucket,
            platform=platform,
        )
    except InvalidPlatformError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except TemplateNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("Failed to get download URL: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to generate download URL",
        ) from exc

    expires_at = datetime.now(timezone.utc) + timedelta(seconds=PRESIGNED_URL_EXPIRY_SECONDS)

    return {
        "download_url": download_url,
        "platform": platform.lower(),
        "expires_at": expires_at.isoformat(),
    }



@router.post("/bundle")
async def create_bundle_endpoint(
    payload: BundleRequest,
    response: Response,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> AsyncBundleResponse | BundleResponse:
    """Create a bundled standalone app.

    If bundle already exists and force=False, returns 200 with download_url immediately.
    Otherwise queues a worker job and returns 202 with job_id for tracking via /bundle-status/{job_id}.
    """
    _require_api_key_or_admin(credentials, db)

    book = _book_repository.get_by_id(db, payload.book_id)
    if book is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Book with ID {payload.book_id} not found",
        )

    publisher = _publisher_repository.get(db, book.publisher_id)
    if publisher is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Publisher for book ID {payload.book_id} not found",
        )

    settings = get_settings()
    client = get_minio_client(settings)
    external_client = get_minio_client_external(settings)

    # Check for existing bundle — return immediately if found
    if not payload.force:
        normalized_platform = payload.platform.lower()
        bundle_prefix = f"bundles/{publisher.id}/{book.book_name}/"
        try:
            found_objects = list(client.list_objects(settings.minio_apps_bucket, prefix=bundle_prefix, recursive=True))
            for obj in found_objects:
                file_name = obj.object_name.split("/")[-1]
                if file_name.lower().startswith(f"({normalized_platform})"):
                    download_url = external_client.presigned_get_object(
                        bucket_name=settings.minio_apps_bucket,
                        object_name=obj.object_name,
                        expires=timedelta(seconds=PRESIGNED_URL_EXPIRY_SECONDS),
                    )
                    expires_at = datetime.now(timezone.utc) + timedelta(seconds=PRESIGNED_URL_EXPIRY_SECONDS)
                    response.status_code = status.HTTP_200_OK
                    return BundleResponse(
                        download_url=download_url,
                        file_name=file_name,
                        file_size=obj.size or 0,
                        expires_at=expires_at,
                    )
        except Exception as exc:
            logger.warning("Failed to check existing bundle: %s", exc)

    # Verify template exists before queuing
    if not template_exists(client, settings.minio_apps_bucket, payload.platform):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Template for platform '{payload.platform}' not found",
        )

    # Queue bundle creation on worker (not API BackgroundTask)
    job_id = str(uuid.uuid4())

    try:
        from arq import create_pool
        from arq.connections import RedisSettings

        from app.services.queue.models import JobPriority, ProcessingJob, ProcessingJobType
        from app.services.queue.redis import get_redis_connection
        from app.services.queue.repository import JobRepository

        redis_conn = await get_redis_connection(url=settings.redis_url)
        repository = JobRepository(
            redis_client=redis_conn.client,
            job_ttl_seconds=settings.queue_job_ttl_seconds,
        )

        job = ProcessingJob(
            job_id=job_id,
            book_id=str(payload.book_id),
            publisher_id=publisher.name,
            job_type=ProcessingJobType.BUNDLE,
            priority=JobPriority.NORMAL,
            metadata={
                "platform": payload.platform,
                "book_name": book.book_name,
                "force": payload.force,
            },
        )
        await repository.create_job(job, check_duplicate=False)

        redis_settings = RedisSettings.from_dsn(settings.redis_url)
        pool = await create_pool(redis_settings)

        await pool.enqueue_job(
            "create_bundle_task",
            job_id=job_id,
            platform=payload.platform,
            book_id=payload.book_id,
            publisher_id=publisher.id,
            book_name=book.book_name,
            force=payload.force,
            _queue_name=f"{settings.queue_name}:normal",
        )

        await pool.close()

    except Exception as exc:
        logger.error("Failed to queue bundle creation: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to queue bundle creation",
        ) from exc

    response.status_code = status.HTTP_202_ACCEPTED
    return AsyncBundleResponse(
        job_id=job_id,
        status="queued",
        message=f"Bundle creation queued for {book.book_name} ({payload.platform})",
    )


@router.get("/bundle-status/{job_id}", response_model=BundleJobResult)
async def get_bundle_status(
    job_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> BundleJobResult:
    """Get the status of a bundle creation job.

    Checks both legacy cache (fcs:upload:) and worker job repository (dcs:job:).
    """
    _require_api_key_or_admin(credentials, db)

    # Try legacy cache first (BackgroundTask-based)
    from app.services.cache import get_upload_progress

    progress = get_upload_progress(job_id)
    if progress is not None:
        return BundleJobResult(
            job_id=job_id,
            status=progress.get("step", "unknown"),
            progress=progress.get("progress", 0),
            current_step=progress.get("detail", ""),
            download_url=progress.get("download_url"),
            error_message=progress.get("error"),
            created_at=datetime.now(timezone.utc),
        )

    # Try worker job repository
    settings = get_settings()
    try:
        from app.services.queue.redis import get_redis_connection
        from app.services.queue.repository import JobRepository

        redis_conn = await get_redis_connection(url=settings.redis_url)
        repository = JobRepository(
            redis_client=redis_conn.client,
            job_ttl_seconds=settings.queue_job_ttl_seconds,
        )

        job = await repository.get_job(job_id)

        # Read download_url directly from job hash
        download_url = await redis_conn.client.hget(f"dcs:job:{job_id}", "download_url")

        return BundleJobResult(
            job_id=job.job_id,
            status=job.status.value,
            progress=job.progress,
            current_step=job.current_step,
            download_url=download_url,
            error_message=job.error_message,
            created_at=job.created_at,
            completed_at=job.completed_at,
        )

    except Exception as exc:
        if "not found" in str(exc).lower():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Bundle job not found or expired",
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get job status",
        ) from exc


@router.get("/bundles", response_model=BundleListResponse)
def list_bundles_endpoint(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> BundleListResponse:
    """List all created bundles.

    Requires admin authentication (JWT token).

    Returns:
        List of all bundles with download URLs
    """
    _require_admin(credentials, db)

    settings = get_settings()
    client = get_minio_client(settings)
    external_client = get_minio_client_external(settings)

    bundles_data = list_bundles(
        client=client,
        external_client=external_client,
        bucket=settings.minio_apps_bucket,
    )

    bundles = [
        BundleInfo(
            publisher_name=b.publisher_name,
            book_name=b.book_name,
            platform=b.platform,
            file_name=b.file_name,
            file_size=b.file_size,
            created_at=b.created_at,
            object_name=b.object_name,
            download_url=b.download_url,
        )
        for b in bundles_data
    ]

    return BundleListResponse(bundles=bundles)


@router.get("/bundle/jobs", response_model=BundleJobListResponse)
async def list_bundle_jobs(
    status_filter: str | None = None,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> BundleJobListResponse:
    """List all bundle creation jobs.

    Requires admin authentication.
    Optional status_filter: queued, processing, completed, failed.
    """
    _require_admin(credentials, db)

    settings = get_settings()

    try:
        from app.services.queue.models import ProcessingJobType, ProcessingStatus
        from app.services.queue.redis import get_redis_connection
        from app.services.queue.repository import JobRepository

        redis_conn = await get_redis_connection(url=settings.redis_url)
        repository = JobRepository(
            redis_client=redis_conn.client,
            job_ttl_seconds=settings.queue_job_ttl_seconds,
        )

        status_enum = None
        if status_filter:
            try:
                status_enum = ProcessingStatus(status_filter)
            except ValueError:
                pass

        all_jobs = await repository.list_jobs(status=status_enum, limit=200)
        bundle_jobs = [j for j in all_jobs if j.job_type == ProcessingJobType.BUNDLE]

        items = [
            BundleJobStatus(
                job_id=j.job_id,
                status=j.status.value,
                progress=j.progress,
                current_step=j.current_step,
                error_message=j.error_message,
                created_at=j.created_at,
                started_at=j.started_at,
                completed_at=j.completed_at,
                platform=j.metadata.get("platform"),
                book_name=j.metadata.get("book_name"),
                book_id=j.book_id,
            )
            for j in bundle_jobs
        ]

        return BundleJobListResponse(jobs=items, total=len(items))

    except Exception as exc:
        logger.error("Failed to list bundle jobs: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list bundle jobs",
        ) from exc


@router.post("/bundle/jobs/{job_id}/cancel")
async def cancel_bundle_job(
    job_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> dict:
    """Cancel a queued or in-progress bundle job."""
    _require_admin(credentials, db)

    settings = get_settings()
    try:
        from app.services.queue.redis import get_redis_connection
        from app.services.queue.service import QueueService

        redis_conn = await get_redis_connection(url=settings.redis_url)
        service = QueueService(redis_conn.client, settings)
        job = await service.cancel_job(job_id)
        return {"job_id": job.job_id, "status": job.status.value}
    except Exception as exc:
        detail = str(exc)
        if "not found" in detail.lower():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail) from exc
        if "Cannot cancel" in detail:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail) from exc
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=detail) from exc


@router.delete("/bundle/jobs/{job_id}")
async def delete_bundle_job(
    job_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> dict:
    """Delete a bundle job record from Redis."""
    _require_admin(credentials, db)

    settings = get_settings()
    from app.services.queue.redis import get_redis_connection
    from app.services.queue.repository import JobRepository

    try:
        redis_conn = await get_redis_connection(url=settings.redis_url)
        repository = JobRepository(redis_client=redis_conn.client, job_ttl_seconds=settings.queue_job_ttl_seconds)
        deleted = await repository.delete_job(job_id)
        if not deleted:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
        return {"deleted": True, "job_id": job_id}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc


@router.delete("/bundle/jobs")
async def clear_bundle_jobs(
    status_filter: str | None = None,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> dict:
    """Clear bundle jobs. If status_filter provided, only clear jobs with that status."""
    _require_admin(credentials, db)

    settings = get_settings()
    try:
        from app.services.queue.models import ProcessingJobType, ProcessingStatus
        from app.services.queue.redis import get_redis_connection
        from app.services.queue.repository import JobRepository

        redis_conn = await get_redis_connection(url=settings.redis_url)
        repository = JobRepository(redis_client=redis_conn.client, job_ttl_seconds=settings.queue_job_ttl_seconds)

        status_enum = None
        if status_filter:
            try:
                status_enum = ProcessingStatus(status_filter)
            except ValueError:
                pass

        all_jobs = await repository.list_jobs(status=status_enum, limit=500)
        bundle_jobs = [j for j in all_jobs if j.job_type == ProcessingJobType.BUNDLE]

        deleted = 0
        for job in bundle_jobs:
            if await repository.delete_job(job.job_id):
                deleted += 1

        return {"deleted": deleted}
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc


@router.post("/bundle/async", response_model=AsyncBundleResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_bundle_async_endpoint(
    payload: AsyncBundleRequest,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> AsyncBundleResponse:
    """Create a bundled standalone app asynchronously.

    Requires API key or admin authentication.

    This endpoint queues a background job to create the bundle and returns immediately
    with a job ID that can be used to poll for status.

    - **platform**: Target platform (mac, win, linux)
    - **book_id**: ID of the book to bundle
    - **force**: If True, recreate bundle even if it already exists
    """
    import uuid

    from arq import create_pool
    from arq.connections import RedisSettings

    _require_api_key_or_admin(credentials, db)

    # Get book information
    book = _book_repository.get_by_id(db, payload.book_id)
    if book is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Book with ID {payload.book_id} not found",
        )

    # Get publisher information
    publisher = _publisher_repository.get(db, book.publisher_id)
    if publisher is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Publisher for book ID {payload.book_id} not found",
        )

    settings = get_settings()
    client = get_minio_client(settings)

    # Verify template exists
    if not template_exists(client, settings.minio_apps_bucket, payload.platform):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Template for platform '{payload.platform}' not found",
        )

    # Generate job ID
    job_id = str(uuid.uuid4())

    try:
        # First, create job record in Redis for status tracking
        from app.services.queue.models import JobPriority, ProcessingJob, ProcessingJobType
        from app.services.queue.redis import get_redis_connection
        from app.services.queue.repository import JobRepository

        redis_conn = await get_redis_connection(url=settings.redis_url)
        repository = JobRepository(
            redis_client=redis_conn.client,
            job_ttl_seconds=settings.queue_job_ttl_seconds,
        )

        job = ProcessingJob(
            job_id=job_id,
            book_id=str(payload.book_id),
            publisher_id=publisher.name,
            job_type=ProcessingJobType.BUNDLE,
            priority=JobPriority.NORMAL,
            metadata={
                "platform": payload.platform,
                "book_name": book.book_name,
                "force": payload.force,
            },
        )
        # Skip duplicate check for bundle jobs (same book can have multiple platform bundles)
        await repository.create_job(job, check_duplicate=False)

        # Then enqueue to arq worker
        redis_settings = RedisSettings.from_dsn(settings.redis_url)
        pool = await create_pool(redis_settings)

        await pool.enqueue_job(
            "create_bundle_task",
            job_id=job_id,
            platform=payload.platform,
            book_id=payload.book_id,
            publisher_id=publisher.id,
            book_name=book.book_name,
            force=payload.force,
            _queue_name=f"{settings.queue_name}:normal",
        )

        await pool.close()

        logger.info(
            "Queued async bundle creation job %s for book %s (platform: %s)",
            job_id,
            book.book_name,
            payload.platform,
        )

        return AsyncBundleResponse(
            job_id=job_id,
            status="queued",
            message=f"Bundle creation job queued for {book.book_name} ({payload.platform})",
        )

    except Exception as exc:
        logger.error("Failed to queue bundle creation job: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to queue bundle creation job",
        ) from exc


@router.get("/bundle/jobs/{job_id}", response_model=BundleJobResult)
async def get_bundle_job_status(
    job_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> BundleJobResult:
    """Get the status of an async bundle creation job.

    Requires API key or admin authentication.

    Returns the current status, progress, and result (if completed).
    """
    from arq import create_pool
    from arq.connections import RedisSettings
    from arq.jobs import Job

    _require_api_key_or_admin(credentials, db)

    settings = get_settings()

    try:
        from app.services.queue.redis import get_redis_connection
        from app.services.queue.repository import JobRepository

        redis_conn = await get_redis_connection(url=settings.redis_url)
        repository = JobRepository(
            redis_client=redis_conn.client,
            job_ttl_seconds=settings.queue_job_ttl_seconds,
        )

        job = await repository.get_job(job_id)

        # Try to get arq job result if completed
        result_data = {}
        if job.status.value in ("completed", "failed"):
            try:
                redis_settings = RedisSettings.from_dsn(settings.redis_url)
                pool = await create_pool(redis_settings)
                arq_job = Job(job_id, pool)
                result = await arq_job.result(timeout=1)
                if result and isinstance(result, dict):
                    result_data = result
                await pool.close()
            except Exception as e:
                logger.debug("Could not get arq job result: %s", e)

        return BundleJobResult(
            job_id=job.job_id,
            status=job.status.value,
            progress=job.progress,
            current_step=job.current_step,
            download_url=result_data.get("download_url"),
            file_name=result_data.get("file_name"),
            file_size=result_data.get("file_size"),
            cached=result_data.get("cached", False),
            error_message=job.error_message,
            created_at=job.created_at,
            completed_at=job.completed_at,
        )

    except Exception as exc:
        if "JobNotFoundError" in str(type(exc).__name__) or "not found" in str(exc).lower():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Job {job_id} not found",
            ) from exc
        logger.error("Failed to get job status: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get job status",
        ) from exc


@router.delete("/bundles/{object_name:path}")
def delete_bundle_endpoint(
    object_name: str,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> Response:
    """Delete a bundle by its object path.

    Requires admin authentication (JWT token).

    Args:
        object_name: Full object path of the bundle (e.g., bundles/publisher/book/file.zip)

    Returns:
        204 No Content on success
    """
    _require_admin(credentials, db)

    settings = get_settings()
    client = get_minio_client(settings)

    try:
        delete_bundle(
            client=client,
            bucket=settings.minio_apps_bucket,
            object_name=object_name,
        )
    except BundleNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    return Response(status_code=status.HTTP_204_NO_CONTENT)
