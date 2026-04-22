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
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import decode_access_token, verify_api_key_from_db
from app.db import SessionLocal, get_db
from app.models.book import Book, BookStatusEnum, BookTypeEnum
from app.models.publisher import Publisher
from app.models.webhook import WebhookEventType
from app.repositories.book import BookRepository
from app.repositories.publisher import PublisherRepository
from app.repositories.user import UserRepository
from app.schemas.book import BookCreate, BookRead, BookUpdate
from app.services import (
    DirectDeletionError,
    RelocationError,
    UploadError,
    delete_prefix_directly,
    get_minio_client,
    move_prefix_to_trash,
    upload_book_archive,
)
from app.services.ai_processing import trigger_auto_processing
from app.services.storage import BOOK_CACHE_DIR, _normalize_filename, _prefix_exists, _safe_pdf_filename, normalize_book_name
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


def _trigger_auto_bundles(
    book_id: int,
    publisher_id: int,
    publisher_slug: str,
    book_name: str,
    local_book_path: str | None = None,
    book_type: str = BookTypeEnum.STANDARD.value,
) -> None:
    """Enqueue bundle creation for all platforms after book upload.

    Creates a fresh event loop to run async arq enqueue from a sync thread.
    Skips entirely when ``book_type`` is not ``standard`` (e.g. PDF child
    books are download-only and never bundled).
    """
    import asyncio

    from app.services.standalone_apps import ALLOWED_PLATFORMS, template_exists

    if book_type != BookTypeEnum.STANDARD.value:
        logger.info("[AUTO-BUNDLE] Skipping non-standard book %s (type=%s)", book_name, book_type)
        if local_book_path:
            import shutil
            shutil.rmtree(local_book_path, ignore_errors=True)
        return

    settings = get_settings()
    client = get_minio_client(settings)
    apps_bucket = settings.minio_apps_bucket

    platforms_with_templates = [
        p for p in ALLOWED_PLATFORMS
        if template_exists(client, apps_bucket, p)
    ]

    if not platforms_with_templates:
        logger.info("[AUTO-BUNDLE] No templates found, skipping auto-bundle for book %s", book_name)
        # Clean up cache dir if no bundles to create
        if local_book_path:
            import shutil
            shutil.rmtree(local_book_path, ignore_errors=True)
        return

    # Write platform count so bundle tasks know when to clean up cache
    if local_book_path:
        count_file = os.path.join(local_book_path, ".platform_count")
        with open(count_file, "w") as f:
            f.write(str(len(platforms_with_templates)))

    async def _enqueue() -> None:
        from arq import create_pool
        from arq.connections import RedisSettings

        from app.services.queue import JobRepository, ProcessingJob, ProcessingJobType, get_redis_connection

        # Create job records in repository so task can update progress
        redis_conn = await get_redis_connection(url=settings.redis_url)
        repository = JobRepository(
            redis_client=redis_conn.client,
            job_ttl_seconds=settings.queue_job_ttl_seconds,
        )

        pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
        queue_name = f"{settings.queue_name}:normal"

        for platform in platforms_with_templates:
            job_id = f"auto-bundle-{book_id}-{platform}"

            # Create job record so task can track progress
            job = ProcessingJob(
                job_id=job_id,
                book_id=str(book_id),
                publisher_id=str(publisher_id),
                job_type=ProcessingJobType.BUNDLE,
                metadata={"platform": platform, "book_name": book_name, "auto": True},
            )
            try:
                await repository.create_job(job, check_duplicate=False)
            except Exception:
                # Job may exist from a previous upload, update it
                try:
                    await repository.update_job_status(job_id, ProcessingJob.Status.QUEUED if hasattr(ProcessingJob, 'Status') else "queued")
                except Exception:
                    pass

            await pool.enqueue_job(
                "create_bundle_task",
                job_id=job_id,
                platform=platform,
                book_id=book_id,
                publisher_slug=publisher_slug,
                book_name=book_name,
                force=True,
                local_book_path=local_book_path,
                _queue_name=queue_name,
            )
            logger.info("[AUTO-BUNDLE] Enqueued %s bundle for book %s (id=%d)", platform, book_name, book_id)
        await pool.close()

    try:
        loop = asyncio.new_event_loop()
        loop.run_until_complete(_enqueue())
        loop.close()
    except Exception as exc:
        logger.error("[AUTO-BUNDLE] Failed to enqueue bundles: %s", exc, exc_info=True)


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


