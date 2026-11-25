"""Strategies without a token exchange."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from urllib.parse import parse_qsl, quote

import httpx

from py_erp_bridge.auth.base import AuthStrategy
from py_erp_bridge.config.auth import ApiKeyAuth, BasicAuth, BearerAuth, OAuth1Auth


class NoAuthStrategy(AuthStrategy):
    """Send requests unchanged."""

    async def apply(self, request: httpx.Request) -> None:
        """Do nothing."""


def basic_header(username: str, password: str) -> str:
    """Build a Basic Authorization value."""
    return "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()


class BasicStrategy(AuthStrategy):
    """HTTP Basic."""

    def __init__(self, config: BasicAuth) -> None:
        self._header = basic_header(config.username, config.password.get_secret_value())

    async def apply(self, request: httpx.Request) -> None:
        """Set the Authorization header."""
        request.headers["Authorization"] = self._header


class BearerStrategy(AuthStrategy):
    """Static bearer token."""

    def __init__(self, config: BearerAuth) -> None:
        self._config = config

    async def apply(self, request: httpx.Request) -> None:
        """Set the Authorization header."""
        request.headers["Authorization"] = f"Bearer {self._config.token.get_secret_value()}"


class ApiKeyStrategy(AuthStrategy):
    """API key in a header or the query string."""

    def __init__(self, config: ApiKeyAuth) -> None:
        self._config = config

    async def apply(self, request: httpx.Request) -> None:
        """Add the key."""
        value = self._config.prefix + self._config.key.get_secret_value()
        if self._config.location == "header":
            request.headers[self._config.name] = value
        else:
            request.url = request.url.copy_merge_params({self._config.name: value})


def _encode(value: str) -> str:
    return quote(value, safe="-._~")


class OAuth1Strategy(AuthStrategy):
    """OAuth 1.0a with HMAC-SHA256 (RFC 5849)."""

    def __init__(self, config: OAuth1Auth) -> None:
        self._config = config

    def header(self, method: str, url: httpx.URL, nonce: str, timestamp: str) -> str:
        """Build the Authorization header. Exposed for testing."""
        cfg = self._config
        oauth = {
            "oauth_consumer_key": cfg.consumer_key,
            "oauth_nonce": nonce,
            "oauth_signature_method": "HMAC-SHA256",
            "oauth_timestamp": timestamp,
            "oauth_token": cfg.token_id,
            "oauth_version": "1.0",
        }
        query = parse_qsl(url.query.decode(), keep_blank_values=True)
        pairs = sorted((_encode(k), _encode(v)) for k, v in [*oauth.items(), *query])
        normalized = "&".join(f"{k}={v}" for k, v in pairs)
        base_url = str(url.copy_with(query=None, fragment=None))
        base = "&".join([method.upper(), _encode(base_url), _encode(normalized)])
        key = "&".join(
            _encode(s.get_secret_value()) for s in (cfg.consumer_secret, cfg.token_secret)
        )
        digest = hmac.new(key.encode(), base.encode(), hashlib.sha256).digest()
        oauth["oauth_signature"] = base64.b64encode(digest).decode()
        fields = ", ".join(f'{k}="{_encode(v)}"' for k, v in oauth.items())
        return f'OAuth realm="{_encode(cfg.realm)}", {fields}'

    async def apply(self, request: httpx.Request) -> None:
        """Sign the request."""
        request.headers["Authorization"] = self.header(
            request.method, request.url, secrets.token_hex(16), str(int(time.time()))
        )
