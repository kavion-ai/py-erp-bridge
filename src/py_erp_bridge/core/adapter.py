"""The contract every provider adapter implements."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Mapping
from inspect import getattr_static
from typing import Any, ClassVar, TypeVar, cast

import httpx
from pydantic import BaseModel, ConfigDict

from py_erp_bridge.config.settings import ProviderConfig
from py_erp_bridge.core.capabilities import EntitySpec, FieldMap, ProviderInfo
from py_erp_bridge.core.query import Filter, Query, RawPage
from py_erp_bridge.exceptions import (
    APIError,
    ConfigurationError,
    PayloadValidationError,
    UnsupportedOperationError,
)
from py_erp_bridge.transport.http import ErrorDetails, HttpTransport
from py_erp_bridge.types import JsonObject, JsonValue, RecordKey, as_object, as_objects, text

F = TypeVar("F", bound=Callable[..., Awaitable[object]])


def operation(method: F) -> F:
    """Expose an async adapter method through ``client.execute(name, ...)``."""
    method.__erp_operation__ = True  # type: ignore[attr-defined]
    return method


class HealthStatus(BaseModel):
    """Result of a connection test."""

    healthy: bool
    provider: str
    latency: float | None = None
    detail: str = ""


class FileUpload(BaseModel):
    """A document to attach to a record."""

    model_config = ConfigDict(extra="forbid")

    filename: str
    content: bytes
    content_type: str = "application/octet-stream"
    entity: str | None = None
    record_id: str | None = None


class WebhookRegistration(BaseModel):
    """A webhook subscription to create in the ERP."""

    model_config = ConfigDict(extra="forbid")

    url: str
    entity: str
    event: str
    secret: str | None = None


class ERPAdapter(ABC):
    """Base class of adapters.

    Adapters exchange *raw* provider payloads; mapping to canonical models happens in
    the core. Adapters are async and own no retry logic: the transport does that.
    """

    info: ClassVar[ProviderInfo]
    config_model: ClassVar[type[ProviderConfig]] = ProviderConfig
    default_entities: ClassVar[Mapping[str, EntitySpec]] = {}
    max_page_size: ClassVar[int] = 1000

    def __init__(self, config: ProviderConfig, http: HttpTransport) -> None:
        self.config = config
        self.http = http
        http.error_parser = self.parse_error
        self.entities = self._build_entities()

    # -- entity configuration -------------------------------------------------

    def _build_entities(self) -> dict[str, EntitySpec]:
        entities = dict(self.default_entities)
        for name, override in self.config.entity_overrides.items():
            if name not in entities:
                raise ConfigurationError(f"cannot override unknown entity '{name}'")
            spec = entities[name]
            fields = {f.canonical: f for f in spec.fields}
            for canonical, source in override.fields.items():
                old = fields.get(canonical)
                fields[canonical] = (
                    old.model_copy(update={"source": source})
                    if old
                    else FieldMap(canonical=canonical, source=source)
                )
            entities[name] = spec.model_copy(
                update={
                    "path": override.path or spec.path,
                    "fields": tuple(fields.values()),
                    "modified_field": override.modified_field or spec.modified_field,
                    "create_defaults": {**spec.create_defaults, **override.create_defaults},
                }
            )
        entities.update(self.config.custom_entities)
        return entities

    def key_values(self, spec: EntitySpec, key: RecordKey) -> dict[str, str]:
        """Resolve a key to its parts. Composite keys are mappings or ``a/b`` strings."""
        names = spec.key_fields
        if isinstance(key, Mapping):
            if set(key) != set(names):
                raise PayloadValidationError(f"key must contain exactly: {', '.join(names)}")
            return {n: str(key[n]) for n in names}
        parts = [str(key)] if len(names) == 1 else str(key).split("/")
        if len(parts) != len(names) or not all(parts):
            raise PayloadValidationError(f"key must provide: {'/'.join(names)}")
        return dict(zip(names, parts, strict=True))

    def record_id(self, spec: EntitySpec, raw: JsonObject) -> str | None:
        """Extract the record id from a provider payload."""
        parts = [text(raw.get(name)) for name in spec.key_fields]
        return "/".join(parts) if all(parts) else None

    # -- required operations --------------------------------------------------

    @abstractmethod
    async def list_page(self, spec: EntitySpec, query: Query, cursor: str | None) -> RawPage:
        """Return one page. ``query`` uses provider field names."""

    @abstractmethod
    async def get(self, spec: EntitySpec, key: RecordKey) -> JsonObject:
        """Return one record or raise NotFoundError."""

    @abstractmethod
    async def health_check(self) -> None:
        """Raise if the connection or credentials do not work."""

    # -- optional operations --------------------------------------------------

    async def create(
        self, spec: EntitySpec, payload: JsonObject, idempotency_key: str | None
    ) -> JsonObject:
        """Create a record; the result must at least contain the key fields."""
        raise self.unsupported("create")

    async def update(
        self, spec: EntitySpec, key: RecordKey, payload: JsonObject, etag: str | None
    ) -> JsonObject | None:
        """Update a record; None means the ERP returned no representation."""
        raise self.unsupported("update")

    async def delete(self, spec: EntitySpec, key: RecordKey, etag: str | None) -> None:
        """Delete a record."""
        raise self.unsupported("delete")

    async def bulk_create(
        self, spec: EntitySpec, payloads: list[JsonObject]
    ) -> list[JsonObject | Exception] | None:
        """Create natively in bulk; None makes the core issue individual requests."""
        return None

    async def upload_file(self, upload: FileUpload) -> JsonObject:
        """Upload a document."""
        raise self.unsupported("file upload")

    async def register_webhook(self, registration: WebhookRegistration) -> JsonObject:
        """Create a webhook subscription."""
        raise self.unsupported("webhook registration")

    async def aclose(self) -> None:  # noqa: B027
        """Release adapter resources other than the HTTP transport."""

    def parse_error(self, response: httpx.Response) -> ErrorDetails:
        """Extract code and message from an error response."""
        return ErrorDetails()

    # -- helpers --------------------------------------------------------------

    def unsupported(self, what: str) -> UnsupportedOperationError:
        """Build a consistent error."""
        return UnsupportedOperationError(f"{self.info.name} does not support {what}")

    def unsupported_filter(self, item: Filter) -> UnsupportedOperationError:
        """Build the error for a filter the provider cannot express."""
        return self.unsupported(f"filter operator '{item.op}' on '{item.field}'")

    def page_size(self, query: Query) -> int:
        """Requested page size, capped at the provider maximum."""
        return min(query.page_size, self.max_page_size)

    def json_object(self, response: httpx.Response) -> JsonObject:
        """Decode a JSON object body, raising APIError for anything else."""
        try:
            data = response.json()
        except ValueError:
            data = None
        if not isinstance(data, dict):
            raise APIError(
                "response is not a JSON object",
                provider=self.info.name,
                status_code=response.status_code,
            )
        return as_object(data)

    def record_list(self, value: JsonValue) -> list[JsonObject]:
        """Require an array, so that an unexpected body is never read as "no records"."""
        if not isinstance(value, list):
            raise APIError("response contains no record list", provider=self.info.name)
        return as_objects(value)

    @classmethod
    def operations(cls) -> list[str]:
        """Names usable with ``client.execute``."""
        return sorted(n for n in dir(cls) if hasattr(getattr_static(cls, n), "__erp_operation__"))

    async def execute(self, name: str, **params: Any) -> object:  # noqa: ANN401
        """Run a provider-specific operation."""
        if name not in self.operations():
            known = ", ".join(self.operations()) or "none"
            raise UnsupportedOperationError(
                f"{self.info.name} has no operation '{name}'; available: {known}"
            )
        method = cast("Callable[..., Awaitable[object]]", getattr(self, name))
        return await method(**params)
