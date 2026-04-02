"""CRUD endpoints for publisher metadata."""

from __future__ import annotations

import asyncio
import io
import logging
import re
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import decode_access_token, verify_api_key_from_db
from app.db import SessionLocal, get_db
from app.models.webhook import WebhookEventType
from app.repositories.publisher import PublisherRepository
from app.repositories.user import UserRepository
from app.schemas.asset import AssetFileInfo, AssetTypeInfo, PublisherAssetsResponse
from app.schemas.book import BookRead
from app.schemas.publisher import (
    PublisherCreate,
    PublisherListItem,
    PublisherListResponse,
    PublisherRead,
    PublisherUpdate,
)
from app.services import DirectDeletionError, get_minio_client, delete_prefix_directly
from app.services.webhook import WebhookService

router = APIRouter(prefix="/publishers", tags=["Publishers"])
_bearer_scheme = HTTPBearer(auto_error=True)
_publisher_repository = PublisherRepository()
_user_repository = UserRepository()
_webhook_service = WebhookService()
logger = logging.getLogger(__name__)


def _invalidate_publisher_cache() -> None:
    from app.services.cache import get_cache

    try:
        get_cache().invalidate("fcs:publishers:*")
    except Exception:
        pass


def _trigger_publisher_webhook(publisher_id: int, event_type: WebhookEventType) -> None:
    """Trigger webhook broadcast for a publisher event (runs in background)."""
    logger.info(f"[WEBHOOK] Triggering {event_type.value} webhook for publisher_id={publisher_id}")
    try:
        with SessionLocal() as session:
            publisher = _publisher_repository.get(session, publisher_id)
            if publisher:
                asyncio.run(_webhook_service.broadcast_publisher_event(session, event_type, publisher))
            else:
                logger.warning(f"[WEBHOOK] Publisher {publisher_id} not found for webhook broadcast")
    except Exception as e:
        logger.error(f"[WEBHOOK] Failed to trigger publisher webhook: {e}", exc_info=True)


# Asset type validation
ASSET_TYPE_PATTERN = re.compile(r"^[a-z0-9_-]{1,50}$")
RESERVED_ASSET_TYPES = {"books", "trash", "temp"}


def validate_asset_type(asset_type: str) -> None:
    """Validate asset type format and check for reserved names."""
    if not ASSET_TYPE_PATTERN.match(asset_type):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid asset type format. Must be lowercase alphanumeric, hyphens, or underscores (1-50 chars)",
        )
    if asset_type in RESERVED_ASSET_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"'{asset_type}' is a reserved name",
        )


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
        return -1

    # Both JWT and API key failed
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid token",
    )


@router.post("/", response_model=PublisherRead, status_code=status.HTTP_201_CREATED)
def create_publisher(
    payload: PublisherCreate,
    background_tasks: BackgroundTasks,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> PublisherRead:
    """Create a new publisher record."""

    _require_admin(credentials, db)

    try:
        publisher = _publisher_repository.create(db, data=payload.model_dump())
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Publisher with name '{payload.name}' already exists",
        )

    # Trigger webhook in background
    logger.info(
        f"[WEBHOOK-TRIGGER] Scheduling PUBLISHER_CREATED webhook for publisher_id={publisher.id}, name='{publisher.name}'"
    )
    background_tasks.add_task(_trigger_publisher_webhook, publisher.id, WebhookEventType.PUBLISHER_CREATED)
    _invalidate_publisher_cache()
    logger.debug(
        f"[WEBHOOK-TRIGGER] PUBLISHER_CREATED webhook task added to background queue for publisher_id={publisher.id}"
    )

    return PublisherRead.model_validate(publisher)


