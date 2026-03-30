"""Upload endpoints for application builds."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Response, UploadFile, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import decode_access_token, verify_api_key_from_db
from app.db import get_db
from app.services import (
    RelocationError,
    UploadConflictError,
    UploadError,
    ensure_version_target,
    extract_manifest_version,
    get_minio_client,
    move_prefix_to_trash,
    upload_app_archive,
)

router = APIRouter(prefix="/apps", tags=["Apps"])
_bearer_scheme = HTTPBearer(auto_error=True)

ALLOWED_PLATFORMS = {"linux", "macos", "windows"}
logger = logging.getLogger(__name__)


class AppDeleteRequest(BaseModel):
    """Payload describing the application build to soft-delete."""

    path: str


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


@router.post("/{platform}/upload", status_code=status.HTTP_201_CREATED)
async def upload_application_build(
    platform: str,
    file: UploadFile,
    override: bool = Query(
        False,
        description="When true, replace an existing version folder if one already exists.",
    ),
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
):
    """Upload an application build archive to the apps bucket."""

    _require_admin(credentials, db)

    normalized_platform = platform.lower()
    if normalized_platform not in ALLOWED_PLATFORMS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported platform")

    settings = get_settings()
    client = get_minio_client(settings)

    archive_bytes = await file.read()
    try:
        version = extract_manifest_version(archive_bytes)
    except UploadError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    prefix = f"{normalized_platform}/{version}/"

    try:
        existing_prefix = ensure_version_target(
            client=client,
            bucket=settings.minio_apps_bucket,
            prefix=prefix,
            version=version,
            override=override,
        )
    except UploadConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": str(exc),
                "code": "VERSION_EXISTS",
                "version": exc.version,
            },
        ) from exc
    except UploadError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Unable to verify existing application builds",
        ) from exc

    if existing_prefix and override:
        try:
            move_prefix_to_trash(
                client=client,
                source_bucket=settings.minio_apps_bucket,
                prefix=prefix,
                trash_bucket=settings.minio_trash_bucket,
            )
        except RelocationError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Failed to relocate existing version before override",
            ) from exc

    try:
        manifest = upload_app_archive(
            client=client,
            archive_bytes=archive_bytes,
            bucket=settings.minio_apps_bucket,
            platform=normalized_platform,
            version=version,
            content_type="application/octet-stream",
        )
    except UploadError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to upload application build",
        ) from exc

    logger.info(
        "Uploaded application build for %s version %s (override=%s, files=%s)",
        normalized_platform,
        version,
        bool(existing_prefix and override),
        len(manifest),
    )

    return {
        "platform": normalized_platform,
        "version": version,
        "files": manifest,
    }


@router.delete("/{platform}", status_code=status.HTTP_204_NO_CONTENT)
def soft_delete_application_build(
    platform: str,
    payload: AppDeleteRequest,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> Response:
    """Soft-delete an application build by moving its assets into the trash bucket."""

    admin_id = _require_admin(credentials, db)

    normalized_platform = platform.lower()
    if normalized_platform not in ALLOWED_PLATFORMS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported platform")

    trimmed_path = payload.path.strip()
    if not trimmed_path:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Path is required")

    normalized_path = trimmed_path.strip("/")
    segments = [segment for segment in normalized_path.split("/") if segment]
    if not segments:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Path is invalid")

    if segments[0].lower() != normalized_platform:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Path must begin with the provided platform",
        )

    if len(segments) == 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Path must include a version or folder under the platform",
        )

    relative_segments = segments[1:]
    prefix = "/".join([normalized_platform, *relative_segments])
    if trimmed_path.endswith("/"):
        prefix = f"{prefix}/"

    settings = get_settings()
    client = get_minio_client(settings)

    try:
        report = move_prefix_to_trash(
            client=client,
            source_bucket=settings.minio_apps_bucket,
            prefix=prefix,
            trash_bucket=settings.minio_trash_bucket,
        )
    except RelocationError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to relocate application build",
        ) from exc

    logger.info(
        "User %s soft-deleted app build prefix '%s'; moved %s objects to %s/%s",
        admin_id,
        report.source_prefix,
        report.objects_moved,
        report.destination_bucket,
        report.destination_prefix,
    )

    return Response(status_code=status.HTTP_204_NO_CONTENT)
