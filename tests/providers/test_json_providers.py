from __future__ import annotations

import json
from datetime import UTC, date, datetime

import httpx
import pytest

from helpers import MockServer, config, ok
from py_erp_bridge import AsyncERPClient, FileUpload, WebhookRegistration
from py_erp_bridge.exceptions import (
    APIError,
    AuthenticationError,
    BusinessRuleError,
    ConfigurationError,
    NotFoundError,
    PayloadValidationError,
    RequestRejectedError,
    UnsupportedOperationError,
)
from py_erp_bridge.providers.infor import load_ionapi


def client(provider, server, **kw):
    return AsyncERPClient(provider, config(provider, **kw), http_transport=server)


# -- ERPNext ----------------------------------------------------------------


async def test_erpnext_list_wire_format():
    server = MockServer(ok({"data": []}))
    async with client("erpnext", server) as erp:
        await erp.sales_orders.get_all(
            filters={
                "customer_id": "C",
                "order_date__ge": date(2024, 1, 1),
                "number__contains": "SO",
            },
            sort=["-order_date"],
            select=["customer_id"],
        )
    params = server.last.url.params
    assert json.loads(params["filters"]) == [
        ["customer", "=", "C"],
        ["transaction_date", ">=", "2024-01-01"],
        ["name", "like", "%SO%"],
    ]
    assert json.loads(params["fields"]) == ["name", "customer"]
    assert params["order_by"] == "transaction_date desc"


async def test_erpnext_names_are_escaped_and_lines_mapped():
    doc = {"name": "SO/1", "items": [{"idx": 1, "item_code": "A", "qty": 2.0, "rate": 5.5}]}
    server = MockServer(ok({"data": doc}))
    async with client("erpnext", server) as erp:
        order = await erp.sales_orders.get("SO/1")
    assert server.last.url.raw_path == b"/api/resource/Sales%20Order/SO%2F1"
    assert order.items[0].product_id == "A"
    assert order.items[0].raw["qty"] == 2.0


async def test_erpnext_wildcards_in_filters_are_refused():
    async with client("erpnext", MockServer()) as erp:
        with pytest.raises(UnsupportedOperationError):
            await erp.customers.get_all(filters={"name__contains": "50%"})


async def test_erpnext_submit_upload_and_webhook():
    server = MockServer(
        ok({"data": {"docstatus": 1}}), ok({"message": {"name": "F"}}), ok({"data": {"name": "W"}})
    )
    async with client("erpnext", server) as erp:
        await erp.execute("submit", entity="invoices", key="INV-1")
        assert server.body() == {"docstatus": 1}
        await erp.upload_file(
            FileUpload(filename="a.txt", content=b"hello", entity="invoices", record_id="INV-1")
        )
        assert b"Sales Invoice" in server.last.content
        assert b"hello" in server.last.content
        await erp.register_webhook(
            WebhookRegistration(
                url="https://app.test/h", entity="customers", event="on_update", secret="s"
            )
        )
        assert server.body()["webhook_doctype"] == "Customer"
        assert server.body()["enable_security"] == 1
        with pytest.raises(ConfigurationError):
            await erp.execute("submit", entity="nope", key="1")


async def test_erpnext_errors():
    server = MockServer(ok({"exc_type": "DoesNotExistError"}, 404), ok({"exception": "bad"}, 417))
    async with client("erpnext", server) as erp:
        with pytest.raises(NotFoundError):
            await erp.customers.get("x")
        with pytest.raises(RequestRejectedError, match="bad"):
            await erp.customers.create({"name": "x"})


# -- Odoo -------------------------------------------------------------------


