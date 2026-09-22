"""The Redis pool has to survive one page of the AI Processing dashboard.

That page asks for the most recent job of every book on the page at once —
twenty concurrent lookups — against a pool that held ten connections and raised
`ConnectionError: Too many connections` the moment it ran dry. Typing in the
search box fired one of those bursts per keystroke, so searches returned 500s
and the page looked broken rather than slow.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.config import Settings
from app.services.queue.redis import RedisConnection


class TestPoolConfiguration:
    def test_pool_is_wider_than_a_page_of_books(self) -> None:
        """page_size on the dashboard is 20; the pool must clear that."""
        assert Settings().redis_max_connections >= 20

    def test_a_caller_waits_for_a_connection(self) -> None:
        assert Settings().redis_pool_timeout_seconds > 0

    @pytest.mark.asyncio
    async def test_connect_uses_a_blocking_pool(self) -> None:
        """Blocking, so a burst queues instead of failing the request."""
        connection = RedisConnection(url="redis://localhost:6379")

        with patch("app.services.queue.redis.BlockingConnectionPool") as pool, patch(
            "app.services.queue.redis.Redis"
        ) as client:
            pool.from_url.return_value = MagicMock()
            client.return_value.ping = AsyncMock()

            await connection.connect()

        kwargs = pool.from_url.call_args.kwargs
        assert kwargs["max_connections"] == Settings().redis_max_connections
        assert kwargs["timeout"] == Settings().redis_pool_timeout_seconds

    @pytest.mark.asyncio
    async def test_an_explicit_size_still_wins(self) -> None:
        connection = RedisConnection(url="redis://localhost:6379", max_connections=5)

        with patch("app.services.queue.redis.BlockingConnectionPool") as pool, patch(
            "app.services.queue.redis.Redis"
        ) as client:
            pool.from_url.return_value = MagicMock()
            client.return_value.ping = AsyncMock()

            await connection.connect()

        assert pool.from_url.call_args.kwargs["max_connections"] == 5
