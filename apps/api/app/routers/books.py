"""CRUD endpoints for book metadata."""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import zipfile
from collections.abc import Iterable

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, UploadFile, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import decode_access_token, verify_api_key_from_db
from app.db import SessionLocal, get_db
from app.models.book import Book, BookStatusEnum
from app.models.webhook import WebhookEventType
from app.repositories.book import BookRepository
from app.repositories.publisher import PublisherRepository
from app.repositories.user import UserRepository
from app.schemas.book import BookCreate, BookRead, BookUpdate
from app.services import (
    RelocationError,
    UploadError,
    get_minio_client,
    move_prefix_to_trash,
    upload_book_archive,
)
from app.services.ai_processing import trigger_auto_processing
from app.services.storage import _prefix_exists
from app.services.webhook import WebhookService

# TODO [PERF-C3/C4]: Upload endpoints buffer full archives in memory.
#   Refactor upload_book_archive to stream chunks to MinIO to reduce peak memory.
# TODO [PERF-C2]: Teachers list endpoint has N+1 query pattern.
#   Refactor to use eager loading (joinedload / selectinload) or a single aggregated query.
# TODO [PERF-H2]: Material stats endpoint issues 4 separate queries.
#   Combine into a single aggregated query (related to PERF-C2 refactor).

router = APIRouter(prefix="/books", tags=["Books"])
_bearer_scheme = HTTPBearer(auto_error=True)
_book_repository = BookRepository()
_publisher_repository = PublisherRepository()
_user_repository = UserRepository()
_webhook_service = WebhookService()
logger = logging.getLogger(__name__)


def _invalidate_book_cache() -> None:
    from app.services.cache import get_cache

    try:
        get_cache().invalidate("fcs:books:*")
    except Exception:
        pass


def _require_admin(credentials: HTTPAuthorizationCredentials, db: Session) -> int:
    """Validate JWT token or API key and ensure authentication is valid."""

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
        # API key authentication successful
        # Return a special value to indicate API key auth (or could return the api_key_id)
        # For now, return -1 to indicate API key authentication (not a user_id)
        return -1

    # Both JWT and API key failed
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid token",
    )


def _trigger_webhook(book_id: int, event_type: WebhookEventType) -> None:
    """Trigger webhook broadcast for a book event (runs in background)."""
    logger.info(f"[WEBHOOK] Triggering {event_type.value} webhook for book_id={book_id}")
    try:
        with SessionLocal() as session:
            book = _book_repository.get_by_id(session, book_id)
            if book:
                logger.info(f"[WEBHOOK] Book {book_id} found, broadcasting event to webhook service")
                asyncio.run(_webhook_service.broadcast_event(session, event_type, book))
                logger.info(f"[WEBHOOK] Broadcast completed for book {book_id}")
            else:
                logger.warning(f"[WEBHOOK] Book {book_id} not found in database, cannot trigger webhook")
    except Exception as e:
        logger.error(f"[WEBHOOK] Failed to trigger webhook for book {book_id}: {e}", exc_info=True)