@router.post("/sync-r2", status_code=status.HTTP_200_OK)
def sync_books_with_r2(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
):
    """Sync DB book records with R2 storage.

    - Books in R2 but not in DB → creates DB record
    - Books in DB but not in R2 → deletes DB record
    """
    _require_admin(credentials, db)

    settings = get_settings()
    client = get_minio_client(settings)
    bucket = settings.minio_publishers_bucket

    # Build slug → publisher mapping from DB
    all_publishers = db.execute(select(Publisher)).scalars().all()
    slug_to_publisher: dict[str, Publisher] = {p.slug: p for p in all_publishers}

    # Walk R2 at two levels:
    #   {slug}/books/{name}/                      → top-level
    #   {slug}/books/{parent}/additional-resources/{child}/   → nested child
    # For each found book, record its R2 location so we can reconstruct
    # parent_book_id + book_type from path alone (crucial when DB was
    # wiped and is being rebuilt from R2).
    r2_book_info: dict[tuple[int, str], dict[str, object]] = {}
    slug_for_pub: dict[int, str] = {}

    def _detect_child_type(prefix: str) -> str:
        """Return 'pdf' if the only content under prefix is raw/*.pdf, else 'standard'."""
        has_raw_pdf = False
        has_non_raw = False
        try:
            for obj in client.list_objects(bucket, prefix=prefix, recursive=True):
                if obj.is_dir:
                    continue
                rel = obj.object_name[len(prefix):]
                if rel.startswith("raw/") and rel.lower().endswith(".pdf"):
                    has_raw_pdf = True
                elif rel:
                    has_non_raw = True
        except Exception:
            pass
        if has_raw_pdf and not has_non_raw:
            return BookTypeEnum.PDF.value
        return BookTypeEnum.STANDARD.value

    try:
        for pub_obj in client.list_objects(bucket, recursive=False):
            pub_slug = pub_obj.object_name.rstrip("/")
            publisher = slug_to_publisher.get(pub_slug)
            if publisher is None:
                logger.warning("Sync: R2 folder '%s' has no matching publisher in DB, skipping", pub_slug)
                continue
            pub_id = publisher.id
            slug_for_pub[pub_id] = pub_slug
            books_prefix = f"{pub_slug}/books/"
            for book_obj in client.list_objects(bucket, prefix=books_prefix, recursive=False):
                book_name = book_obj.object_name[len(books_prefix):].rstrip("/")
                if not book_name:
                    continue
                # Top-level book record
                r2_book_info[(pub_id, book_name)] = {
                    "parent_book_name": None,
                    "book_type": BookTypeEnum.STANDARD.value,
                }
                # Walk its additional-resources/ subfolder for child books
                ar_prefix = f"{books_prefix}{book_name}/additional-resources/"
                for child_obj in client.list_objects(bucket, prefix=ar_prefix, recursive=False):
                    child_name = child_obj.object_name[len(ar_prefix):].rstrip("/")
                    if not child_name:
                        continue
                    child_prefix = f"{ar_prefix}{child_name}/"
                    r2_book_info[(pub_id, child_name)] = {
                        "parent_book_name": book_name,
                        "book_type": _detect_child_type(child_prefix),
                    }
    except Exception as exc:
        logger.error("Failed to list R2 books: %s", exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Failed to list R2 storage") from exc

    # Get all books from DB
    all_db_books = db.execute(select(Book)).scalars().all()
    db_books = {(b.publisher_id, b.book_name): b for b in all_db_books}

    created = []
    removed = []
    updated = []

    # Process top-level books first so their IDs are available when we
    # create children with parent_book_id.
    r2_sorted = sorted(
        r2_book_info.items(),
        key=lambda kv: 0 if kv[1]["parent_book_name"] is None else 1,
    )

    for (pub_id, book_name), info in r2_sorted:
        if (pub_id, book_name) in db_books:
            # Self-heal: if R2 says this book is nested under a parent or is
            # a different book_type than DB records, update the DB record
            # so sync is a one-click "make DB match R2" button.
            existing = db_books[(pub_id, book_name)]
            desired_parent_name = info["parent_book_name"]
            desired_parent_id: int | None = None
            if desired_parent_name is not None:
                parent_book = _book_repository.get_by_publisher_id_and_name(
                    db, publisher_id=pub_id, book_name=desired_parent_name
                )
                if parent_book is not None:
                    desired_parent_id = parent_book.id
            update_data: dict[str, object] = {}
            if existing.parent_book_id != desired_parent_id:
                update_data["parent_book_id"] = desired_parent_id
            if (existing.book_type or BookTypeEnum.STANDARD.value) != info["book_type"]:
                update_data["book_type"] = info["book_type"]
            if update_data:
                _book_repository.update(db, existing, data=update_data)
                updated.append({
                    "id": existing.id,
                    "publisher_id": pub_id,
                    "book_name": book_name,
                    "changes": update_data,
                })
                logger.info(
                    "Sync: updated DB record for %s/%s (id=%d) to match R2 layout: %s",
                    slug_for_pub[pub_id], book_name, existing.id, update_data,
                )
            continue
        pub_slug = slug_for_pub[pub_id]
        parent_book_name = info["parent_book_name"]
        book_type = info["book_type"]

        if parent_book_name is None:
            config_path = f"{pub_slug}/books/{book_name}/config.json"
        else:
            config_path = (
                f"{pub_slug}/books/{parent_book_name}/additional-resources/{book_name}/config.json"
            )

        book_data: dict[str, object] = {
            "publisher_id": pub_id,
            "book_name": book_name,
            "book_title": book_name,
            "language": "en",
            "status": BookStatusEnum.PUBLISHED,
            "book_type": book_type,
        }

        # Resolve parent_book_id from DB (parent must exist at this point
        # since we sorted top-level first).
        if parent_book_name is not None:
            parent_book = _book_repository.get_by_publisher_id_and_name(
                db, publisher_id=pub_id, book_name=parent_book_name
            )
            if parent_book is None:
                logger.warning(
                    "Sync: child %s/%s has parent %s that is not in DB — skipping",
                    pub_slug, book_name, parent_book_name,
                )
                continue
            book_data["parent_book_id"] = parent_book.id

        # PDF children have no config.json; skip metadata read for them
        if book_type != BookTypeEnum.PDF.value:
            try:
                response = client.get_object(bucket, config_path)
                config_data = json.loads(response.read())
                response.close()
                response.release_conn()
                if config_data.get("book_title"):
                    book_data["book_title"] = config_data["book_title"]
                if config_data.get("language"):
                    book_data["language"] = config_data["language"]
                if config_data.get("category"):
                    book_data["category"] = config_data["category"]
                book_data["activity_count"] = _count_activities(config_data)
                book_data["activity_details"] = _collect_activity_details(config_data)
                cover = config_data.get("book_cover")
                if cover:
                    book_data["book_cover"] = os.path.basename(cover)
            except Exception as exc:
                logger.warning("Sync: could not read config.json for %s/%s: %s", pub_slug, book_name, exc)

        book = _book_repository.create(db, data=book_data)
        created.append({
            "id": book.id,
            "publisher_id": pub_id,
            "book_name": book_name,
            "book_type": book_type,
            "parent_book_name": parent_book_name,
        })
        # Refresh db_books so later children can find this parent
        db_books[(pub_id, book_name)] = book
        logger.info(
            "Sync: created DB record for R2 book %s/%s (id=%d, type=%s, parent=%s)",
            pub_slug, book_name, book.id, book_type, parent_book_name,
        )

    # DB'de var, R2'de yok → sil (kontrol hem top-level hem nested lokasyonlara bakar)
    for (pub_id, book_name), book in list(db_books.items()):
        if (pub_id, book_name) not in r2_book_info:
            _book_repository.delete(db, book)
            removed.append({"id": book.id, "publisher_id": pub_id, "book_name": book_name})
            logger.info("Sync: removed orphan DB record for %s/%s (id=%d)", pub_id, book_name, book.id)

    if created or removed or updated:
        _invalidate_book_cache()

    # --- Teacher Materials Sync ---
    from app.models.material import Material

    teachers_bucket = settings.minio_teachers_bucket
    r2_materials: dict[tuple[int, str], dict] = {}  # (teacher_id, filename) → metadata
    materials_created = []
    materials_removed = []

    try:
        for teacher_obj in client.list_objects(teachers_bucket, recursive=False):
            teacher_prefix = teacher_obj.object_name.rstrip("/")
            try:
                t_id = int(teacher_prefix)
            except ValueError:
                continue
            mat_prefix = f"{t_id}/materials/"
            for mat_obj in client.list_objects(teachers_bucket, prefix=mat_prefix, recursive=False):
                filename = mat_obj.object_name[len(mat_prefix):].rstrip("/")
                if filename:
                    r2_materials[(t_id, filename)] = {
                        "size": mat_obj.size or 0,
                        "content_type": mat_obj.content_type or "application/octet-stream",
                    }
    except Exception as exc:
        logger.warning("Sync: failed to list R2 teacher materials: %s", exc)

    if r2_materials:
        all_db_materials = db.execute(select(Material)).scalars().all()
        db_materials = {(m.teacher_id, m.material_name): m for m in all_db_materials}

        for (t_id, filename), meta in r2_materials.items():
            if (t_id, filename) not in db_materials:
                ext = os.path.splitext(filename)[1].lstrip(".").lower() or "bin"
                try:
                    mat = Material(
                        teacher_id=t_id,
                        material_name=filename,
                        display_name=filename,
                        file_type=ext,
                        content_type=meta["content_type"],
                        size=meta["size"],
                        status="active",
                    )
                    db.add(mat)
                    db.commit()
                    db.refresh(mat)
                    materials_created.append({"id": mat.id, "teacher_id": t_id, "filename": filename})
                    logger.info("Sync: created material record for %s/%s", t_id, filename)
                except Exception as exc:
                    db.rollback()
                    logger.warning("Sync: failed to create material %s/%s: %s", t_id, filename, exc)

        for (t_id, filename), mat in db_materials.items():
            if (t_id, filename) not in r2_materials:
                try:
                    db.delete(mat)
                    db.commit()
                    materials_removed.append({"id": mat.id, "teacher_id": t_id, "filename": filename})
                    logger.info("Sync: removed orphan material %s/%s", t_id, filename)
                except Exception as exc:
                    db.rollback()
                    logger.warning("Sync: failed to remove material %s/%s: %s", t_id, filename, exc)

    return {
        "synced": True,
        "books": {
            "created": created,
            "updated": updated,
            "removed": removed,
            "r2_count": len(r2_book_info),
            "db_count": len(db_books),
        },
        "materials": {
            "created": materials_created,
            "removed": materials_removed,
            "r2_count": len(r2_materials),
        },
    }


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

    parent_id = data.get("parent_book_id")
    if parent_id is not None:
        parent = _book_repository.get_by_id(db, parent_id)
        if parent is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"parent_book_id {parent_id} does not reference an existing book",
            )
        if parent.parent_book_id is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Child books cannot themselves have children",
            )
        # Inherit publisher from parent to keep R2 layout consistent
        data["publisher_id"] = parent.publisher_id

    book_type_val = data.get("book_type")
    if hasattr(book_type_val, "value"):
        data["book_type"] = book_type_val.value

    book = _book_repository.create(db, data=data)

    # Trigger webhook in background
    logger.info(
        f"[WEBHOOK-TRIGGER] Scheduling BOOK_CREATED webhook for book_id={book.id}, book_name='{book.book_name}', publisher='{book.publisher}'"
    )
    background_tasks.add_task(_trigger_webhook, book.id, WebhookEventType.BOOK_CREATED)
    _invalidate_book_cache()
    logger.debug(f"[WEBHOOK-TRIGGER] BOOK_CREATED webhook task added to background queue for book_id={book.id}")

    return BookRead.model_validate(book)


