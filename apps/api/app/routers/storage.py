"""Endpoints for listing stored content and restoring items from trash.

Includes HTTP Range support for efficient audio/video streaming.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import timedelta
from pathlib import PurePosixPath
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from minio.error import S3Error
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import decode_access_token, verify_api_key_from_db
from app.db import SessionLocal, get_db
from app.repositories.book import BookRepository
from app.repositories.publisher import PublisherRepository
from app.repositories.user import UserRepository
from app.schemas.book import BookRead
from app.schemas.storage import (
    RestoreRequest,
    RestoreResponse,
    TrashDeleteRequest,
    TrashDeleteResponse,
    TrashEntryRead,
)
from app.services import (
    RestorationError,
    TrashDeletionError,
    TrashEntryNotFoundError,
    TrashRetentionError,
    delete_prefix_from_trash,
    get_minio_client,
    list_objects_tree,
    list_trash_entries,
    restore_prefix_from_trash,
)
from app.services.minio import get_minio_client_external

router = APIRouter(prefix="/storage", tags=["Storage"])
_bearer_scheme = HTTPBearer(auto_error=True)
_user_repository = UserRepository()
_book_repository = BookRepository()
_publisher_repository = PublisherRepository()


def _get_publisher_slug(db: Session, publisher_id: int) -> str:
    """Resolve publisher slug from ID, raise 404 if not found."""
    publisher = _publisher_repository.get(db, publisher_id)
    if publisher is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Publisher not found")
    return publisher.slug
logger = logging.getLogger(__name__)

# Media MIME types for proper Content-Type headers
MEDIA_MIME_TYPES = {
    ".mp3": "audio/mpeg",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".ogg": "audio/ogg",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".flac": "audio/flac",
    ".srt": "text/plain",
    ".vtt": "text/vtt",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".pdf": "application/pdf",
    ".json": "application/json",
}


def _get_media_type(path: str, default: str | None = None) -> str:
    """Get MIME type based on file extension."""
    path_lower = path.lower()
    for ext, mime_type in MEDIA_MIME_TYPES.items():
        if path_lower.endswith(ext):
            return mime_type
    return default or "application/octet-stream"


def _parse_range_header(range_header: str, file_size: int) -> tuple[int, int]:
    """
    Parse HTTP Range header and return (start, end) byte positions.

    Supports formats:
    - bytes=0-1023  (first 1024 bytes)
    - bytes=1024-   (from byte 1024 to end)
    - bytes=-500    (last 500 bytes)

    Raises HTTPException with 416 status for invalid ranges.
    """
    range_match = re.match(r"bytes=(\d*)-(\d*)", range_header)
    if not range_match:
        raise HTTPException(
            status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
            detail="Invalid Range header format",
            headers={"Content-Range": f"bytes */{file_size}"},
        )

    range_start, range_end = range_match.groups()

    # Handle different range formats
    if range_start and range_end:
        # bytes=0-1023
        start = int(range_start)
        end = int(range_end)
    elif range_start:
        # bytes=1024- (from start to end of file)
        start = int(range_start)
        end = file_size - 1
    elif range_end:
        # bytes=-500 (last 500 bytes)
        start = max(0, file_size - int(range_end))
        end = file_size - 1
    else:
        raise HTTPException(
            status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
            detail="Invalid Range header",
            headers={"Content-Range": f"bytes */{file_size}"},
        )

    # Validate range bounds
    if start < 0 or start >= file_size or end >= file_size or start > end:
        raise HTTPException(
            status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
            detail="Range Not Satisfiable",
            headers={"Content-Range": f"bytes */{file_size}"},
        )

    return start, end


def _require_admin(credentials: HTTPAuthorizationCredentials, db: Session) -> int:
    """Validate JWT token or API key."""
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
        pass

    # Try API key
    api_key_info = verify_api_key_from_db(token, db)
    if api_key_info is not None:
        return -1

    raise HTTPException(status_code=401, detail="Invalid token")


def _sanitize_segment(segment: str, label: str) -> str:
    sanitized = segment.strip()
    if not sanitized:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"{label} is required")
    if any(separator in sanitized for separator in ("/", "\\")):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"{label} contains invalid characters")
    if sanitized in {"..", "."}:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"{label} is invalid")
    return sanitized


def _normalize_relative_path(path: str) -> str:
    trimmed = path.strip()
    if not trimmed:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="path is required")

    posix_path = PurePosixPath(trimmed)
    if any(part in {"..", "."} for part in posix_path.parts):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="path must not traverse directories")

    normalized = str(posix_path)
    if normalized.endswith("/"):
        normalized = normalized.rstrip("/")
    if not normalized:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="path is invalid")
    return normalized


def _build_book_object_key(publisher_slug: str, book_name: str, relative_path: str | None = None) -> str:
    """Build the MinIO object key for book content.

    Path structure: {publisher_slug}/books/{book_name}/...

    Reserved publisher path prefixes:
        - {publisher_slug}/books/    - Book content (implemented)
        - {publisher_slug}/logos/    - Publisher logos (reserved for future use)
        - {publisher_slug}/materials/ - Publisher materials (reserved for future use)
    """
    book_segment = _sanitize_segment(book_name, "book name")
    if relative_path is None:
        return f"{publisher_slug}/books/{book_segment}/"
    normalized_path = _normalize_relative_path(relative_path)
    return f"{publisher_slug}/books/{book_segment}/{normalized_path}"


@router.get("/books/{publisher_id}/{book_name}")
async def list_book_contents(
    publisher_id: int,
    book_name: str,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
):
    """List the stored files for a specific book."""

    _require_admin(credentials, db)
    slug = _get_publisher_slug(db, publisher_id)
    settings = get_settings()
    client = get_minio_client(settings)
    prefix = _build_book_object_key(slug, book_name, None)
    tree = list_objects_tree(client, settings.minio_publishers_bucket, prefix)
    return tree


@router.get("/books/{publisher_id}/{book_name}/config")
async def get_book_config(
    publisher_id: int,
    book_name: str,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
):
    """Return the `config.json` payload for a stored book."""

    _require_admin(credentials, db)
    slug = _get_publisher_slug(db, publisher_id)

    settings = get_settings()
    client = get_minio_client(settings)

    # Build path: {publisher_slug}/books/{book_name}/config.json
    object_key = _build_book_object_key(slug, book_name, "config.json")

    try:
        client.stat_object(settings.minio_publishers_bucket, object_key)
    except S3Error as exc:
        if exc.code == "NoSuchKey":
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="config.json not found") from exc
        logger.error("Failed statting config '%s/%s': %s", publisher_id, book_name, exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail="Unable to load config.json") from exc

    try:
        response = client.get_object(settings.minio_publishers_bucket, object_key)
    except S3Error as exc:
        if exc.code == "NoSuchKey":
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="config.json not found") from exc
        logger.error("Failed retrieving config '%s/%s': %s", publisher_id, book_name, exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail="Unable to load config.json") from exc

    try:
        raw_data = response.read()
    finally:
        response.close()
        response.release_conn()

    try:
        payload = json.loads(raw_data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        logger.error("Invalid config.json for '%s/%s': %s", publisher_id, book_name, exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail="config.json is invalid") from exc

    return payload


@router.get("/books/{publisher_id}/{book_name}/cover")
async def get_book_cover(
    publisher_id: int,
    book_name: str,
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
):
    """Stream the book cover image."""

    _require_admin(credentials, db)
    slug = _get_publisher_slug(db, publisher_id)

    # Look up book to get the cover filename
    book = _book_repository.get_by_publisher_id_and_book_name(db, publisher_id, book_name)
    if book is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Book not found")

    # Use book_cover from database, fallback to default
    cover_filename = book.book_cover or "book_cover.png"

    settings = get_settings()
    client = get_minio_client(settings)
    object_key = _build_book_object_key(slug, book_name, f"images/{cover_filename}")

    # Get file metadata
    try:
        stat = client.stat_object(settings.minio_publishers_bucket, object_key)
    except S3Error as exc:
        if exc.code == "NoSuchKey":
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Book cover not found") from exc
        logger.error("Failed statting book cover '%s': %s", object_key, exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail="Unable to load book cover") from exc

    file_size = stat.size
    if file_size is None:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail="Unable to determine file size")

    # ETag support — return 304 if client already has this version
    etag = stat.etag
    if etag:
        if_none_match = request.headers.get("if-none-match")
        if if_none_match and if_none_match.strip('"') == etag.strip('"'):
            return JSONResponse(
                status_code=304,
                content=None,
                headers={"ETag": f'"{etag}"', "Cache-Control": "public, max-age=86400, immutable"},
            )

    # Determine MIME type from filename
    media_type = _get_media_type(cover_filename, "image/png")

    def iterator():
        """Stream chunks from MinIO."""
        try:
            obj = client.get_object(settings.minio_publishers_bucket, object_key)
            try:
                for chunk in obj.stream(32 * 1024):
                    yield chunk
            finally:
                obj.close()
                obj.release_conn()
        except S3Error as exc:
            logger.error("Failed streaming book cover '%s': %s", object_key, exc)
            raise

    headers = {
        "Content-Length": str(file_size),
        "Content-Disposition": f'inline; filename="{cover_filename}"',
        "Cache-Control": "public, max-age=86400, immutable",
    }
    if etag:
        headers["ETag"] = f'"{etag}"'

    return StreamingResponse(
        iterator(),
        status_code=status.HTTP_200_OK,
        media_type=media_type,
        headers=headers,
    )


@router.get("/books/{publisher_id}/{book_name}/object")
async def download_book_object(
    publisher_id: int,
    book_name: str,
    request: Request,
    path: str = Query(..., description="Relative path to the object within the book"),
    range_header: str | None = Header(None, alias="Range"),
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
):
    """
    Download or stream a specific object stored under a book prefix.

    Supports HTTP Range requests for efficient audio/video streaming.
    This enables YouTube-style playback where users can seek without
    downloading the entire file.

    **Range Header Examples:**
    - `Range: bytes=0-1023` - First 1024 bytes
    - `Range: bytes=1024-` - From byte 1024 to end
    - `Range: bytes=-500` - Last 500 bytes

    **Response Codes:**
    - `200 OK` - Full file (no Range header)
    - `206 Partial Content` - Partial file (Range header present)
    - `416 Range Not Satisfiable` - Invalid range requested
    """
    _require_admin(credentials, db)
    slug = _get_publisher_slug(db, publisher_id)
    settings = get_settings()
    client = get_minio_client(settings)
    object_key = _build_book_object_key(slug, book_name, path)

    # Get file metadata (size is required for Range support)
    try:
        stat = client.stat_object(settings.minio_publishers_bucket, object_key)
    except S3Error as exc:
        if exc.code == "NoSuchKey":
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="File not found") from exc
        logger.error("Failed statting book object '%s': %s", object_key, exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail="Unable to load object metadata") from exc

    file_size = stat.size
    if file_size is None:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail="Unable to determine file size")

    # Determine MIME type (prefer our mapping over MinIO's detection)
    media_type = _get_media_type(path, getattr(stat, "content_type", None))

    # ETag support — return 304 if client already has this version
    etag = stat.etag
    if etag and not range_header:
        if_none_match = request.headers.get("if-none-match")
        if if_none_match and if_none_match.strip('"') == etag.strip('"'):
            return JSONResponse(
                status_code=304,
                content=None,
                headers={"ETag": f'"{etag}"', "Cache-Control": "public, max-age=86400"},
            )

    # Parse Range header if present
    start = 0
    end = file_size - 1
    is_range_request = False

    if range_header:
        is_range_request = True
        start, end = _parse_range_header(range_header, file_size)
        logger.debug("Range request for '%s': bytes=%d-%d (total=%d)", object_key, start, end, file_size)

    content_length = end - start + 1

    def iterator():
        """Stream chunks from MinIO with optional offset/length for Range requests."""
        try:
            # Use offset and length for partial reads (efficient streaming)
            obj = client.get_object(
                settings.minio_publishers_bucket,
                object_key,
                offset=start,
                length=content_length,
            )
            try:
                for chunk in obj.stream(32 * 1024):
                    yield chunk
            finally:
                obj.close()
                obj.release_conn()
        except S3Error as exc:
            logger.error("Failed streaming book object '%s': %s", object_key, exc)
            raise

    # Build response headers
    filename = PurePosixPath(object_key).name or "download"

    # Set Cache-Control based on content type (static assets are immutable until re-upload)
    if media_type.startswith(("image/", "audio/", "video/")):
        cache_control = "public, max-age=86400, immutable"
    elif media_type == "application/json":
        cache_control = "private, max-age=3600"
    else:
        cache_control = "public, max-age=3600"

    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(content_length),
        "Content-Disposition": f'inline; filename="{filename}"',
        "Cache-Control": cache_control,
    }
    if etag:
        headers["ETag"] = f'"{etag}"'

    if is_range_request:
        # 206 Partial Content for range requests
        headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"
        logger.info("Streaming range %d-%d/%d for '%s'", start, end, file_size, object_key)
        return StreamingResponse(
            iterator(),
            status_code=status.HTTP_206_PARTIAL_CONTENT,
            media_type=media_type,
            headers=headers,
        )
    else:
        # 200 OK for full file requests
        logger.debug("Streaming full file '%s' (%d bytes)", object_key, file_size)
        return StreamingResponse(
            iterator(),
            status_code=status.HTTP_200_OK,
            media_type=media_type,
            headers=headers,
        )


@router.get("/books/{publisher_id}/{book_name}/presigned")
async def get_presigned_url(
    publisher_id: int,
    book_name: str,
    path: str = Query(..., description="Relative path to the object within the book"),
    expires: int = Query(3600, ge=60, le=86400, description="URL expiry in seconds (default 1h, max 24h)"),
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
):
    """Generate a presigned URL for direct browser access to a book asset.

    Returns a temporary URL that allows direct download from MinIO
    without proxying through the API server. Useful for large assets
    like images, audio, and video files.
    """
    _require_admin(credentials, db)
    slug = _get_publisher_slug(db, publisher_id)
    settings = get_settings()
    object_key = _build_book_object_key(slug, book_name, path)

    # Verify the object exists
    client = get_minio_client(settings)
    try:
        client.stat_object(settings.minio_publishers_bucket, object_key)
    except S3Error as exc:
        if exc.code == "NoSuchKey":
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="File not found") from exc
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail="Unable to verify object") from exc

    # Generate presigned URL using external client (correct host for browser access)
    external_client = get_minio_client_external(settings)
    presigned_url = external_client.presigned_get_object(
        bucket_name=settings.minio_publishers_bucket,
        object_name=object_key,
        expires=timedelta(seconds=expires),
    )

    return {"url": presigned_url, "expires_in": expires}


@router.get("/apps/{platform}")
async def list_app_contents(
    platform: str,
    version: str | None = Query(default=None),
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
):
    """List stored files for application builds on a platform (optional version filter)."""

    _require_admin(credentials, db)
    settings = get_settings()
    client = get_minio_client(settings)

    prefix = f"{platform}/"
    if version:
        prefix = f"{prefix}{version}/"

    tree = list_objects_tree(client, settings.minio_apps_bucket, prefix)
    return tree


@router.get("/trash", response_model=list[TrashEntryRead])
async def list_trash_contents(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
):
    """Return aggregated list of items currently stored in the trash bucket."""

    _require_admin(credentials, db)
    settings = get_settings()
    client = get_minio_client(settings)
    retention = timedelta(days=settings.trash_retention_days)
    entries = list_trash_entries(client, settings.minio_trash_bucket, retention)
    logger.info(
        "Fetched %s trash entries with retention window of %s days",
        len(entries),
        settings.trash_retention_days,
    )
    return [
        TrashEntryRead(
            key=entry.key,
            bucket=entry.bucket,
            path=entry.path,
            item_type=entry.item_type,
            object_count=entry.object_count,
            total_size=entry.total_size,
            metadata=entry.metadata,
            youngest_last_modified=entry.youngest_last_modified,
            eligible_at=entry.eligible_at,
            eligible_for_deletion=entry.eligible_for_deletion,
        )
        for entry in entries
    ]


def _parse_trash_key(key: str) -> tuple[str, list[str]]:
    normalized = key.strip()
    if not normalized:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Key is required")

    parts = [segment for segment in normalized.split("/") if segment]
    if len(parts) < 2:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Key is invalid")
    return parts[0], parts[1:]


def _extract_book_identifiers(path_parts: list[str]) -> tuple[str, str]:
    if len(path_parts) < 3 or path_parts[1] != "books":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Book restore key is incomplete")
    return path_parts[0], path_parts[2]


def _extract_teacher_identifiers(path_parts: list[str]) -> tuple[str, str]:
    """Extract teacher_id and material path from trash key parts."""
    if len(path_parts) < 2 or path_parts[1] != "materials":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Teacher restore key is incomplete")
    return path_parts[0], "/".join(path_parts[2:]) if len(path_parts) > 2 else ""


@router.post("/restore", response_model=RestoreResponse)
def restore_item(
    payload: RestoreRequest,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
):
    """Restore a soft-deleted book or application build from the trash bucket."""

    admin_id = _require_admin(credentials, db)
    bucket, path_parts = _parse_trash_key(payload.key)

    # Release DB connection before slow storage operation
    db.close()

    settings = get_settings()
    client = get_minio_client(settings)

    key_with_bucket = f"{bucket}/{'/'.join(path_parts)}/"

    try:
        report = restore_prefix_from_trash(
            client=client,
            trash_bucket=settings.minio_trash_bucket,
            key=key_with_bucket,
        )
    except RestorationError as exc:
        message = str(exc)
        status_code = status.HTTP_404_NOT_FOUND if "No trash objects" in message else status.HTTP_502_BAD_GATEWAY
        raise HTTPException(status_code=status_code, detail=message) from exc

    item_type: Literal["book", "app", "teacher_material", "unknown"] = "unknown"
    book_read: BookRead | None = None

    if bucket == "publishers":
        item_type = "book"
        publisher_slug, book_name = _extract_book_identifiers(path_parts)

        # Re-open DB session for the restore update
        db = SessionLocal()
        try:
            publisher = _publisher_repository.get_by_slug(db, publisher_slug)
            if publisher is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Publisher not found for slug in trash key")

            book = _book_repository.get_by_publisher_id_and_book_name(db, publisher.id, book_name)
            if book is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Book not found")

            try:
                restored_book = _book_repository.restore(db, book)
            except ValueError as exc:
                raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc

            book_read = BookRead.model_validate(restored_book)
        finally:
            db.close()
    elif bucket == "apps":
        item_type = "app"
    elif bucket == "teachers":
        item_type = "teacher_material"
        # No database record for teacher materials, just restore files

    logger.info(
        "User %s restored trash key '%s'; moved %s objects",
        admin_id,
        key_with_bucket,
        report.objects_moved,
    )

    return RestoreResponse(
        restored_key=key_with_bucket,
        objects_moved=report.objects_moved,
        item_type=item_type,
        book=book_read,
    )


@router.delete("/trash", response_model=TrashDeleteResponse)
def delete_trash_entry(
    payload: TrashDeleteRequest,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
):
    """Permanently delete a trash entry after retention checks succeed."""

    admin_id = _require_admin(credentials, db)
    bucket, path_parts = _parse_trash_key(payload.key)

    # Override reason is optional now - no longer required for force deletion
    override_reason = payload.override_reason.strip() if payload.override_reason else None

    # Release DB connection before slow storage operation
    db.close()

    settings = get_settings()
    client = get_minio_client(settings)

    key_with_bucket = f"{bucket}/{'/'.join(path_parts)}/"
    retention_period = timedelta(days=settings.trash_retention_days)

    try:
        report = delete_prefix_from_trash(
            client=client,
            trash_bucket=settings.minio_trash_bucket,
            key=key_with_bucket,
            retention=retention_period,
            force=payload.force,
            override_reason=override_reason,
        )
    except TrashEntryNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except TrashRetentionError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except TrashDeletionError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    item_type: Literal["book", "app", "teacher_material", "unknown"] = "unknown"
    if bucket == "publishers":
        item_type = "book"
        publisher_slug, book_name = _extract_book_identifiers(path_parts)
        # Re-open DB session only for the delete operation
        db = SessionLocal()
        try:
            publisher = _publisher_repository.get_by_slug(db, publisher_slug)
            if publisher is not None:
                book = _book_repository.get_by_publisher_id_and_book_name(db, publisher.id, book_name)
                if book is not None:
                    _book_repository.delete(db, book)
        finally:
            db.close()
    elif bucket == "apps":
        item_type = "app"
    elif bucket == "teachers":
        item_type = "teacher_material"
        # No database record for teacher materials, just delete files

    logger.info(
        "User %s permanently deleted trash key '%s'; removed %s objects (force=%s, override_reason=%s)",
        admin_id,
        key_with_bucket,
        report.objects_removed,
        payload.force,
        override_reason,
    )

    return TrashDeleteResponse(
        deleted_key=key_with_bucket,
        objects_removed=report.objects_removed,
        item_type=item_type,
    )


@router.get("/incomplete-uploads")
def list_incomplete_uploads_endpoint(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> dict:
    """List incomplete multipart uploads across all buckets.

    Requires admin authentication.
    """
    _require_admin(credentials, db)
    db.close()

    settings = get_settings()
    client = get_minio_client(settings)

    buckets = [
        settings.minio_publishers_bucket,
        settings.minio_apps_bucket,
        settings.minio_trash_bucket,
        settings.minio_teachers_bucket,
    ]

    results = []
    for bucket in buckets:
        try:
            result = client._list_multipart_uploads(bucket)
            for upload in result.uploads:
                results.append({
                    "bucket": bucket,
                    "object_name": upload.object_name,
                    "upload_id": upload.upload_id,
                    "initiated": upload.initiated.isoformat() if upload.initiated else None,
                })
        except Exception as exc:
            logger.warning("Failed to list incomplete uploads for %s: %s", bucket, exc)

    return {"incomplete_uploads": results, "total": len(results)}


@router.delete("/incomplete-uploads")
def abort_incomplete_uploads_endpoint(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> dict:
    """Abort all incomplete multipart uploads across all buckets.

    Requires admin authentication.
    """
    _require_admin(credentials, db)
    db.close()

    settings = get_settings()
    client = get_minio_client(settings)

    buckets = [
        settings.minio_publishers_bucket,
        settings.minio_apps_bucket,
        settings.minio_trash_bucket,
        settings.minio_teachers_bucket,
    ]

    aborted = 0
    errors = []
    for bucket in buckets:
        try:
            result = client._list_multipart_uploads(bucket)
            for upload in result.uploads:
                try:
                    client._abort_multipart_upload(bucket, upload.object_name, upload.upload_id)
                    aborted += 1
                    logger.info("Aborted incomplete upload: %s/%s (id: %s)", bucket, upload.object_name, upload.upload_id)
                except Exception as exc:
                    errors.append(f"{bucket}/{upload.object_name}: {exc}")
        except Exception as exc:
            errors.append(f"{bucket}: {exc}")

    return {"aborted": aborted, "errors": errors}