@router.get("/", response_model=PublisherListResponse)
def list_publishers(
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum records to return"),
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> PublisherListResponse:
    """Return active publishers with pagination."""

    _require_admin(credentials, db)

    from app.services.cache import cache_key, get_cache

    cache = get_cache()
    ck = cache_key("publishers", "list", skip, limit)
    cached = cache.get(ck)
    if cached is not None:
        return cached

    publishers = _publisher_repository.list_active(db, skip=skip, limit=limit)
    total = _publisher_repository.count_active(db)

    items = [
        PublisherListItem(
            id=p.id,
            name=p.name,
            display_name=p.display_name,
            description=p.description,
            contact_email=p.contact_email,
            status=p.status,
            created_at=p.created_at,
            updated_at=p.updated_at,
            logo_url=f"/publishers/{p.id}/logo",
        )
        for p in publishers
    ]

    result = PublisherListResponse(items=items, total=total).model_dump(mode="json")
    cache.set(ck, result, ttl=600)
    return result


@router.get("/trash", response_model=PublisherListResponse)
def list_trashed_publishers(
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum records to return"),
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> PublisherListResponse:
    """Return archived/trashed publishers with pagination."""

    _require_admin(credentials, db)
    publishers = _publisher_repository.list_archived(db, skip=skip, limit=limit)
    total = _publisher_repository.count_archived(db)

    items = [
        PublisherListItem(
            id=p.id,
            name=p.name,
            display_name=p.display_name,
            description=p.description,
            contact_email=p.contact_email,
            status=p.status,
            created_at=p.created_at,
            updated_at=p.updated_at,
            logo_url=f"/publishers/{p.id}/logo",
        )
        for p in publishers
    ]

    return PublisherListResponse(items=items, total=total)


@router.get("/{publisher_id}", response_model=PublisherRead)
def get_publisher(
    publisher_id: int,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> PublisherRead:
    """Retrieve a single publisher by ID."""

    _require_admin(credentials, db)
    publisher = _publisher_repository.get(db, publisher_id)
    if publisher is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Publisher not found",
        )
    return PublisherRead.model_validate(publisher)


@router.get("/by-name/{name}", response_model=PublisherRead)
def get_publisher_by_name(
    name: str,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> PublisherRead:
    """Retrieve a single publisher by unique name."""

    _require_admin(credentials, db)
    publisher = _publisher_repository.get_by_name(db, name)
    if publisher is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Publisher not found",
        )
    return PublisherRead.model_validate(publisher)


@router.put("/{publisher_id}", response_model=PublisherRead)
def update_publisher(
    publisher_id: int,
    payload: PublisherUpdate,
    background_tasks: BackgroundTasks,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> PublisherRead:
    """Update metadata for an existing publisher."""

    _require_admin(credentials, db)
    publisher = _publisher_repository.get(db, publisher_id)
    if publisher is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Publisher not found",
        )

    update_data = payload.model_dump(exclude_unset=True)
    if not update_data:
        return PublisherRead.model_validate(publisher)

    try:
        updated = _publisher_repository.update(db, publisher, data=update_data)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Publisher with name '{payload.name}' already exists",
        )

    # Trigger webhook in background
    logger.info(
        f"[WEBHOOK-TRIGGER] Scheduling PUBLISHER_UPDATED webhook for publisher_id={updated.id}, name='{updated.name}', updated_fields={list(update_data.keys())}"
    )
    background_tasks.add_task(_trigger_publisher_webhook, updated.id, WebhookEventType.PUBLISHER_UPDATED)
    _invalidate_publisher_cache()
    logger.debug(
        f"[WEBHOOK-TRIGGER] PUBLISHER_UPDATED webhook task added to background queue for publisher_id={updated.id}"
    )

    return PublisherRead.model_validate(updated)


@router.delete("/{publisher_id}", response_model=PublisherRead)
def soft_delete_publisher(
    publisher_id: int,
    background_tasks: BackgroundTasks,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> PublisherRead:
    """Permanently delete a publisher, all its books, and storage files."""
    _require_admin(credentials, db)
    publisher = _publisher_repository.get_with_books(db, publisher_id)
    if publisher is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Publisher not found",
        )

    publisher_name = publisher.name
    result = PublisherRead.model_validate(publisher)

    # Release DB before slow storage operation
    db.close()

    # Delete all files from R2 storage
    settings = get_settings()
    client = get_minio_client(settings)
    try:
        report = delete_prefix_directly(
            client=client,
            bucket=settings.minio_publishers_bucket,
            prefix=f"{publisher_id}/",
        )
        logger.info("Deleted %d objects from R2 for publisher %s", report.objects_removed, publisher_name)
    except DirectDeletionError as e:
        logger.error("Error deleting R2 objects for publisher %s: %s", publisher_name, e)

    # Re-open DB to delete record
    from app.db import SessionLocal
    db = SessionLocal()
    try:
        publisher = _publisher_repository.get_with_books(db, publisher_id)
        if publisher:
            _publisher_repository.delete(db, publisher)
    finally:
        db.close()

    # Trigger webhook in background
    background_tasks.add_task(_trigger_publisher_webhook, publisher_id, WebhookEventType.PUBLISHER_DELETED)
    _invalidate_publisher_cache()

    return result


@router.post("/{publisher_id}/restore", response_model=PublisherRead)
def restore_publisher(
    publisher_id: int,
    background_tasks: BackgroundTasks,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> PublisherRead:
    """Restore a trashed publisher by setting status back to active."""
    _require_admin(credentials, db)
    publisher = _publisher_repository.get(db, publisher_id)
    if publisher is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Publisher not found",
        )

    if publisher.status != "inactive":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Publisher is not in trash",
        )

    # Restore: set status to active
    updated = _publisher_repository.update(db, publisher, data={"status": "active"})

    # Trigger webhook as created (since it's being restored)
    logger.info(
        f"[WEBHOOK-TRIGGER] Scheduling PUBLISHER_CREATED webhook (restore) for publisher_id={updated.id}, name='{updated.name}', status='active'"
    )
    background_tasks.add_task(_trigger_publisher_webhook, updated.id, WebhookEventType.PUBLISHER_CREATED)
    _invalidate_publisher_cache()
    logger.debug(
        f"[WEBHOOK-TRIGGER] PUBLISHER_CREATED webhook task (restore) added to background queue for publisher_id={updated.id}"
    )

    return PublisherRead.model_validate(updated)


