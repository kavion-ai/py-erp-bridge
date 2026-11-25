"""Client-side token bucket plus concurrency limit."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from py_erp_bridge.config.settings import RateLimitConfig


class RateLimiter:
    """Limits request rate and concurrency of one client."""

    def __init__(self, config: RateLimitConfig) -> None:
        self._rate = config.requests_per_second
        self._capacity = float(config.burst)
        self._tokens = self._capacity
        self._updated = time.monotonic()
        self._lock = asyncio.Lock()
        self._slots = asyncio.Semaphore(config.max_concurrency)

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        """Wait for permission to send a request."""
        async with self._slots:
            if self._rate is not None:
                async with self._lock:
                    now = time.monotonic()
                    self._tokens = min(
                        self._capacity, self._tokens + (now - self._updated) * self._rate
                    )
                    self._updated = now
                    if self._tokens < 1:
                        await asyncio.sleep((1 - self._tokens) / self._rate)
                        self._tokens = 1
                        self._updated = time.monotonic()
                    self._tokens -= 1
            yield
