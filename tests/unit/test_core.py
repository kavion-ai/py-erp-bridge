import json
import logging
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from helpers import MockServer, config, ok
from py_erp_bridge import (
    AsyncERPClient,
    EntitySpec,
    ERPClient,
    FileUpload,
    Filter,
    FilterOp,
    IncrementalSync,
    InMemorySyncState,
    Operation,
    ProviderRegistry,
    Sort,
    TenantManager,
    Transform,
    WebhookRegistration,
    available_providers,
    config_from_env,
    register_transform,
)
from py_erp_bridge.config import ProviderConfig
from py_erp_bridge.core.mapping import get_path, set_path
from py_erp_bridge.core.query import matches, parse_filters, parse_sort
from py_erp_bridge.core.transforms import get_transform
from py_erp_bridge.exceptions import (
    ConfigurationError,
    MappingError,
    PartialFailureError,
    PayloadValidationError,
    ProviderNotFoundError,
    UnsupportedOperationError,
)
from py_erp_bridge.models import Customer, SalesOrder
from py_erp_bridge.observability import JsonFormatter
from py_erp_bridge.providers.erpnext import ERPNextAdapter


def erpnext(server, **extra):
    return AsyncERPClient("erpnext", config("erpnext", **extra), http_transport=server)


# -- configuration ----------------------------------------------------------


@pytest.mark.parametrize("url", ["ftp://x", "erp.test", "http://erp.test", "https://u:p@erp.test"])
def test_bad_urls_are_rejected(url):
    with pytest.raises(ValidationError):
        ProviderConfig(base_url=url)


def test_http_is_fine_for_loopback_or_when_allowed():
    ProviderConfig(base_url="http://localhost:9000")
    ProviderConfig(base_url="http://127.0.0.1")
    ProviderConfig(base_url="http://10.0.0.5", allow_insecure_http=True)


def test_config_errors_do_not_echo_secrets():
    with pytest.raises(ConfigurationError) as info:
        ERPClient(
            "odoo", {"base_url": "https://x.test", "auth": {"type": "bearer", "tokn": "S3CRET"}}
        )
    assert "auth" in str(info.value)
    assert "S3CRET" not in str(info.value)
    assert info.value.__cause__ is None


def test_secrets_are_masked_in_repr():
    cfg = ProviderConfig.model_validate(config("odoo", auth={"type": "bearer", "token": "S3CRET"}))
    assert "S3CRET" not in repr(cfg)


def test_config_from_env():
    env = {
        "ODOO_BASE_URL": "https://x.test",
        "ODOO_AUTH__TYPE": "bearer",
        "ODOO_AUTH__TOKEN": "t",
        "ODOO_RETRY__MAX_ATTEMPTS": "2",
        "ODOO_HEADERS": '{"A": "b"}',
        "ODOO_PROXY": "",
        "OTHER": "x",
    }
    data = config_from_env("odoo", env)
    cfg = ProviderConfig.model_validate(data)
    assert cfg.retry.max_attempts == 2
    assert cfg.headers == {"A": "b"}
    assert cfg.proxy is None


def test_config_from_env_leaves_passwords_alone():
    env = {"X_A": "{not json", "X_B": r"a\nb", "X_C": r"-----BEGIN KEY\nabc"}
    assert config_from_env("X", env) == {
        "a": "{not json",
        "b": r"a\nb",
        "c": "-----BEGIN KEY" + chr(10) + "abc",
    }


# -- registry ---------------------------------------------------------------


def test_all_ten_providers_are_available():
    assert set(available_providers()) >= {
        "sap_s4hana",
        "sap_ecc",
        "oracle_fusion",
        "netsuite",
        "dynamics365",
        "odoo",
        "erpnext",
        "tally",
        "infor",
        "sage_intacct",
    }


def test_eleventh_provider_needs_no_core_change():
    class Eleventh(ERPNextAdapter):
        info = ERPNextAdapter.info.model_copy(update={"name": "eleventh"})

    registry = ProviderRegistry()
    registry.register("eleventh", Eleventh)
    server = MockServer(ok({"data": []}))
    with ERPClient("eleventh", config("erpnext"), registry=registry, http_transport=server) as erp:
        assert erp.customers.get_all() == []
    with pytest.raises(ConfigurationError):
        registry.register("eleventh", Eleventh)


def test_unknown_and_invalid_providers():
    registry = ProviderRegistry()
    with pytest.raises(ProviderNotFoundError):
        registry.get("nope")
    registry.register("bad", "json:JSONDecoder")
    with pytest.raises(ConfigurationError):
        registry.get("bad")