def _attach_child_counts(db: Session, books: list[Book]) -> list[dict]:
    """Serialize books with an accurate ``child_count`` for each."""
    parent_ids = [b.id for b in books]
    counts = _book_repository.count_children_by_parent(db, parent_ids)
    result: list[dict] = []
    for book in books:
        payload = BookRead.model_validate(book).model_dump(mode="json")
        payload["child_count"] = counts.get(book.id, 0)
        result.append(payload)
    return result


@router.get("/", response_model=list[BookRead])
def list_books(
    publisher_id: int | None = Query(default=None, description="Filter books by publisher ID"),
    parent_book_id: int | None = Query(
        default=None, description="Filter by parent book ID (children of the given parent)"
    ),
    top_level_only: bool = Query(
        default=True,
        description="When true (default), excludes child books (parent_book_id IS NULL).",
    ),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(50, ge=1, le=500, description="Max number of records to return"),
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> list[BookRead]:
    """Return stored books with pagination, optionally filtered by publisher/parent."""

    _require_admin(credentials, db)

    from app.services.cache import cache_key, get_cache

    # If caller asks for children, ignore top_level_only.
    effective_top_level = top_level_only and parent_book_id is None

    cache = get_cache()
    ck = cache_key("books", "list", publisher_id, parent_book_id, effective_top_level, skip, limit)
    cached = cache.get(ck)
    if cached is not None:
        return cached

    if publisher_id is not None:
        books = _book_repository.list_by_publisher_id(
            db, publisher_id, skip=skip, limit=limit, top_level_only=effective_top_level
        )
    else:
        books = _book_repository.list_all_books(
            db,
            skip=skip,
            limit=limit,
            parent_book_id=parent_book_id,
            top_level_only=effective_top_level,
        )
    result = _attach_child_counts(db, books)
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
    counts = _book_repository.count_children_by_parent(db, [book.id])
    result = BookRead.model_validate(book)
    result.child_count = counts.get(book.id, 0)
    return result


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


@router.delete("/{book_id}", status_code=status.HTTP_202_ACCEPTED)
def delete_book(
    book_id: int,
    background_tasks: BackgroundTasks,
    delete_bundles: bool = Query(default=False, description="Also delete associated bundles"),
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
):
    """Permanently delete a book — removes files from R2 and DB record.

    Returns a job_id for tracking deletion progress via /books/delete-status/{job_id}.
    """
    import uuid

    from app.services.cache import set_deletion_progress

    admin_id = _require_admin(credentials, db)
    book = _book_repository.get_by_id(db, book_id)
    if book is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Book not found")

    # Capture book info and release DB connection before slow storage operation
    book_publisher_id = book.publisher_id
    book_publisher_slug = book.publisher_rel.slug
    book_name = book.book_name
    # Resolve R2 prefix while the session is still attached (nested for
    # children, flat for top-level). Deleting this prefix removes all
    # descendant objects, so children nested underneath a parent are
    # cleaned up in the parent's prefix delete — no separate child R2
    # loop needed for content.
    book_prefix = book.r2_prefix
    children = _book_repository.list_children(db, book.id)
    children_info = [(c.book_name, c.book_type) for c in children]
    book_result = BookRead.model_validate(book)
    book_result.child_count = len(children_info)
    db.close()

    job_id = str(uuid.uuid4())
    set_deletion_progress(job_id, 0, "starting", f"Deleting {book_name}...")

    def _process_deletion():
        try:
            set_deletion_progress(job_id, 10, "listing", "Listing files...")

            settings = get_settings()
            client = get_minio_client(settings)

            def on_progress(removed: int, total: int):
                pct = 10 + int((removed / max(total, 1)) * 60)  # 10-70%
                set_deletion_progress(job_id, pct, "deleting", f"{removed}/{total} files")

            # For top-level books, book_prefix recursively includes nested
            # `additional-resources/` children so their R2 content is
            # cleaned up in this single delete. For a direct child delete,
            # book_prefix is the child's nested location only.
            report = delete_prefix_directly(
                client=client,
                bucket=settings.minio_publishers_bucket,
                prefix=book_prefix,
                on_progress=on_progress,
            )

            total_removed = report.objects_removed

            # Delete bundles if requested
            if delete_bundles:
                set_deletion_progress(job_id, 75, "deleting_bundles", "Deleting bundles...")
                # Bundles live under bundles/{publisher_slug}/{book_name}/ — see
                # BUNDLE_PREFIX in standalone_apps.py and create_bundle_task in
                # queue/tasks.py. Using the slug (not the numeric publisher id)
                # is critical; the id-based path never existed in storage.
                bundle_prefix = f"bundles/{book_publisher_slug}/{book_name}/"
                try:
                    bundle_report = delete_prefix_directly(
                        client=client,
                        bucket=settings.minio_apps_bucket,
                        prefix=bundle_prefix,
                    )
                    total_removed += bundle_report.objects_removed
                    logger.info("Deleted %d bundle objects for book %s", bundle_report.objects_removed, book_name)
                except Exception as exc:
                    logger.warning("Failed to delete bundles for book %s: %s", book_name, exc)
                # Bundles for child books too (only 'standard' children have bundles)
                for child_name, child_type in children_info:
                    if child_type != BookTypeEnum.STANDARD.value:
                        continue
                    child_bundle_prefix = f"bundles/{book_publisher_slug}/{child_name}/"
                    try:
                        bundle_report = delete_prefix_directly(
                            client=client,
                            bucket=settings.minio_apps_bucket,
                            prefix=child_bundle_prefix,
                        )
                        total_removed += bundle_report.objects_removed
                    except Exception as exc:
                        logger.warning(
                            "Failed to delete bundles for child %s: %s", child_name, exc
                        )

            set_deletion_progress(job_id, 85, "database", "Removing database record...")

            # Trigger webhook BEFORE deleting DB record (webhook needs the book data)
            _trigger_webhook(book_id, WebhookEventType.BOOK_DELETED)

            # Delete DB record
            session = SessionLocal()
            try:
                db_book = _book_repository.get_by_id(session, book_id)
                if db_book is not None:
                    _book_repository.delete(session, db_book)
            finally:
                session.close()

            _invalidate_book_cache()

            set_deletion_progress(job_id, 100, "completed", f"{total_removed} files deleted")

            logger.info(
                "User %s permanently deleted book %s (%s); removed %s objects (bundles=%s)",
                admin_id, book_id, book_name, total_removed, delete_bundles,
            )

        except Exception as exc:
            logger.error("Failed to delete book %s: %s", book_id, exc, exc_info=True)
            set_deletion_progress(job_id, 0, "error", error=str(exc))

    background_tasks.add_task(_process_deletion)

    return {
        "job_id": job_id,
        "status": "accepted",
        "book": book_result.model_dump(mode="json"),
        "children": [{"book_name": n, "book_type": t} for n, t in children_info],
    }


@router.post("/{book_id}/upload", status_code=status.HTTP_201_CREATED)
async def upload_book(
    book_id: int,
    file: UploadFile,
    background_tasks: BackgroundTasks,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
):
    """Upload/replace content for an existing book.

    For ``book_type='pdf'`` books, uploads a single PDF under ``raw/``
    instead of extracting a ZIP archive. PDF books skip config.json
    validation, auto-bundling, and AI auto-processing.
    """
    import asyncio
    import os
    import tempfile

    _require_admin(credentials, db)
    book = _book_repository.get_by_id(db, book_id)
    if book is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Book not found")

    # PHASE 1: Extract needed data, release DB connection
    book_publisher_id = book.publisher_id
    book_publisher_slug = book.publisher_rel.slug
    book_name = book.book_name
    book_type = book.book_type or BookTypeEnum.STANDARD.value
    # Resolve R2 prefix while the session is still attached (nested for
    # children via parent_rel, flat for top-level).
    prefix = book.r2_prefix
    db.commit()  # Release connection before long S3 ops

    is_pdf = book_type == BookTypeEnum.PDF.value
    suffix = ".pdf" if is_pdf else ".zip"

    # Stream upload to temp file (never load entire archive into memory)
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp_path = tmp.name
        while chunk := await file.read(1024 * 1024):  # 1MB chunks
            tmp.write(chunk)

    # PHASE 2: S3 operations (no DB held)
    try:
        settings = get_settings()
        client = get_minio_client(settings)

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

        if is_pdf:
            upload_name = file.filename or "original.pdf"
            if not upload_name.lower().endswith(".pdf"):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="PDF books require a .pdf upload",
                )
            safe_name = _safe_pdf_filename(upload_name)
            object_name = f"{prefix}raw/{safe_name}"
            try:
                file_size = os.path.getsize(tmp_path)
                await asyncio.to_thread(
                    client.fput_object,
                    settings.minio_publishers_bucket,
                    object_name,
                    tmp_path,
                    content_type="application/pdf",
                )
            except Exception as exc:
                logger.error("Failed to upload PDF for book %s: %s", book_id, exc)
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="Failed to upload PDF",
                ) from exc

            # Update total_size on the book record (best-effort)
            try:
                session = SessionLocal()
                try:
                    db_book = _book_repository.get_by_id(session, book_id)
                    if db_book is not None:
                        _book_repository.update(
                            session, db_book, data={"total_size": file_size}
                        )
                finally:
                    session.close()
            except Exception:
                logger.warning("Failed to update total_size for pdf book %s", book_id, exc_info=True)

            background_tasks.add_task(_trigger_webhook, book_id, WebhookEventType.BOOK_UPDATED)
            _invalidate_book_cache()

            return {
                "book_id": book_id,
                "files": [{"object_name": object_name, "size": file_size}],
                "book_type": book_type,
            }

        # Standard ZIP flow
        try:
            _bn = book_name
            manifest = await asyncio.to_thread(
                lambda: upload_book_archive(
                    client=client,
                    archive_path=tmp_path,
                    bucket=settings.minio_publishers_bucket,
                    object_prefix=prefix,
                    content_type="application/octet-stream",
                    strip_root_folder=True,
                    book_name=_bn,
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
            publisher_id=book_publisher_id,
            publisher_slug=book_publisher_slug,
            book_name=book_name,
            force=True,
        )

        # Trigger auto-bundles for standard books so the generated
        # standalone app ZIPs stay in sync with the new content.
        background_tasks.add_task(
            _trigger_auto_bundles,
            book_id=book_id,
            publisher_id=book_publisher_id,
            publisher_slug=book_publisher_slug,
            book_name=book_name,
            book_type=book_type,
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


@router.get("/delete-status/{job_id}")
def get_delete_status(
    job_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
):
    """Get deletion progress for an async delete job."""
    _require_admin(credentials, db)

    from app.services.cache import get_deletion_progress

    progress = get_deletion_progress(job_id)
    if progress is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Delete job not found")
    return progress


def _run_target_book_processing(
    job_id: str,
    tmp_path: str,
    target_book_id: int,
    book_type: str,
    upload_filename: str,
    cleanup_dir: str | None = None,
    auto_bundle: bool = True,
) -> None:
    """Background task: push a reassembled file into an existing Book.

    Used by the chunked upload flow when the caller already created the
    Book record (e.g. child books with a ``parent_book_id``). The target
    book's ``book_type`` controls whether we extract a ZIP or store a
    raw PDF.
    """
    import shutil

    from app.services.cache import set_upload_progress

    session = None
    try:
        session = SessionLocal()
        book = _book_repository.get_by_id(session, target_book_id)
        if book is None:
            set_upload_progress(job_id, 0, "error", error=f"Target book {target_book_id} not found")
            return
        book_publisher_id = book.publisher_id
        book_publisher_slug = book.publisher_rel.slug
        book_name = book.book_name
        # Capture the nested-aware R2 prefix while the session still holds
        # publisher_rel / parent_rel.
        prefix = book.r2_prefix
        session.close()
        session = None

        settings = get_settings()
        client = get_minio_client(settings)

        # Clear existing objects so the replacement is clean
        set_upload_progress(job_id, 45, "clearing", "Clearing existing files...")
        try:
            existing = list(
                client.list_objects(settings.minio_publishers_bucket, prefix=prefix, recursive=True)
            )
            for obj in existing:
                client.remove_object(settings.minio_publishers_bucket, obj.object_name)
        except Exception as exc:
            logger.error("[UPLOAD:%s] Failed to clear existing files: %s", job_id, exc)
            set_upload_progress(job_id, 0, "error", error="Failed to clear existing files")
            return

        if book_type == BookTypeEnum.PDF.value:
            safe_name = _safe_pdf_filename(upload_filename)
            object_name = f"{prefix}raw/{safe_name}"
            set_upload_progress(job_id, 60, "uploading", "Uploading PDF...")
            try:
                client.fput_object(
                    settings.minio_publishers_bucket,
                    object_name,
                    tmp_path,
                    content_type="application/pdf",
                )
                file_size = os.path.getsize(tmp_path)
            except Exception as exc:
                logger.error("[UPLOAD:%s] PDF upload failed: %s", job_id, exc)
                set_upload_progress(job_id, 0, "error", error="Failed to upload PDF")
                return

            # Best-effort size update on the book record
            try:
                session = SessionLocal()
                db_book = _book_repository.get_by_id(session, target_book_id)
                if db_book is not None:
                    _book_repository.update(session, db_book, data={"total_size": file_size})
            except Exception:
                logger.warning("[UPLOAD:%s] Failed updating total_size", job_id, exc_info=True)
            finally:
                if session is not None:
                    session.close()
                    session = None

            _invalidate_book_cache()
            _trigger_webhook(target_book_id, WebhookEventType.BOOK_UPDATED)
            set_upload_progress(
                job_id,
                100,
                "completed",
                f"PDF uploaded ({file_size // 1024 // 1024}MB)",
                book_id=target_book_id,
            )
            return

        # Standard ZIP path
        set_upload_progress(job_id, 60, "uploading", "Uploading book assets...")
        try:
            manifest = upload_book_archive(
                client=client,
                archive_path=tmp_path,
                bucket=settings.minio_publishers_bucket,
                object_prefix=prefix,
                content_type="application/octet-stream",
                strip_root_folder=True,
                book_name=book_name,
            )
        except UploadError as exc:
            set_upload_progress(job_id, 0, "error", error=str(exc))
            return
        except Exception as exc:
            logger.error("[UPLOAD:%s] Archive upload failed: %s", job_id, exc)
            set_upload_progress(job_id, 0, "error", error="Failed to upload archive")
            return

        _invalidate_book_cache()
        _trigger_webhook(target_book_id, WebhookEventType.BOOK_UPDATED)

        if auto_bundle:
            _trigger_auto_bundles(
                book_id=target_book_id,
                publisher_id=book_publisher_id,
                publisher_slug=book_publisher_slug,
                book_name=book_name,
                book_type=book_type,
            )

        set_upload_progress(
            job_id,
            100,
            "completed",
            f"{len(manifest)} files uploaded",
            book_id=target_book_id,
        )
    except Exception as exc:
        logger.error("[UPLOAD:%s] Unexpected failure: %s", job_id, exc, exc_info=True)
        try:
            set_upload_progress(job_id, 0, "error", error=str(exc))
        except Exception:
            pass
    finally:
        if session is not None:
            session.close()
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        except Exception:
            pass
        if cleanup_dir:
            shutil.rmtree(cleanup_dir, ignore_errors=True)


def _run_book_processing(
    job_id: str,
    tmp_path: str,
    book_name_from_zip: str,
    override: bool,
    override_pub_id: int | None,
    override_pub_name: str | None,
    cleanup_dir: str | None = None,
    auto_bundle: bool = True,
) -> None:
    """Background task: process a ZIP archive into MinIO + DB.

    Used by both the regular async upload and the chunked upload endpoints.
    ``cleanup_dir`` is an optional temp directory to remove after processing
    (used by chunked uploads to clean up the chunk staging area).
    """
    import shutil
    import time as _time

    from app.services.cache import set_upload_progress

    try:
        logger.info("[UPLOAD:%s] Phase 2 started — processing temp file %s", job_id, tmp_path)
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
            if override_pub_id is not None:
                book_data["publisher"] = override_pub_name
                pub = _publisher_repository.get(session, override_pub_id)
                pub_id = override_pub_id
                pub_slug = pub.slug if pub else str(override_pub_id)
            else:
                publisher_name = book_data.get("publisher", "")
                pub = _publisher_repository.get_or_create_by_name(session, publisher_name)
                pub_id = pub.id
                pub_slug = pub.slug
                session.commit()

            settings = get_settings()
            client = get_minio_client(settings)
            object_prefix = f"{pub_slug}/books/{book_data['book_name']}/"

            # Check conflict
            prefix_exists = _prefix_exists(client, settings.minio_publishers_bucket, object_prefix)
            if prefix_exists and not override:
                set_upload_progress(
                    job_id, 0, "error", error=f"Book '{book_data['book_name']}' already exists. Use override=true."
                )
                return

            if prefix_exists and override:
                set_upload_progress(job_id, 52, "clearing", "Removing existing files...")
                from minio.deleteobjects import DeleteObject
                delete_list = [
                    DeleteObject(obj.object_name)
                    for obj in client.list_objects(settings.minio_publishers_bucket, prefix=object_prefix, recursive=True)
                ]
                if delete_list:
                    errors = list(client.remove_objects(settings.minio_publishers_bucket, delete_list))
                    if errors:
                        logger.warning("[UPLOAD:%s] Some objects failed to delete: %s", job_id, errors)

            logger.info("[UPLOAD:%s] Starting S3 upload — prefix: %s", job_id, object_prefix)
            _upload_start = _time.time()
            set_upload_progress(job_id, 55, "uploading", "Uploading files to storage...")

            # Upload with progress callback
            def on_file_progress(uploaded: int, total: int):
                pct = 55 + int((uploaded / max(total, 1)) * 40)  # 55-95%
                set_upload_progress(job_id, pct, "uploading", f"{uploaded}/{total} files")

            # Create local cache dir for bundle task to use instead of re-downloading from R2
            book_cache_dir = BOOK_CACHE_DIR / f"{pub_slug}_{book_data['book_name']}"
            book_cache_dir.mkdir(parents=True, exist_ok=True)

            manifest = upload_book_archive(
                client=client,
                archive_path=tmp_path,
                bucket=settings.minio_publishers_bucket,
                object_prefix=object_prefix,
                content_type="application/octet-stream",
                strip_root_folder=True,
                on_progress=on_file_progress,
                book_name=book_data["book_name"],
                local_cache_dir=str(book_cache_dir),
            )

            _upload_elapsed = _time.time() - _upload_start
            logger.info("[UPLOAD:%s] S3 upload complete — %d files in %.1fs", job_id, len(manifest), _upload_elapsed)
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

            logger.info("[UPLOAD:%s] DB record saved — book_id=%d", job_id, book.id)
            set_upload_progress(job_id, 100, "completed", f"{len(manifest)} files uploaded", book_id=book.id)

            # Auto-create bundles for all platforms (before webhook to avoid timeout blocking)
            if auto_bundle:
                _trigger_auto_bundles(book.id, pub_id, pub_slug, book_data["book_name"], local_book_path=str(book_cache_dir))
            else:
                import shutil
                shutil.rmtree(book_cache_dir, ignore_errors=True)
                logger.info("[UPLOAD:%s] Auto-bundle disabled, skipping", job_id)

            # Trigger webhook for book creation/update
            event_type = WebhookEventType.BOOK_UPDATED if existing_book else WebhookEventType.BOOK_CREATED
            logger.info(f"[UPLOAD:{job_id}] Triggering {event_type.value} webhook for book_id={book.id}")
            _trigger_webhook(book.id, event_type)

        finally:
            session.close()

    except Exception as exc:
        logger.error("[UPLOAD:%s] FAILED: %s", job_id, exc, exc_info=True)
        from app.services.cache import set_upload_progress as _set_progress

        _set_progress(job_id, 0, "error", error=str(exc))
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        if cleanup_dir:
            shutil.rmtree(cleanup_dir, ignore_errors=True)


@router.post("/upload-async", status_code=status.HTTP_202_ACCEPTED)
async def upload_new_book_async(
    file: UploadFile,
    background_tasks: BackgroundTasks,
    publisher_id: int | None = Query(default=None, description="Override publisher from config.json"),
    override: bool = Query(default=False, description="If true, replace existing book"),
    auto_bundle: bool = Query(default=True, description="Auto-create bundles after upload"),
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
):
    """Upload a book asynchronously with progress tracking.

    Returns a job_id immediately. Poll /books/upload-status/{job_id} for progress.
    """
    import tempfile
    import uuid

    from app.services.cache import set_upload_progress

    _require_admin(credentials, db)

    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File must be a ZIP archive")

    job_id = str(uuid.uuid4())
    book_name_from_zip = normalize_book_name(file.filename[:-4])

    set_upload_progress(job_id, 0, "receiving", "Uploading file to server...")

    # Stream to temp file
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        tmp_path = tmp.name
        total_bytes = 0
        while chunk := await file.read(1024 * 1024):
            tmp.write(chunk)
            total_bytes += len(chunk)

    set_upload_progress(job_id, 40, "received", f"File received ({total_bytes // 1024 // 1024}MB)")

    # Extract needed DB data into plain values before releasing connection
    override_pub_id: int | None = None
    override_pub_name: str | None = None
    if publisher_id is not None:
        resolved_publisher = _publisher_repository.get(db, publisher_id)
        if resolved_publisher is None:
            os.unlink(tmp_path)
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=f"Publisher with ID {publisher_id} not found"
            )
        override_pub_id = resolved_publisher.id
        override_pub_name = resolved_publisher.name
    db.commit()

    background_tasks.add_task(
        _run_book_processing,
        job_id=job_id,
        tmp_path=tmp_path,
        book_name_from_zip=book_name_from_zip,
        override=override,
        override_pub_id=override_pub_id,
        override_pub_name=override_pub_name,
        auto_bundle=auto_bundle,
    )

    return {"job_id": job_id, "status": "accepted"}


# ---------------------------------------------------------------------------
# Chunked upload endpoints (bypass Cloudflare 100MB limit)
# ---------------------------------------------------------------------------

_MAX_CHUNK_SIZE = 50 * 1024 * 1024  # 50 MB
_MAX_TOTAL_SIZE = 5 * 1024 * 1024 * 1024  # 5 GB


class ChunkedUploadInit(BaseModel):
    filename: str
    total_size: int = Field(..., gt=0, le=_MAX_TOTAL_SIZE)
    chunk_size: int = Field(..., gt=0, le=_MAX_CHUNK_SIZE)
    total_chunks: int = Field(..., gt=0)
    publisher_id: int | None = None
    override: bool = False
    auto_bundle: bool = True
    # For child-book uploads: target an existing Book record (created
    # up-front via POST /books/ with parent_book_id + book_type). When
    # set, the session uses the target book's publisher/slug/name and
    # `book_type` drives the processing path (ZIP vs raw PDF).
    target_book_id: int | None = None


@router.post("/chunked-upload/init")
async def chunked_upload_init(
    body: ChunkedUploadInit,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
):
    """Start a chunked upload session. Returns an upload_id."""
    import math
    import tempfile
    import uuid

    from app.services.cache import set_chunked_session

    _require_admin(credentials, db)

    expected_chunks = math.ceil(body.total_size / body.chunk_size)
    if body.total_chunks != expected_chunks:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"total_chunks must be {expected_chunks} for the given size/chunk_size",
        )

    target_book_id: int | None = None
    target_book_type: str | None = None
    if body.target_book_id is not None:
        target = _book_repository.get_by_id(db, body.target_book_id)
        if target is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                detail=f"target_book_id {body.target_book_id} not found",
            )
        target_book_id = target.id
        target_book_type = target.book_type or BookTypeEnum.STANDARD.value

    # Filename suffix check depends on target book type (if any)
    name_lower = body.filename.lower()
    if target_book_type == BookTypeEnum.PDF.value:
        if not name_lower.endswith(".pdf"):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, detail="PDF child books require a .pdf upload"
            )
    else:
        if not name_lower.endswith(".zip"):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, detail="File must be a ZIP archive"
            )

    # Resolve publisher early so we can fail fast
    override_pub_id: int | None = None
    override_pub_name: str | None = None
    if body.publisher_id is not None:
        resolved = _publisher_repository.get(db, body.publisher_id)
        if resolved is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Publisher {body.publisher_id} not found")
        override_pub_id = resolved.id
        override_pub_name = resolved.name

    upload_id = str(uuid.uuid4())
    temp_dir = tempfile.mkdtemp(prefix=f"chunked_{upload_id[:8]}_")

    session_data = {
        "filename": body.filename,
        "total_size": body.total_size,
        "chunk_size": body.chunk_size,
        "total_chunks": body.total_chunks,
        "temp_dir": temp_dir,
        "override": body.override,
        "override_pub_id": override_pub_id,
        "override_pub_name": override_pub_name,
        "auto_bundle": body.auto_bundle,
        "status": "uploading",
        "target_book_id": target_book_id,
        "target_book_type": target_book_type,
    }
    set_chunked_session(upload_id, session_data)

    return {"upload_id": upload_id}


