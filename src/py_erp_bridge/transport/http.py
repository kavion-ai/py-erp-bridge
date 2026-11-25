"""HTTP transport: auth, resilience, error mapping and observability in one place."""

from __future__ import annotations

import ssl
import time
import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel

from py_erp_bridge.auth.base import AuthStrategy
from py_erp_bridge.config.settings import ProviderConfig, TLSConfig
from py_erp_bridge.exceptions import (
    APIError,
    ConflictError,
    ERPConnectionError,
    ERPTimeoutError,
    InvalidCredentialsError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    RequestRejectedError,
    ServerError,
    TransportError,
    UnsupportedOperationError,
)
from py_erp_bridge.observability import MetricsSink, NullMetrics, logger
from py_erp_bridge.resilience import CircuitBreaker, RateLimiter, RetryPolicy
from py_erp_bridge.types import JsonValue

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "PUT", "DELETE"})
_BODY_LIMIT = 4000
QueryParams = Mapping[str, str | int]


class ErrorDetails(BaseModel):
    """What an adapter could extract from an error response."""

    code: str | None = None
    message: str | None = None
    not_found: bool = False


def build_ssl_context(tls: TLSConfig) -> ssl.SSLContext:
    """Create the TLS context described by the configuration."""
    context = ssl.create_default_context(cafile=str(tls.ca_bundle) if tls.ca_bundle else None)
    if not tls.verify:
        logger.warning("TLS certificate verification is disabled")
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    if tls.client_cert:
        password = tls.client_key_password
        context.load_cert_chain(
            str(tls.client_cert),
            str(tls.client_key) if tls.client_key else None,
            password.get_secret_value() if password else None,
        )
    return context


def build_client(
    config: ProviderConfig, transport: httpx.AsyncBaseTransport | None = None
) -> httpx.AsyncClient:
    """Create the pooled httpx client. ``transport`` replaces the network, for tests."""
    return httpx.AsyncClient(
        timeout=httpx.Timeout(**config.timeout.model_dump()),
        limits=httpx.Limits(
            max_connections=config.pool.max_connections,
            max_keepalive_connections=config.pool.max_keepalive,
            keepalive_expiry=config.pool.keepalive_expiry,
        ),
        verify=build_ssl_context(config.tls),
        proxy=config.proxy.get_secret_value() if config.proxy and transport is None else None,
        headers=config.headers,
        # Redirects could carry credentials to another host, so they are never followed.
        follow_redirects=False,
        transport=transport,
    )


def parse_retry_after(value: str | None) -> float | None:
    """Parse a Retry-After header (seconds or HTTP date)."""
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        moment = parsedate_to_datetime(value)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return max(0.0, (moment - datetime.now(UTC)).total_seconds())


def _origin(url: str) -> tuple[str, str | None, int | None]:
    parts = urlsplit(url)
    return parts.scheme, parts.hostname, parts.port or {"https": 443, "http": 80}.get(parts.scheme)


