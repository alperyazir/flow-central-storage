"""CRUD endpoints for teacher metadata and materials management."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import decode_access_token, verify_api_key_from_db
from app.db import get_db
from app.repositories.material import MaterialRepository
from app.repositories.teacher import TeacherRepository
from app.repositories.user import UserRepository
from app.schemas.teacher import (
    FileTypeStats,
    MaterialListItem,
    MaterialListResponse,
    MaterialRead,
    StorageStatsResponse,
    TeacherCreate,
    TeacherListItem,
    TeacherListResponse,
    TeacherRead,
    TeacherUpdate,
)
from app.services import DirectDeletionError, delete_prefix_directly, get_minio_client

router = APIRouter(prefix="/teachers-manage", tags=["Teachers Management"])
_bearer_scheme = HTTPBearer(auto_error=True)
_teacher_repository = TeacherRepository()
_material_repository = MaterialRepository()
_user_repository = UserRepository()
logger = logging.getLogger(__name__)


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
        return -1

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid token",
    )


# =============================================================================
# Teacher CRUD Endpoints
# =============================================================================


def _invalidate_teacher_cache() -> None:
    from app.services.cache import get_cache

    try:
        get_cache().invalidate("fcs:teachers:*")
    except Exception:
        pass


@router.post("/", response_model=TeacherRead, status_code=status.HTTP_201_CREATED)
def create_teacher(
    payload: TeacherCreate,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> TeacherRead:
    """Create a new teacher record."""
    _require_admin(credentials, db)

    try:
        teacher = _teacher_repository.create(db, data=payload.model_dump())
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Teacher with ID '{payload.teacher_id}' already exists",
        )

    _invalidate_teacher_cache()
    return TeacherRead.model_validate(teacher)


@router.get("/", response_model=TeacherListResponse)
def list_teachers(
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum records to return"),
    search: str = Query(None, description="Search by teacher_id or display_name"),
    status_filter: str = Query(None, alias="status", description="Filter by status"),
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> TeacherListResponse:
    """Return active teachers with pagination and optional search."""
    _require_admin(credentials, db)

    if search or status_filter:
        teachers = _teacher_repository.search(db, query=search or "", status=status_filter, skip=skip, limit=limit)
        total = len(teachers)  # For search, count matches (simplified)
    else:
        teachers = _teacher_repository.list_active(db, skip=skip, limit=limit)
        total = _teacher_repository.count_active(db)

    teacher_ids = [t.id for t in teachers]
    bulk_stats = _material_repository.get_bulk_storage_stats(db, teacher_ids)

    items = []
    for t in teachers:
        stats = bulk_stats.get(t.id, {"total_count": 0, "total_size": 0})
        items.append(
            TeacherListItem(
                id=t.id,
                teacher_id=t.teacher_id,
                display_name=t.display_name,
                email=t.email,
                status=t.status,
                ai_auto_process_enabled=t.ai_auto_process_enabled,
                ai_processing_priority=t.ai_processing_priority,
                ai_audio_languages=t.ai_audio_languages,
                created_at=t.created_at,
                updated_at=t.updated_at,
                material_count=stats["total_count"],
                total_storage_size=stats["total_size"],
            )
        )

    return TeacherListResponse(items=items, total=total)


@router.get("/trash", response_model=TeacherListResponse)
def list_trashed_teachers(
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum records to return"),
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> TeacherListResponse:
    """Return archived/trashed teachers with pagination."""
    _require_admin(credentials, db)
    teachers = _teacher_repository.list_archived(db, skip=skip, limit=limit)
    total = _teacher_repository.count_archived(db)

    teacher_ids = [t.id for t in teachers]
    bulk_stats = _material_repository.get_bulk_storage_stats(db, teacher_ids)

    items = []
    for t in teachers:
        stats = bulk_stats.get(t.id, {"total_count": 0, "total_size": 0})
        items.append(
            TeacherListItem(
                id=t.id,
                teacher_id=t.teacher_id,
                display_name=t.display_name,
                email=t.email,
                status=t.status,
                ai_auto_process_enabled=t.ai_auto_process_enabled,
                ai_processing_priority=t.ai_processing_priority,
                ai_audio_languages=t.ai_audio_languages,
                created_at=t.created_at,
                updated_at=t.updated_at,
                material_count=stats["total_count"],
                total_storage_size=stats["total_size"],
            )
        )

    return TeacherListResponse(items=items, total=total)


@router.get("/{teacher_id}", response_model=TeacherRead)
def get_teacher(
    teacher_id: int,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> TeacherRead:
    """Retrieve a single teacher by database ID."""
    _require_admin(credentials, db)
    teacher = _teacher_repository.get(db, teacher_id)
    if teacher is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Teacher not found",
        )
    return TeacherRead.model_validate(teacher)


@router.get("/by-teacher-id/{external_id}", response_model=TeacherRead)
def get_teacher_by_external_id(
    external_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> TeacherRead:
    """Retrieve a single teacher by external teacher_id."""
    _require_admin(credentials, db)
    teacher = _teacher_repository.get_by_teacher_id(db, external_id)
    if teacher is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Teacher not found",
        )
    return TeacherRead.model_validate(teacher)


@router.put("/{teacher_id}", response_model=TeacherRead)
def update_teacher(
    teacher_id: int,
    payload: TeacherUpdate,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> TeacherRead:
    """Update metadata for an existing teacher."""
    _require_admin(credentials, db)
    teacher = _teacher_repository.get(db, teacher_id)
    if teacher is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Teacher not found",
        )

    update_data = payload.model_dump(exclude_unset=True)
    if not update_data:
        return TeacherRead.model_validate(teacher)

    updated = _teacher_repository.update(db, teacher, data=update_data)
    return TeacherRead.model_validate(updated)


@router.delete("/{teacher_id}", response_model=TeacherRead)
def soft_delete_teacher(
    teacher_id: int,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> TeacherRead:
    """Permanently delete a teacher and all their materials from storage."""
    _require_admin(credentials, db)
    teacher = _teacher_repository.get_with_materials(db, teacher_id)
    if teacher is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Teacher not found",
        )

    result = TeacherRead.model_validate(teacher)
    teacher_ext_id = teacher.teacher_id

    # Release DB before slow storage operation
    db.close()

    # Delete all files from R2
    settings = get_settings()
    client = get_minio_client(settings)
    try:
        delete_prefix_directly(
            client=client,
            bucket=settings.minio_teachers_bucket,
            prefix=f"{teacher_ext_id}/",
        )
    except DirectDeletionError as e:
        logger.error("Error deleting R2 objects for teacher %s: %s", teacher_ext_id, e)

    # Re-open DB to delete record
    from app.db import SessionLocal
    db = SessionLocal()
    try:
        teacher = _teacher_repository.get_with_materials(db, teacher_id)
        if teacher:
            _teacher_repository.delete(db, teacher)
    finally:
        db.close()

    return result


@router.post("/{teacher_id}/restore", response_model=TeacherRead)
def restore_teacher(
    teacher_id: int,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> TeacherRead:
    """Restore a trashed teacher by setting status back to active."""
    _require_admin(credentials, db)
    teacher = _teacher_repository.get(db, teacher_id)
    if teacher is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Teacher not found",
        )

    if teacher.status != "inactive":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Teacher is not in trash",
        )

    updated = _teacher_repository.update(db, teacher, data={"status": "active"})
    return TeacherRead.model_validate(updated)


@router.delete("/{teacher_id}/permanent", status_code=status.HTTP_204_NO_CONTENT)
def permanent_delete_teacher(
    teacher_id: int,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
):
    """Permanently delete a trashed teacher from the database and MinIO storage."""
    _require_admin(credentials, db)
    teacher = _teacher_repository.get_with_materials(db, teacher_id)
    if teacher is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Teacher not found",
        )

    if teacher.status != "inactive":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Teacher must be in trash before permanent deletion",
        )

    # Delete all files from MinIO storage
    settings = get_settings()
    client = get_minio_client(settings)
    teacher_prefix = f"{teacher.teacher_id}/"

    try:
        objects_to_delete = list(
            client.list_objects(
                settings.minio_teachers_bucket,
                prefix=teacher_prefix,
                recursive=True,
            )
        )
        for obj in objects_to_delete:
            client.remove_object(settings.minio_teachers_bucket, obj.object_name)
        logger.info(f"Deleted {len(objects_to_delete)} objects from MinIO for teacher {teacher.teacher_id}")
    except Exception as e:
        logger.error(f"Error deleting MinIO objects for teacher {teacher.teacher_id}: {e}")

    # Permanently delete from database
    _teacher_repository.delete(db, teacher)


# =============================================================================
# Storage Stats Endpoints
# =============================================================================


@router.get("/{teacher_id}/storage-stats", response_model=StorageStatsResponse)
def get_teacher_storage_stats(
    teacher_id: int,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> StorageStatsResponse:
    """Get storage statistics for a teacher."""
    _require_admin(credentials, db)
    teacher = _teacher_repository.get(db, teacher_id)
    if teacher is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Teacher not found",
        )

    stats = _material_repository.get_storage_stats(db, teacher_id)

    # Convert by_type dict to FileTypeStats objects
    by_type = {
        file_type: FileTypeStats(count=data["count"], size=data["size"]) for file_type, data in stats["by_type"].items()
    }

    return StorageStatsResponse(
        total_size=stats["total_size"],
        total_count=stats["total_count"],
        by_type=by_type,
        ai_processable_count=stats["ai_processable_count"],
        ai_processed_count=stats["ai_processed_count"],
    )


# =============================================================================
# Materials Endpoints
# =============================================================================


@router.get("/{teacher_id}/materials", response_model=MaterialListResponse)
def list_teacher_materials_db(
    teacher_id: int,
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum records to return"),
    file_type: str = Query(None, description="Filter by file type (pdf, txt, docx, etc.)"),
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> MaterialListResponse:
    """List all materials for a teacher from the database."""
    _require_admin(credentials, db)
    teacher = _teacher_repository.get(db, teacher_id)
    if teacher is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Teacher not found",
        )

    materials = _material_repository.list_by_teacher(
        db, teacher_id, status="active", file_type=file_type, skip=skip, limit=limit
    )
    total = _material_repository.count_by_teacher(db, teacher_id, status="active")

    items = [MaterialListItem.model_validate(m) for m in materials]
    return MaterialListResponse(items=items, total=total)


@router.get("/{teacher_id}/materials/{material_id}", response_model=MaterialRead)
def get_material(
    teacher_id: int,
    material_id: int,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> MaterialRead:
    """Get a specific material by ID."""
    _require_admin(credentials, db)
    teacher = _teacher_repository.get(db, teacher_id)
    if teacher is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Teacher not found",
        )

    material = _material_repository.get(db, material_id)
    if material is None or material.teacher_id != teacher_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Material not found",
        )

    return MaterialRead.model_validate(material)