@router.post("/", response_model=BookRead, status_code=status.HTTP_201_CREATED)
def create_book(
    payload: BookCreate,
    background_tasks: BackgroundTasks,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> BookRead:
    """Create a new book metadata record."""

    _require_admin(credentials, db)

    # Convert publisher name to publisher_id
    data = payload.model_dump()
    publisher_name = data.pop("publisher")
    publisher = _publisher_repository.get_or_create_by_name(db, publisher_name)
    data["publisher_id"] = publisher.id

    book = _book_repository.create(db, data=data)

    # Trigger webhook in background
    logger.info(
        f"[WEBHOOK-TRIGGER] Scheduling BOOK_CREATED webhook for book_id={book.id}, book_name='{book.book_name}', publisher='{book.publisher}'"
    )
    background_tasks.add_task(_trigger_webhook, book.id, WebhookEventType.BOOK_CREATED)
    _invalidate_book_cache()
    logger.debug(f"[WEBHOOK-TRIGGER] BOOK_CREATED webhook task added to background queue for book_id={book.id}")

    return BookRead.model_validate(book)


@router.get("/", response_model=list[BookRead])
def list_books(
    publisher_id: int | None = Query(default=None, description="Filter books by publisher ID"),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(50, ge=1, le=500, description="Max number of records to return"),
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> list[BookRead]:
    """Return stored books with pagination, optionally filtered by publisher."""

    _require_admin(credentials, db)

    from app.services.cache import cache_key, get_cache

    cache = get_cache()
    ck = cache_key("books", "list", publisher_id, skip, limit)
    cached = cache.get(ck)
    if cached is not None:
        return cached

    if publisher_id is not None:
        books = _book_repository.list_by_publisher_id(db, publisher_id, skip=skip, limit=limit)
    else:
        books = _book_repository.list_all_books(db, skip=skip, limit=limit)
    result = [BookRead.model_validate(book).model_dump(mode="json") for book in books]
    cache.set(ck, result, ttl=300)
    return result


class _BatchBookRequest(BaseModel):
    """Request body for batch book retrieval."""

    ids: list[int] = Field(..., max_length=100, description="List of book IDs to retrieve (max 100)")


@router.post("/batch", response_model=list[BookRead])
def get_books_batch(
    payload: _BatchBookRequest,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> list[BookRead]:
    """Retrieve multiple books by IDs in a single request.

    Returns books found (silently skips missing/archived IDs).
    """
    _require_admin(credentials, db)

    if not payload.ids:
        return []

    from sqlalchemy import select as sa_select

    statement = sa_select(Book).where(
        Book.id.in_(payload.ids),
        Book.status != BookStatusEnum.ARCHIVED,
    )
    books = list(db.scalars(statement).all())
    return [BookRead.model_validate(book) for book in books]


@router.get("/{book_id}", response_model=BookRead)
def get_book(
    book_id: int,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> BookRead:
    """Retrieve a single book by identifier."""

    _require_admin(credentials, db)
    book = _book_repository.get_by_id(db, book_id)
    if book is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Book not found")
    return BookRead.model_validate(book)


@router.put("/{book_id}", response_model=BookRead)
def update_book(
    book_id: int,
    payload: BookUpdate,
    background_tasks: BackgroundTasks,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> BookRead:
    """Update metadata for an existing book."""

    _require_admin(credentials, db)
    book = _book_repository.get_by_id(db, book_id)
    if book is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Book not found")

    update_data = payload.model_dump(exclude_unset=True)
    if not update_data:
        return BookRead.model_validate(book)

    # Convert publisher name to publisher_id if provided
    if "publisher" in update_data:
        publisher_name = update_data.pop("publisher")
        publisher = _publisher_repository.get_or_create_by_name(db, publisher_name)
        update_data["publisher_id"] = publisher.id

    updated = _book_repository.update(db, book, data=update_data)

    # Trigger webhook in background
    logger.info(
        f"[WEBHOOK-TRIGGER] Scheduling BOOK_UPDATED webhook for book_id={book_id}, book_name='{updated.book_name}', updated_fields={list(update_data.keys())}"
    )
    background_tasks.add_task(_trigger_webhook, book_id, WebhookEventType.BOOK_UPDATED)
    _invalidate_book_cache()
    logger.debug(f"[WEBHOOK-TRIGGER] BOOK_UPDATED webhook task added to background queue for book_id={book_id}")

    return BookRead.model_validate(updated)


@router.delete("/{book_id}", response_model=BookRead)
def soft_delete_book(
    book_id: int,
    background_tasks: BackgroundTasks,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> BookRead:
    """Soft-delete a book by archiving metadata and moving assets to the trash bucket."""

    admin_id = _require_admin(credentials, db)
    book = _book_repository.get_by_id(db, book_id)
    if book is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Book not found")

    if book.status == BookStatusEnum.ARCHIVED:
        return BookRead.model_validate(book)

    settings = get_settings()
    client = get_minio_client(settings)
    prefix = f"{book.publisher_id}/books/{book.book_name}/"

    try:
        report = move_prefix_to_trash(
            client=client,
            source_bucket=settings.minio_publishers_bucket,
            prefix=prefix,
            trash_bucket=settings.minio_trash_bucket,
        )
    except RelocationError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to relocate book assets",
        ) from exc

    archived = _book_repository.archive(db, book)

    logger.info(
        "User %s archived book %s; moved %s objects from %s/%s to %s/%s",
        admin_id,
        archived.id,
        report.objects_moved,
        report.source_bucket,
        report.source_prefix,
        report.destination_bucket,
        report.destination_prefix,
    )

    # Trigger webhook in background
    logger.info(
        f"[WEBHOOK-TRIGGER] Scheduling BOOK_DELETED webhook for book_id={book_id}, book_name='{archived.book_name}', objects_moved={report.objects_moved}"
    )
    background_tasks.add_task(_trigger_webhook, book_id, WebhookEventType.BOOK_DELETED)
    _invalidate_book_cache()
    logger.debug(f"[WEBHOOK-TRIGGER] BOOK_DELETED webhook task added to background queue for book_id={book_id}")

    return BookRead.model_validate(archived)


@router.post("/{book_id}/upload", status_code=status.HTTP_201_CREATED)
async def upload_book(
    book_id: int,
    file: UploadFile,
    background_tasks: BackgroundTasks,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
):
    """Upload/replace content for an existing book."""
    import asyncio
    import os
    import tempfile

    _require_admin(credentials, db)
    book = _book_repository.get_by_id(db, book_id)
    if book is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Book not found")

    # PHASE 1: Extract needed data, release DB connection
    book_publisher_id = book.publisher_id
    book_name = book.book_name
    db.commit()  # Release connection before long S3 ops

    # Stream upload to temp file (never load entire ZIP into memory)
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        tmp_path = tmp.name
        while chunk := await file.read(1024 * 1024):  # 1MB chunks
            tmp.write(chunk)

    # PHASE 2: S3 operations (no DB held)
    try:
        settings = get_settings()
        client = get_minio_client(settings)
        prefix = f"{book_publisher_id}/books/{book_name}/"

        # Clear existing files
        try:
            objects = await asyncio.to_thread(
                lambda: list(client.list_objects(settings.minio_publishers_bucket, prefix=prefix, recursive=True))
            )
            for obj in objects:
                await asyncio.to_thread(client.remove_object, settings.minio_publishers_bucket, obj.object_name)
            logger.info("Cleared %d existing objects for book %s", len(objects), book_id)
        except Exception as exc:
            logger.error("Failed to clear existing book files: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Failed to clear existing book files",
            ) from exc

        # Upload from temp file (disk-based, low memory)
        try:
            manifest = await asyncio.to_thread(
                lambda: upload_book_archive(
                    client=client,
                    archive_path=tmp_path,
                    bucket=settings.minio_publishers_bucket,
                    object_prefix=prefix,
                    content_type="application/octet-stream",
                    strip_root_folder=True,
                )
            )
        except UploadError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        except Exception as exc:
            logger.error("Failed to upload book archive: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Failed to upload book archive",
            ) from exc

        logger.info(
            "Uploaded book assets for book_id=%s with %s files",
            book_id,
            len(manifest),
        )

        # Trigger webhook for book update
        background_tasks.add_task(_trigger_webhook, book_id, WebhookEventType.BOOK_UPDATED)
        _invalidate_book_cache()

        # Trigger auto-processing (force=True since content was replaced)
        background_tasks.add_task(
            trigger_auto_processing,
            book_id=book_id,
            publisher_id=book.publisher_id,
            book_name=book.book_name,
            force=True,
        )

        return {"book_id": book_id, "files": manifest}
    finally:
        os.unlink(tmp_path)


@router.get("/upload-status/{job_id}")
def get_upload_status(
    job_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
):
    """Get upload progress for an async upload job."""
    _require_admin(credentials, db)

    from app.services.cache import get_upload_progress

    progress = get_upload_progress(job_id)
    if progress is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Upload job not found")
    return progress


@router.post("/upload-async", status_code=status.HTTP_202_ACCEPTED)
async def upload_new_book_async(
    file: UploadFile,
    background_tasks: BackgroundTasks,
    publisher_id: int | None = Query(default=None, description="Override publisher from config.json"),
    override: bool = Query(default=False, description="If true, replace existing book"),
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
):
    """Upload a book asynchronously with progress tracking.

    Returns a job_id immediately. Poll /books/upload-status/{job_id} for progress.
    """
    import os
    import tempfile
    import uuid

    from app.services.cache import set_upload_progress

    _require_admin(credentials, db)

    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File must be a ZIP archive")

    job_id = str(uuid.uuid4())
    book_name_from_zip = file.filename[:-4]

    set_upload_progress(job_id, 0, "receiving", "Uploading file to server...")

    # Stream to temp file
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        tmp_path = tmp.name
        total_bytes = 0
        while chunk := await file.read(1024 * 1024):
            tmp.write(chunk)
            total_bytes += len(chunk)

    set_upload_progress(job_id, 40, "received", f"File received ({total_bytes // 1024 // 1024}MB)")

    # Extract needed DB data before releasing connection
    resolved_publisher = None
    if publisher_id is not None:
        resolved_publisher = _publisher_repository.get(db, publisher_id)
        if resolved_publisher is None:
            os.unlink(tmp_path)
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=f"Publisher with ID {publisher_id} not found"
            )
    db.commit()

    # Run the rest in background
    def _process_upload():
        from app.db.session import SessionLocal

        try:
            set_upload_progress(job_id, 45, "extracting", "Reading metadata...")

            # Extract metadata
            try:
                create_payload = _extract_book_metadata(archive_path=tmp_path)
            except UploadError as exc:
                set_upload_progress(job_id, 0, "error", error=str(exc))
                return

            additional_metadata = _extract_additional_metadata(archive_path=tmp_path)

            book_data = create_payload.model_dump()
            book_data["book_name"] = book_name_from_zip
            book_data.update(additional_metadata)

            for field in ("publisher", "book_name", "book_title", "language", "category"):
                value = book_data.get(field)
                if isinstance(value, str):
                    book_data[field] = value.strip()

            set_upload_progress(job_id, 50, "resolving", "Resolving publisher...")

            # Resolve publisher
            session = SessionLocal()
            try:
                if resolved_publisher:
                    book_data["publisher"] = resolved_publisher.name
                    pub_id = resolved_publisher.id
                else:
                    publisher_name = book_data.get("publisher", "")
                    pub = _publisher_repository.get_or_create_by_name(session, publisher_name)
                    pub_id = pub.id
                    session.commit()

                settings = get_settings()
                client = get_minio_client(settings)
                object_prefix = f"{pub_id}/books/{book_data['book_name']}/"

                # Check conflict
                prefix_exists = _prefix_exists(client, settings.minio_publishers_bucket, object_prefix)
                if prefix_exists and not override:
                    set_upload_progress(
                        job_id, 0, "error", error=f"Book '{book_data['book_name']}' already exists. Use override=true."
                    )
                    return

                if prefix_exists and override:
                    set_upload_progress(job_id, 52, "clearing", "Removing existing files...")
                    objects = list(
                        client.list_objects(settings.minio_publishers_bucket, prefix=object_prefix, recursive=True)
                    )
                    for obj in objects:
                        client.remove_object(settings.minio_publishers_bucket, obj.object_name)

                set_upload_progress(job_id, 55, "uploading", "Uploading files to storage...")

                # Upload with progress callback
                def on_file_progress(uploaded: int, total: int):
                    pct = 55 + int((uploaded / max(total, 1)) * 40)  # 55-95%
                    set_upload_progress(job_id, pct, "uploading", f"{uploaded}/{total} files")

                manifest = upload_book_archive(
                    client=client,
                    archive_path=tmp_path,
                    bucket=settings.minio_publishers_bucket,
                    object_prefix=object_prefix,
                    content_type="application/octet-stream",
                    strip_root_folder=True,
                    on_progress=on_file_progress,
                )

                set_upload_progress(job_id, 96, "saving", "Creating database record...")

                # DB operations
                book_data.pop("publisher", None)
                book_data["publisher_id"] = pub_id
                if book_data.get("status") is None or book_data["status"] == BookStatusEnum.DRAFT:
                    book_data["status"] = BookStatusEnum.PUBLISHED

                existing_book = _book_repository.get_by_publisher_id_and_name(
                    session, publisher_id=pub_id, book_name=book_data["book_name"]
                )
                if existing_book:
                    book = _book_repository.update(session, existing_book, data=book_data)
                else:
                    book = _book_repository.create(session, data=book_data)
                session.commit()

                _invalidate_book_cache()

                set_upload_progress(job_id, 100, "completed", f"{len(manifest)} files uploaded", book_id=book.id)

            finally:
                session.close()

        except Exception as exc:
            logger.error("Async upload failed for job %s: %s", job_id, exc, exc_info=True)
            set_upload_progress(job_id, 0, "error", error=str(exc))
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    background_tasks.add_task(_process_upload)

    return {"job_id": job_id, "status": "accepted"}


@router.post("/upload", status_code=status.HTTP_201_CREATED)
async def upload_new_book(
    file: UploadFile,
    background_tasks: BackgroundTasks,
    override: bool = Query(
        False,
        description="When true, backup existing book and upload new one.",
    ),
    publisher_id: int | None = Query(
        None,
        description="Optional publisher ID to override the publisher from config.json.",
    ),
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
):
    """Upload a zipped book folder and create new metadata from the archive.

    Book name is derived from the ZIP filename (e.g., BRAINS.zip → book name: BRAINS).
    Storage path: publisher/zipfilename/ (no version tracking).
    If publisher_id is provided, it overrides the publisher specified in config.json.
    """

    admin_id = _require_admin(credentials, db)

    if not file.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File must have a filename")

    # Extract book name from ZIP filename (remove .zip extension)
    zip_filename = file.filename
    if not zip_filename.lower().endswith(".zip"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File must be a ZIP archive")

    import asyncio
    import tempfile

    book_name_from_zip = zip_filename[:-4]  # Remove .zip extension

    # Stream upload to temp file (never load entire ZIP into memory)
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        tmp_path = tmp.name
        while chunk := await file.read(1024 * 1024):  # 1MB chunks
            tmp.write(chunk)

    try:
        # Extract metadata from config.json (reads from disk)
        try:
            create_payload = _extract_book_metadata(archive_path=tmp_path)
        except UploadError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

        # Extract additional metadata
        additional_metadata = _extract_additional_metadata(archive_path=tmp_path)

        # Use ZIP filename as the book name, override config.json value
        book_data = create_payload.model_dump()
        book_data["book_name"] = book_name_from_zip

        # Add additional metadata
        book_data.update(additional_metadata)

        # Clean up string fields
        for field in ("publisher", "book_name", "book_title", "language", "category"):
            value = book_data.get(field)
            if isinstance(value, str):
                book_data[field] = value.strip()

        # Override publisher if publisher_id is provided
        if publisher_id is not None:
            override_publisher = _publisher_repository.get(db, publisher_id)
            if override_publisher is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Publisher with ID {publisher_id} not found",
                )
            book_data["publisher"] = override_publisher.name
            logger.info(
                "Publisher override: using '%s' (ID: %d) instead of config.json value",
                override_publisher.name,
                publisher_id,
            )
        elif not book_data.get("publisher"):
            # No publisher_id provided and no publisher in config.json
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="config.json does not have publisher_name and no publisher_id was provided",
            )

        # Default new uploads to published status
        if book_data.get("status") is None or book_data["status"] == BookStatusEnum.DRAFT:
            book_data["status"] = BookStatusEnum.PUBLISHED

        # PHASE 1: Quick DB operations — resolve publisher, then release connection
        publisher_name = book_data.get("publisher", "")
        resolved_publisher = _publisher_repository.get_or_create_by_name(db, publisher_name)
        publisher_id = resolved_publisher.id
        db.commit()  # Release DB connection back to PGBouncer pool before long S3 ops

        # PHASE 2: S3 operations (may take minutes — no DB connection held)
        settings = get_settings()
        client = get_minio_client(settings)
        object_prefix = f"{publisher_id}/books/{book_data['book_name']}/"

        # Check if book already exists in storage
        try:
            prefix_exists = await asyncio.to_thread(
                _prefix_exists, client, settings.minio_publishers_bucket, object_prefix
            )
        except Exception as exc:
            logger.error("Failed to check existing book prefix '%s': %s", object_prefix, exc)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Unable to check for existing book",
            ) from exc

        # Handle conflict
        if prefix_exists and not override:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "message": f"Book '{book_data['book_name']}' already exists for publisher '{book_data['publisher']}'. Use override=true to backup and replace.",
                    "code": "BOOK_EXISTS",
                    "publisher": book_data["publisher"],
                    "book_name": book_data["book_name"],
                },
            )

        # Delete existing book if override is true
        if prefix_exists and override:
            try:
                # Delete all objects at this prefix
                objects = await asyncio.to_thread(
                    lambda: list(
                        client.list_objects(settings.minio_publishers_bucket, prefix=object_prefix, recursive=True)
                    )
                )
                for obj in objects:
                    await asyncio.to_thread(client.remove_object, settings.minio_publishers_bucket, obj.object_name)

                logger.info(
                    "Deleted %d existing objects for book %s/%s",
                    len(objects),
                    book_data["publisher"],
                    book_data["book_name"],
                )
            except Exception as exc:
                logger.error("Failed to delete existing book: %s", exc)
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="Failed to delete existing book",
                ) from exc

        # Upload the book archive from temp file (disk-based, low memory)
        try:
            manifest = await asyncio.to_thread(
                lambda: upload_book_archive(
                    client=client,
                    archive_path=tmp_path,
                    bucket=settings.minio_publishers_bucket,
                    object_prefix=object_prefix,
                    content_type="application/octet-stream",
                    strip_root_folder=True,
                )
            )
        except UploadError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        except Exception as exc:  # pragma: no cover - defensive guard
            logger.error("Failed to upload book archive: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Failed to upload book archive",
            ) from exc

        # PHASE 3: Quick DB operations — session will lazily reconnect via PGBouncer
        book_data.pop("publisher", None)
        book_data["publisher_id"] = publisher_id

        existing_book = _book_repository.get_by_publisher_id_and_name(
            db,
            publisher_id=resolved_publisher.id,
            book_name=book_data["book_name"],
        )

        if existing_book:
            # Update existing book
            book = _book_repository.update(db, existing_book, data=book_data)
        else:
            # Create new book
            book = _book_repository.create(db, data=book_data)

        book_read = BookRead.model_validate(book)

        logger.info(
            "User %s uploaded book %s under prefix %s with %s files",
            admin_id,
            book_read.id,
            object_prefix,
            len(manifest),
        )

        # Trigger webhook in background
        logger.info(
            f"[WEBHOOK-TRIGGER] Scheduling BOOK_CREATED webhook (new upload) for book_id={book.id}, book_name='{book.book_name}', files_uploaded={len(manifest)}"
        )
        background_tasks.add_task(_trigger_webhook, book.id, WebhookEventType.BOOK_CREATED)
        _invalidate_book_cache()
        logger.debug(
            f"[WEBHOOK-TRIGGER] BOOK_CREATED webhook task (new upload) added to background queue for book_id={book.id}"
        )

        # Trigger auto-processing for new book (force if override was used)
        logger.info(f"[AUTO-PROCESS] Scheduling auto-processing for book_id={book.id}, book_name='{book.book_name}'")
        background_tasks.add_task(
            trigger_auto_processing,
            book_id=book.id,
            publisher_id=resolved_publisher.id,
            book_name=book.book_name,
            force=override,  # Force reprocess if override was used
        )

        return {"book": book_read.model_dump(), "files": manifest}
    finally:
        os.unlink(tmp_path)