# -- query ------------------------------------------------------------------


def test_filter_shorthand():
    got = parse_filters({"name": "a", "total__gt": 5, "id": ["1", "2"], "odd__name": 1})
    assert [(f.field, f.op) for f in got] == [
        ("name", "eq"),
        ("total", "gt"),
        ("id", "in"),
        ("odd__name", "eq"),
    ]
    assert parse_sort(["-a", "b"]) == (Sort(field="a", descending=True), Sort(field="b"))


@pytest.mark.parametrize(
    ("op", "value", "expected"),
    [
        ("eq", 10, True),
        ("ne", 10, False),
        ("gt", 9, True),
        ("lt", 9, False),
        ("ge", 10, True),
        ("le", 9.5, False),
        ("in", [1, 10], True),
        ("contains", "0", True),
        ("startswith", "2", False),
    ],
)
def test_local_matching(op, value, expected):
    assert (
        matches(
            {"f": "10.00" if op not in {"contains", "startswith"} else "10"},
            [Filter(field="f", op=FilterOp(op), value=value)],
        )
        is expected
    )


def test_local_matching_of_missing_numbers():
    assert not matches({}, [Filter(field="f", op=FilterOp.LT, value=5)])
    assert matches({}, [Filter(field="f", op=FilterOp.NE, value=5)])


# -- mapping ----------------------------------------------------------------


def test_paths():
    data = {}
    set_path(data, "a.b", 1)
    assert get_path(data, "a.b") == 1
    assert get_path(data, "a.b.c") is None
    assert get_path({"a.b": 2}, "a.b") == 2
    with pytest.raises(MappingError):
        set_path({"a": 1}, "a.b", 1)


@pytest.mark.parametrize(
    ("name", "raw", "expected"),
    [
        ("compact_date", "20240131", "2024-01-31"),
        ("compact_date", "00000000", None),
        ("odata_v2_date", "/Date(1706659200000)/", "2024-01-31"),
        ("odata_v2_datetime", "/Date(0+0000)/", "1970-01-01T00:00:00+00:00"),
        ("decimal", " 1,200.50- ", "-1200.50"),
        ("decimal", "", None),
        ("decimal", "abc", None),
        ("alpha10", "0000001001", "1001"),
        ("alpha10", "0000000000", "0"),
        ("alpha10", "AB", "AB"),
        ("relation", [5, "X"], 5),
        ("date", "2024-01-31 10:00:00", "2024-01-31"),
    ],
)
def test_transform_read(name, raw, expected):
    assert get_transform(name).read(raw) == expected


def test_transform_write():
    assert get_transform("compact_date").write(date(2024, 1, 31)) == "20240131"
    assert get_transform("odata_v2_date").write(date(2024, 1, 31)) == "/Date(1706659200000)/"
    assert get_transform("alpha10").write("1001") == "0000001001"
    assert get_transform("decimal_string").write(Decimal("1.50")) == "1.50"
    assert get_transform("identity").write(Decimal("2.0")) == 2


def test_custom_transforms():
    register_transform("test-upper", Transform(lambda v: str(v).upper()))
    assert get_transform("test-upper").read("a") == "A"
    with pytest.raises(ConfigurationError):
        register_transform("identity", Transform(lambda v: v))
    with pytest.raises(ConfigurationError):
        get_transform("nope")


async def test_read_maps_fields_and_keeps_raw():
    raw = {
        "name": "C-1",
        "customer_name": "Acme",
        "custom_x": {"y": 1},
        "modified": "2024-01-01 10:00:00",
    }
    async with erpnext(MockServer(ok({"data": raw}))) as erp:
        customer = await erp.customers.get("C-1")
    assert customer.id == "C-1"
    assert customer.name == "Acme"
    assert customer.provider == "erpnext"
    assert customer.raw == raw
    assert customer.modified_at == datetime(2024, 1, 1, 10)


async def test_write_sends_only_set_fields_plus_provider_fields():
    server = MockServer(ok({"data": {"name": "C"}}), ok({"data": {"name": "C"}}))
    async with erpnext(server) as erp:
        await erp.customers.create({"name": "Acme", "provider_fields": {"customer_group": "All"}})
        assert server.body() == {"customer_name": "Acme", "customer_group": "All"}
        await erp.customers.update("C", Customer(email=None))
        assert server.body() == {"email_id": None}