class HttpTransport:
    """Sends requests for one client."""

    def __init__(
        self,
        provider: str,
        config: ProviderConfig,
        auth: AuthStrategy,
        client: httpx.AsyncClient,
        metrics: MetricsSink | None = None,
    ) -> None:
        self.provider = provider
        self.config = config
        self.auth = auth
        self.client = client
        self.metrics = metrics or NullMetrics()
        self.breaker = CircuitBreaker(config.circuit_breaker)
        self.limiter = RateLimiter(config.rate_limit)
        self.retry = RetryPolicy(config.retry)
        self.error_parser: Callable[[httpx.Response], ErrorDetails] = lambda _: ErrorDetails()

    def url(self, path: str) -> str:
        """Resolve a path. Absolute URLs must share the origin of ``base_url``."""
        if path.startswith(("http://", "https://")):
            if _origin(path) != _origin(self.config.base_url):
                raise TransportError(
                    "refusing to send credentials to a different origin", request_sent=False
                )
            return path
        return f"{self.config.base_url.rstrip('/')}/{path.lstrip('/')}"

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: QueryParams | None = None,
        json: JsonValue = None,
        content: bytes | str | None = None,
        data: Mapping[str, str] | None = None,
        files: Mapping[str, tuple[str, bytes, str]] | None = None,
        headers: Mapping[str, str] | None = None,
        idempotent: bool | None = None,
        idempotency_key: str | None = None,
    ) -> httpx.Response:
        """Send a request and return a successful response, or raise.

        ``idempotent`` marks e.g. a POST that only reads as safe to retry.
        """
        method = method.upper()
        url = self.url(path)
        request_id = uuid.uuid4().hex
        all_headers = {"X-Request-ID": request_id, **(headers or {})}
        if idempotency_key is not None:
            if not self.config.idempotency_header:
                raise UnsupportedOperationError("idempotency_key needs config.idempotency_header")
            all_headers[self.config.idempotency_header] = idempotency_key
        safe = (
            idempotent
            if idempotent is not None
            else method in _SAFE_METHODS or idempotency_key is not None
        )

        def build() -> httpx.Request:
            return self.client.build_request(
                method,
                url,
                params=dict(params) if params else None,
                json=json,
                content=content,
                data=dict(data) if data else None,
                files=dict(files) if files else None,
                headers=all_headers,
            )

        async def attempt() -> httpx.Response:
            # Concurrency slots are released while waiting between attempts.
            async with self.breaker.guard(), self.limiter.slot():
                response = await self._send(build(), request_id)
                if response.status_code == 401 and await self.auth.refresh(response.request):
                    response = await self._send(build(), request_id)
                if not response.is_success:
                    raise self.to_error(response, request_id)
                return response

        return await self.retry.run(attempt, idempotent=safe, request_id=request_id)

    async def _send(self, request: httpx.Request, request_id: str) -> httpx.Response:
        await self.auth.apply(request)
        started = time.perf_counter()
        status: int | None = None
        try:
            response = await self.client.send(request)
            status = response.status_code
            return response
        except (httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
            raise ERPTimeoutError(type(exc).__name__, request_sent=False) from exc
        except httpx.TimeoutException as exc:
            raise ERPTimeoutError(type(exc).__name__) from exc
        except (httpx.ConnectError, httpx.ProxyError, httpx.UnsupportedProtocol) as exc:
            raise ERPConnectionError(f"{type(exc).__name__}: {exc}", request_sent=False) from exc
        except httpx.TransportError as exc:
            raise ERPConnectionError(f"{type(exc).__name__}: {exc}") from exc
        finally:
            duration = time.perf_counter() - started
            self.metrics.observe_request(
                provider=self.provider, method=request.method, status=status, duration=duration
            )
            logger.info(
                "erp request",
                extra={
                    "provider": self.provider,
                    "request_id": request_id,
                    "method": request.method,
                    "host": request.url.host,
                    "path": request.url.path,
                    "status": status,
                    "duration_ms": round(duration * 1000, 1),
                },
            )

    def to_error(self, response: httpx.Response, request_id: str | None = None) -> APIError:
        """Convert an error response to the matching exception."""
        details = self.error_parser(response)
        status = response.status_code
        kind: type[APIError]
        if details.not_found or status in {404, 410}:
            kind = NotFoundError
        elif status == 401:
            kind = InvalidCredentialsError
        elif status == 403:
            kind = PermissionDeniedError
        elif status in {409, 412, 428}:
            kind = ConflictError
        elif status == 429:
            kind = RateLimitError
        elif status >= 500:
            kind = ServerError
        else:
            kind = RequestRejectedError
        return kind(
            f"{self.provider}: HTTP {status}: {details.message or response.reason_phrase}",
            provider=self.provider,
            status_code=status,
            code=details.code,
            request_id=request_id,
            retry_after=parse_retry_after(response.headers.get("Retry-After")),
            body=response.text[:_BODY_LIMIT],
        )

    async def aclose(self) -> None:
        """Close the connection pool."""
        await self.client.aclose()