@router.post("/upload-bulk", status_code=status.HTTP_201_CREATED)
async def upload_bulk_books(
    files: list[UploadFile],
    background_tasks: BackgroundTasks,
    override: bool = Query(
        False,
        description="When true, backup existing books and upload new ones.",
    ),
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
):
    """Upload multiple zipped book folders at once.

    Each file is processed independently. Returns detailed results for each upload.
    """

    admin_id = _require_admin(credentials, db)

    if not files:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No files provided")

    if len(files) > 50:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Maximum 50 files allowed per bulk upload")

    results = []
    successful_count = 0
    failed_count = 0

    settings = get_settings()
    client = get_minio_client(settings)

    for file in files:
        result = {
            "filename": file.filename,
            "success": False,
            "book_id": None,
            "book_name": None,
            "publisher": None,
            "error": None,
        }

        try:
            # Validate filename
            if not file.filename:
                result["error"] = "File must have a filename"
                results.append(result)
                failed_count += 1
                continue

            zip_filename = file.filename
            if not zip_filename.lower().endswith(".zip"):
                result["error"] = "File must be a ZIP archive"
                results.append(result)
                failed_count += 1
                continue

            book_name_from_zip = zip_filename[:-4]
            contents = await file.read()

            # Extract metadata
            try:
                create_payload = _extract_book_metadata(contents)
            except UploadError as exc:
                result["error"] = str(exc)
                results.append(result)
                failed_count += 1
                continue

            additional_metadata = _extract_additional_metadata(contents)

            book_data = create_payload.model_dump()
            book_data["book_name"] = book_name_from_zip
            book_data.update(additional_metadata)

            # Clean up string fields
            for field in ("publisher", "book_name", "book_title", "language", "category"):
                value = book_data.get(field)
                if isinstance(value, str):
                    book_data[field] = value.strip()

            # Default to published status
            if book_data.get("status") is None or book_data["status"] == BookStatusEnum.DRAFT:
                book_data["status"] = BookStatusEnum.PUBLISHED

            object_prefix = f"{book_data['publisher']}/books/{book_data['book_name']}/"
            result["publisher"] = book_data["publisher"]
            result["book_name"] = book_data["book_name"]

            # Check if book exists
            try:
                prefix_exists = _prefix_exists(client, settings.minio_publishers_bucket, object_prefix)
            except Exception as exc:
                logger.error("Failed to check existing book prefix '%s': %s", object_prefix, exc)
                result["error"] = "Unable to check for existing book"
                results.append(result)
                failed_count += 1
                continue

            # Handle conflict
            if prefix_exists and not override:
                result["error"] = "Book already exists. Use override=true to replace."
                results.append(result)
                failed_count += 1
                continue

            # Delete existing book if override is true
            if prefix_exists and override:
                try:
                    objects = list(
                        client.list_objects(settings.minio_publishers_bucket, prefix=object_prefix, recursive=True)
                    )
                    for obj in objects:
                        client.remove_object(settings.minio_publishers_bucket, obj.object_name)

                    logger.info(
                        "Deleted %d existing objects for book %s/%s",
                        len(objects),
                        book_data["publisher"],
                        book_data["book_name"],
                    )
                except Exception as exc:
                    logger.error("Failed to delete existing book: %s", exc)
                    result["error"] = "Failed to delete existing book"
                    results.append(result)
                    failed_count += 1
                    continue

            # Upload the book
            try:
                manifest = upload_book_archive(
                    client=client,
                    archive_bytes=contents,
                    bucket=settings.minio_publishers_bucket,
                    object_prefix=object_prefix,
                    content_type="application/octet-stream",
                    strip_root_folder=True,
                )
            except UploadError as exc:
                result["error"] = str(exc)
                results.append(result)
                failed_count += 1
                continue
            except Exception as exc:
                logger.error("Failed to upload book archive: %s", exc)
                result["error"] = "Failed to upload book archive"
                results.append(result)
                failed_count += 1
                continue

            # Convert publisher name to publisher_id
            publisher_name = book_data.pop("publisher")
            publisher = _publisher_repository.get_or_create_by_name(db, publisher_name)
            book_data["publisher_id"] = publisher.id

            # Create or update database record
            existing_book = _book_repository.get_by_publisher_id_and_name(
                db,
                publisher_id=publisher.id,
                book_name=book_data["book_name"],
            )

            if existing_book:
                book = _book_repository.update(db, existing_book, data=book_data)
            else:
                book = _book_repository.create(db, data=book_data)

            result["success"] = True
            result["book_id"] = book.id
            successful_count += 1

            logger.info(
                "User %s uploaded book %s (bulk) under prefix %s with %s files",
                admin_id,
                book.id,
                object_prefix,
                len(manifest),
            )

            # Trigger webhook in background
            logger.info(
                f"[WEBHOOK-TRIGGER] Scheduling BOOK_CREATED webhook (bulk upload) for book_id={book.id}, book_name='{book.book_name}', files_uploaded={len(manifest)}"
            )
            background_tasks.add_task(_trigger_webhook, book.id, WebhookEventType.BOOK_CREATED)
            _invalidate_book_cache()
            logger.debug(
                f"[WEBHOOK-TRIGGER] BOOK_CREATED webhook task (bulk upload) added to background queue for book_id={book.id}"
            )

            # Trigger auto-processing for bulk uploaded book
            logger.info(
                f"[AUTO-PROCESS] Scheduling auto-processing (bulk) for book_id={book.id}, book_name='{book.book_name}'"
            )
            background_tasks.add_task(
                trigger_auto_processing,
                book_id=book.id,
                publisher_id=publisher.id,
                book_name=book.book_name,
                force=override,  # Force reprocess if override was used
            )

        except Exception as exc:
            logger.error("Unexpected error processing file %s: %s", file.filename, exc)
            result["error"] = "Unexpected error during upload"
            failed_count += 1

        results.append(result)

    return {
        "total": len(files),
        "successful": successful_count,
        "failed": failed_count,
        "results": results,
    }


