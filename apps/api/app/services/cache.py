"""Redis-based caching service for frequently-read data."""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

import redis as sync_redis

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_sync_redis_client: sync_redis.Redis | None = None


def _get_sync_redis() -> sync_redis.Redis:
    """Get or create the sync Redis client for caching."""
    global _sync_redis_client
    if _sync_redis_client is None:
        settings = get_settings()
        _sync_redis_client = sync_redis.Redis.from_url(settings.redis_url, decode_responses=True)
    return _sync_redis_client


def cache_key(resource: str, *parts: Any) -> str:
    """Build a cache key: fcs:{resource}:{parts_hash}."""
    raw = ":".join(str(p) for p in parts if p is not None)
    if raw:
        short_hash = hashlib.md5(raw.encode()).hexdigest()[:10]
        return f"fcs:{resource}:{short_hash}"
    return f"fcs:{resource}:all"


class CacheService:
    """Sync Redis cache with TTL, JSON serialization, and pattern invalidation."""

    def __init__(self, client: sync_redis.Redis):
        self._r = client

    def get(self, key: str) -> Any | None:
        """Get cached value. Returns None on miss or error."""
        try:
            data = self._r.get(key)
            return json.loads(data) if data else None
        except Exception:
            return None

    def set(self, key: str, value: Any, ttl: int = 300) -> None:
        """Cache a value with TTL (seconds). No-op on error."""
        try:
            self._r.setex(key, ttl, json.dumps(value, default=str))
        except Exception:
            pass

    def delete(self, key: str) -> None:
        """Delete a specific cache key."""
        try:
            self._r.delete(key)
        except Exception:
            pass

    def invalidate(self, pattern: str) -> int:
        """Delete all keys matching a pattern. Returns count deleted."""
        try:
            keys = list(self._r.scan_iter(match=pattern, count=200))
            if keys:
                self._r.delete(*keys)
            return len(keys)
        except Exception:
            return 0


def get_cache() -> CacheService:
    """Get a CacheService instance."""
    return CacheService(_get_sync_redis())


# ---------------------------------------------------------------------------
# Upload progress tracking
# ---------------------------------------------------------------------------

_UPLOAD_TTL = 3600  # 1 hour


def set_upload_progress(
    job_id: str, progress: int, step: str, detail: str = "", book_id: int | None = None, error: str | None = None
) -> None:
    """Update upload progress in Redis."""
    try:
        r = _get_sync_redis()
        data = {"progress": progress, "step": step, "detail": detail, "book_id": book_id, "error": error}
        r.setex(f"fcs:upload:{job_id}", _UPLOAD_TTL, json.dumps(data, default=str))
    except Exception:
        pass


def get_upload_progress(job_id: str) -> dict | None:
    """Read upload progress from Redis."""
    try:
        r = _get_sync_redis()
        data = r.get(f"fcs:upload:{job_id}")
        return json.loads(data) if data else None
    except Exception:
        return None


def delete_upload_progress(job_id: str) -> None:
    """Clean up upload progress."""
    try:
        _get_sync_redis().delete(f"fcs:upload:{job_id}")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Deletion progress tracking
# ---------------------------------------------------------------------------

_DELETION_TTL = 3600  # 1 hour


def set_deletion_progress(
    job_id: str, progress: int, step: str, detail: str = "", error: str | None = None
) -> None:
    """Update deletion progress in Redis."""
    try:
        r = _get_sync_redis()
        data = {"progress": progress, "step": step, "detail": detail, "error": error}
        r.setex(f"fcs:deletion:{job_id}", _DELETION_TTL, json.dumps(data, default=str))
    except Exception:
        pass


def get_deletion_progress(job_id: str) -> dict | None:
    """Read deletion progress from Redis."""
    try:
        r = _get_sync_redis()
        data = r.get(f"fcs:deletion:{job_id}")
        return json.loads(data) if data else None
    except Exception:
        return None


def delete_deletion_progress(job_id: str) -> None:
    """Clean up deletion progress."""
    try:
        _get_sync_redis().delete(f"fcs:deletion:{job_id}")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Chunked upload session tracking
# ---------------------------------------------------------------------------

_CHUNKED_UPLOAD_TTL = 7200  # 2 hours


def set_chunked_session(upload_id: str, data: dict) -> None:
    """Store a chunked upload session in Redis."""
    try:
        r = _get_sync_redis()
        r.setex(f"fcs:chunked:{upload_id}", _CHUNKED_UPLOAD_TTL, json.dumps(data, default=str))
    except Exception:
        pass


def get_chunked_session(upload_id: str) -> dict | None:
    """Read a chunked upload session from Redis."""
    try:
        r = _get_sync_redis()
        data = r.get(f"fcs:chunked:{upload_id}")
        return json.loads(data) if data else None
    except Exception:
        return None


def delete_chunked_session(upload_id: str) -> None:
    """Remove a chunked upload session and its chunk set."""
    try:
        r = _get_sync_redis()
        r.delete(f"fcs:chunked:{upload_id}", f"fcs:chunked:{upload_id}:chunks")
    except Exception:
        pass


def add_received_chunk(upload_id: str, chunk_index: int) -> int:
    """Record a received chunk index. Returns the new count of received chunks."""
    try:
        r = _get_sync_redis()
        r.sadd(f"fcs:chunked:{upload_id}:chunks", chunk_index)
        r.expire(f"fcs:chunked:{upload_id}", _CHUNKED_UPLOAD_TTL)
        r.expire(f"fcs:chunked:{upload_id}:chunks", _CHUNKED_UPLOAD_TTL)
        return r.scard(f"fcs:chunked:{upload_id}:chunks")
    except Exception:
        return 0


def get_received_chunks(upload_id: str) -> set[int]:
    """Return the set of chunk indices received so far."""
    try:
        r = _get_sync_redis()
        members = r.smembers(f"fcs:chunked:{upload_id}:chunks")
        return {int(m) for m in members} if members else set()
    except Exception:
        return set()


def count_received_chunks(upload_id: str) -> int:
    """Return the number of chunks received so far."""
    try:
        r = _get_sync_redis()
        return r.scard(f"fcs:chunked:{upload_id}:chunks")
    except Exception:
        return 0
