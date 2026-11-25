"""Metrics extension point; bridge it to Prometheus, OpenTelemetry, StatsD, ..."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Protocol


class MetricsSink(Protocol):
    """Receives one observation per HTTP attempt. Must be fast and must not raise."""

    def observe_request(
        self, *, provider: str, method: str, status: int | None, duration: float
    ) -> None:
        """Record an attempt; ``status`` is None if no response was received."""


class NullMetrics:
    """Discard all metrics."""

    def observe_request(
        self, *, provider: str, method: str, status: int | None, duration: float
    ) -> None:
        """Do nothing."""


@dataclass
class InMemoryMetrics:
    """Simple counters for tests and debugging."""

    requests: Counter[tuple[str, str, int | None]] = field(default_factory=Counter)
    total_duration: float = 0.0

    def observe_request(
        self, *, provider: str, method: str, status: int | None, duration: float
    ) -> None:
        """Count the attempt."""
        self.requests[(provider, method, status)] += 1
        self.total_duration += duration
