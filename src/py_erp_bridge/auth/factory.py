"""Builds strategies from configuration."""

from __future__ import annotations

from collections.abc import Callable

import httpx

from py_erp_bridge.auth.base import AuthStrategy
from py_erp_bridge.auth.oauth2 import (
    AuthorizationCodeStrategy,
    ClientCredentialsStrategy,
    JwtAssertionStrategy,
    PasswordStrategy,
)
from py_erp_bridge.auth.static import (
    ApiKeyStrategy,
    BasicStrategy,
    BearerStrategy,
    NoAuthStrategy,
    OAuth1Strategy,
)
from py_erp_bridge.auth.tokens import TokenStore
from py_erp_bridge.config.auth import (
    ApiKeyAuth,
    AuthConfig,
    BasicAuth,
    BearerAuth,
    CustomAuth,
    JwtAssertionAuth,
    NoAuth,
    OAuth1Auth,
    OAuth2AuthorizationCode,
    OAuth2ClientCredentials,
)
from py_erp_bridge.exceptions import ConfigurationError
from py_erp_bridge.types import JsonObject

AuthPlugin = Callable[[JsonObject, httpx.AsyncClient], AuthStrategy]
_PLUGINS: dict[str, AuthPlugin] = {}


def register_auth(name: str, plugin: AuthPlugin) -> None:
    """Register a factory used by ``{"type": "custom", "plugin": name}``."""
    if name in _PLUGINS:
        raise ConfigurationError(f"auth plugin '{name}' already exists")
    _PLUGINS[name] = plugin


def build_auth(
    config: AuthConfig, client: httpx.AsyncClient, store: TokenStore | None = None
) -> AuthStrategy:
    """Create the strategy for a configuration."""
    if isinstance(config, NoAuth):
        return NoAuthStrategy()
    if isinstance(config, BasicAuth):
        return BasicStrategy(config)
    if isinstance(config, BearerAuth):
        return BearerStrategy(config)
    if isinstance(config, ApiKeyAuth):
        return ApiKeyStrategy(config)
    if isinstance(config, OAuth1Auth):
        return OAuth1Strategy(config)
    if isinstance(config, CustomAuth):
        if config.plugin not in _PLUGINS:
            raise ConfigurationError(f"unknown auth plugin '{config.plugin}'")
        return _PLUGINS[config.plugin](config.options, client)
    if isinstance(config, OAuth2ClientCredentials):
        return ClientCredentialsStrategy(config, client, store)
    if isinstance(config, OAuth2AuthorizationCode):
        return AuthorizationCodeStrategy(config, client, store)
    if isinstance(config, JwtAssertionAuth):
        return JwtAssertionStrategy(config, client, store)
    return PasswordStrategy(config, client, store)
