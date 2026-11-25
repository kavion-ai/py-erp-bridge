"""Shared adapter for OData providers."""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import Mapping
from typing import ClassVar
from urllib.parse import urljoin, urlsplit

import httpx

from py_erp_bridge.core.adapter import ERPAdapter, operation
from py_erp_bridge.core.capabilities import EntitySpec
from py_erp_bridge.core.query import Filter, Query, RawPage
from py_erp_bridge.exceptions import (
    APIError,
    BusinessRuleError,
    ConfigurationError,
    UniversalERPError,
)
from py_erp_bridge.transport.http import ErrorDetails
from py_erp_bridge.transport.odata import (
    BatchOperation,
    BatchResponse,
    ODataVersion,
    build_params,
    condition,
    decode_batch,
    encode_batch,
    key_predicate,
    parse_error,
    parse_page,
)
from py_erp_bridge.types import JsonObject, RecordKey, as_object


class ODataAdapter(ERPAdapter):
    """CRUD, paging and batches over OData.

    ``EntitySpec.extra`` hints: ``expand`` (navigation properties to expand) and
    ``default_sort=false`` (the service cannot sort by its keys).
    """

    version: ClassVar[ODataVersion] = ODataVersion.V4
    max_batch: ClassVar[int] = 100

    @abstractmethod
    def collection_path(self, spec: EntitySpec) -> str:
        """Path of the entity set, relative to ``base_url``."""

    @abstractmethod
    def service_path(self, spec: EntitySpec) -> str:
        """Path of the service root, relative to ``base_url``."""

    def base_params(self) -> dict[str, str]:
        """Parameters added to reads."""
        return {}

    def render_filter(self, item: Filter, version: ODataVersion) -> str:
        """Render one condition; override for dialects."""
        return condition(item, version)

    async def write(
        self,
        method: str,
        spec: EntitySpec,
        path: str,
        *,
        json: JsonObject | None = None,
        content: bytes | None = None,
        headers: Mapping[str, str] | None = None,
        idempotency_key: str | None = None,
    ) -> httpx.Response:
        """Send a modifying request; override to add e.g. CSRF protection."""
        return await self.http.request(
            method,
            path,
            json=json,
            content=content,
            headers={"Accept": "application/json", **(headers or {})},
            # A repeated PATCH with the same values is harmless; POST and $batch are not.
            idempotent=method in {"PATCH", "DELETE"},
            idempotency_key=idempotency_key,
        )

    def record_path(self, spec: EntitySpec, key: RecordKey) -> str:
        """Path of one record."""
        return self.collection_path(spec) + key_predicate(self.key_values(spec, key))

    def unwrap(self, body: JsonObject) -> JsonObject:
        """Extract the entity from a single-record response."""
        if self.version is ODataVersion.V2:
            data = as_object(body.get("d"))
            # Some gateways wrap single results too.
            return as_object(data["results"]) if isinstance(data.get("results"), dict) else data
        return body

    def _params(self, spec: EntitySpec) -> dict[str, str]:
        params = self.base_params()
        if "expand" in spec.extra:
            params["$expand"] = spec.extra["expand"]
        if self.version is ODataVersion.V2:
            params["$format"] = "json"
        return params

    async def list_page(self, spec: EntitySpec, query: Query, cursor: str | None) -> RawPage:
        """Fetch a page; follows server-driven paging, else pages by offset."""
        headers = {"Accept": "application/json"}
        size = self.page_size(query)
        skip = 0
        if cursor and cursor.startswith("link:"):
            response = await self.http.request("GET", cursor[5:], headers=headers)
        else:
            skip = int(cursor) if cursor else 0
            params = build_params(
                query,
                self.version,
                top=size,
                skip=skip,
                key_fields=spec.key_fields,
                default_sort=spec.extra.get("default_sort") != "false",
                render=self.render_filter,
            )
            response = await self.http.request(
                "GET",
                self.collection_path(spec),
                params={**self._params(spec), **params},
                headers=headers,
            )
        page, link = parse_page(self.json_object(response), self.version)
        if link:
            page.next_cursor = "link:" + self._resolve_link(str(response.request.url), link)
        elif not (cursor and cursor.startswith("link:")) and len(page.records) >= size:
            page.next_cursor = str(skip + len(page.records))
        return page

    def _resolve_link(self, current: str, link: str) -> str:
        """Resolve a link, keeping our origin: proxies often leak internal host names."""
        target = urlsplit(urljoin(current, link))
        base = urlsplit(self.config.base_url)
        return target._replace(scheme=base.scheme, netloc=base.netloc).geturl()

    async def get(self, spec: EntitySpec, key: RecordKey) -> JsonObject:
        """Fetch a record."""
        response = await self.http.request(
            "GET",
            self.record_path(spec, key),
            params=self._params(spec),
            headers={"Accept": "application/json"},
        )
        return self.unwrap(self.json_object(response))

    async def create(
        self, spec: EntitySpec, payload: JsonObject, idempotency_key: str | None
    ) -> JsonObject:
        """Create a record."""
        response = await self.write(
            "POST",
            spec,
            self.collection_path(spec),
            json=payload,
            idempotency_key=idempotency_key,
        )
        return self.unwrap(self.json_object(response))

    async def update(
        self, spec: EntitySpec, key: RecordKey, payload: JsonObject, etag: str | None
    ) -> JsonObject | None:
        """Patch a record. Without an etag the last writer wins."""
        response = await self.write(
            "PATCH",
            spec,
            self.record_path(spec, key),
            json=payload,
            headers={"If-Match": etag or "*"},
        )
        if response.status_code == 204 or not response.content:
            return None
        return self.unwrap(self.json_object(response))

    async def delete(self, spec: EntitySpec, key: RecordKey, etag: str | None) -> None:
        """Delete a record."""
        await self.write(
            "DELETE", spec, self.record_path(spec, key), headers={"If-Match": etag or "*"}
        )

    # -- batches --------------------------------------------------------------

    async def _batch(
        self, spec: EntitySpec, payloads: list[JsonObject], *, atomic: bool
    ) -> list[list[BatchResponse]]:
        root = self.service_path(spec)
        relative = self.collection_path(spec).removeprefix(root).lstrip("/")
        content_type, content = encode_batch(
            [BatchOperation(path=relative, payload=p) for p in payloads], atomic=atomic
        )
        response = await self.write(
            "POST",
            spec,
            f"{root}/$batch",
            content=content,
            headers={"Content-Type": content_type, "Accept": "multipart/mixed"},
        )
        return decode_batch(response.headers.get("Content-Type", ""), response.content)

    def _batch_error(self, response: BatchResponse) -> APIError:
        code, message = parse_error(response.body)
        return BusinessRuleError(
            f"{self.info.name}: HTTP {response.status}: {message or 'batch operation failed'}",
            provider=self.info.name,
            status_code=response.status,
            code=code,
        )

    def _outcome(self, response: BatchResponse) -> JsonObject | Exception:
        if response.status >= 300:
            return self._batch_error(response)
        return self.unwrap(response.body)

    async def bulk_create(
        self, spec: EntitySpec, payloads: list[JsonObject]
    ) -> list[JsonObject | Exception] | None:
        """Create via $batch, each record in its own changeset."""
        results: list[JsonObject | Exception] = []
        for start in range(0, len(payloads), self.max_batch):
            chunk = payloads[start : start + self.max_batch]
            try:
                groups = await self._batch(spec, chunk, atomic=False)
                if len(groups) != len(chunk) or not all(groups):
                    raise APIError("batch response count mismatch", provider=self.info.name)
                results += [self._outcome(g[0]) for g in groups]
            except UniversalERPError as exc:
                # Keep the outcome of earlier chunks; they were already committed.
                results += [exc] * len(chunk)
        return results

    @operation
    async def create_atomic(self, *, entity: str, payloads: list[JsonObject]) -> list[JsonObject]:
        """Create provider-native payloads in one changeset: all succeed or none do."""
        spec = self.entities.get(entity)
        if spec is None:
            raise ConfigurationError(f"unknown entity '{entity}'")
        if not payloads:
            return []
        groups = await self._batch(spec, payloads, atomic=True)
        responses = groups[0] if groups else []
        failed = next((r for r in responses if r.status >= 300), None)
        if failed:
            raise self._batch_error(failed)
        if len(responses) != len(payloads):
            raise APIError("batch response count mismatch", provider=self.info.name)
        return [self.unwrap(r.body) for r in responses]

    def parse_error(self, response: httpx.Response) -> ErrorDetails:
        """Extract OData errors."""
        try:
            code, message = parse_error(as_object(response.json()))
        except ValueError:
            return ErrorDetails()
        return ErrorDetails(code=code, message=message)
