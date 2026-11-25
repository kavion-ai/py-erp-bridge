"""Checks that hold for every adapter, built-in or not."""

from __future__ import annotations

import contextlib

import httpx
import pytest

from helpers import CONFIGS, MockServer, config, ok
from py_erp_bridge import AsyncERPClient, Operation, registry
from py_erp_bridge.client import CANONICAL_MODELS
from py_erp_bridge.core.adapter import ERPAdapter
from py_erp_bridge.core.transforms import get_transform
from py_erp_bridge.exceptions import APIError, UniversalERPError
from py_erp_bridge.models import LineItem

ALL = sorted(CONFIGS)
HTTP = [p for p in ALL if p != "sap_ecc"]
METHODS = {
    Operation.CREATE: "create",
    Operation.UPDATE: "update",
    Operation.DELETE: "delete",
}


@pytest.mark.parametrize("provider", ALL)
def test_description_is_complete(provider):
    info = registry.get(provider).info
    assert info.name == provider
    assert info.documentation
    assert info.limitations
    assert info.protocols
    assert info.auth_types


@pytest.mark.parametrize("provider", ALL)
def test_mappings_refer_to_real_canonical_fields(provider):
    for name, spec in registry.get(provider).default_entities.items():
        model = CANONICAL_MODELS[name]
        for field in spec.fields:
            assert field.canonical in model.model_fields, f"{name}.{field.canonical}"
            get_transform(field.transform)
        for field in spec.lines.fields if spec.lines else ():
            assert field.canonical in LineItem.model_fields
        if spec.modified_field:
            mapped = spec.field("modified_at")
            assert mapped is not None
            assert mapped.source == spec.modified_field
        names = [f.canonical for f in spec.fields]
        assert len(names) == len(set(names))


@pytest.mark.parametrize("provider", ALL)
def test_declared_writes_are_implemented(provider):
    adapter = registry.get(provider)
    for name, spec in adapter.default_entities.items():
        for operation in spec.operations & METHODS.keys():
            method = METHODS[operation]
            assert getattr(adapter, method) is not getattr(ERPAdapter, method), f"{name} {method}"


@pytest.mark.parametrize("provider", ALL)
def test_optional_features_match_the_description(provider):
    adapter = registry.get(provider)
    assert adapter.info.native_bulk == (adapter.bulk_create is not ERPAdapter.bulk_create)
    assert adapter.info.file_upload == (adapter.upload_file is not ERPAdapter.upload_file)
    assert adapter.info.webhook_registration == (
        adapter.register_webhook is not ERPAdapter.register_webhook
    )


@pytest.mark.parametrize("provider", HTTP)
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="<html>login page</html>"),
        httpx.Response(200, json=[]),
        httpx.Response(200, json={}),
        httpx.Response(200, content=b""),
        httpx.Response(500, text="oops"),
        httpx.Response(404, json=[1]),
    ],
    ids=["html", "array", "object", "empty", "500", "404"],
)
async def test_unexpected_responses_never_leak_foreign_exceptions(provider, response):
    server = MockServer(
        handler=lambda _: httpx.Response(
            response.status_code, content=response.content, headers=response.headers
        )
    )
    async with AsyncERPClient(provider, config(provider), http_transport=server) as erp:
        with contextlib.suppress(UniversalERPError):
            await erp.customers.get_all(limit=1)


@pytest.mark.parametrize("provider", HTTP)
@pytest.mark.parametrize("body", ["<html><body>login</body></html>", "{}", "[]x", ""])
async def test_unexpected_body_is_not_mistaken_for_an_empty_list(provider, body):
    server = MockServer(handler=lambda _: httpx.Response(200, text=body))
    async with AsyncERPClient(provider, config(provider), http_transport=server) as erp:
        with pytest.raises(APIError):
            await erp.customers.get_all()


@pytest.mark.parametrize("provider", HTTP)
async def test_health_check_reports_auth_failure(provider):
    server = MockServer(handler=lambda _: ok({}, 401))
    async with AsyncERPClient(provider, config(provider), http_transport=server) as erp:
        status = await erp.test_connection()
    assert not status.healthy
