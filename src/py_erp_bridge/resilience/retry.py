"""Retry policy built on tenacity."""

from __future__ import annotations

import random
from collections.abc import Awaitable, Callable
from typing import TypeVar

from tenacity import AsyncRetrying, RetryCallState, retry_if_exception, stop_after_attempt

from py_erp_bridge.config.settings import RetryConfig
from py_erp_bridge.exceptions import APIError, RateLimitError, TransportError
from py_erp_bridge.observability import logger

T = TypeVar("T")


class RetryPolicy:
    """Decides what is retried and how long to wait.

    Requests that are not idempotent are retried only if the server certainly did not
    process them: the connection was never established, or it answered 429.
    """

    def __init__(self, config: RetryConfig) -> None:
        self._config = config

    def should_retry(self, exc: BaseException, *, idempotent: bool) -> bool:
        """Whether an attempt that failed with *exc* may be repeated."""
        if isinstance(exc, TransportError):
            return idempotent or not exc.request_sent
        if not isinstance(exc, APIError) or exc.status_code not in self._config.retry_statuses:
            return False
        if exc.retry_after is not None and exc.retry_after > self._config.max_retry_after:
            return False
        return idempotent or isinstance(exc, RateLimitError)

    def delay(self, attempt: int, exc: BaseException | None) -> float:
        """Seconds to wait after the given 1-based attempt."""
        if isinstance(exc, APIError) and exc.retry_after is not None:
            return exc.retry_after
        ceiling = min(self._config.max_delay, self._config.base_delay * 2 ** (attempt - 1))
        return random.uniform(0, ceiling) if self._config.jitter else ceiling  # noqa: S311

    async def run(
        self, call: Callable[[], Awaitable[T]], *, idempotent: bool, request_id: str
    ) -> T:
        """Run *call*, retrying as configured."""

        def wait(state: RetryCallState) -> float:
            exc = state.outcome.exception() if state.outcome else None
            return self.delay(state.attempt_number, exc)

        def log(state: RetryCallState) -> None:
            exc = state.outcome.exception() if state.outcome else None
            logger.warning(
                "retrying request",
                extra={
                    "request_id": request_id,
                    "attempt": state.attempt_number,
                    "error": type(exc).__name__,
                },
            )

        retrying = AsyncRetrying(
            stop=stop_after_attempt(self._config.max_attempts),
            wait=wait,
            retry=retry_if_exception(lambda e: self.should_retry(e, idempotent=idempotent)),
            before_sleep=log,
            reraise=True,
        )
        return await retrying(call)
