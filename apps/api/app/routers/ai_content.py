"""Endpoints for AI-generated content storage per book.

Storage path: publishers/{publisher}/books/{book_name}/ai-content/{content_id}/
Each content_id folder contains manifest.json, content.json, and optionally audio/.
"""

from __future__ import annotations

import io
import json
import logging
import re
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from minio.error import S3Error
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import decode_access_token, verify_api_key_from_db
from app.db import get_db
from app.repositories.book import BookRepository
from app.repositories.user import UserRepository
from app.schemas.ai_content import (
    AIContentCreate,
    AIContentCreateResponse,
    AIContentRead,
    AudioUploadResponse,
    BatchAudioResponse,
    ManifestRead,
)
from app.services import get_minio_client

router = APIRouter(prefix="/books/{book_id}/ai-content", tags=["AI Content"])
_bearer_scheme = HTTPBearer(auto_error=True)
_book_repository = BookRepository()
_user_repository = UserRepository()
logger = logging.getLogger(__name__)

# Safe filename pattern: alphanumeric, hyphens, underscores, dots
_SAFE_FILENAME_RE = re.compile(r"^[a-zA-Z0-9_\-]+\.mp3$")


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
        pass

    # Try API key
    api_key_info = verify_api_key_from_db(token, db)
    if api_key_info is not None:
        return -1

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid token",
    )


def _get_book_or_404(db: Session, book_id: int):
    """Look up a book by ID or raise 404."""
    book = _book_repository.get_by_id(db, book_id)
    if book is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Book not found")
    return book


def _ai_content_prefix(publisher_id: int, book_name: str) -> str:
    """Build the base MinIO prefix for ai-content under a book."""
    return f"{publisher_id}/books/{book_name}/ai-content/"


def _content_prefix(publisher_id: int, book_name: str, content_id: str) -> str:
    """Build the MinIO prefix for a specific content_id folder."""
    return f"{publisher_id}/books/{book_name}/ai-content/{content_id}/"


def _validate_content_id(content_id: str) -> None:
    """Ensure content_id is a valid UUID."""
    try:
        uuid.UUID(content_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid content_id format",
        )


def _validate_audio_filename(filename: str) -> None:
    """Ensure filename is safe and is an mp3."""
    if not _SAFE_FILENAME_RE.match(filename):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid audio filename. Must be alphanumeric with .mp3 extension.",
        )


def _parse_range_header(range_header: str, file_size: int) -> tuple[int, int]:
    """Parse HTTP Range header and return (start, end) byte positions."""
    range_match = re.match(r"bytes=(\d*)-(\d*)", range_header)
    if not range_match:
        raise HTTPException(
            status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
            detail="Invalid Range header format",
            headers={"Content-Range": f"bytes */{file_size}"},
        )

    range_start, range_end = range_match.groups()

    if range_start and range_end:
        start = int(range_start)
        end = int(range_end)
    elif range_start:
        start = int(range_start)
        end = file_size - 1
    elif range_end:
        start = max(0, file_size - int(range_end))
        end = file_size - 1
    else:
        raise HTTPException(
            status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
            detail="Invalid Range header",
            headers={"Content-Range": f"bytes */{file_size}"},
        )

    if start < 0 or start >= file_size or end >= file_size or start > end:
        raise HTTPException(
            status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
            detail="Range Not Satisfiable",
            headers={"Content-Range": f"bytes */{file_size}"},
        )

    return start, end


# ---------------------------------------------------------------------------
# 1) POST /books/{book_id}/ai-content/ — Create AI content
# ---------------------------------------------------------------------------


