"""Redis connection management for the queue system."""

import logging
from typing import Optional

from redis.asyncio import BlockingConnectionPool, ConnectionPool, Redis
from redis.asyncio.cluster import RedisCluster
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError

from app.services.queue.models import QueueConnectionError

logger = logging.getLogger(__name__)


class RedisConnection:
    """Manages Redis connections with pooling support.

    Supports both standalone Redis and Redis Cluster configurations.
    """

    def __init__(
        self,
        url: str = "redis://localhost:6379",
        max_connections: int | None = None,
        cluster_mode: bool = False,
    ):
        """Initialize Redis connection manager.

        Args:
            url: Redis connection URL
            max_connections: Maximum connections in pool (settings default)
            cluster_mode: Whether to use Redis Cluster
        """
        from app.core.config import get_settings

        settings = get_settings()
        self._url = url
        self._max_connections = max_connections or settings.redis_max_connections
        self._pool_timeout = settings.redis_pool_timeout_seconds
        self._cluster_mode = cluster_mode
        self._pool: Optional[ConnectionPool] = None
        self._client: Optional[Redis | RedisCluster] = None

    async def connect(self) -> None:
        """Establish connection to Redis.

        Raises:
            QueueConnectionError: If connection fails
        """
        try:
            if self._cluster_mode:
                self._client = RedisCluster.from_url(
                    self._url,
                    decode_responses=True,
                )
            else:
                # Blocking, not the plain pool: when every connection is busy
                # the caller waits for one instead of the request dying with
                # "Too many connections". One page of the AI Processing
                # dashboard asks for the latest job of 20 books at once, so a
                # burst of them used to exhaust the pool and 500 - which is
                # what made the search box feel broken.
                self._pool = BlockingConnectionPool.from_url(
                    self._url,
                    max_connections=self._max_connections,
                    timeout=self._pool_timeout,
                    decode_responses=True,
                )
                self._client = Redis(connection_pool=self._pool)

            # Test connection
            await self._client.ping()
            logger.info("Connected to Redis at %s", self._url)
        except RedisConnectionError as e:
            logger.error("Failed to connect to Redis: %s", e)
            raise QueueConnectionError(
                f"Failed to connect to Redis: {e}",
                {"url": self._url},
            ) from e
        except RedisError as e:
            logger.error("Redis error during connection: %s", e)
            raise QueueConnectionError(
                f"Redis error: {e}",
                {"url": self._url},
            ) from e

    async def disconnect(self) -> None:
        """Close Redis connection and pool."""
        if self._client:
            await self._client.aclose()
            self._client = None
        if self._pool:
            await self._pool.disconnect()
            self._pool = None
        logger.info("Disconnected from Redis")

    async def health_check(self) -> bool:
        """Check if Redis connection is healthy.

        Returns:
            True if connection is healthy, False otherwise
        """
        if not self._client:
            return False
        try:
            await self._client.ping()
            return True
        except (RedisConnectionError, RedisError) as e:
            logger.warning("Redis health check failed: %s", e)
            return False

    @property
    def client(self) -> Redis | RedisCluster:
        """Get the Redis client instance.

        Returns:
            Redis client

        Raises:
            QueueConnectionError: If not connected
        """
        if not self._client:
            raise QueueConnectionError("Not connected to Redis")
        return self._client

    @property
    def is_connected(self) -> bool:
        """Check if client is connected."""
        return self._client is not None


# Global connection instance
_connection: Optional[RedisConnection] = None


async def get_redis_connection(
    url: str = "redis://localhost:6379",
    max_connections: int | None = None,
    cluster_mode: bool = False,
) -> RedisConnection:
    """Get or create the global Redis connection.

    Args:
        url: Redis connection URL
        max_connections: Maximum connections in pool (settings default)
        cluster_mode: Whether to use Redis Cluster

    Returns:
        RedisConnection instance
    """
    global _connection
    if _connection is None or not _connection.is_connected:
        _connection = RedisConnection(
            url=url,
            max_connections=max_connections,
            cluster_mode=cluster_mode,
        )
        await _connection.connect()
    return _connection


async def close_redis_connection() -> None:
    """Close the global Redis connection."""
    global _connection
    if _connection:
        await _connection.disconnect()
        _connection = None
