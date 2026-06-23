"""CRUD endpoints for book groups (Student's Book + Workbook, etc.)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import decode_access_token, verify_api_key_from_db
from app.db import get_db
from app.models.book_group import BookGroup
from app.repositories.book_group import BookGroupRepository
from app.repositories.bundle import BundleRepository
from app.repositories.publisher import PublisherRepository
from app.repositories.user import UserRepository
from app.schemas.book import BookRead
from app.schemas.book_group import (
    BookGroupAddBooks,
    BookGroupCreate,
    BookGroupListResponse,
    BookGroupRead,
    BookGroupUpdate,
    BookGroupWithBooks,
)
from app.services import delete_prefix_directly, get_minio_client
from app.services.storage import normalize_book_name

router = APIRouter(prefix="/book-groups", tags=["Book Groups"])
_bearer_scheme = HTTPBearer(auto_error=True)
_group_repository = BookGroupRepository()
_publisher_repository = PublisherRepository()
_user_repository = UserRepository()
_bundle_repository = BundleRepository()
logger = logging.getLogger(__name__)


def _delete_group_bundles(db: Session, group: BookGroup) -> None:
    """Best-effort removal of a group's bundle objects (R2) + index rows.

    The group bundle lives at ``bundles/{slug}/{normalized group name}/`` (see
    create_bundle_task). Called when a group is deleted or renamed so the old
    bundle doesn't linger. Bundling itself is manual, so we don't rebuild here.
    """
    try:
        publisher = _publisher_repository.get(db, group.publisher_id)
        if publisher is None:
            return
        settings = get_settings()
        prefix = f"bundles/{publisher.slug}/{normalize_book_name(group.name)}/"
        try:
            delete_prefix_directly(
                client=get_minio_client(settings),
                bucket=settings.minio_apps_bucket,
                prefix=prefix,
            )
        except Exception as exc:
            logger.warning("Failed to delete group bundle objects %s: %s", prefix, exc)
        _bundle_repository.delete_by_prefix(db, prefix)
        db.commit()
    except Exception as exc:
        logger.warning("Group bundle cleanup failed for group %s: %s", getattr(group, "id", "?"), exc)


def _require_admin(credentials: HTTPAuthorizationCredentials, db: Session) -> int:
    """Validate JWT token or API key and ensure authentication is valid."""
    token = credentials.credentials

    try:
        payload = decode_access_token(token, settings=get_settings())
        subject = payload.get("sub")
        if subject is not None:
            try:
                user_id = int(subject)
                if _user_repository.get(db, user_id) is not None:
                    return user_id
            except (TypeError, ValueError):
                pass
    except ValueError:
        pass

    if verify_api_key_from_db(token, db) is not None:
        return -1

    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")


def _invalidate_book_cache() -> None:
    """Book responses now carry group_id, so changing membership invalidates them."""
    from app.services.cache import get_cache

    try:
        get_cache().invalidate("fcs:books:*")
    except Exception:
        pass


def _to_read(group: BookGroup) -> BookGroupRead:
    return BookGroupRead(
        id=group.id,
        name=group.name,
        publisher_id=group.publisher_id,
        book_count=len(group.books),
        created_at=group.created_at,
        updated_at=group.updated_at,
    )


def _to_read_with_books(group: BookGroup) -> BookGroupWithBooks:
    return BookGroupWithBooks(
        id=group.id,
        name=group.name,
        publisher_id=group.publisher_id,
        book_count=len(group.books),
        created_at=group.created_at,
        updated_at=group.updated_at,
        books=[BookRead.model_validate(book) for book in group.books],
    )


@router.post("", response_model=BookGroupRead, status_code=status.HTTP_201_CREATED)
def create_book_group(
    payload: BookGroupCreate,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> BookGroupRead:
    """Create a book group under a publisher."""
    _require_admin(credentials, db)

    if _publisher_repository.get(db, payload.publisher_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Publisher with ID {payload.publisher_id} not found",
        )

    group = _group_repository.create(db, name=payload.name, publisher_id=payload.publisher_id)
    return _to_read(group)


@router.get("", response_model=BookGroupListResponse)
def list_book_groups(
    publisher_id: int | None = Query(default=None, description="Filter by publisher"),
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> BookGroupListResponse:
    """List book groups, optionally filtered by publisher."""
    _require_admin(credentials, db)
    groups = _group_repository.list_all(db, publisher_id=publisher_id)
    return BookGroupListResponse(groups=[_to_read(g) for g in groups])


@router.get("/{group_id}", response_model=BookGroupWithBooks)
def get_book_group(
    group_id: int,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> BookGroupWithBooks:
    """Get a group with its member books (used by the Learn viewer for
    auto-transition between sibling books)."""
    _require_admin(credentials, db)
    group = _group_repository.get_with_books(db, group_id)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Book group not found")
    return _to_read_with_books(group)


@router.put("/{group_id}", response_model=BookGroupRead)
def update_book_group(
    group_id: int,
    payload: BookGroupUpdate,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> BookGroupRead:
    """Rename a group."""
    _require_admin(credentials, db)
    group = _group_repository.get(db, group_id)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Book group not found")
    if payload.name is not None and payload.name != group.name:
        # The R2 bundle path is derived from the name; drop the old-name bundle
        # so it doesn't orphan. User re-triggers bundling under the new name.
        _delete_group_bundles(db, group)
        _group_repository.update(db, group, name=payload.name)
    return _to_read(group)


@router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_book_group(
    group_id: int,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> Response:
    """Delete a group; member books are detached (not deleted)."""
    _require_admin(credentials, db)
    group = _group_repository.get(db, group_id)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Book group not found")
    # Remove the group's bundle (R2 + index) before detaching members.
    _delete_group_bundles(db, group)
    _group_repository.delete(db, group)
    _invalidate_book_cache()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{group_id}/books", response_model=BookGroupWithBooks)
def add_books_to_group(
    group_id: int,
    payload: BookGroupAddBooks,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> BookGroupWithBooks:
    """Add books to a group. Books from other publishers are ignored."""
    _require_admin(credentials, db)
    group = _group_repository.get(db, group_id)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Book group not found")

    _group_repository.add_books(db, group, payload.book_ids)
    _invalidate_book_cache()

    refreshed = _group_repository.get_with_books(db, group_id)
    return _to_read_with_books(refreshed)


@router.delete("/{group_id}/books/{book_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_book_from_group(
    group_id: int,
    book_id: int,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> Response:
    """Remove a single book from a group."""
    _require_admin(credentials, db)
    if _group_repository.get(db, group_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Book group not found")
    if not _group_repository.remove_book(db, group_id, book_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Book {book_id} is not a member of group {group_id}",
        )
    _invalidate_book_cache()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
