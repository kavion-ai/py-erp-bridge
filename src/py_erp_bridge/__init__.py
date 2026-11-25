"""py-erp-bridge: one typed SDK for many ERP systems."""

from py_erp_bridge._version import __version__
from py_erp_bridge.client import AsyncERPClient, ERPClient, TenantManager
from py_erp_bridge.config import config_from_env
from py_erp_bridge.core.adapter import (
    ERPAdapter,
    FileUpload,
    HealthStatus,
    WebhookRegistration,
    operation,
)
from py_erp_bridge.core.bulk import BulkResult
from py_erp_bridge.core.capabilities import (
    EntitySpec,
    Evidence,
    FieldMap,
    LineMap,
    Operation,
    ProviderInfo,
)
from py_erp_bridge.core.query import Filter, FilterOp, Page, Sort
from py_erp_bridge.core.registry import ProviderRegistry
from py_erp_bridge.core.sync import IncrementalSync, InMemorySyncState, SyncStateStore
from py_erp_bridge.core.transforms import Transform, register_transform
from py_erp_bridge.exceptions import UniversalERPError
from py_erp_bridge.providers import registry

register_provider = registry.register
available_providers = registry.names

__all__ = [
    "AsyncERPClient",
    "BulkResult",
    "ERPAdapter",
    "ERPClient",
    "EntitySpec",
    "Evidence",
    "FieldMap",
    "FileUpload",
    "Filter",
    "FilterOp",
    "HealthStatus",
    "InMemorySyncState",
    "IncrementalSync",
    "LineMap",
    "Operation",
    "Page",
    "ProviderInfo",
    "ProviderRegistry",
    "Sort",
    "SyncStateStore",
    "TenantManager",
    "Transform",
    "UniversalERPError",
    "WebhookRegistration",
    "__version__",
    "available_providers",
    "config_from_env",
    "operation",
    "register_provider",
    "register_transform",
    "registry",
]
