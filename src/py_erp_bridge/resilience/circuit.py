"""Circuit breaker."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from enum import StrEnum

from py_erp_bridge.config.settings import CircuitBreakerConfig
from py_erp_bridge.exceptions import CircuitOpenError, ServerError, TransportError


class CircuitState(StrEnum):
    """Breaker states."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Opens after consecutive server or transport failures; 4xx responses do not count."""

    def __init__(
        self, config: CircuitBreakerConfig, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._config = config
        self._clock = clock
        self._failures = 0
        self._opened_at: float | None = None
        self._probing = False

    @property
    def state(self) -> CircuitState:
        """Current state."""
        if self._opened_at is None:
            return CircuitState.CLOSED
        if self._clock() - self._opened_at >= self._config.reset_timeout:
            return CircuitState.HALF_OPEN
        return CircuitState.OPEN

    @asynccontextmanager
    async def guard(self) -> AsyncIterator[None]:
        """Wrap one attempt."""
        if not self._config.enabled:
            yield
            return
        state = self.state
        if state is CircuitState.OPEN or (state is CircuitState.HALF_OPEN and self._probing):
            raise CircuitOpenError("circuit breaker is open")
        probe = state is CircuitState.HALF_OPEN
        self._probing = self._probing or probe
        try:
            yield
        except (ServerError, TransportError):
            self._failures += 1
            if probe or self._failures >= self._config.failure_threshold:
                self._opened_at = self._clock()
            raise
        except Exception:
            # The server answered; it is the request that was wrong.
            self._close()
            raise
        else:
            self._close()
        finally:
            if probe:
                self._probing = False

    def _close(self) -> None:
        self._failures = 0
        self._opened_at = None