async def test_odoo_json2_search():
    server = MockServer(
        ok([{"id": 7, "name": "Acme", "email": False, "country_id": [233, "US"], "vat": False}])
    )
    async with client("odoo", server, database="db", context={"lang": "en_US"}) as erp:
        page = await erp.customers.list_page(
            filters={"name__contains": "ac", "modified_at__ge": datetime(2024, 1, 1, tzinfo=UTC)},
            page_size=50,
        )
    assert server.last.url.path == "/json/2/res.partner/search_read"
    assert server.last.headers["authorization"] == "Bearer t"
    assert server.last.headers["x-odoo-database"] == "db"
    body = server.body()
    assert body["domain"] == [
        ["customer_rank", ">", 0],
        ["name", "ilike", "ac"],
        ["write_date", ">=", "2024-01-01 00:00:00"],
    ]
    assert body["limit"] == 50
    assert body["context"] == {"lang": "en_US"}
    got = page.items[0]
    assert (got.id, got.email, got.country, got.tax_id) == ("7", None, "233", None)
    assert got.raw["email"] is False


async def test_odoo_create_update_delete():
    server = MockServer(ok([11]), ok(True), ok([{"id": 11}]), ok(True))
    order = {"customer_id": "7", "items": [{"product_id": "3", "quantity": 2}]}
    async with client("odoo", server) as erp:
        assert (await erp.sales_orders.create(order)).id == "11"
        assert server.body() == {
            "vals_list": [
                {"partner_id": 7, "order_line": [[0, 0, {"product_id": 3, "product_uom_qty": 2}]]}
            ]
        }
        await erp.sales_orders.update("11", {"items": [{"product_id": "4"}]})
        assert server.body(1) == {
            "ids": [11],
            "vals": {"order_line": [[5, 0, 0], [0, 0, {"product_id": 4}]]},
        }
        await erp.sales_orders.delete("11")
        assert server.body() == {"ids": [11]}


async def test_odoo_bulk_is_one_call():
    server = MockServer(
        ok([1, 2]), ok({"name": "odoo.exceptions.ValidationError", "message": "x"}, 422)
    )
    async with client("odoo", server) as erp:
        result = await erp.customers.bulk_create([{"name": "a"}, {"name": "b"}])
        assert [i.record.id for i in result.items] == ["1", "2"]
        assert server.body()["vals_list"][0] == {"customer_rank": 1, "name": "a"}
        assert len((await erp.customers.bulk_create([{"name": "a"}, {"name": "b"}])).failed) == 2


async def test_odoo_missing_and_invalid_ids():
    async with client("odoo", MockServer(ok([]))) as erp:
        with pytest.raises(NotFoundError):
            await erp.customers.get("5")
        with pytest.raises(PayloadValidationError):
            await erp.customers.get("abc")


def legacy(server, **kw):
    auth = {"type": "basic", "username": "me", "password": "key"}
    return client("odoo", server, api="jsonrpc", database="db", auth=auth, **kw)


async def test_odoo_jsonrpc():
    server = MockServer(ok({"result": 2}), ok({"result": [{"id": 1}]}), ok({"result": True}))
    async with legacy(server) as erp:
        assert len(await erp.customers.get_all()) == 1
        await erp.customers.delete("1")
    login, search, unlink = (server.body(i)["params"] for i in range(3))
    assert login == {"service": "common", "method": "login", "args": ["db", "me", "key"]}
    assert search["args"][:6] == ["db", 2, "key", "res.partner", "search_read", []]
    assert unlink["args"][4:] == ["unlink", [[1]], {}]
    assert "authorization" not in server.last.headers


async def test_odoo_jsonrpc_failures():
    error = {"error": {"data": {"name": "odoo.exceptions.MissingError", "message": "gone"}}}
    async with legacy(MockServer(ok({"result": False}))) as erp:
        with pytest.raises(AuthenticationError):
            await erp.customers.get_all()
    async with legacy(MockServer(ok({"result": 2}), ok(error))) as erp:
        with pytest.raises(NotFoundError, match="gone"):
            await erp.customers.get_all()


