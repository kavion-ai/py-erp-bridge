"""Shared test doubles."""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx

Handler = Callable[[httpx.Request], httpx.Response]
FAST = {"base_delay": 0, "max_delay": 0}


class MockServer(httpx.MockTransport):
    """Records requests and answers with queued responses or a handler."""

    def __init__(self, *responses, handler: Handler | None = None):
        self.requests: list[httpx.Request] = []
        self.queue = list(responses)
        self.custom = handler
        super().__init__(self._handle)

    def _handle(self, request):
        self.requests.append(request)
        if self.custom:
            return self.custom(request)
        assert self.queue, f"unexpected request {request.method} {request.url}"
        answer = self.queue.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    @property
    def last(self):
        return self.requests[-1]

    def body(self, index=-1):
        return json.loads(self.requests[index].content)


def ok(data=None, status=200, **kwargs):
    return (
        httpx.Response(status, json=data, **kwargs)
        if data is not None
        else httpx.Response(status, **kwargs)
    )


CONFIGS = {
    "sap_s4hana": {
        "base_url": "https://erp.test",
        "auth": {"type": "basic", "username": "u", "password": "p"},
    },
    "sap_ecc": {"rfc": {"client": "100", "user": "u", "passwd": "p", "ashost": "h", "sysnr": "00"}},
    "oracle_fusion": {
        "base_url": "https://erp.test",
        "auth": {"type": "basic", "username": "u", "password": "p"},
    },
    "netsuite": {"base_url": "https://erp.test", "auth": {"type": "bearer", "token": "t"}},
    "dynamics365": {
        "base_url": "https://erp.test",
        "auth": {"type": "bearer", "token": "t"},
        "company": "usmf",
    },
    "odoo": {"base_url": "https://erp.test", "auth": {"type": "bearer", "token": "t"}},
    "erpnext": {
        "base_url": "https://erp.test",
        "auth": {"type": "api_key", "key": "k:s", "prefix": "token "},
    },
    "tally": {"base_url": "http://localhost:9000", "company": "Acme & Co"},
    "infor": {
        "base_url": "https://erp.test",
        "tenant": "T_TST",
        "auth": {"type": "bearer", "token": "t"},
    },
    "sage_intacct": {
        "base_url": "https://erp.test/ia/api/v1",
        "auth": {"type": "bearer", "token": "t"},
    },
}


def config(provider, **extra):
    return {**CONFIGS[provider], "retry": FAST, **extra}
