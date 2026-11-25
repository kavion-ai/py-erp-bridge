"""Incremental synchronization."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Generic, Protocol, TypeVar

from pydantic import BaseModel

from py_erp_bridge.core.query import Filter, FilterOp, Sort
from py_erp_bridge.core.resources import AsyncEntityResource
from py_erp_bridge.exceptions import UnsupportedOperationError
from py_erp_bridge.models import ERPRecord

R = TypeVar("R", bound=ERPRecord)


class SyncStateStore(Protocol):
    """Persists one watermark per key."""

    async def load(self, key: str) -> datetime | None:
        """Return the watermark."""

    async def save(self, key: str, watermark: datetime) -> None:
        """Store the watermark."""


class InMemorySyncState:
    """Volatile store for tests and one-shot jobs."""

    def __init__(self) -> None:
        self.values: dict[str, datetime] = {}

    async def load(self, key: str) -> datetime | None:
        """Return the watermark."""
        return self.values.get(key)

    async def save(self, key: str, watermark: datetime) -> None:
        """Store the watermark."""
        self.values[key] = watermark


class SyncReport(BaseModel):
    """Summary of a run."""

    processed: int
    watermark: datetime | None


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


class IncrementalSync(Generic[R]):
    """Delivers records changed since the last successful run.

    Delivery is *at least once*: the watermark moves only after every record was handled,
    and an overlap window re-reads recent changes to tolerate clock skew and late commits.
    Handlers must therefore be idempotent. Deletions are not detected.
    """

    def __init__(
        self,
        resource: AsyncEntityResource[R],
        store: SyncStateStore,
        *,
        key: str | None = None,
        overlap: timedelta = timedelta(minutes=5),
    ) -> None:
        if resource.spec.modified_field is None or resource.spec.field("modified_at") is None:
            raise UnsupportedOperationError(
                f"{resource.entity} has no modification timestamp; map 'modified_at' and set "
                "modified_field through entity_overrides if your ERP exposes one"
            )
        self._resource = resource
        self._store = store
        self._key = key or resource.entity
        self._overlap = overlap

    async def run(
        self, handler: Callable[[R], Awaitable[None]], *, ordered: bool = True
    ) -> SyncReport:
        """Process changes. Set ``ordered=False`` for providers that cannot sort."""
        previous = await self._store.load(self._key)
        filters = []
        if previous is not None:
            since = _aware(previous) - self._overlap
            filters.append(Filter(field="modified_at", op=FilterOp.GE, value=since))
        watermark = _aware(previous) if previous else None
        processed = 0
        sort = [Sort(field="modified_at")] if ordered else None
        async for record in self._resource.iterate(filters=filters, sort=sort):
            await handler(record)
            processed += 1
            if record.modified_at is not None:
                seen = _aware(record.modified_at)
                watermark = max(watermark, seen) if watermark else seen
        if watermark is not None and watermark != previous:
            await self._store.save(self._key, watermark)
        return SyncReport(processed=processed, watermark=watermark)