@router.post("/chunked-upload/{upload_id}/chunk")
async def chunked_upload_chunk(
    upload_id: str,
    chunk_index: int = Query(..., ge=0),
    chunk: UploadFile = ...,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
):
    """Upload a single chunk. Idempotent — re-uploading the same index overwrites it."""
    from app.services.cache import add_received_chunk, get_chunked_session

    _require_admin(credentials, db)

    session_data = get_chunked_session(upload_id)
    if session_data is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Upload session not found or expired")

    if session_data.get("status") != "uploading":
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Upload session is no longer accepting chunks")

    total_chunks = session_data["total_chunks"]
    if chunk_index >= total_chunks:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"chunk_index must be < {total_chunks}")

    temp_dir = session_data["temp_dir"]
    chunk_path = os.path.join(temp_dir, f"chunk_{chunk_index:06d}")

    # Stream chunk to disk in 1MB pieces
    written = 0
    with open(chunk_path, "wb") as f:
        while data := await chunk.read(1024 * 1024):
            f.write(data)
            written += len(data)

    # Validate chunk size
    chunk_size = session_data["chunk_size"]
    is_last = chunk_index == total_chunks - 1
    if is_last:
        expected = session_data["total_size"] - chunk_size * (total_chunks - 1)
    else:
        expected = chunk_size

    if written != expected:
        os.unlink(chunk_path)
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"Chunk {chunk_index} size mismatch: got {written}, expected {expected}",
        )

    received = add_received_chunk(upload_id, chunk_index)
    return {"chunk_index": chunk_index, "received": received, "total_chunks": total_chunks}


