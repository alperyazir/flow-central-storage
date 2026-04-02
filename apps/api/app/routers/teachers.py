"""Endpoints for teacher material storage management.

Includes HTTP Range support for efficient audio/video streaming.
"""

from __future__ import annotations

import io
import logging
import re
from pathlib import PurePosixPath

from fastapi import APIRouter, Depends, Header, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import decode_access_token, verify_api_key_from_db
from app.db import SessionLocal, get_db
from app.repositories.material import MaterialRepository
from app.repositories.teacher import TeacherRepository
from app.repositories.user import UserRepository
from app.services import DirectDeletionError, get_minio_client, delete_prefix_directly

router = APIRouter(prefix="/teachers", tags=["Teachers"])
_bearer_scheme = HTTPBearer(auto_error=True)
_user_repository = UserRepository()
_teacher_repository = TeacherRepository()
_material_repository = MaterialRepository()
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
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".mov": "video/quicktime",
}


def _get_media_type(path: str, default: str | None = None) -> str:
    """Get MIME type based on file extension."""
    path_lower = path.lower()
    for ext, mime_type in MEDIA_MIME_TYPES.items():
        if path_lower.endswith(ext):
            return mime_type
    return default or "application/octet-stream"


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


def _require_admin(credentials: HTTPAuthorizationCredentials, db: Session) -> int:
    """Validate JWT token or API key."""
    token = credentials.credentials

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

    api_key_info = verify_api_key_from_db(token, db)
    if api_key_info is not None:
        return -1

    raise HTTPException(status_code=401, detail="Invalid token")


def _sanitize_segment(segment: str, label: str) -> str:
    """Sanitize a path segment to prevent directory traversal."""
    sanitized = segment.strip()
    if not sanitized:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"{label} is required")
    if any(separator in sanitized for separator in ("/", "\\")):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"{label} contains invalid characters")
    if sanitized in {"..", "."}:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"{label} is invalid")
    return sanitized


def _normalize_relative_path(path: str) -> str:
    """Normalize and validate a relative file path."""
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


def _build_teacher_object_key(teacher_id: str, relative_path: str | None = None) -> str:
    """Build the MinIO object key for teacher materials.

    Path structure: {teacher_id}/materials/{relative_path}
    """
    teacher_segment = _sanitize_segment(teacher_id, "teacher_id")
    if relative_path is None:
        return f"{teacher_segment}/materials/"
    normalized_path = _normalize_relative_path(relative_path)
    return f"{teacher_segment}/materials/{normalized_path}"


def _validate_file_type(content_type: str | None, settings) -> str:
    """Validate file MIME type against allowed types."""
    if content_type is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File content type is required",
        )

    if content_type not in settings.teacher_all_allowed_mime_types:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"File type '{content_type}' is not allowed. Allowed types: {', '.join(settings.teacher_all_allowed_mime_types)}",
        )

    return content_type


@router.post("/{teacher_id}/upload", status_code=status.HTTP_201_CREATED)
async def upload_teacher_material(
    teacher_id: str,
    file: UploadFile,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
):
    """Upload a material file to a teacher's storage namespace.

    Validates file type and size against configured limits.
    Also creates database records for the teacher (if not exists) and material.
    """
    _require_admin(credentials, db)
    settings = get_settings()

    # Validate file has a name
    if not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File must have a filename",
        )

    # Validate MIME type
    _validate_file_type(file.content_type, settings)

    import asyncio
    import os
    import tempfile

    # Stream to temp file instead of loading into memory
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp_path = tmp.name
        total_bytes = 0
        while chunk := await file.read(1024 * 1024):  # 1MB chunks
            tmp.write(chunk)
            total_bytes += len(chunk)
            if total_bytes > settings.teacher_max_file_size_bytes:
                os.unlink(tmp_path)
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"File exceeds max size ({settings.teacher_max_file_size_bytes // 1024 // 1024}MB)",
                )

    try:
        # Get or create Teacher record in database
        teacher = _teacher_repository.get_or_create_by_teacher_id(db, teacher_id)
        teacher_db_id = teacher.id
        logger.info(
            "Teacher record: id=%d, teacher_id='%s'",
            teacher.id,
            teacher.teacher_id,
        )
        db.commit()  # Release DB connection before S3 upload

        client = get_minio_client(settings)
        object_key = _build_teacher_object_key(teacher_id, file.filename)

        # Upload from temp file
        await asyncio.to_thread(
            client.fput_object,
            settings.minio_teachers_bucket,
            object_key,
            tmp_path,
            content_type=file.content_type or "application/octet-stream",
        )

        # Extract file extension for file_type
        file_ext = ""
        if file.filename and "." in file.filename:
            file_ext = file.filename.rsplit(".", 1)[-1].lower()

        # Create Material record in database
        material = _material_repository.create(
            db,
            data={
                "material_name": file.filename,
                "file_type": file_ext,
                "content_type": file.content_type or "application/octet-stream",
                "size": total_bytes,
                "teacher_id": teacher_db_id,
                "status": "active",
            },
        )

        logger.info(
            "Uploaded teacher material '%s' (%d bytes) - Material ID: %d",
            object_key,
            total_bytes,
            material.id,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to upload teacher material: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to upload file",
        ) from exc
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    return {
        "teacher_id": teacher_id,
        "material_id": material.id,
        "filename": file.filename,
        "path": object_key,
        "size": total_bytes,
        "content_type": file.content_type,
    }