_CONFIG_ALIASES: dict[str, tuple[str, ...]] = {
    "publisher": ("publisher", "publisher_name", "publisherName"),
    "book_title": ("book_title", "bookTitle", "title"),
    "language": ("language", "lang"),
    "category": ("category", "subject", "book_category", "bookCategory"),
    "status": ("status", "book_status", "bookStatus"),
}


def _count_activities(config_data: dict) -> int:
    """Count total activities in the config.json structure."""
    count = 0

    def count_recursive(obj):
        nonlocal count
        if isinstance(obj, dict):
            if "activity" in obj:
                count += 1
            for value in obj.values():
                count_recursive(value)
        elif isinstance(obj, list):
            for item in obj:
                count_recursive(item)

    count_recursive(config_data)
    return count


def _collect_activity_details(config_data: dict) -> dict:
    """Collect frequency of each activity type in the config.json structure."""
    activity_freq = {}

    def collect_recursive(obj):
        if isinstance(obj, dict):
            if "activity" in obj:
                activity_obj = obj["activity"]
                if isinstance(activity_obj, dict) and "type" in activity_obj:
                    activity_type = activity_obj["type"]
                    if isinstance(activity_type, str):
                        activity_freq[activity_type] = activity_freq.get(activity_type, 0) + 1
            for value in obj.values():
                collect_recursive(value)
        elif isinstance(obj, list):
            for item in obj:
                collect_recursive(item)

    collect_recursive(config_data)
    return activity_freq