@router.get("/chunked-upload/{upload_id}/status")
async def chunked_upload_status(
    upload_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
):
    """Check which chunks have been received (useful for resume)."""
    from app.services.cache import get_chunked_session, get_received_chunks

    _require_admin(credentials, db)

    session_data = get_chunked_session(upload_id)
    if session_data is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Upload session not found or expired")

    received = get_received_chunks(upload_id)
    return {
        "upload_id": upload_id,
        "status": session_data.get("status", "uploading"),
        "received_chunks": sorted(received),
        "total_chunks": session_data["total_chunks"],
    }


@router.post("/chunked-upload/{upload_id}/complete", status_code=status.HTTP_202_ACCEPTED)
async def chunked_upload_complete(
    upload_id: str,
    background_tasks: BackgroundTasks,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
):
    """Reassemble chunks and trigger processing. Returns a job_id for progress polling."""
    import uuid
    import zipfile

    from app.services.cache import (
        count_received_chunks,
        delete_chunked_session,
        get_chunked_session,
        set_chunked_session,
        set_upload_progress,
    )

    _require_admin(credentials, db)

    session_data = get_chunked_session(upload_id)
    if session_data is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Upload session not found or expired")

    if session_data.get("status") != "uploading":
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Upload session is not in uploading state")

    total_chunks = session_data["total_chunks"]
    received = count_received_chunks(upload_id)
    if received < total_chunks:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"Missing chunks: received {received}/{total_chunks}",
        )

    # Mark session as assembling so no more chunks are accepted
    session_data["status"] = "assembling"
    set_chunked_session(upload_id, session_data)

    temp_dir = session_data["temp_dir"]
    target_book_id = session_data.get("target_book_id")
    target_book_type = session_data.get("target_book_type") or BookTypeEnum.STANDARD.value
    is_target_pdf = target_book_id is not None and target_book_type == BookTypeEnum.PDF.value

    final_name = "final.pdf" if is_target_pdf else "final.zip"
    final_path = os.path.join(temp_dir, final_name)
    job_id = str(uuid.uuid4())

    set_upload_progress(job_id, 5, "assembling", "Reassembling chunks...")

    # Reassemble chunks into a single file
    with open(final_path, "wb") as out:
        for i in range(total_chunks):
            chunk_path = os.path.join(temp_dir, f"chunk_{i:06d}")
            with open(chunk_path, "rb") as inp:
                while block := inp.read(1024 * 1024):
                    out.write(block)
            os.unlink(chunk_path)

    # Validate archive format (skip ZIP validation for PDF uploads)
    if not is_target_pdf and not zipfile.is_zipfile(final_path):
        import shutil

        shutil.rmtree(temp_dir, ignore_errors=True)
        delete_chunked_session(upload_id)
        set_upload_progress(job_id, 0, "error", error="Reassembled file is not a valid ZIP archive")
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Reassembled file is not a valid ZIP archive")

    set_upload_progress(job_id, 40, "received", f"File assembled ({session_data['total_size'] // 1024 // 1024}MB)")

    # Clean up Redis session (temp dir cleaned by processor)
    delete_chunked_session(upload_id)

    if target_book_id is not None:
        background_tasks.add_task(
            _run_target_book_processing,
            job_id=job_id,
            tmp_path=final_path,
            target_book_id=target_book_id,
            book_type=target_book_type,
            upload_filename=session_data["filename"],
            cleanup_dir=temp_dir,
            auto_bundle=session_data.get("auto_bundle", True),
        )
    else:
        book_name_from_zip = session_data["filename"]
        if book_name_from_zip.lower().endswith(".zip"):
            book_name_from_zip = book_name_from_zip[:-4]
        book_name_from_zip = normalize_book_name(book_name_from_zip)
        background_tasks.add_task(
            _run_book_processing,
            job_id=job_id,
            tmp_path=final_path,
            book_name_from_zip=book_name_from_zip,
            override=session_data.get("override", False),
            override_pub_id=session_data.get("override_pub_id"),
            override_pub_name=session_data.get("override_pub_name"),
            cleanup_dir=temp_dir,
            auto_bundle=session_data.get("auto_bundle", True),
        )

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

    book_name_from_zip = normalize_book_name(zip_filename[:-4])

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
        publisher_slug = resolved_publisher.slug
        db.commit()  # Release DB connection back to PGBouncer pool before long S3 ops

        # PHASE 2: S3 operations (may take minutes — no DB connection held)
        settings = get_settings()
        client = get_minio_client(settings)
        object_prefix = f"{publisher_slug}/books/{book_data['book_name']}/"

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
                # Delete all objects at this prefix (batch)
                from minio.deleteobjects import DeleteObject
                delete_list = await asyncio.to_thread(
                    lambda: [
                        DeleteObject(obj.object_name)
                        for obj in client.list_objects(settings.minio_publishers_bucket, prefix=object_prefix, recursive=True)
                    ]
                )
                if delete_list:
                    errors = await asyncio.to_thread(
                        lambda: list(client.remove_objects(settings.minio_publishers_bucket, delete_list))
                    )
                    if errors:
                        logger.warning("Some objects failed to delete: %s", errors)

                logger.info(
                    "Deleted %d existing objects for book %s/%s",
                    len(delete_list),
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
            _bn2 = book_data["book_name"]
            manifest = await asyncio.to_thread(
                lambda: upload_book_archive(
                    client=client,
                    archive_path=tmp_path,
                    bucket=settings.minio_publishers_bucket,
                    object_prefix=object_prefix,
                    content_type="application/octet-stream",
                    strip_root_folder=True,
                    book_name=_bn2,
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
            publisher_slug=resolved_publisher.slug,
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

            book_name_from_zip = normalize_book_name(zip_filename[:-4])
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

            # Delete existing book if override is true (batch)
            if prefix_exists and override:
                try:
                    from minio.deleteobjects import DeleteObject
                    delete_list = [
                        DeleteObject(obj.object_name)
                        for obj in client.list_objects(settings.minio_publishers_bucket, prefix=object_prefix, recursive=True)
                    ]
                    if delete_list:
                        errors = list(client.remove_objects(settings.minio_publishers_bucket, delete_list))
                        if errors:
                            logger.warning("Some objects failed to delete: %s", errors)

                    logger.info(
                        "Deleted %d existing objects for book %s/%s",
                        len(delete_list),
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
                    book_name=book_data["book_name"],
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
                publisher_slug=publisher.slug,
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
                book_cover_filename = _normalize_filename(os.path.basename(book_cover_path))

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


# ---------------------------------------------------------------------------
# Book download (ZIP)
# ---------------------------------------------------------------------------


_download_temp_files: dict[str, tuple[str, str]] = {}  # job_id → (tmp_path, book_name)


def _run_book_download(job_id: str, book_prefix: str, book_name: str) -> None:
    """Background task: zip book assets from S3 into a temp file.

    ``book_prefix`` is the nested-aware R2 prefix for the book (caller
    computes it via ``book.r2_prefix``). ``book_name`` is used as the
    top-level folder inside the generated ZIP.
    """
    import tempfile

    from app.services.cache import set_upload_progress
    from app.services.standalone_apps import should_skip_bundled_path

    try:
        settings = get_settings()
        client = get_minio_client(settings)
        bucket = settings.minio_publishers_bucket
        prefix = book_prefix

        set_upload_progress(job_id, 10, "listing", "Listing book files...")

        # Skip the same folders we exclude from bundles: AI artifacts,
        # raw PDFs, additional-resources. The downloaded ZIP should
        # contain only the flowbook content itself.
        objects = [
            obj for obj in client.list_objects(bucket, prefix=prefix, recursive=True)
            if not obj.is_dir and not should_skip_bundled_path(obj.object_name[len(prefix):])
        ]
        total = len(objects)

        if total == 0:
            set_upload_progress(job_id, 0, "error", error="No files found for this book")
            return

        set_upload_progress(job_id, 20, "zipping", f"Zipping {total} files...")

        tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
        tmp_path = tmp.name
        tmp.close()

        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for idx, obj in enumerate(objects):
                relative = obj.object_name[len(prefix):]
                response = client.get_object(bucket, obj.object_name)
                zf.writestr(f"{book_name}/{relative}", response.read())
                response.close()
                response.release_conn()
                pct = 20 + int((idx + 1) / total * 70)
                set_upload_progress(job_id, pct, "zipping", f"{idx + 1}/{total} files")

        _download_temp_files[job_id] = (tmp_path, book_name)

        zip_size = os.path.getsize(tmp_path)
        set_upload_progress(
            job_id, 100, "completed",
            detail=f"{book_name}.zip ({zip_size // 1024 // 1024}MB)",
        )

    except Exception as exc:
        logger.error("[DOWNLOAD:%s] FAILED: %s", job_id, exc, exc_info=True)
        set_upload_progress(job_id, 0, "error", error=str(exc))


@router.get("/{book_id}/pdf-url")
def get_pdf_download_url(
    book_id: int,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
):
    """Return a presigned URL for a PDF book's raw file.

    Only valid for books where ``book_type == 'pdf'``. Returns 400 for
    standard books (use ``POST /books/{id}/download`` instead).
    """
    from datetime import timedelta

    from app.services import get_minio_client_external

    _require_admin(credentials, db)
    book = _book_repository.get_by_id(db, book_id)
    if book is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Book not found")
    if (book.book_type or BookTypeEnum.STANDARD.value) != BookTypeEnum.PDF.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Book is not a PDF book; use the standard download endpoint",
        )

    settings = get_settings()
    internal_client = get_minio_client(settings)
    bucket = settings.minio_publishers_bucket
    # PDF lives under the book's R2 prefix (nested for children) in raw/
    prefix = f"{book.r2_prefix}raw/"
    objects = [
        obj for obj in internal_client.list_objects(bucket, prefix=prefix, recursive=True)
        if not obj.is_dir
    ]
    if not objects:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="PDF not uploaded yet")

    object_name = objects[0].object_name
    external = get_minio_client_external(settings)
    expires = timedelta(hours=6)
    try:
        url = external.presigned_get_object(bucket, object_name, expires=expires)
    except Exception as exc:
        logger.error("Failed to presign PDF url for book %s: %s", book_id, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="Could not generate download URL"
        ) from exc

    filename = object_name.rsplit("/", 1)[-1]
    return {"download_url": url, "filename": filename, "expires_in_seconds": int(expires.total_seconds())}


@router.post("/{book_id}/download", status_code=status.HTTP_202_ACCEPTED)
def download_book(
    book_id: int,
    background_tasks: BackgroundTasks,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
):
    """Start async ZIP creation for downloading a book's assets."""
    _require_admin(credentials, db)

    book = _book_repository.get_by_id(db, book_id)
    if book is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Book not found")
    if (book.book_type or BookTypeEnum.STANDARD.value) == BookTypeEnum.PDF.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="PDF books do not support ZIP download; use /books/{id}/pdf-url",
        )

    import uuid

    job_id = str(uuid.uuid4())

    from app.services.cache import set_upload_progress

    set_upload_progress(job_id, 0, "queued", detail="Download queued...")

    background_tasks.add_task(
        _run_book_download,
        job_id=job_id,
        book_prefix=book.r2_prefix,
        book_name=book.book_name,
    )

    return {"job_id": job_id, "status": "queued"}


@router.get("/download-status/{job_id}")
def get_download_status(
    job_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
):
    """Get download job status."""
    _require_admin(credentials, db)

    from app.services.cache import get_upload_progress

    progress = get_upload_progress(job_id)
    if progress is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    return {
        "job_id": job_id,
        "progress": progress.get("progress", 0),
        "step": progress.get("step", "unknown"),
        "detail": progress.get("detail", ""),
        "error": progress.get("error"),
        "ready": progress.get("step") == "completed",
    }


@router.get("/download-file/{job_id}")
def download_file(
    job_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
):
    """Stream the completed ZIP file and clean up temp file after."""
    _require_admin(credentials, db)

    entry = _download_temp_files.get(job_id)
    if entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Download not ready or expired")

    tmp_path, book_name = entry

    if not os.path.exists(tmp_path):
        _download_temp_files.pop(job_id, None)
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="File expired")

    from fastapi.responses import FileResponse

    from app.services.cache import delete_upload_progress

    def cleanup() -> None:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        _download_temp_files.pop(job_id, None)
        delete_upload_progress(job_id)

    # Use background task for cleanup after response is sent
    from starlette.background import BackgroundTask

    return FileResponse(
        path=tmp_path,
        filename=f"{book_name}.zip",
        media_type="application/zip",
        background=BackgroundTask(cleanup),
    )
