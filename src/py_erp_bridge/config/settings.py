"""Connection settings common to all providers."""

from __future__ import annotations

from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from py_erp_bridge.config.auth import AuthConfig, NoAuth
from py_erp_bridge.core.capabilities import EntitySpec


class _Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TimeoutConfig(_Settings):
    """Timeouts in seconds."""

    connect: float = Field(default=10, gt=0)
    read: float = Field(default=60, gt=0)
    write: float = Field(default=60, gt=0)
    pool: float = Field(default=10, gt=0)


class RetryConfig(_Settings):
    """Exponential backoff with full jitter."""

    max_attempts: int = Field(default=4, ge=1)
    base_delay: float = Field(default=0.5, ge=0)
    max_delay: float = Field(default=30, ge=0)
    jitter: bool = True
    retry_statuses: frozenset[int] = frozenset({408, 429, 500, 502, 503, 504})
    #: A server asking for a longer wait than this is not retried.
    max_retry_after: float = Field(default=120, ge=0)


class CircuitBreakerConfig(_Settings):
    """Open after consecutive failures; probe again after ``reset_timeout``."""

    enabled: bool = True
    failure_threshold: int = Field(default=5, ge=1)
    reset_timeout: float = Field(default=30, gt=0)


class RateLimitConfig(_Settings):
    """Client-side limits. ``requests_per_second`` is off by default."""

    requests_per_second: float | None = Field(default=None, gt=0)
    burst: int = Field(default=1, ge=1)
    max_concurrency: int = Field(default=10, ge=1)


class PoolConfig(_Settings):
    """HTTP connection pool."""

    max_connections: int = Field(default=20, ge=1)
    max_keepalive: int = Field(default=10, ge=0)
    keepalive_expiry: float = Field(default=30, ge=0)


class TLSConfig(_Settings):
    """TLS verification and mutual TLS."""

    verify: bool = True
    ca_bundle: Path | None = None
    client_cert: Path | None = None
    client_key: Path | None = None
    client_key_password: SecretStr | None = None


class EntityOverride(_Settings):
    """Adjust a built-in entity for a tenant (custom fields, other resource, ...)."""

    path: str | None = None
    #: canonical field -> provider field. Merged over the defaults.
    fields: dict[str, str] = Field(default_factory=dict)
    modified_field: str | None = None
    create_defaults: dict[str, str | int | float | bool] = Field(default_factory=dict)


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


class ProviderConfig(_Settings):
    """Base configuration; providers subclass it to add their own settings."""

    base_url: str
    auth: AuthConfig = Field(default_factory=NoAuth)
    timeout: TimeoutConfig = Field(default_factory=TimeoutConfig)
    retry: RetryConfig = Field(default_factory=RetryConfig)
    circuit_breaker: CircuitBreakerConfig = Field(default_factory=CircuitBreakerConfig)
    rate_limit: RateLimitConfig = Field(default_factory=RateLimitConfig)
    pool: PoolConfig = Field(default_factory=PoolConfig)
    tls: TLSConfig = Field(default_factory=TLSConfig)
    proxy: SecretStr | None = None
    headers: dict[str, str] = Field(default_factory=dict, repr=False)
    #: Header carrying ``idempotency_key``. Set only if your ERP or gateway honours it.
    idempotency_header: str | None = None
    page_size: int = Field(default=100, ge=1)
    #: Plain http is refused for non-loopback hosts unless this is set.
    allow_insecure_http: bool = False
    entity_overrides: dict[str, EntityOverride] = Field(default_factory=dict)
    custom_entities: dict[str, EntitySpec] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_url(self) -> ProviderConfig:
        parts = urlsplit(self.base_url)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise ValueError("base_url must be an absolute http(s) URL")
        if parts.username or parts.password:
            raise ValueError("put credentials in 'auth', not in base_url")
        if (
            parts.scheme == "http"
            and not self.allow_insecure_http
            and not _is_loopback(parts.hostname)
        ):
            raise ValueError("base_url uses plain http; use https or set allow_insecure_http")
        return self