def _calculate_archive_size(archive_bytes: bytes) -> int:
    """Calculate the total uncompressed size of all files in the archive."""
    try:
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            total_size = sum(entry.file_size for entry in archive.infolist() if not entry.is_dir())
            return total_size
    except Exception as e:
        logger.warning("Failed to calculate archive size: %s", e)
        return 0


def _extract_additional_metadata(
    archive_bytes: bytes | None = None, archive_path: str | None = None
) -> dict[str, object]:
    """Extract book_title, book_cover, activity_count, activity_details, and total_size."""
    try:
        # Calculate archive size
        total_size = os.path.getsize(archive_path) if archive_path else _calculate_archive_size(archive_bytes)

        with zipfile.ZipFile(archive_path or io.BytesIO(archive_bytes)) as archive:
            names = archive.namelist()
            config_path = _first_matching(names, "config.json")

            if config_path is None:
                logger.warning("config.json not found in archive for metadata extraction")
                return {"total_size": total_size}

            config_data = _read_json_from_archive(archive, config_path, label="config.json", required=False)

            # Extract only the filename from book_cover path
            book_cover_path = config_data.get("book_cover")
            book_cover_filename = None
            if book_cover_path:
                # Extract just the filename from paths like "./books/BRAINS/images/book_cover.png"
                book_cover_filename = os.path.basename(book_cover_path)

            # Collect activity details
            activity_details = _collect_activity_details(config_data)

            return {
                "book_title": config_data.get("book_title"),
                "book_cover": book_cover_filename,
                "activity_count": _count_activities(config_data),
                "activity_details": activity_details,
                "total_size": total_size,
            }
    except Exception as exc:
        logger.error("Failed to extract additional metadata: %s", exc, exc_info=True)
        return {}


