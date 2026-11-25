import base64
import hashlib
import hmac

import pytest
from pydantic import SecretStr

from py_erp_bridge.exceptions import SignatureVerificationError, WebhookError
from py_erp_bridge.webhooks import (
    HmacVerifier,
    InMemoryDeduplication,
    SharedSecretVerifier,
    WebhookProcessor,
    frappe_verifier,
)

BODY = b'{"doctype": "Customer", "id": "1"}'
SECRET = SecretStr("s")
SIGNATURE = base64.b64encode(hmac.new(b"s", BODY, hashlib.sha256).digest()).decode()
HEADERS = {"x-frappe-webhook-signature": SIGNATURE}


def processor(**kw):
    return WebhookProcessor(frappe_verifier(SECRET), event_type=lambda h, p: p.get("doctype"), **kw)


async def test_dispatch_and_deduplication():
    hooks, seen = processor(), []

    @hooks.on("Customer")
    async def specific(event):
        seen.append(("c", event.payload["id"]))

    @hooks.on("*")
    async def everything(event):
        seen.append(("*", event.type))

    assert (await hooks.process(HEADERS, BODY)).type == "Customer"
    assert await hooks.process(HEADERS, BODY) is None
    assert seen == [("c", "1"), ("*", "Customer")]


async def test_failure_allows_redelivery():
    hooks, calls = processor(), []

    @hooks.on("Customer")
    async def flaky(event):
        calls.append(event.id)
        if len(calls) == 1:
            raise RuntimeError

    with pytest.raises(RuntimeError):
        await hooks.process(HEADERS, BODY)
    assert await hooks.process(HEADERS, BODY)
    assert len(calls) == 2


@pytest.mark.parametrize(
    "headers", [{}, {"X-Frappe-Webhook-Signature": "bad"}, {"X-Frappe-Webhook-Signature": "é"}]
)
async def test_bad_signatures(headers):
    with pytest.raises(SignatureVerificationError):
        await processor().process(headers, BODY)


async def test_tampered_body():
    with pytest.raises(SignatureVerificationError):
        await processor().process(HEADERS, BODY + b" ")


@pytest.mark.parametrize("body", [b"not json", b"[]"])
async def test_bad_bodies(body):
    hooks = WebhookProcessor(SharedSecretVerifier(SECRET, "X-Key"), event_type=lambda h, p: None)
    with pytest.raises(WebhookError):
        await hooks.process({"X-Key": "s"}, body)
    small = processor(max_body=1)
    with pytest.raises(WebhookError, match="large"):
        await small.process(HEADERS, BODY)


def test_hex_prefix_and_empty_secret():
    digest = hmac.new(b"s", BODY, hashlib.sha512).hexdigest()
    HmacVerifier(SECRET, "X-Sig", algorithm="sha512", prefix="v1=").verify(
        {"x-sig": f"v1={digest}"}, BODY
    )
    with pytest.raises(ValueError, match="empty"):
        HmacVerifier(SecretStr(""), "X")
    with pytest.raises(ValueError, match="empty"):
        SharedSecretVerifier(SecretStr(""), "X")


async def test_deduplication_expires():
    now = [0.0]
    store = InMemoryDeduplication(ttl=10, clock=lambda: now[0])
    assert await store.claim("a")
    assert not await store.claim("a")
    now[0] = 11
    assert await store.claim("a")