def test_odoo_auth_must_fit_the_api():
    with pytest.raises(ConfigurationError):
        client("odoo", MockServer(), api="jsonrpc")
    with pytest.raises(ConfigurationError):
        client("odoo", MockServer(), auth={"type": "basic", "username": "u", "password": "p"})


async def test_odoo_attachment():
    server = MockServer(ok([9]))
    async with client("odoo", server) as erp:
        upload = FileUpload(filename="a", content=b"x", entity="customers", record_id="3")
        assert await erp.upload_file(upload) == {"id": 9}
    values = server.body()["vals_list"][0]
    assert (values["res_model"], values["res_id"], values["datas"]) == ("res.partner", 3, "eA==")


# -- NetSuite ---------------------------------------------------------------


async def test_netsuite_list_expands_and_skips_deleted():
    def handler(request):
        if request.url.path.endswith("/customer"):
            stubs = [{"id": "1"}, {"id": "2"}]
            return ok({"items": stubs, "hasMore": False, "totalResults": 2})
        if request.url.path.endswith("/2"):
            return ok({"o:errorDetails": [{"o:errorCode": "NONEXISTENT_ID"}]}, 404)
        return ok({"id": "1", "companyName": "Acme"})

    async with client("netsuite", MockServer(handler=handler)) as erp:
        assert [c.name for c in await erp.customers.get_all()] == ["Acme"]


async def test_netsuite_offsets_are_multiples_of_the_limit():
    server = MockServer(
        ok({"items": [{"id": "1"}], "hasMore": True}), ok({"items": [], "hasMore": False})
    )
    async with client("netsuite", server, expand_lists=False) as erp:
        await erp.customers.get_all(page_size=10)
    assert server.last.url.params["offset"] == "10"


async def test_netsuite_filters():
    server = MockServer(ok({"items": []}))
    async with client("netsuite", server) as erp:
        await erp.invoices.get_all(
            filters={
                "customer_id": "5",
                "total__gt": 10,
                "invoice_date__ge": date(2024, 1, 31),
                "number__contains": "INV 1",
                "status": ["A", "B"],
            }
        )
        assert server.last.url.params["q"] == (
            'entity ANY_OF [5] AND total GREATER 10 AND tranDate ON_OR_AFTER "01/31/2024" '
            'AND tranId CONTAIN "INV 1" AND (status.refName IS "A" OR status.refName IS "B")'
        )
        for bad in ({"number": 'a"b'}, {"customer_id": "x] OR"}, {"number__gt": "a"}):
            with pytest.raises(UnsupportedOperationError):
                await erp.invoices.get_all(filters=bad)
        with pytest.raises(UnsupportedOperationError):
            await erp.invoices.get_all(sort=["number"])


async def test_netsuite_create_reads_location():
    where = "https://erp.test/services/rest/record/v1/salesOrder/647"
    server = MockServer(httpx.Response(204, headers={"Location": where}), httpx.Response(204))
    async with client("netsuite", server) as erp:
        order = await erp.sales_orders.create(
            {"customer_id": "5", "items": [{"product_id": "3", "quantity": 1}]}
        )
        assert order.id == "647"
        assert server.body() == {
            "entity": {"id": "5"},
            "item": {"items": [{"item": {"id": "3"}, "quantity": 1}]},
        }
        with pytest.raises(APIError, match="Location"):
            await erp.sales_orders.create({"customer_id": "5"})


async def test_netsuite_suiteql_and_errors():
    server = MockServer(
        ok({"items": [{"id": "1"}], "hasMore": False}),
        ok({"title": "Bad", "o:errorDetails": [{"detail": "d1", "o:errorCode": "INVALID"}]}, 400),
    )
    async with client("netsuite", server) as erp:
        await erp.execute("suiteql", query="SELECT id FROM customer", limit=5)
        assert server.last.headers["prefer"] == "transient"
        assert server.body() == {"q": "SELECT id FROM customer"}
        with pytest.raises(RequestRejectedError, match="d1") as info:
            await erp.customers.get("1")
        assert info.value.code == "INVALID"