@router.delete("/{publisher_id}/permanent", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def permanent_delete_publisher(
    publisher_id: int,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
):
    """Permanently delete a trashed publisher from the database and MinIO storage."""
    _require_admin(credentials, db)
    # Use get_with_books to ensure books relationship is loaded for cascade delete
    publisher = _publisher_repository.get_with_books(db, publisher_id)
    if publisher is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Publisher not found",
        )

    if publisher.status != "inactive":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Publisher must be in trash before permanent deletion",
        )

    # Delete all files from MinIO storage
    settings = get_settings()
    client = get_minio_client(settings)
    publisher_prefix = f"{publisher.id}/"

    try:
        # List and delete all objects under publisher prefix
        objects_to_delete = list(
            client.list_objects(
                settings.minio_publishers_bucket,
                prefix=publisher_prefix,
                recursive=True,
            )
        )
        for obj in objects_to_delete:
            client.remove_object(settings.minio_publishers_bucket, obj.object_name)
        logger.info(f"Deleted {len(objects_to_delete)} objects from MinIO for publisher {publisher.name}")
    except Exception as e:
        logger.error(f"Error deleting MinIO objects for publisher {publisher.name}: {e}")
        # Continue with database deletion even if MinIO deletion fails

    # Permanently delete from database
    _publisher_repository.delete(db, publisher)


@router.get("/{publisher_id}/books", response_model=list[BookRead])
def get_publisher_books(
    publisher_id: int,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> list[BookRead]:
    """List all books for a specific publisher."""

    _require_admin(credentials, db)
    publisher = _publisher_repository.get_with_books(db, publisher_id)
    if publisher is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Publisher not found",
        )

    return [BookRead.model_validate(book) for book in publisher.books]