@router.post("/", response_model=AIContentCreateResponse, status_code=status.HTTP_201_CREATED)
def create_ai_content(
    book_id: int,
    payload: AIContentCreate,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> AIContentCreateResponse:
    """Store manifest.json and content.json for a new AI content generation.

    DCS assigns the content_id (UUID). Returns the content_id for subsequent
    audio uploads.
    """
    _require_admin(credentials, db)
    book = _get_book_or_404(db, book_id)

    settings = get_settings()
    client = get_minio_client(settings)
    bucket = settings.minio_publishers_bucket

    content_id = str(uuid.uuid4())
    prefix = _content_prefix(book.publisher_id, book.book_name, content_id)

    # Build manifest with server-assigned content_id
    manifest_dict = payload.manifest.model_dump(mode="json")
    manifest_dict["content_id"] = content_id

    # Upload manifest.json
    manifest_bytes = json.dumps(manifest_dict, ensure_ascii=False).encode("utf-8")
    client.put_object(
        bucket,
        f"{prefix}manifest.json",
        io.BytesIO(manifest_bytes),
        length=len(manifest_bytes),
        content_type="application/json",
    )

    # Upload content.json
    content_bytes = json.dumps(payload.content, ensure_ascii=False).encode("utf-8")
    client.put_object(
        bucket,
        f"{prefix}content.json",
        io.BytesIO(content_bytes),
        length=len(content_bytes),
        content_type="application/json",
    )

    logger.info(
        "Created AI content %s for book_id=%s (%s/%s)",
        content_id,
        book_id,
        book.publisher_id,
        book.book_name,
    )

    return AIContentCreateResponse(
        content_id=content_id,
        storage_path=prefix,
    )


# ---------------------------------------------------------------------------
# 2) GET /books/{book_id}/ai-content/ — List all content manifests
# ---------------------------------------------------------------------------


@router.get("/", response_model=list[ManifestRead])
def list_ai_content(
    book_id: int,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> list[ManifestRead]:
    """List all AI content manifests for a book."""
    _require_admin(credentials, db)
    book = _get_book_or_404(db, book_id)

    settings = get_settings()
    client = get_minio_client(settings)
    bucket = settings.minio_publishers_bucket
    base_prefix = _ai_content_prefix(book.publisher_id, book.book_name)

    # List all objects under ai-content/ to discover content_id folders
    objects = list(client.list_objects(bucket, prefix=base_prefix, recursive=True))

    # Collect manifest.json paths
    manifest_keys = [obj.object_name for obj in objects if obj.object_name.endswith("/manifest.json")]

    manifests: list[ManifestRead] = []
    for key in manifest_keys:
        try:
            response = client.get_object(bucket, key)
            try:
                raw = response.read()
            finally:
                response.close()
                response.release_conn()

            data = json.loads(raw.decode("utf-8"))
            manifests.append(ManifestRead.model_validate(data))
        except Exception as exc:
            logger.warning("Failed to read manifest at %s: %s", key, exc)
            continue

    return manifests


# ---------------------------------------------------------------------------
# 3) GET /books/{book_id}/ai-content/{content_id} — Get manifest + content
# ---------------------------------------------------------------------------


@router.get("/{content_id}", response_model=AIContentRead)
def get_ai_content(
    book_id: int,
    content_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> AIContentRead:
    """Retrieve manifest and content for a specific AI content generation."""
    _require_admin(credentials, db)
    _validate_content_id(content_id)
    book = _get_book_or_404(db, book_id)

    settings = get_settings()
    client = get_minio_client(settings)
    bucket = settings.minio_publishers_bucket
    prefix = _content_prefix(book.publisher_id, book.book_name, content_id)

    # Read manifest.json
    try:
        resp = client.get_object(bucket, f"{prefix}manifest.json")
        try:
            manifest_data = json.loads(resp.read().decode("utf-8"))
        finally:
            resp.close()
            resp.release_conn()
    except S3Error as exc:
        if exc.code == "NoSuchKey":
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="AI content not found") from exc
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail="Failed to read manifest") from exc

    # Read content.json
    try:
        resp = client.get_object(bucket, f"{prefix}content.json")
        try:
            content_data = json.loads(resp.read().decode("utf-8"))
        finally:
            resp.close()
            resp.release_conn()
    except S3Error as exc:
        if exc.code == "NoSuchKey":
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Content data not found") from exc
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail="Failed to read content") from exc

    return AIContentRead(
        content_id=content_id,
        manifest=manifest_data,
        content=content_data,
    )


# ---------------------------------------------------------------------------
# 4) DELETE /books/{book_id}/ai-content/{content_id} — Hard delete all
# ---------------------------------------------------------------------------


@router.delete("/{content_id}", status_code=status.HTTP_200_OK)
def delete_ai_content(
    book_id: int,
    content_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
):
    """Hard-delete all files for an AI content generation (manifest, content, audio)."""
    _require_admin(credentials, db)
    _validate_content_id(content_id)
    book = _get_book_or_404(db, book_id)

    settings = get_settings()
    client = get_minio_client(settings)
    bucket = settings.minio_publishers_bucket
    prefix = _content_prefix(book.publisher_id, book.book_name, content_id)

    objects = list(client.list_objects(bucket, prefix=prefix, recursive=True))
    if not objects:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="AI content not found")

    removed = 0
    for obj in objects:
        client.remove_object(bucket, obj.object_name)
        removed += 1

    logger.info(
        "Deleted AI content %s for book_id=%s; removed %d objects",
        content_id,
        book_id,
        removed,
    )

    return {"content_id": content_id, "objects_removed": removed}


# ---------------------------------------------------------------------------
# 5) PUT /books/{book_id}/ai-content/{content_id}/audio/{filename} — Upload one audio
# ---------------------------------------------------------------------------