def _extract_book_metadata(archive_bytes: bytes | None = None, archive_path: str | None = None) -> BookCreate:
    """Return book metadata parsed from ``config.json`` with legacy fallbacks."""

    try:
        with zipfile.ZipFile(archive_path or io.BytesIO(archive_bytes)) as archive:
            names = archive.namelist()
            config_path = _first_matching(names, "config.json")
            metadata_path = _first_matching(names, "metadata.json")

            if config_path is None:
                raise UploadError("config.json is missing from the archive")

            config_payload = _read_json_from_archive(archive, config_path, label="config.json", required=True)
            metadata_payload = None
            if metadata_path is not None:
                try:
                    metadata_payload = _read_json_from_archive(
                        archive,
                        metadata_path,
                        label="metadata.json",
                        required=False,
                    )
                except UploadError:
                    metadata_payload = None

    except zipfile.BadZipFile as exc:
        raise UploadError("Uploaded file is not a valid ZIP archive") from exc

    try:
        payload, used_metadata = _coalesce_metadata(config_payload, metadata_payload)
        if metadata_payload is not None:
            logger.warning(
                "metadata.json detected in upload archive; this file is deprecated%s",
                " and was used to fill missing fields" if used_metadata else "",
            )

        # book_name will be set from ZIP filename, so provide a placeholder for validation
        if "book_name" not in payload or not payload["book_name"]:
            payload["book_name"] = "placeholder"

        # Default to "en" if language is not specified
        if "language" not in payload or not payload["language"]:
            payload["language"] = "en"

        return BookCreate.model_validate(payload)
    except ValidationError as exc:
        missing = {error["loc"][-1] for error in exc.errors() if error.get("type") == "missing"}
        if missing:
            # Filter out book_name from missing fields since we get it from ZIP filename
            missing = {f for f in missing if f != "book_name"}
            if missing:
                missing_fields = ", ".join(sorted(str(field) for field in missing))
                message = f"config.json is missing required fields: {missing_fields}"
            else:
                message = "config.json contains invalid values"
        else:
            message = "config.json contains invalid values"
        raise UploadError(message) from exc


