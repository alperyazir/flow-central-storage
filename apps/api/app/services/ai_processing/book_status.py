"""Mirror AI processing status onto the Book DB row.

The authoritative live status lives in the Redis job (TTL-bound) and the
detailed per-stage status in ``ai-data/metadata.json``. This helper keeps a
small, persistent copy on the Book row so every view (book list, detail,
AI-data page) can show a reliable badge with a single cheap DB read.

All updates are best-effort: failures are logged and swallowed so a DB hiccup
can never break the worker or the enqueue path.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Status values mirrored from the worker / queue.
QUEUED = "queued"
PROCESSING = "processing"
COMPLETED = "completed"
PARTIAL = "partial"
FAILED = "failed"
CANCELLED = "cancelled"

# Statuses that represent the end of a successful (or partially successful) run.
_FINISHED_OK = {COMPLETED, PARTIAL}


def set_book_ai_status(book_id: int | str, status: str) -> None:
    """Persist ``status`` onto ``Book.ai_processing_status`` (best-effort).

    ``ai_processed_at`` is stamped when the run finishes with data
    (completed/partial).
    """
    try:
        bid = int(book_id)
    except (TypeError, ValueError):
        logger.warning("set_book_ai_status: invalid book_id %r", book_id)
        return

    from app.db import SessionLocal
    from app.models.book import Book

    try:
        with SessionLocal() as session:
            book = session.get(Book, bid)
            if book is None:
                return
            book.ai_processing_status = status
            if status in _FINISHED_OK:
                book.ai_processed_at = datetime.now(timezone.utc)
            session.commit()
    except Exception as exc:  # pragma: no cover - best effort
        logger.warning("Failed to mirror AI status %s for book %s: %s", status, bid, exc)


def clear_book_ai_status(book_id: int | str) -> None:
    """Reset the mirrored status back to "never processed" (best-effort).

    Used after the ai-data is deleted: leaving the row on ``completed`` would
    show a badge and a processed-at date for data that is no longer there.
    """
    try:
        bid = int(book_id)
    except (TypeError, ValueError):
        logger.warning("clear_book_ai_status: invalid book_id %r", book_id)
        return

    from app.db import SessionLocal
    from app.models.book import Book

    try:
        with SessionLocal() as session:
            book = session.get(Book, bid)
            if book is None:
                return
            book.ai_processing_status = None
            book.ai_processed_at = None
            session.commit()
    except Exception as exc:  # pragma: no cover - best effort
        logger.warning("Failed to clear AI status for book %s: %s", bid, exc)
