"""Runs the async implementation for blocking callers."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Coroutine
from typing import TypeVar

T = TypeVar("T")


class LoopThread:
    """A private event loop in a daemon thread.

    This makes the blocking client usable from scripts, threads and notebooks alike, with
    a single (async) implementation. It must not be shared across ``os.fork``.
    """

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._loop.run_forever, name="py-erp-bridge", daemon=True
        )
        self._thread.start()

    def run(self, coro: Coroutine[object, object, T]) -> T:
        """Run *coro* to completion and return its result."""
        if not self._thread.is_alive():
            coro.close()
            raise RuntimeError("client is closed")
        if threading.current_thread() is self._thread:
            coro.close()
            raise RuntimeError("blocking call made from the client's own event loop")

        async def wrap() -> tuple[T | None, BaseException | None]:
            # StopAsyncIteration cannot travel through a Future, so carry errors as values.
            try:
                return await coro, None
            except StopAsyncIteration as exc:
                return None, exc

        future = asyncio.run_coroutine_threadsafe(wrap(), self._loop)
        try:
            value, error = future.result()
        except BaseException:
            future.cancel()
            raise
        if error is not None:
            raise error
        return value  # type: ignore[return-value]

    def close(self) -> None:
        """Stop the loop."""
        if not self._thread.is_alive():
            return
        self.run(self._loop.shutdown_asyncgens())
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join()
        self._loop.close()