@router.get("/")
async def list_all_teachers(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
):
    """List all teacher IDs that have materials stored."""
    _require_admin(credentials, db)
    settings = get_settings()
    client = get_minio_client(settings)

    # List all objects at root level to get unique teacher IDs
    teacher_ids = set()
    try:
        objects = client.list_objects(settings.minio_teachers_bucket, prefix="", recursive=False)
        for obj in objects:
            # Object names are like "teacher_123/materials/..."
            # We want just the teacher_id part
            name = obj.object_name
            if name and "/" in name:
                teacher_id = name.split("/")[0]
                if teacher_id:
                    teacher_ids.add(teacher_id)
    except Exception as exc:
        logger.error("Failed to list teachers: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to list teachers",
        ) from exc

    return {"teachers": sorted(teacher_ids)}


@router.get("/{teacher_id}/materials")
async def list_teacher_materials(
    teacher_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
):
    """List all materials stored for a specific teacher."""
    from app.services import list_objects_tree

    _require_admin(credentials, db)
    settings = get_settings()
    client = get_minio_client(settings)

    prefix = _build_teacher_object_key(teacher_id, None)
    tree = list_objects_tree(client, settings.minio_teachers_bucket, prefix)

    return tree


@router.get("/{teacher_id}/materials/{path:path}")
async def download_teacher_material(
    teacher_id: str,
    path: str,
    range_header: str | None = Header(None, alias="Range"),
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
):
    """Download or stream a specific teacher material.

    Supports HTTP Range requests for efficient audio/video streaming.

    **Range Header Examples:**
    - `Range: bytes=0-1023` - First 1024 bytes
    - `Range: bytes=1024-` - From byte 1024 to end
    - `Range: bytes=-500` - Last 500 bytes
    """
    from minio.error import S3Error

    _require_admin(credentials, db)
    settings = get_settings()
    client = get_minio_client(settings)
    object_key = _build_teacher_object_key(teacher_id, path)

    # Get file metadata
    try:
        stat = client.stat_object(settings.minio_teachers_bucket, object_key)
    except S3Error as exc:
        if exc.code == "NoSuchKey":
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="File not found") from exc
        logger.error("Failed statting teacher material '%s': %s", object_key, exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail="Unable to load file metadata") from exc

    file_size = stat.size
    if file_size is None:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail="Unable to determine file size")

    media_type = _get_media_type(path, getattr(stat, "content_type", None))

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
            obj = client.get_object(
                settings.minio_teachers_bucket,
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
            logger.error("Failed streaming teacher material '%s': %s", object_key, exc)
            raise

    filename = PurePosixPath(object_key).name or "download"
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(content_length),
        "Content-Disposition": f'inline; filename="{filename}"',
    }

    if is_range_request:
        headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"
        logger.info("Streaming range %d-%d/%d for '%s'", start, end, file_size, object_key)
        return StreamingResponse(
            iterator(),
            status_code=status.HTTP_206_PARTIAL_CONTENT,
            media_type=media_type,
            headers=headers,
        )
    else:
        logger.debug("Streaming full file '%s' (%d bytes)", object_key, file_size)
        return StreamingResponse(
            iterator(),
            status_code=status.HTTP_200_OK,
            media_type=media_type,
            headers=headers,
        )


@router.delete("/{teacher_id}/materials/{path:path}")
async def delete_teacher_material(
    teacher_id: str,
    path: str,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
):
    """Permanently delete a teacher material from storage."""
    _require_admin(credentials, db)

    # Release DB connection before slow storage operation
    db.close()

    settings = get_settings()
    client = get_minio_client(settings)

    object_key = _build_teacher_object_key(teacher_id, path)

    try:
        report = delete_prefix_directly(
            client=client,
            bucket=settings.minio_teachers_bucket,
            prefix=object_key,
        )
    except DirectDeletionError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to delete material",
        ) from exc

    if report.objects_removed == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found",
        )

    logger.info(
        "Permanently deleted teacher material '%s'; %d objects removed",
        object_key,
        report.objects_removed,
    )

    return {
        "deleted": True,
        "teacher_id": teacher_id,
        "path": path,
        "objects_removed": report.objects_removed,
    }
