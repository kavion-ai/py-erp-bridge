"""Entity resources: the canonical, typed operations offered to applications."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Coroutine, Iterator, Mapping, Sequence
from typing import Generic, TypeVar

from py_erp_bridge.core.adapter import ERPAdapter
from py_erp_bridge.core.bulk import BulkItemResult, BulkResult
from py_erp_bridge.core.capabilities import EntitySpec, Operation
from py_erp_bridge.core.mapping import EntityMapper
from py_erp_bridge.core.portal import LoopThread
from py_erp_bridge.core.query import (
    FilterInput,
    Page,
    Query,
    Sort,
    parse_filters,
    parse_sort,
)
from py_erp_bridge.exceptions import (
    PartialFailureError,
    UniversalERPError,
    UnsupportedOperationError,
)
from py_erp_bridge.models import ERPRecord
from py_erp_bridge.types import JsonObject, RecordKey

R = TypeVar("R", bound=ERPRecord)
T = TypeVar("T")
RecordInput = Mapping[str, object] | ERPRecord


class AsyncEntityResource(Generic[R]):
    """Asynchronous operations on one entity."""

    def __init__(self, adapter: ERPAdapter, entity: str, spec: EntitySpec, model: type[R]) -> None:
        self._adapter = adapter
        self.entity = entity
        self.spec = spec
        self._mapper = EntityMapper(adapter.info.name, entity, spec, model)

    def supports(self, operation: Operation) -> bool:
        """Whether the operation is available."""
        return operation in self.spec.operations

    def _require(self, operation: Operation) -> None:
        if not self.supports(operation):
            hint = f" ({self.spec.notes})" if self.spec.notes else ""
            raise UnsupportedOperationError(
                f"{self._adapter.info.name} does not support {operation} on {self.entity}{hint}"
            )

    def _model(self, raw: JsonObject) -> R:
        return self._mapper.to_model(self._adapter.record_id(self.spec, raw), raw)

    def _query(
        self,
        filters: FilterInput | None,
        sort: Sequence[str | Sort] | None,
        select: Sequence[str],
        page_size: int | None,
        params: Mapping[str, str] | None,
    ) -> Query:
        return self._mapper.translate(
            Query(
                filters=parse_filters(filters),
                sort=parse_sort(sort),
                select=tuple(select),
                page_size=page_size or self._adapter.config.page_size,
                params=dict(params or {}),
            )
        )

    async def list_page(
        self,
        *,
        filters: FilterInput | None = None,
        sort: Sequence[str | Sort] | None = None,
        select: Sequence[str] = (),
        page_size: int | None = None,
        cursor: str | None = None,
        params: Mapping[str, str] | None = None,
    ) -> Page[R]:
        """Fetch one page. Continue with ``cursor=page.next_cursor`` and the same query."""
        self._require(Operation.LIST)
        query = self._query(filters, sort, select, page_size, params)
        page = await self._adapter.list_page(self.spec, query, cursor)
        return Page(
            items=[self._model(r) for r in page.records],
            next_cursor=page.next_cursor,
            total=page.total,
        )

    async def iterate(
        self,
        *,
        filters: FilterInput | None = None,
        sort: Sequence[str | Sort] | None = None,
        select: Sequence[str] = (),
        page_size: int | None = None,
        limit: int | None = None,
        params: Mapping[str, str] | None = None,
    ) -> AsyncIterator[R]:
        """Yield records across pages, up to ``limit``."""
        cursor: str | None = None
        remaining = limit
        while remaining is None or remaining > 0:
            page = await self.list_page(
                filters=filters,
                sort=sort,
                select=select,
                page_size=page_size,
                cursor=cursor,
                params=params,
            )
            for item in page.items[:remaining]:
                yield item
            if remaining is not None:
                remaining -= len(page.items)
            # A repeated cursor would loop forever.
            if page.next_cursor is None or page.next_cursor == cursor:
                return
            cursor = page.next_cursor

    async def get_all(
        self,
        *,
        filters: FilterInput | None = None,
        sort: Sequence[str | Sort] | None = None,
        select: Sequence[str] = (),
        page_size: int | None = None,
        limit: int | None = None,
        params: Mapping[str, str] | None = None,
    ) -> list[R]:
        """Collect all records in memory. Prefer ``iterate`` for large entities."""
        return [
            item
            async for item in self.iterate(
                filters=filters,
                sort=sort,
                select=select,
                page_size=page_size,
                limit=limit,
                params=params,
            )
        ]

    async def get(self, key: RecordKey) -> R:
        """Fetch one record."""
        self._require(Operation.GET)
        return self._model(await self._adapter.get(self.spec, key))

    async def create(
        self, data: RecordInput, *, idempotency_key: str | None = None, refresh: bool = False
    ) -> R:
        """Create a record.

        Some ERPs return only the new id; pass ``refresh=True`` to read the record back.
        """
        self._require(Operation.CREATE)
        payload = self._mapper.to_payload(data, creating=True)
        record = self._model(await self._adapter.create(self.spec, payload, idempotency_key))
        if refresh and record.id is not None:
            return await self.get(record.id)
        return record

    async def update(self, key: RecordKey, data: RecordInput, *, etag: str | None = None) -> R:
        """Change the given fields. Reads the record back if the ERP returns nothing."""
        self._require(Operation.UPDATE)
        payload = self._mapper.to_payload(data, creating=False)
        raw = await self._adapter.update(self.spec, key, payload, etag)
        return self._model(raw) if raw is not None else await self.get(key)

    async def delete(self, key: RecordKey, *, etag: str | None = None) -> None:
        """Delete a record."""
        self._require(Operation.DELETE)
        await self._adapter.delete(self.spec, key, etag)

    async def bulk_create(
        self,
        items: Sequence[RecordInput],
        *,
        stop_on_error: bool = False,
        raise_on_failure: bool = False,
    ) -> BulkResult:
        """Create many records, natively if the provider can.

        Without native support records are created one request each; that is not atomic.
        All inputs are validated before anything is sent.
        """
        self._require(Operation.CREATE)
        payloads = [self._mapper.to_payload(item, creating=True) for item in items]
        outcomes: list[JsonObject | Exception] | None = None
        if payloads and not stop_on_error:
            outcomes = await self._adapter.bulk_create(self.spec, payloads)
        if outcomes is not None:
            results = [self._bulk_item(i, outcome) for i, outcome in enumerate(outcomes)]
        elif stop_on_error:
            results = []
            for i, payload in enumerate(payloads):
                if results and not results[-1].ok:
                    results.append(BulkItemResult(index=i, skipped=True))
                else:
                    results.append(self._bulk_item(i, await self._create_one(payload)))
        else:
            # The transport's concurrency limit bounds this fan-out.
            created = await asyncio.gather(*(self._create_one(p) for p in payloads))
            results = [self._bulk_item(i, outcome) for i, outcome in enumerate(created)]
        result = BulkResult(items=results)
        if raise_on_failure and result.failed:
            raise PartialFailureError(
                f"{len(result.failed)} of {len(results)} items failed", result
            )
        return result

    async def _create_one(self, payload: JsonObject) -> JsonObject | Exception:
        try:
            return await self._adapter.create(self.spec, payload, None)
        except UniversalERPError as exc:
            return exc

    def _bulk_item(self, index: int, outcome: JsonObject | Exception) -> BulkItemResult:
        if isinstance(outcome, Exception):
            return BulkItemResult(index=index, error=outcome)
        return BulkItemResult(index=index, record=self._model(outcome))


class EntityResource(Generic[R]):
    """Blocking counterpart of :class:`AsyncEntityResource`; see there for semantics."""

    def __init__(self, resource: AsyncEntityResource[R], loop: LoopThread) -> None:
        self._async = resource
        self._loop = loop
        self.entity = resource.entity
        self.spec = resource.spec

    def _run(self, coro: Coroutine[object, object, T]) -> T:
        return self._loop.run(coro)

    def supports(self, operation: Operation) -> bool:
        """Whether the operation is available."""
        return self._async.supports(operation)

    def list_page(
        self,
        *,
        filters: FilterInput | None = None,
        sort: Sequence[str | Sort] | None = None,
        select: Sequence[str] = (),
        page_size: int | None = None,
        cursor: str | None = None,
        params: Mapping[str, str] | None = None,
    ) -> Page[R]:
        """Fetch one page."""
        return self._run(
            self._async.list_page(
                filters=filters,
                sort=sort,
                select=select,
                page_size=page_size,
                cursor=cursor,
                params=params,
            )
        )

    def iterate(
        self,
        *,
        filters: FilterInput | None = None,
        sort: Sequence[str | Sort] | None = None,
        select: Sequence[str] = (),
        page_size: int | None = None,
        limit: int | None = None,
        params: Mapping[str, str] | None = None,
    ) -> Iterator[R]:
        """Yield records across pages, up to ``limit``."""
        iterator = self._async.iterate(
            filters=filters,
            sort=sort,
            select=select,
            page_size=page_size,
            limit=limit,
            params=params,
        )

        async def step() -> R:
            return await anext(iterator)

        while True:
            try:
                item = self._run(step())
            except StopAsyncIteration:
                return
            yield item

    def get_all(
        self,
        *,
        filters: FilterInput | None = None,
        sort: Sequence[str | Sort] | None = None,
        select: Sequence[str] = (),
        page_size: int | None = None,
        limit: int | None = None,
        params: Mapping[str, str] | None = None,
    ) -> list[R]:
        """Collect all records in memory."""
        return self._run(
            self._async.get_all(
                filters=filters,
                sort=sort,
                select=select,
                page_size=page_size,
                limit=limit,
                params=params,
            )
        )

    def get(self, key: RecordKey) -> R:
        """Fetch one record."""
        return self._run(self._async.get(key))

    def create(
        self, data: RecordInput, *, idempotency_key: str | None = None, refresh: bool = False
    ) -> R:
        """Create a record."""
        return self._run(self._async.create(data, idempotency_key=idempotency_key, refresh=refresh))

    def update(self, key: RecordKey, data: RecordInput, *, etag: str | None = None) -> R:
        """Change the given fields."""
        return self._run(self._async.update(key, data, etag=etag))

    def delete(self, key: RecordKey, *, etag: str | None = None) -> None:
        """Delete a record."""
        self._run(self._async.delete(key, etag=etag))

    def bulk_create(
        self,
        items: Sequence[RecordInput],
        *,
        stop_on_error: bool = False,
        raise_on_failure: bool = False,
    ) -> BulkResult:
        """Create many records."""
        return self._run(
            self._async.bulk_create(
                items, stop_on_error=stop_on_error, raise_on_failure=raise_on_failure
            )
        )