@router.put("/{content_id}/audio/{filename}", response_model=AudioUploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_audio(
    book_id: int,
    content_id: str,
    filename: str,
    file: UploadFile,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> AudioUploadResponse:
    """Upload a single audio file for an AI content generation."""
    _require_admin(credentials, db)
    _validate_content_id(content_id)
    _validate_audio_filename(filename)
    book = _get_book_or_404(db, book_id)

    settings = get_settings()
    client = get_minio_client(settings)
    bucket = settings.minio_publishers_bucket
    prefix = _content_prefix(book.publisher_id, book.book_name, content_id)

    # Verify content exists (manifest.json must be present)
    try:
        client.stat_object(bucket, f"{prefix}manifest.json")
    except S3Error as exc:
        if exc.code == "NoSuchKey":
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="AI content not found") from exc
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail="Storage error") from exc

    audio_data = await file.read()
    object_key = f"{prefix}audio/{filename}"

    client.put_object(
        bucket,
        object_key,
        io.BytesIO(audio_data),
        length=len(audio_data),
        content_type="audio/mpeg",
    )

    logger.info("Uploaded audio %s for content %s (book_id=%s)", filename, content_id, book_id)

    return AudioUploadResponse(
        filename=filename,
        storage_path=object_key,
        size=len(audio_data),
    )


# ---------------------------------------------------------------------------
# 6) POST /books/{book_id}/ai-content/{content_id}/audio/batch — Batch audio upload
# ---------------------------------------------------------------------------


@router.post("/{content_id}/audio/batch", response_model=BatchAudioResponse, status_code=status.HTTP_201_CREATED)
async def upload_audio_batch(
    book_id: int,
    content_id: str,
    files: list[UploadFile],
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> BatchAudioResponse:
    """Upload multiple audio files at once for an AI content generation."""
    _require_admin(credentials, db)
    _validate_content_id(content_id)
    book = _get_book_or_404(db, book_id)

    settings = get_settings()
    client = get_minio_client(settings)
    bucket = settings.minio_publishers_bucket
    prefix = _content_prefix(book.publisher_id, book.book_name, content_id)

    # Verify content exists
    try:
        client.stat_object(bucket, f"{prefix}manifest.json")
    except S3Error as exc:
        if exc.code == "NoSuchKey":
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="AI content not found") from exc
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail="Storage error") from exc

    uploaded: list[AudioUploadResponse] = []
    failed: list[str] = []

    for upload_file in files:
        fname = upload_file.filename or ""
        if not _SAFE_FILENAME_RE.match(fname):
            failed.append(fname)
            continue

        try:
            audio_data = await upload_file.read()
            object_key = f"{prefix}audio/{fname}"
            client.put_object(
                bucket,
                object_key,
                io.BytesIO(audio_data),
                length=len(audio_data),
                content_type="audio/mpeg",
            )
            uploaded.append(
                AudioUploadResponse(
                    filename=fname,
                    storage_path=object_key,
                    size=len(audio_data),
                )
            )
        except Exception as exc:
            logger.error("Failed to upload audio %s for content %s: %s", fname, content_id, exc)
            failed.append(fname)

    logger.info(
        "Batch audio upload for content %s (book_id=%s): %d uploaded, %d failed",
        content_id,
        book_id,
        len(uploaded),
        len(failed),
    )

    return BatchAudioResponse(uploaded=uploaded, failed=failed)


# ---------------------------------------------------------------------------
# 7) GET /books/{book_id}/ai-content/{content_id}/audio/{filename} — Stream audio
# ---------------------------------------------------------------------------


@router.get("/{content_id}/audio/{filename}")
async def stream_audio(
    book_id: int,
    content_id: str,
    filename: str,
    range_header: str | None = Header(None, alias="Range"),
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
):
    """Stream an audio file with HTTP Range support for seeking."""
    _require_admin(credentials, db)
    _validate_content_id(content_id)
    _validate_audio_filename(filename)
    book = _get_book_or_404(db, book_id)

    settings = get_settings()
    client = get_minio_client(settings)
    bucket = settings.minio_publishers_bucket
    prefix = _content_prefix(book.publisher_id, book.book_name, content_id)
    object_key = f"{prefix}audio/{filename}"

    # Get file metadata
    try:
        stat = client.stat_object(bucket, object_key)
    except S3Error as exc:
        if exc.code == "NoSuchKey":
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Audio file not found") from exc
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail="Storage error") from exc

    file_size = stat.size
    if file_size is None:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail="Unable to determine file size")

    start = 0
    end = file_size - 1
    is_range_request = False

    if range_header:
        is_range_request = True
        start, end = _parse_range_header(range_header, file_size)

    content_length = end - start + 1

    def iterator():
        obj = client.get_object(bucket, object_key, offset=start, length=content_length)
        try:
            for chunk in obj.stream(32 * 1024):
                yield chunk
        finally:
            obj.close()
            obj.release_conn()

    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(content_length),
        "Content-Disposition": f'inline; filename="{filename}"',
    }

    if is_range_request:
        headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"
        return StreamingResponse(
            iterator(),
            status_code=status.HTTP_206_PARTIAL_CONTENT,
            media_type="audio/mpeg",
            headers=headers,
        )

    return StreamingResponse(
        iterator(),
        status_code=status.HTTP_200_OK,
        media_type="audio/mpeg",
        headers=headers,
    )
