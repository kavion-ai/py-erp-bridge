"""Public clients."""

from __future__ import annotations

import threading
import time
from collections.abc import Coroutine, Mapping
from types import TracebackType
from typing import Any, Self, TypeVar

import httpx
from pydantic import ValidationError

from py_erp_bridge.auth.factory import build_auth
from py_erp_bridge.auth.tokens import TokenStore
from py_erp_bridge.config.settings import ProviderConfig
from py_erp_bridge.core.adapter import (
    ERPAdapter,
    FileUpload,
    HealthStatus,
    WebhookRegistration,
)
from py_erp_bridge.core.capabilities import EntitySpec, Operation, ProviderInfo
from py_erp_bridge.core.portal import LoopThread
from py_erp_bridge.core.registry import ProviderRegistry
from py_erp_bridge.core.resources import AsyncEntityResource, EntityResource
from py_erp_bridge.exceptions import ConfigurationError, UniversalERPError
from py_erp_bridge.models import (
    Customer,
    CustomRecord,
    Employee,
    ERPRecord,
    FinancialRecord,
    InventoryItem,
    Invoice,
    Payment,
    Product,
    PurchaseOrder,
    SalesOrder,
    Vendor,
)
from py_erp_bridge.observability import MetricsSink
from py_erp_bridge.providers import registry as default_registry
from py_erp_bridge.transport.http import HttpTransport, build_client
from py_erp_bridge.types import JsonObject, JsonValue

R = TypeVar("R", bound=ERPRecord)
T = TypeVar("T")

ConfigInput = Mapping[str, object] | ProviderConfig

_NO_OPERATIONS = EntitySpec(
    path="",
    operations=frozenset(),
    notes="no mapping is included; define one through custom_entities",
)

CANONICAL_MODELS: dict[str, type[ERPRecord]] = {
    "customers": Customer,
    "vendors": Vendor,
    "products": Product,
    "inventory": InventoryItem,
    "sales_orders": SalesOrder,
    "purchase_orders": PurchaseOrder,
    "invoices": Invoice,
    "payments": Payment,
    "employees": Employee,
    "financial_records": FinancialRecord,
}


def _validate_config(adapter: type[ERPAdapter], config: ConfigInput) -> ProviderConfig:
    if isinstance(config, adapter.config_model):
        return config
    if isinstance(config, ProviderConfig):
        raise ConfigurationError(
            f"{adapter.info.name} needs {adapter.config_model.__name__}, "
            f"got {type(config).__name__}"
        )
    try:
        return adapter.config_model.model_validate(dict(config))
    except ValidationError as exc:
        # Do not chain or echo the pydantic error: it contains input values, i.e. secrets.
        problems = "; ".join(
            f"{'.'.join(map(str, e['loc'])) or 'config'}: {e['msg']}" for e in exc.errors()
        )
        raise ConfigurationError(f"invalid {adapter.info.name} config: {problems}") from None


