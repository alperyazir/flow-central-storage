"""Async wrapper around the synchronous minio-py S3 client.

Wraps blocking S3 calls with asyncio.to_thread() to prevent
event loop blocking in async FastAPI endpoints.
"""

from __future__ import annotations

import asyncio
from typing import Any

from minio import Minio
from minio.datatypes import Object
from minio.helpers import ObjectWriteResult


class AsyncS3Client:
    """Async wrapper for minio.Minio that runs all operations in a thread pool."""

    def __init__(self, client: Minio):
        self._client = client

    @property
    def sync_client(self) -> Minio:
        """Access the underlying sync client (for non-async contexts)."""
        return self._client

    async def list_objects(self, bucket: str, prefix: str = "", recursive: bool = False) -> list[Object]:
        """List objects — consumes the iterator in a thread to avoid blocking."""
        return await asyncio.to_thread(
            lambda: list(self._client.list_objects(bucket, prefix=prefix, recursive=recursive))
        )

    async def put_object(
        self,
        bucket: str,
        key: str,
        data: Any,
        length: int,
        content_type: str = "application/octet-stream",
        **kwargs: Any,
    ) -> ObjectWriteResult:
        return await asyncio.to_thread(
            self._client.put_object, bucket, key, data, length, content_type=content_type, **kwargs
        )

    async def get_object(self, bucket: str, key: str, **kwargs: Any) -> Any:
        return await asyncio.to_thread(self._client.get_object, bucket, key, **kwargs)

    async def stat_object(self, bucket: str, key: str) -> Any:
        return await asyncio.to_thread(self._client.stat_object, bucket, key)

    async def remove_object(self, bucket: str, key: str) -> None:
        await asyncio.to_thread(self._client.remove_object, bucket, key)

    async def copy_object(self, bucket: str, key: str, source: Any) -> Any:
        return await asyncio.to_thread(self._client.copy_object, bucket, key, source)

    async def presigned_get_object(self, bucket: str, key: str, expires: Any = None) -> str:
        return await asyncio.to_thread(self._client.presigned_get_object, bucket, key, expires=expires)

    async def fget_object(self, bucket: str, key: str, file_path: str) -> Any:
        return await asyncio.to_thread(self._client.fget_object, bucket, key, file_path)

    async def fput_object(
        self, bucket: str, key: str, file_path: str, content_type: str = "application/octet-stream"
    ) -> Any:
        return await asyncio.to_thread(self._client.fput_object, bucket, key, file_path, content_type=content_type)

    async def bucket_exists(self, bucket: str) -> bool:
        return await asyncio.to_thread(self._client.bucket_exists, bucket)

    async def make_bucket(self, bucket: str) -> None:
        await asyncio.to_thread(self._client.make_bucket, bucket)

    async def list_buckets(self) -> list[Any]:
        return await asyncio.to_thread(self._client.list_buckets)
