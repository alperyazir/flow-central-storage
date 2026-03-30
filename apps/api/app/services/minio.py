"""MinIO client helpers and bootstrap utilities."""

from __future__ import annotations

import logging
from typing import Iterable
from urllib.parse import urlparse

from minio import Minio
from minio.error import S3Error

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)


def get_minio_client(settings: Settings | None = None) -> Minio:
    """Create a MinIO client using application settings (internal endpoint)."""

    config = settings or get_settings()
    return Minio(
        config.minio_endpoint,
        access_key=config.minio_access_key,
        secret_key=config.minio_secret_key,
        secure=config.minio_secure,
    )


def get_minio_client_external(settings: Settings | None = None) -> Minio:
    """Create a MinIO client using external URL for presigned URL generation.

    This client should be used when generating presigned URLs that will be
    accessed by browsers, as the URL signature includes the host.

    Note: Uses a fixed region to avoid MinIO making HTTP requests to verify
    the bucket region (which would fail from inside Docker containers).
    """
    config = settings or get_settings()

    # Parse external URL to extract endpoint and secure flag
    parsed = urlparse(config.minio_external_url)
    endpoint = parsed.netloc  # e.g., "localhost:9000"
    secure = parsed.scheme == "https"

    return Minio(
        endpoint,
        access_key=config.minio_access_key,
        secret_key=config.minio_secret_key,
        secure=secure,
        region="us-east-1",  # Fixed region to skip region lookup HTTP request
    )


def get_async_s3_client(settings: Settings | None = None):
    """Create an async S3 client wrapping the sync MinIO client."""
    from app.services.async_s3 import AsyncS3Client

    return AsyncS3Client(get_minio_client(settings))


def get_async_s3_client_external(settings: Settings | None = None):
    """Create an async S3 client using external URL (for presigned URLs)."""
    from app.services.async_s3 import AsyncS3Client

    return AsyncS3Client(get_minio_client_external(settings))


def ensure_buckets(client: Minio, bucket_names: Iterable[str]) -> None:
    """Ensure that each bucket in ``bucket_names`` exists."""

    for bucket in bucket_names:
        try:
            if client.bucket_exists(bucket):
                logger.debug("MinIO bucket '%s' already exists", bucket)
                continue
            client.make_bucket(bucket)
            logger.info("Created MinIO bucket '%s'", bucket)
        except S3Error as exc:  # pragma: no cover - specific to MinIO SDK
            logger.error("Failed to ensure bucket '%s': %s", bucket, exc)
            raise RuntimeError(f"Unable to ensure bucket '{bucket}'") from exc
        except Exception as exc:  # pragma: no cover - guard unexpected errors
            logger.error("Unexpected error ensuring bucket '%s': %s", bucket, exc)
            raise