class AsyncERPClient:
    """Asynchronous client. Use as ``async with`` or call :meth:`aclose`."""

    def __init__(
        self,
        provider: str,
        config: ConfigInput,
        *,
        registry: ProviderRegistry | None = None,
        token_store: TokenStore | None = None,
        metrics: MetricsSink | None = None,
        http_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        adapter_class = (registry or default_registry).get(provider)
        settings = _validate_config(adapter_class, config)
        if settings.auth.type not in adapter_class.info.auth_types | {"custom"}:
            allowed = ", ".join(sorted(adapter_class.info.auth_types))
            raise ConfigurationError(
                f"{provider} does not support auth type '{settings.auth.type}'; use: {allowed}"
            )
        client = build_client(settings, http_transport)
        auth = build_auth(settings.auth, client, token_store)
        transport = HttpTransport(adapter_class.info.name, settings, auth, client, metrics)
        self.adapter = adapter_class(settings, transport)

    async def __aenter__(self) -> Self:
        """Enter the context."""
        return self

    async def __aexit__(
        self,
        kind: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the client."""
        await self.aclose()

    async def aclose(self) -> None:
        """Release all resources."""
        try:
            await self.adapter.aclose()
        finally:
            await self.adapter.http.aclose()

    @property
    def info(self) -> ProviderInfo:
        """Provider description and limitations."""
        return self.adapter.info

    def capabilities(self) -> dict[str, frozenset[Operation]]:
        """Supported operations per entity."""
        return {name: spec.operations for name, spec in self.adapter.entities.items()}

    def _resource(self, name: str, model: type[R]) -> AsyncEntityResource[R]:
        spec = self.adapter.entities.get(name, _NO_OPERATIONS)
        return AsyncEntityResource(self.adapter, name, spec, model)

    def entity(
        self, name: str, *, spec: EntitySpec | None = None
    ) -> AsyncEntityResource[CustomRecord]:
        """Access a custom entity, configured in ``custom_entities`` or by *spec*."""
        spec = spec or self.adapter.entities.get(name)
        if spec is None:
            raise ConfigurationError(f"unknown entity '{name}'")
        return AsyncEntityResource(self.adapter, name, spec, CustomRecord)

    @property
    def customers(self) -> AsyncEntityResource[Customer]:
        """Customers."""
        return self._resource("customers", Customer)

    @property
    def vendors(self) -> AsyncEntityResource[Vendor]:
        """Vendors."""
        return self._resource("vendors", Vendor)

    @property
    def products(self) -> AsyncEntityResource[Product]:
        """Products."""
        return self._resource("products", Product)

    @property
    def inventory(self) -> AsyncEntityResource[InventoryItem]:
        """Inventory."""
        return self._resource("inventory", InventoryItem)

    @property
    def sales_orders(self) -> AsyncEntityResource[SalesOrder]:
        """Sales orders."""
        return self._resource("sales_orders", SalesOrder)

    @property
    def purchase_orders(self) -> AsyncEntityResource[PurchaseOrder]:
        """Purchase orders."""
        return self._resource("purchase_orders", PurchaseOrder)

    @property
    def invoices(self) -> AsyncEntityResource[Invoice]:
        """Customer invoices."""
        return self._resource("invoices", Invoice)

    @property
    def payments(self) -> AsyncEntityResource[Payment]:
        """Payments."""
        return self._resource("payments", Payment)

    @property
    def employees(self) -> AsyncEntityResource[Employee]:
        """Employees."""
        return self._resource("employees", Employee)

    @property
    def financial_records(self) -> AsyncEntityResource[FinancialRecord]:
        """Financial records."""
        return self._resource("financial_records", FinancialRecord)

    async def test_connection(self) -> HealthStatus:
        """Check connectivity and credentials without raising SDK errors."""
        started = time.perf_counter()
        try:
            await self.adapter.health_check()
        except UniversalERPError as exc:
            return HealthStatus(
                healthy=False,
                provider=self.info.name,
                latency=time.perf_counter() - started,
                detail=f"{type(exc).__name__}: {exc}",
            )
        return HealthStatus(
            healthy=True, provider=self.info.name, latency=time.perf_counter() - started
        )

    async def execute(self, operation: str, **params: Any) -> object:  # noqa: ANN401
        """Run a provider-specific operation; see ``adapter.operations()``."""
        return await self.adapter.execute(operation, **params)

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str | int] | None = None,
        json: JsonValue = None,
        content: bytes | str | None = None,
        headers: Mapping[str, str] | None = None,
        idempotent: bool | None = None,
    ) -> httpx.Response:
        """Send a raw request with the client's auth and resilience."""
        return await self.adapter.http.request(
            method,
            path,
            params=params,
            json=json,
            content=content,
            headers=headers,
            idempotent=idempotent,
        )

    async def upload_file(self, upload: FileUpload) -> JsonObject:
        """Upload a document, where supported."""
        return await self.adapter.upload_file(upload)

    async def register_webhook(self, registration: WebhookRegistration) -> JsonObject:
        """Create a webhook subscription, where supported."""
        return await self.adapter.register_webhook(registration)


class ERPClient:
    """Blocking client with the same capabilities as :class:`AsyncERPClient`."""

    def __init__(
        self,
        provider: str,
        config: ConfigInput,
        *,
        registry: ProviderRegistry | None = None,
        token_store: TokenStore | None = None,
        metrics: MetricsSink | None = None,
        http_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = AsyncERPClient(
            provider,
            config,
            registry=registry,
            token_store=token_store,
            metrics=metrics,
            http_transport=http_transport,
        )
        self._loop = LoopThread()
        self._closed = False

    @staticmethod
    def create_async(
        provider: str,
        config: ConfigInput,
        *,
        registry: ProviderRegistry | None = None,
        token_store: TokenStore | None = None,
        metrics: MetricsSink | None = None,
        http_transport: httpx.AsyncBaseTransport | None = None,
    ) -> AsyncERPClient:
        """Create an asynchronous client."""
        return AsyncERPClient(
            provider,
            config,
            registry=registry,
            token_store=token_store,
            metrics=metrics,
            http_transport=http_transport,
        )

    def __enter__(self) -> Self:
        """Enter the context."""
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the client."""
        self.close()

    def close(self) -> None:
        """Release all resources. Safe to call more than once."""
        if self._closed:
            return
        self._closed = True
        try:
            self._loop.run(self._client.aclose())
        finally:
            self._loop.close()

    @property
    def adapter(self) -> ERPAdapter:
        """The provider adapter (async); run its coroutines with :meth:`run`."""
        return self._client.adapter

    @property
    def info(self) -> ProviderInfo:
        """Provider description and limitations."""
        return self._client.info

    def capabilities(self) -> dict[str, frozenset[Operation]]:
        """Supported operations per entity."""
        return self._client.capabilities()

    def run(self, coro: Coroutine[object, object, T]) -> T:
        """Run an adapter coroutine on the client's event loop."""
        return self._loop.run(coro)

    def entity(self, name: str, *, spec: EntitySpec | None = None) -> EntityResource[CustomRecord]:
        """Access a custom entity."""
        return EntityResource(self._client.entity(name, spec=spec), self._loop)

    @property
    def customers(self) -> EntityResource[Customer]:
        """Customers."""
        return EntityResource(self._client.customers, self._loop)

    @property
    def vendors(self) -> EntityResource[Vendor]:
        """Vendors."""
        return EntityResource(self._client.vendors, self._loop)

    @property
    def products(self) -> EntityResource[Product]:
        """Products."""
        return EntityResource(self._client.products, self._loop)

    @property
    def inventory(self) -> EntityResource[InventoryItem]:
        """Inventory."""
        return EntityResource(self._client.inventory, self._loop)

    @property
    def sales_orders(self) -> EntityResource[SalesOrder]:
        """Sales orders."""
        return EntityResource(self._client.sales_orders, self._loop)

    @property
    def purchase_orders(self) -> EntityResource[PurchaseOrder]:
        """Purchase orders."""
        return EntityResource(self._client.purchase_orders, self._loop)

    @property
    def invoices(self) -> EntityResource[Invoice]:
        """Customer invoices."""
        return EntityResource(self._client.invoices, self._loop)

    @property
    def payments(self) -> EntityResource[Payment]:
        """Payments."""
        return EntityResource(self._client.payments, self._loop)

    @property
    def employees(self) -> EntityResource[Employee]:
        """Employees."""
        return EntityResource(self._client.employees, self._loop)

    @property
    def financial_records(self) -> EntityResource[FinancialRecord]:
        """Financial records."""
        return EntityResource(self._client.financial_records, self._loop)

    def test_connection(self) -> HealthStatus:
        """Check connectivity and credentials without raising SDK errors."""
        return self._loop.run(self._client.test_connection())

    def execute(self, operation: str, **params: Any) -> object:  # noqa: ANN401
        """Run a provider-specific operation."""
        return self._loop.run(self._client.execute(operation, **params))

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str | int] | None = None,
        json: JsonValue = None,
        content: bytes | str | None = None,
        headers: Mapping[str, str] | None = None,
        idempotent: bool | None = None,
    ) -> httpx.Response:
        """Send a raw request with the client's auth and resilience."""
        return self._loop.run(
            self._client.request(
                method,
                path,
                params=params,
                json=json,
                content=content,
                headers=headers,
                idempotent=idempotent,
            )
        )

    def upload_file(self, upload: FileUpload) -> JsonObject:
        """Upload a document, where supported."""
        return self._loop.run(self._client.upload_file(upload))

    def register_webhook(self, registration: WebhookRegistration) -> JsonObject:
        """Create a webhook subscription, where supported."""
        return self._loop.run(self._client.register_webhook(registration))


class TenantManager:
    """Lazily creates one isolated blocking client per tenant. Thread-safe."""

    def __init__(self, tenants: Mapping[str, tuple[str, ConfigInput]]) -> None:
        self._tenants = dict(tenants)
        self._clients: dict[str, ERPClient] = {}
        self._lock = threading.Lock()

    def get(self, tenant: str) -> ERPClient:
        """Return the tenant's client."""
        with self._lock:
            if tenant not in self._clients:
                if tenant not in self._tenants:
                    raise ConfigurationError(f"unknown tenant '{tenant}'")
                provider, config = self._tenants[tenant]
                self._clients[tenant] = ERPClient(provider, config)
            return self._clients[tenant]

    def close(self) -> None:
        """Close every client."""
        with self._lock:
            clients, self._clients = self._clients, {}
        for client in clients.values():
            client.close()