def _first_matching(names: Iterable[str], suffix: str) -> str | None:
    suffix_lower = suffix.lower()
    return next((name for name in names if name.lower().endswith(suffix_lower)), None)


def _read_json_from_archive(
    archive: zipfile.ZipFile,
    path: str,
    *,
    label: str,
    required: bool,
) -> dict[str, object]:
    try:
        with archive.open(path) as file_handle:
            try:
                raw_text = file_handle.read().decode("utf-8")
            except UnicodeDecodeError as exc:
                message = f"{label} must be UTF-8 encoded"
                if required:
                    raise UploadError(message) from exc
                raise UploadError(message) from exc
    except KeyError as exc:
        if required:
            raise UploadError(f"{label} could not be opened") from exc
        raise UploadError(f"{label} could not be opened") from exc

    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        message = f"{label} is not valid JSON"
        if required:
            raise UploadError(message) from exc
        raise UploadError(message) from exc

    if not isinstance(payload, dict):
        message = f"{label} must contain a JSON object"
        if required:
            raise UploadError(message)
        raise UploadError(message)

    return payload


def _coalesce_metadata(
    config_payload: dict[str, object],
    metadata_payload: dict[str, object] | None,
) -> tuple[dict[str, object], bool]:
    result: dict[str, object] = {}
    metadata_used = False

    for target, aliases in _CONFIG_ALIASES.items():
        value = _first_non_empty(config_payload, aliases)
        if value is None and metadata_payload is not None:
            value = _first_non_empty(metadata_payload, aliases)
            if value is not None:
                metadata_used = True

        if isinstance(value, str):
            normalized = value.strip()
            if normalized:
                result[target] = normalized
            elif target in {"version", "status"}:
                # Preserve empty optional values as omitted.
                continue
        elif value is not None:
            result[target] = value

    return result, metadata_used


def _first_non_empty(payload: dict[str, object], aliases: Iterable[str]) -> object | None:
    for alias in aliases:
        if alias in payload:
            candidate = payload[alias]
            if isinstance(candidate, str):
                stripped = candidate.strip()
                if stripped:
                    return stripped
            elif candidate is not None:
                return candidate
    return None