async def test_write_rejects_what_it_cannot_express():
    server = MockServer()
    async with erpnext(server) as erp:
        with pytest.raises(PayloadValidationError):
            await erp.customers.create({"nmae": "typo"})
        with pytest.raises(MappingError, match="no mapping"):
            await erp.customers.create({"street": "x"})
        with pytest.raises(MappingError, match="read-only"):
            await erp.sales_orders.create({"total": 5})
        with pytest.raises(PayloadValidationError):
            await erp.customers.create(SalesOrder())
    assert not server.requests


async def test_bad_record_error_hides_values():
    async with erpnext(MockServer(ok({"data": {"name": "SO", "grand_total": "S3CRET"}}))) as erp:
        with pytest.raises(MappingError) as info:
            await erp.sales_orders.get("SO")
    assert "total" in str(info.value)
    assert "S3CRET" not in str(info.value)


async def test_entity_overrides_and_custom_entities():
    server = MockServer(
        ok({"data": {"name": "C", "x_tier": "EUR"}}),
        ok({"data": {"name": "T1", "a": 1}}),
        ok({"data": {"name": "T2"}}),
    )
    async with erpnext(
        server,
        entity_overrides={"customers": {"fields": {"currency": "x_tier"}}},
        custom_entities={"tickets": {"path": "Issue", "key_fields": ["name"]}},
    ) as erp:
        assert (await erp.customers.get("C")).currency == "EUR"
        assert (await erp.entity("tickets").get("T1")).raw["a"] == 1
        await erp.entity("tickets").create({"subject": "hello"})
        assert server.body() == {"subject": "hello"}
        with pytest.raises(ConfigurationError):
            erp.entity("unknown")
    with pytest.raises(ConfigurationError):
        erpnext(MockServer(), entity_overrides={"nope": {}})


async def test_adhoc_entity():
    server = MockServer(ok({"data": []}))
    async with erpnext(server) as erp:
        spec = EntitySpec(path="Sales Person", key_fields=("name",))
        assert await erp.entity("people", spec=spec).get_all() == []
    assert server.last.url.raw_path.startswith(b"/api/resource/Sales%20Person")


# -- paging, bulk -----------------------------------------------------------


def paged(total):
    def handler(request):
        start = int(request.url.params.get("limit_start", "0"))
        size = int(request.url.params.get("limit_page_length", "9"))
        rows = [{"name": str(i)} for i in range(start, min(start + size, total))]
        return ok({"data": rows})

    return MockServer(handler=handler)


@pytest.mark.parametrize(("total", "size"), [(0, 2), (1, 2), (4, 2), (5, 2), (5, 100)])
async def test_iteration_yields_everything_once(total, size):
    async with erpnext(paged(total)) as erp:
        ids = [c.id async for c in erp.customers.iterate(page_size=size)]
    assert ids == [str(i) for i in range(total)]


async def test_limit_stops_early():
    server = paged(100)
    async with erpnext(server) as erp:
        assert len(await erp.customers.get_all(page_size=2, limit=3)) == 3
    assert len(server.requests) == 2


async def test_bulk_reports_per_item():
    def handler(request):
        name = json.loads(request.content)["customer_name"]
        return ok({"message": "bad"}, 417) if name == "b" else ok({"data": {"name": name}})

    async with erpnext(MockServer(handler=handler)) as erp:
        items = [{"name": n} for n in "abc"]
        result = await erp.customers.bulk_create(items)
        assert [i.ok for i in result.items] == [True, False, True]
        assert result.items[2].record.id == "c"
        with pytest.raises(PartialFailureError) as info:
            await erp.customers.bulk_create(items, raise_on_failure=True)
        assert len(info.value.result.failed) == 1
        stopped = await erp.customers.bulk_create(items, stop_on_error=True)
        assert [i.skipped for i in stopped.items] == [False, False, True]


async def test_bulk_validates_before_sending():
    server = MockServer()
    async with erpnext(server) as erp:
        with pytest.raises(PayloadValidationError):
            await erp.customers.bulk_create([{"name": "a"}, {"bad": 1}])
        assert (await erp.customers.bulk_create([])).items == []
    assert not server.requests


# -- clients ----------------------------------------------------------------


def test_sync_client_end_to_end():
    server = paged(5)
    erp = ERPClient("erpnext", config("erpnext"), http_transport=server)
    assert len(erp.customers.get_all()) == 5
    assert [c.id for c in erp.customers.iterate(page_size=2)] == list("01234")
    assert erp.customers.list_page(page_size=2).next_cursor == "2"
    assert erp.customers.supports(Operation.CREATE)
    assert erp.capabilities()["inventory"] == {Operation.LIST, Operation.GET}
    assert erp.test_connection().healthy is True
    assert erp.info.name == "erpnext"
    erp.close()
    erp.close()
    with pytest.raises(RuntimeError, match="closed"):
        erp.customers.get("1")