async def test_netsuite_update_reads_back():
    server = MockServer(httpx.Response(204), ok({"id": "1", "companyName": "N"}))
    async with client("netsuite", server) as erp:
        assert (await erp.customers.update("1", {"name": "N"})).name == "N"
    assert [r.method for r in server.requests] == ["PATCH", "GET"]


# -- Oracle Fusion ----------------------------------------------------------


def fusion_row(identifier):
    href = f"https://erp.test/fscmRestApi/resources/11.13.18.05/suppliers/{identifier}"
    return {"SupplierId": 3, "Supplier": "Acme", "links": [{"rel": "self", "href": href}]}


async def test_fusion_list():
    server = MockServer(ok({"items": [fusion_row("00AB%20C")], "hasMore": True}), ok({"items": []}))
    async with client("oracle_fusion", server) as erp:
        got = await erp.vendors.get_all(
            filters={"name__startswith": "Ac", "tax_id": ["1", "2"]}, sort=["-name"]
        )
    assert got[0].id == "00AB C"
    first = server.requests[0].url
    assert first.path == "/fscmRestApi/resources/11.13.18.05/suppliers"
    assert first.params["q"] == "Supplier LIKE 'Ac%';TaxpayerId in ('1','2')"
    assert first.params["orderBy"] == "Supplier:desc"
    assert server.last.url.params["offset"] == "1"


async def test_fusion_default_sort_and_page_cap():
    server = MockServer(ok({"items": []}))
    async with client("oracle_fusion", server) as erp:
        await erp.vendors.get_all(page_size=5000)
    assert server.last.url.params["limit"] == "500"
    assert server.last.url.params["orderBy"] == "SupplierId"


async def test_fusion_rejects_injection():
    async with client("oracle_fusion", MockServer()) as erp:
        for value in ("a';b", "x;y", 'q"'):
            with pytest.raises(UnsupportedOperationError):
                await erp.vendors.get_all(filters={"name": value})


async def test_fusion_writes():
    server = MockServer(ok(fusion_row("5"), 201), ok(fusion_row("5")))
    async with client("oracle_fusion", server) as erp:
        assert (await erp.vendors.create({"name": "Acme"})).id == "5"
        assert server.last.headers["content-type"] == "application/vnd.oracle.adf.resourceitem+json"
        await erp.vendors.update("5", {"name": "New"}, etag="abc")
        assert server.last.headers["if-match"] == "abc"
        with pytest.raises(UnsupportedOperationError):
            await erp.vendors.delete("5")


async def test_fusion_plain_text_errors_and_describe():
    server = MockServer(httpx.Response(400, text="bad attribute"), ok({"Resources": {}}))
    async with client("oracle_fusion", server) as erp:
        with pytest.raises(RequestRejectedError, match="bad attribute"):
            await erp.vendors.get("1")
        assert await erp.execute("describe", entity="vendors") == {"Resources": {}}


# -- Infor ------------------------------------------------------------------


def m3(*results):
    return ok({"results": list(results)})


async def test_infor_list_and_get():
    server = MockServer(
        m3({"records": [{"CUNO": "C1  ", "CUNM": "Acme "}]}), m3({"records": [{"CUNM": "Acme"}]})
    )
    async with client("infor", server, cono=100) as erp:
        got = await erp.customers.get_all(filters={"name": "Acme"})
        assert (got[0].id, got[0].name) == ("C1", "Acme")
        url = server.last.url
        assert url.path == "/T_TST/M3/m3api-rest/v2/execute/CRS610MI/LstByNumber"
        assert (url.params["CUNM"], url.params["cono"]) == ("Acme", "100")
        assert (await erp.customers.get("C1")).id == "C1"