@router.get("/{publisher_id}/assets", response_model=PublisherAssetsResponse)
def list_publisher_assets(
    publisher_id: int,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> PublisherAssetsResponse:
    """List all asset types for a publisher with file counts and sizes."""

    _require_admin(credentials, db)
    publisher = _publisher_repository.get(db, publisher_id)
    if publisher is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Publisher not found",
        )

    settings = get_settings()
    client = get_minio_client(settings)
    assets_prefix = f"{publisher.id}/assets/"

    # List all folders under assets/
    asset_types: dict[str, AssetTypeInfo] = {}
    try:
        objects = client.list_objects(
            settings.minio_publishers_bucket,
            prefix=assets_prefix,
            recursive=True,
        )
        for obj in objects:
            # Extract asset type from path: {publisher}/assets/{type}/{filename}
            rel_path = obj.object_name[len(assets_prefix) :]
            if "/" in rel_path:
                asset_type = rel_path.split("/")[0]
                if asset_type not in asset_types:
                    asset_types[asset_type] = AssetTypeInfo(
                        name=asset_type,
                        file_count=0,
                        total_size=0,
                    )
                asset_types[asset_type].file_count += 1
                asset_types[asset_type].total_size += obj.size
    except Exception as e:
        logger.error(f"Error listing assets for publisher {publisher_id}: {e}")
        # Return empty list if no assets or error
        pass

    return PublisherAssetsResponse(
        publisher_id=publisher.id,
        publisher_name=publisher.name,
        asset_types=list(asset_types.values()),
    )


@router.get("/{publisher_id}/assets/{asset_type}", response_model=list[AssetFileInfo])
def list_asset_type_files(
    publisher_id: int,
    asset_type: str,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> list[AssetFileInfo]:
    """List all files in a specific asset type for a publisher."""

    _require_admin(credentials, db)
    validate_asset_type(asset_type)

    publisher = _publisher_repository.get(db, publisher_id)
    if publisher is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Publisher not found",
        )

    settings = get_settings()
    client = get_minio_client(settings)
    prefix = f"{publisher.id}/assets/{asset_type}/"

    files: list[AssetFileInfo] = []
    try:
        objects = client.list_objects(
            settings.minio_publishers_bucket,
            prefix=prefix,
            recursive=True,
        )
        for obj in objects:
            # Extract filename from full path
            filename = obj.object_name[len(prefix) :]
            files.append(
                AssetFileInfo(
                    name=filename,
                    path=obj.object_name,
                    size=obj.size,
                    content_type=obj.content_type or "application/octet-stream",
                    last_modified=obj.last_modified,
                )
            )
    except Exception as e:
        logger.error(f"Error listing files for asset type {asset_type}: {e}")
        # Return empty list if no files or error
        pass

    return files