async def test_sync_client_works_inside_a_running_loop():
    with ERPClient("erpnext", config("erpnext"), http_transport=paged(1)) as erp:
        assert len(erp.customers.get_all()) == 1


async def test_sync_client_abandoned_iterator():
    with ERPClient("erpnext", config("erpnext"), http_transport=paged(9)) as erp:
        for _ in erp.customers.iterate(page_size=2):
            break


def test_sync_writes_and_raw_access():
    doc = {"data": {"name": "C"}}
    server = MockServer(ok(doc), ok(doc), ok({"message": "ok"}), ok(doc), ok({"message": 7}))
    with ERPClient("erpnext", config("erpnext"), http_transport=server) as erp:
        assert erp.customers.create({"name": "a"}).id == "C"
        assert erp.customers.update("C", {"name": "b"}).id == "C"
        erp.customers.delete("C")
        assert erp.customers.bulk_create([{"name": "x"}]).succeeded
        assert erp.execute("call_method", method="a.b", params={"x": 1}) == 7
        assert [r.method for r in server.requests] == ["POST", "PUT", "DELETE", "POST", "POST"]


async def test_create_async_and_unmapped_entity():
    async with ERPClient.create_async("tally", config("tally"), http_transport=MockServer()) as erp:
        with pytest.raises(UnsupportedOperationError, match="does not support list on employees"):
            await erp.employees.get_all()
        with pytest.raises(UnsupportedOperationError):
            await erp.upload_file(FileUpload(filename="a", content=b""))
        with pytest.raises(UnsupportedOperationError):
            await erp.register_webhook(WebhookRegistration(url="u", entity="e", event="x"))
        with pytest.raises(UnsupportedOperationError, match="import_voucher"):
            await erp.execute("aclose")


async def test_failed_health_check_is_reported():
    server = MockServer(ok({}, 401))
    async with erpnext(server) as erp:
        status = await erp.test_connection()
    assert not status.healthy
    assert "401" in status.detail


def test_tenants_are_isolated():
    manager = TenantManager({"a": ("erpnext", config("erpnext")), "b": ("odoo", config("odoo"))})
    assert manager.get("a") is manager.get("a")
    assert manager.get("a").info.name != manager.get("b").info.name
    with pytest.raises(ConfigurationError):
        manager.get("c")
    manager.close()


def test_config_object_must_match_provider():
    with pytest.raises(ConfigurationError):
        ERPClient("odoo", ProviderConfig(base_url="https://x.test"))


# -- incremental sync -------------------------------------------------------


def changes(*rows):
    return ok({"data": [{"name": n, "modified": m} for n, m in rows]})


async def test_incremental_sync_advances_watermark():
    server = MockServer(
        changes(("a", "2024-01-01 10:00:00"), ("b", "2024-01-01 11:00:00")), changes()
    )
    store, seen = InMemorySyncState(), []

    async def handle(record):
        seen.append(record.id)

    async with erpnext(server) as erp:
        sync = IncrementalSync(erp.customers, store, overlap=timedelta(minutes=5))
        report = await sync.run(handle)
        assert report.processed == 2
        assert store.values["customers"] == datetime(2024, 1, 1, 11, tzinfo=UTC)
        await sync.run(handle)
    assert json.loads(server.last.url.params["filters"]) == [
        ["modified", ">=", "2024-01-01 10:55:00"]
    ]
    assert server.last.url.params["order_by"] == "modified asc"
    assert seen == ["a", "b"]


async def test_failed_handler_keeps_watermark():
    store = InMemorySyncState()

    async def fail(record):
        raise RuntimeError

    async with erpnext(MockServer(changes(("a", "2024-01-01 10:00:00")))) as erp:
        with pytest.raises(RuntimeError):
            await IncrementalSync(erp.customers, store).run(fail)
    assert store.values == {}


async def test_sync_needs_a_timestamp():
    async with AsyncERPClient("tally", config("tally")) as erp:
        with pytest.raises(UnsupportedOperationError):
            IncrementalSync(erp.customers, InMemorySyncState())


def test_json_logs():
    record = logging.makeLogRecord({"msg": "hi", "levelname": "INFO", "provider": "x"})
    assert json.loads(JsonFormatter().format(record))["provider"] == "x"
