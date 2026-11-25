"""Provider registration and discovery."""

from __future__ import annotations

from collections.abc import Callable
from importlib import import_module
from importlib.metadata import entry_points

from py_erp_bridge.core.adapter import ERPAdapter
from py_erp_bridge.exceptions import ConfigurationError, ProviderNotFoundError

ENTRY_POINT_GROUP = "py_erp_bridge.providers"
AdapterClass = type[ERPAdapter]


class ProviderRegistry:
    """Maps provider names to adapter classes, loading them lazily."""

    def __init__(self) -> None:
        self._loaders: dict[str, Callable[[], object]] = {}
        self._loaded: dict[str, AdapterClass] = {}
        self._discovered = False

    def register(self, name: str, adapter: AdapterClass | str, *, replace: bool = False) -> None:
        """Register a class or a ``"module:Class"`` reference."""
        if name in self._loaders and not replace:
            raise ConfigurationError(f"provider '{name}' is already registered")
        self._loaded.pop(name, None)
        if isinstance(adapter, str):
            module, _, attribute = adapter.partition(":")
            self._loaders[name] = lambda: getattr(import_module(module), attribute)
        else:
            self._loaders[name] = lambda: adapter

    def _discover(self) -> None:
        if self._discovered:
            return
        self._discovered = True
        for entry in entry_points(group=ENTRY_POINT_GROUP):
            # Explicit and built-in registrations win over installed plugins.
            self._loaders.setdefault(entry.name, entry.load)

    def names(self) -> list[str]:
        """All known provider names."""
        self._discover()
        return sorted(self._loaders)

    def get(self, name: str) -> AdapterClass:
        """Load an adapter class."""
        self._discover()
        if name not in self._loaded:
            if name not in self._loaders:
                known = ", ".join(self.names())
                raise ProviderNotFoundError(f"unknown provider '{name}'; known: {known}")
            adapter = self._loaders[name]()
            if not (isinstance(adapter, type) and issubclass(adapter, ERPAdapter)):
                raise ConfigurationError(f"provider '{name}' is not an ERPAdapter subclass")
            self._loaded[name] = adapter
        return self._loaded[name]