async def test_infor_never_truncates_silently():
    rows = [{"CUNO": str(i)} for i in range(3)]
    server = MockServer(m3({"records": rows}))
    async with client("infor", server, max_records=2) as erp:
        with pytest.raises(BusinessRuleError, match="more than 2"):
            await erp.customers.get_all()
    assert server.last.url.params["maxrecs"] == "3"


async def test_infor_errors_and_bulk():
    server = MockServer(
        m3({"errorMessage": "Customer X does not exist"}),
        m3({"records": []}, {"errorMessage": "Name missing", "errorCode": "E"}),
        m3({"records": []}),
    )
    async with client("infor", server) as erp:
        with pytest.raises(NotFoundError):
            await erp.customers.get("X")
        result = await erp.customers.bulk_create([{"name": "a"}, {"name": "b"}])
        assert [i.ok for i in result.items] == [True, False]
        assert server.body()["transactions"][1] == {"transaction": "Add", "record": {"CUNM": "b"}}
        # One result for two records: the outcome is unknown, so both are failures.
        result = await erp.customers.bulk_create([{"name": "a"}, {"name": "b"}])
        assert len(result.failed) == 2


async def test_infor_custom_mi_is_only_retried_when_read_only():
    server = MockServer(ok({}, 503))
    async with client("infor", server) as erp:
        with pytest.raises(APIError):
            await erp.execute("mi", program="OIS100MI", transaction="AddX", record={"A": "1"})
    assert len(server.requests) == 1


def test_infor_ionapi(tmp_path):
    path = tmp_path / "x.ionapi"
    data = {
        "ti": "T",
        "ci": "T~c",
        "cs": "s",
        "iu": "https://ion.test",
        "ot": "token.oauth2",
        "pu": "https://sso.test:443/T/as/",
        "saak": "a",
        "sask": "b",
    }
    path.write_text(json.dumps(data))
    loaded = load_ionapi(path)
    assert loaded["auth"]["token_url"] == "https://sso.test:443/T/as/token.oauth2"
    AsyncERPClient("infor", loaded)
    path.write_text(json.dumps({**data, "saak": ""}))
    with pytest.raises(ConfigurationError, match="saak"):
        load_ionapi(path)


# -- Sage Intacct -----------------------------------------------------------


async def test_intacct_query():
    row = {"key": "11", "id": "V1", "name": "Acme"}
    server = MockServer(
        ok({"ia::result": [row], "ia::meta": {"totalCount": 2, "next": 2}}),
        ok({"ia::result": [{"key": "12"}], "ia::meta": {"totalCount": 2, "next": None}}),
    )
    async with client("sage_intacct", server, entity_id="100") as erp:
        got = await erp.vendors.get_all(filters={"name__contains": "Ac"}, page_size=1)
    assert [v.id for v in got] == ["11", "12"]
    body = server.body(0)
    assert server.requests[0].url.path == "/ia/api/v1/services/core/query"
    assert body["object"] == "accounts-payable/vendor"
    assert body["filters"] == [{"$contains": {"name": "Ac"}}]
    assert (body["start"], body["size"], body["orderBy"]) == (1, 1, [{"key": "asc"}])
    assert server.body(1)["start"] == 2
    assert server.last.headers["x-ia-api-param-entity"] == "100"


async def test_intacct_crud_and_errors():
    server = MockServer(
        ok({"ia::result": {"key": "5", "id": "C5"}}, 201),
        ok({"ia::result": {"key": "5"}}),
        ok({"ia::result": {"key": "5", "name": "N"}}),
        httpx.Response(204),
        ok({"ia::result": {"ia::error": {"code": "invalid", "message": "bad"}}}, 400),
    )
    async with client("sage_intacct", server) as erp:
        assert (await erp.customers.create({"name": "N"})).id == "5"
        assert (await erp.customers.update("5", {"name": "N"})).name == "N"
        await erp.customers.delete("5")
        with pytest.raises(RequestRejectedError, match="bad") as info:
            await erp.customers.get("5")
        assert info.value.code == "invalid"
