import asyncio
import time

import httpx
import pytest

from helpers import FAST, MockServer, config, ok
from py_erp_bridge import AsyncERPClient
from py_erp_bridge.config import CircuitBreakerConfig, RateLimitConfig, RetryConfig
from py_erp_bridge.exceptions import (
    APIError,
    CircuitOpenError,
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
from py_erp_bridge.observability import InMemoryMetrics
from py_erp_bridge.resilience import CircuitBreaker, CircuitState, RateLimiter, RetryPolicy
from py_erp_bridge.transport.http import parse_retry_after


def client(server, **extra):
    return AsyncERPClient("erpnext", config("erpnext", **extra), http_transport=server)


@pytest.mark.parametrize(
    ("status", "kind"),
    [
        (400, RequestRejectedError),
        (401, InvalidCredentialsError),
        (403, PermissionDeniedError),
        (404, NotFoundError),
        (409, ConflictError),
        (412, ConflictError),
        (422, RequestRejectedError),
        (429, RateLimitError),
        (500, ServerError),
        (503, ServerError),
        (302, RequestRejectedError),
    ],
)
async def test_status_mapping(status, kind):
    async with client(MockServer(ok({}, status)), retry={"max_attempts": 1}) as erp:
        with pytest.raises(kind) as info:
            await erp.request("GET", "/x")
        assert info.value.status_code == status
        assert info.value.provider == "erpnext"
        assert info.value.request_id


async def test_get_is_retried_until_success():
    server = MockServer(ok({}, 503), httpx.ReadTimeout("slow"), ok({"a": 1}))
    async with client(server) as erp:
        assert (await erp.request("GET", "/x")).json() == {"a": 1}
    assert len(server.requests) == 3


@pytest.mark.parametrize(
    "failure", [ok({}, 503), httpx.ReadTimeout("slow"), httpx.ReadError("reset")]
)
async def test_post_is_not_retried_when_outcome_is_unknown(failure):
    server = MockServer(failure)
    async with client(server) as erp:
        with pytest.raises((ServerError, TransportError)):
            await erp.request("POST", "/x", json={})
    assert len(server.requests) == 1


@pytest.mark.parametrize(
    "failure", [ok({}, 429), httpx.ConnectError("refused"), httpx.ConnectTimeout("t")]
)
async def test_post_is_retried_when_certainly_not_processed(failure):
    server = MockServer(failure, ok({}))
    async with client(server) as erp:
        await erp.request("POST", "/x", json={})
    assert len(server.requests) == 2


async def test_idempotency_key_makes_post_retryable():
    server = MockServer(ok({}, 503), ok({"data": {"name": "C"}}))
    async with client(server, idempotency_header="Idempotency-Key") as erp:
        await erp.customers.create({"name": "C"}, idempotency_key="k1")
    assert [r.headers["Idempotency-Key"] for r in server.requests] == ["k1", "k1"]


async def test_idempotency_key_without_support_is_an_error():
    server = MockServer()
    async with client(server) as erp:
        with pytest.raises(UnsupportedOperationError):
            await erp.customers.create({"name": "C"}, idempotency_key="k1")
    assert not server.requests


async def test_attempts_are_bounded():
    server = MockServer(*[ok({}, 500)] * 3)
    async with client(server, retry={**FAST, "max_attempts": 3}) as erp:
        with pytest.raises(ServerError):
            await erp.request("GET", "/x")


def test_retry_after_is_honoured_and_bounded():
    policy = RetryPolicy(RetryConfig(max_retry_after=10))
    short = RateLimitError("x", status_code=429, retry_after=7)
    assert policy.delay(1, short) == 7
    assert policy.should_retry(short, idempotent=False)
    long = RateLimitError("x", status_code=429, retry_after=11)
    assert not policy.should_retry(long, idempotent=True)


def test_backoff_grows_to_cap():
    policy = RetryPolicy(RetryConfig(base_delay=1, max_delay=5, jitter=False))
    assert [policy.delay(n, None) for n in (1, 2, 3, 4)] == [1, 2, 4, 5]
    jittered = RetryPolicy(RetryConfig(base_delay=1, max_delay=5))
    assert all(0 <= jittered.delay(3, None) <= 4 for _ in range(50))


def test_parse_retry_after():
    assert parse_retry_after("3") == 3
    assert parse_retry_after(None) is None
    assert parse_retry_after("garbage") is None
    assert parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT") == 0


async def test_redirects_are_not_followed():
    server = MockServer(httpx.Response(302, headers={"Location": "https://evil.test/"}))
    async with client(server) as erp:
        with pytest.raises(APIError):
            await erp.request("GET", "/x")
    assert len(server.requests) == 1


@pytest.mark.parametrize(
    "url", ["https://evil.test/x", "http://erp.test/x", "https://erp.test:8443/x"]
)
async def test_credentials_never_leave_the_origin(url):
    server = MockServer()
    async with client(server) as erp:
        with pytest.raises(TransportError):
            await erp.request("GET", url)
    assert not server.requests


async def test_connect_errors_are_translated():
    async with client(MockServer(httpx.ConnectError("no")), retry={"max_attempts": 1}) as erp:
        with pytest.raises(ERPConnectionError) as info:
            await erp.request("GET", "/x")
        assert not info.value.request_sent
    async with client(MockServer(httpx.ReadTimeout("no")), retry={"max_attempts": 1}) as erp:
        with pytest.raises(ERPTimeoutError) as info:
            await erp.request("GET", "/x")
        assert info.value.request_sent


async def test_circuit_opens_probes_and_closes():
    now = [0.0]
    breaker = CircuitBreaker(
        CircuitBreakerConfig(failure_threshold=2, reset_timeout=10), lambda: now[0]
    )

    async def attempt(error=None):
        async with breaker.guard():
            if error:
                raise error

    for _ in range(2):
        with pytest.raises(ServerError):
            await attempt(ServerError("x"))
    assert breaker.state is CircuitState.OPEN
    with pytest.raises(CircuitOpenError):
        await attempt()
    now[0] = 10
    with pytest.raises(ServerError):
        await attempt(ServerError("x"))
    assert breaker.state is CircuitState.OPEN
    now[0] = 20
    await attempt()
    assert breaker.state is CircuitState.CLOSED


async def test_circuit_allows_one_probe_and_survives_cancellation():
    now = [0.0]
    breaker = CircuitBreaker(
        CircuitBreakerConfig(failure_threshold=1, reset_timeout=1), lambda: now[0]
    )
    with pytest.raises(ServerError):
        async with breaker.guard():
            raise ServerError("x")
    now[0] = 1

    async def hang():
        async with breaker.guard():
            await asyncio.sleep(60)

    probe = asyncio.create_task(hang())
    await asyncio.sleep(0)
    with pytest.raises(CircuitOpenError):
        async with breaker.guard():
            pass
    probe.cancel()
    with pytest.raises(asyncio.CancelledError):
        await probe
    async with breaker.guard():
        pass
    assert breaker.state is CircuitState.CLOSED


async def test_client_errors_do_not_open_the_circuit():
    server = MockServer(handler=lambda _: ok({}, 400))
    async with client(server, circuit_breaker={"failure_threshold": 2}) as erp:
        for _ in range(5):
            with pytest.raises(RequestRejectedError):
                await erp.request("GET", "/x")


async def test_circuit_stops_traffic():
    server = MockServer(handler=lambda _: ok({}, 500))
    async with client(
        server, circuit_breaker={"failure_threshold": 2}, retry={"max_attempts": 1}
    ) as erp:
        for _ in range(2):
            with pytest.raises(ServerError):
                await erp.request("GET", "/x")
        with pytest.raises(CircuitOpenError):
            await erp.request("GET", "/x")
    assert len(server.requests) == 2


async def test_rate_limiter_limits_rate_and_concurrency():
    limiter = RateLimiter(RateLimitConfig(requests_per_second=50, burst=1, max_concurrency=2))
    active = peak = 0

    async def work():
        nonlocal active, peak
        async with limiter.slot():
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.03)
            active -= 1

    started = time.perf_counter()
    await asyncio.gather(*(work() for _ in range(5)))
    assert peak <= 2
    assert time.perf_counter() - started >= 0.06


async def test_metrics_and_logs_hold_no_secrets(caplog):
    metrics = InMemoryMetrics()
    server = MockServer(ok({}))
    with caplog.at_level("INFO", logger="py_erp_bridge"):
        async with AsyncERPClient(
            "erpnext", config("erpnext"), http_transport=server, metrics=metrics
        ) as erp:
            await erp.request("GET", "/x", params={"email": "someone@example.com"})
    assert metrics.requests[("erpnext", "GET", 200)] == 1
    logged = " ".join(str(vars(r)) for r in caplog.records)
    assert "k:s" not in logged
    assert "someone@example.com" not in logged
    assert "someone" not in logged