@router.post("/{publisher_id}/assets/{asset_type}", response_model=AssetFileInfo, status_code=status.HTTP_201_CREATED)
async def upload_asset_file(
    publisher_id: int,
    asset_type: str,
    file: UploadFile = File(...),
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> AssetFileInfo:
    """Upload a file to a specific asset type for a publisher."""

    _require_admin(credentials, db)
    validate_asset_type(asset_type)

    import asyncio

    publisher = _publisher_repository.get(db, publisher_id)
    if publisher is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Publisher not found",
        )

    if not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Filename is required",
        )

    pub_id = publisher.id
    db.commit()  # Release DB connection before S3 upload

    settings = get_settings()
    client = get_minio_client(settings)
    object_key = f"{pub_id}/assets/{asset_type}/{file.filename}"

    # Read file contents
    contents = await file.read()
    file_size = len(contents)
    if file_size > settings.publisher_asset_max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds max size ({settings.publisher_asset_max_bytes // 1024 // 1024}MB)",
        )

    # Upload to S3
    try:
        await asyncio.to_thread(
            client.put_object,
            settings.minio_publishers_bucket,
            object_key,
            io.BytesIO(contents),
            file_size,
            content_type=file.content_type or "application/octet-stream",
        )
    except Exception as e:
        logger.error(f"Error uploading asset file: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to upload file",
        )

    return AssetFileInfo(
        name=file.filename,
        path=object_key,
        size=file_size,
        content_type=file.content_type or "application/octet-stream",
        last_modified=datetime.now(timezone.utc),
    )


@router.get("/{publisher_id}/logo")
def get_publisher_logo(
    publisher_id: int,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
):
    """Get the publisher's logo directly.

    Returns the first logo file found under assets/logos/.
    Returns 404 if no logo exists.
    """
    from fastapi.responses import StreamingResponse

    _require_admin(credentials, db)

    publisher = _publisher_repository.get(db, publisher_id)
    if publisher is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Publisher not found",
        )

    settings = get_settings()
    client = get_minio_client(settings)
    logo_prefix = f"{publisher.id}/assets/logos/"

    # List files under the logo prefix
    try:
        objects = list(
            client.list_objects(
                settings.minio_publishers_bucket,
                prefix=logo_prefix,
                recursive=False,
            )
        )
    except Exception as e:
        logger.error(f"Error listing logo for publisher {publisher_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Logo not found",
        )

    if not objects:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Logo not found",
        )

    # Pick the first logo file
    logo_object = objects[0]
    object_key = logo_object.object_name
    filename = object_key.split("/")[-1]

    try:
        response = client.get_object(settings.minio_publishers_bucket, object_key)
        stat = client.stat_object(settings.minio_publishers_bucket, object_key)

        return StreamingResponse(
            response,
            media_type=stat.content_type or "image/png",
            headers={
                "Content-Disposition": f'inline; filename="{filename}"',
                "Content-Length": str(stat.size),
                "Cache-Control": "public, max-age=86400, immutable",
            },
        )
    except Exception as e:
        logger.error(f"Error fetching logo for publisher {publisher_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Logo not found",
        )


@router.get("/{publisher_id}/assets/{asset_type}/{filename}")
def download_asset_file(
    publisher_id: int,
    asset_type: str,
    filename: str,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
):
    _require_admin(credentials, db)
    """Download an asset file."""
    from fastapi.responses import StreamingResponse

    validate_asset_type(asset_type)

    publisher = _publisher_repository.get(db, publisher_id)
    if publisher is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Publisher not found",
        )

    settings = get_settings()
    client = get_minio_client(settings)
    object_key = f"{publisher.id}/assets/{asset_type}/{filename}"

    try:
        response = client.get_object(settings.minio_publishers_bucket, object_key)
        stat = client.stat_object(settings.minio_publishers_bucket, object_key)

        return StreamingResponse(
            response,
            media_type=stat.content_type or "application/octet-stream",
            headers={
                "Content-Disposition": f'inline; filename="{filename}"',
                "Content-Length": str(stat.size),
            },
        )
    except Exception as e:
        logger.error(f"Error downloading asset file: {e}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found",
        )


@router.delete("/{publisher_id}/assets/{asset_type}/{filename}")
def delete_asset_file(
    publisher_id: int,
    asset_type: str,
    filename: str,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> dict:
    """Permanently delete an asset file from storage."""

    _require_admin(credentials, db)
    validate_asset_type(asset_type)

    publisher = _publisher_repository.get(db, publisher_id)
    if publisher is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Publisher not found",
        )

    settings = get_settings()
    client = get_minio_client(settings)
    object_key = f"{publisher.id}/assets/{asset_type}/{filename}"

    try:
        report = delete_prefix_directly(
            client=client,
            bucket=settings.minio_publishers_bucket,
            prefix=object_key,
        )
    except DirectDeletionError as e:
        logger.error(f"Error deleting asset file: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete file: {e}",
        )

    return {
        "message": "File permanently deleted",
        "objects_removed": report.objects_removed,
    }
