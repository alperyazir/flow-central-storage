"""Health check endpoint with service connectivity verification."""

import logging
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db import get_db

logger = logging.getLogger(__name__)

router = APIRouter()

_TIMEOUT_SECONDS = 2


def _check_db(db: Session) -> str:
    """Check database connectivity."""
    try:
        db.execute(text("SELECT 1"))
        return "ok"
    except Exception as exc:
        logger.warning("Health check: DB failed: %s", exc)
        return "error"


def _check_redis() -> str:
    """Check Redis connectivity."""
    try:
        import redis

        settings = get_settings()
        r = redis.Redis.from_url(settings.redis_url, socket_timeout=_TIMEOUT_SECONDS)
        r.ping()
        return "ok"
    except Exception as exc:
        logger.warning("Health check: Redis failed: %s", exc)
        return "error"


def _check_minio() -> str:
    """Check S3-compatible storage connectivity (works with R2, MinIO, etc.)."""
    try:
        from minio import Minio

        settings = get_settings()
        client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
        )
        # Use bucket_exists instead of list_buckets — R2 doesn't support ListBuckets
        client.bucket_exists(settings.minio_publishers_bucket)
        return "ok"
    except Exception as exc:
        logger.warning("Health check: S3 storage failed: %s", exc)
        return "error"


@router.get("", tags=["Health"])
def read_health(db: Session = Depends(get_db)) -> dict[str, Any]:
    """Return system health status including DB, Redis, and MinIO connectivity."""
    settings = get_settings()

    db_status = _check_db(db)
    redis_status = _check_redis()
    minio_status = _check_minio()

    all_ok = all(s == "ok" for s in [db_status, redis_status, minio_status])

    return {
        "status": "healthy" if all_ok else "degraded",
        "service": settings.app_name,
        "version": settings.app_version,
        "checks": {
            "db": db_status,
            "redis": redis_status,
            "minio": minio_status,
        },
    }
