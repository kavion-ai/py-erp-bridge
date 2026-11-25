from __future__ import annotations

from datetime import date

import httpx
import pytest

from helpers import MockServer, config, ok
from py_erp_bridge import AsyncERPClient
from py_erp_bridge.exceptions import (
    BusinessRuleError,
    PayloadValidationError,
    PermissionDeniedError,
    UnsupportedOperationError,
)

TOKEN = {"x-csrf-token": "tok", "set-cookie": "SAP_SESSION=1; path=/"}


def s4(server, **kw):
    return AsyncERPClient("sap_s4hana", config("sap_s4hana", **kw), http_transport=server)


def d365(server, **kw):
    return AsyncERPClient("dynamics365", config("dynamics365", **kw), http_transport=server)


# -- S/4HANA ----------------------------------------------------------------


async def test_s4_list_request_and_mapping():
    order = {
        "SalesOrder": "1",
        "SoldToParty": "1001",
        "SalesOrderDate": "/Date(1706659200000)/",
        "TotalNetAmount": "10.50",
        "to_Item": {"results": [{"SalesOrderItem": "10", "RequestedQuantity": "2"}]},
    }
    server = MockServer(ok({"d": {"results": [order]}}))
    async with s4(server, sap_client="100") as erp:
        page = await erp.sales_orders.list_page(
            filters={"customer_id": "O'B", "order_date__ge": date(2024, 1, 1)},
            sort=["-order_date"],
            page_size=50,
        )
    assert server.last.url.path == "/sap/opu/odata/sap/API_SALES_ORDER_SRV/A_SalesOrder"
    params = server.last.url.params
    assert params["$filter"] == (
        "SoldToParty eq 'O''B' and SalesOrderDate ge datetime'2024-01-01T00:00:00'"
    )
    assert params["$orderby"] == "SalesOrderDate desc"
    assert (params["$top"], params["$expand"], params["sap-client"]) == ("50", "to_Item", "100")
    got = page.items[0]
    assert (got.id, got.customer_id, got.order_date) == ("1", "1001", date(2024, 1, 31))
    assert got.items[0].line_number == "10"
    assert page.next_cursor is None


async def test_s4_create_fetches_csrf_token_once_and_keeps_session():
    created = ok({"d": {"SalesOrder": "9"}}, 201)
    server = MockServer(ok({}, headers=TOKEN), created, ok({"d": {"SalesOrder": "10"}}, 201))
    data = {
        "customer_id": "1001",
        "order_date": date(2024, 1, 31),
        "items": [{"product_id": "M1", "quantity": "2"}],
        "provider_fields": {"SalesOrderType": "OR"},
    }
    async with s4(server) as erp:
        assert (await erp.sales_orders.create(data)).id == "9"
        await erp.sales_orders.create(data)
    assert server.requests[0].headers["x-csrf-token"] == "Fetch"
    assert server.requests[1].headers["x-csrf-token"] == "tok"
    assert "SAP_SESSION=1" in server.requests[1].headers["cookie"]
    assert len(server.requests) == 3
    assert server.body(1) == {
        "SoldToParty": "1001",
        "SalesOrderDate": "/Date(1706659200000)/",
        "to_Item": [{"Material": "M1", "RequestedQuantity": "2"}],
        "SalesOrderType": "OR",
    }


async def test_s4_renews_expired_csrf_token():
    server = MockServer(
        ok({}, headers={"x-csrf-token": "old"}),
        httpx.Response(403, text="CSRF token validation failed"),
        ok({}, headers={"x-csrf-token": "new"}),
        httpx.Response(204),
    )
    async with s4(server) as erp:
        await erp.sales_orders.delete("1")
    assert server.last.headers["x-csrf-token"] == "new"
    assert server.last.headers["if-match"] == "*"


async def test_s4_real_403_is_not_retried():
    server = MockServer(ok({}, headers=TOKEN), ok({"error": {"message": {"value": "no"}}}, 403))
    async with s4(server) as erp:
        with pytest.raises(PermissionDeniedError, match="no"):
            await erp.sales_orders.delete("1")


async def test_s4_update_reads_back_and_sends_etag():
    server = MockServer(
        ok({}, headers=TOKEN), httpx.Response(204), ok({"d": {"PurchaseOrder": "4"}})
    )
    async with s4(server) as erp:
        got = await erp.purchase_orders.update("4", {"currency": "EUR"}, etag='W/"1"')
    assert got.id == "4"
    assert server.requests[1].headers["if-match"] == 'W/"1"'
    assert server.requests[1].method == "PATCH"
    assert server.requests[1].url.path.endswith("A_PurchaseOrder('4')")


async def test_s4_follows_next_link_on_own_origin():
    link = "https://internal.sap:44300/sap/opu/odata/sap/X/Y?$skiptoken=2"
    server = MockServer(
        ok({"d": {"results": [{"Customer": "1"}], "__next": link}}),
        ok({"d": {"results": [{"Customer": "2"}]}}),
    )
    async with s4(server) as erp:
        assert [c.id for c in await erp.customers.get_all()] == ["1", "2"]
    assert server.last.url.host == "erp.test"
    assert server.last.url.params["$skiptoken"] == "2"


async def test_s4_composite_key():
    server = MockServer(ok({"d": {"Ledger": "0L"}}))
    key = {
        "Ledger": "0L",
        "CompanyCode": "1010",
        "FiscalYear": "2024",
        "AccountingDocument": "1",
        "LedgerGLLineItem": "000001",
    }
    async with s4(server) as erp:
        await erp.financial_records.get(key)
        with pytest.raises(PayloadValidationError):
            await erp.financial_records.get("0L/1010")
        with pytest.raises(PayloadValidationError):
            await erp.financial_records.get({"Ledger": "0L"})
    assert "(Ledger='0L',CompanyCode='1010'" in server.last.url.path


async def test_s4_customers_cannot_be_created_directly():
    async with s4(MockServer()) as erp:
        with pytest.raises(UnsupportedOperationError, match="A_BusinessPartner"):
            await erp.customers.create({"name": "x"})


def batch(*groups):
    out = ""
    for i, (status, body) in enumerate(groups):
        part = (
            "Content-Type: application/http\r\nContent-Transfer-Encoding: binary\r\n\r\n"
            f"HTTP/1.1 {status} X\r\nContent-Type: application/json\r\n\r\n{body}\r\n"
        )
        if status < 300:
            part = (
                f"Content-Type: multipart/mixed; boundary=c{i}\r\n\r\n--c{i}\r\n{part}--c{i}--\r\n"
            )
        out += f"--b\r\n{part}"
    return httpx.Response(
        202,
        content=(out + "--b--\r\n").encode(),
        headers={"Content-Type": "multipart/mixed; boundary=b"},
    )


async def test_s4_bulk_create_uses_batch():
    server = MockServer(
        ok({}, headers=TOKEN),
        batch((201, '{"d":{"Product":"A"}}'), (400, '{"error":{"code":"E1","message":"bad"}}')),
    )
    async with s4(server) as erp:
        result = await erp.products.bulk_create([{"sku": "A"}, {"sku": "B"}])
    assert server.last.url.path.endswith("/API_PRODUCT_SRV/$batch")
    assert b"POST A_Product HTTP/1.1" in server.last.content
    assert result.items[0].record.id == "A"
    assert isinstance(result.items[1].error, BusinessRuleError)
    assert result.items[1].error.code == "E1"


async def test_s4_bulk_reports_mismatching_batch_as_failure():
    server = MockServer(ok({}, headers=TOKEN), batch((201, '{"d":{"Product":"A"}}')))
    async with s4(server) as erp:
        result = await erp.products.bulk_create([{"sku": "A"}, {"sku": "B"}])
    assert len(result.failed) == 2


# -- Dynamics 365 -----------------------------------------------------------


async def test_d365_list_is_limited_to_the_company():
    server = MockServer(ok({"value": [{"dataAreaId": "usmf", "CustomerAccount": "US-1"}]}))
    async with d365(server) as erp:
        got = await erp.customers.get_all(filters={"name__contains": "tail"})
    params = server.last.url.params
    assert params["$filter"] == "dataAreaId eq 'usmf' and OrganizationName eq '*tail*'"
    assert params["cross-company"] == "true"
    assert got[0].id == "usmf/US-1"


async def test_d365_cross_company_has_no_company_filter():
    server = MockServer(ok({"value": []}))
    async with d365(server, cross_company=True) as erp:
        await erp.customers.get_all()
    assert "$filter" not in server.last.url.params


async def test_d365_keys_and_creates_use_the_company():
    record = {"dataAreaId": "usmf", "CustomerAccount": "US-1"}
    server = MockServer(ok(record), ok(record), ok(record, 201))
    async with d365(server) as erp:
        await erp.customers.get("US-1")
        assert server.last.url.path == "/data/CustomersV3(dataAreaId='usmf',CustomerAccount='US-1')"
        await erp.customers.get("demf/DE-1")
        assert "dataAreaId='demf'" in server.last.url.path
        await erp.customers.create({"name": "Acme"})
    assert server.body() == {"dataAreaId": "usmf", "OrganizationName": "Acme"}


async def test_d365_rejects_what_it_cannot_filter():
    async with d365(MockServer()) as erp:
        with pytest.raises(UnsupportedOperationError):
            await erp.customers.get_all(filters={"name__contains": "a*b"})
        with pytest.raises(UnsupportedOperationError):
            await erp.customers.get_all(filters={"name": []})


async def test_d365_dates_and_offset_paging():
    rows = [{"dataAreaId": "usmf", "InvoiceNumber": str(i)} for i in range(2)]
    server = MockServer(ok({"value": rows}), ok({"value": []}))
    async with d365(server) as erp:
        await erp.invoices.get_all(filters={"invoice_date__ge": date(2024, 1, 1)}, page_size=2)
    assert "InvoiceDate ge 2024-01-01T00:00:00Z" in server.requests[0].url.params["$filter"]
    assert server.last.url.params["$skip"] == "2"


async def test_d365_next_link_is_followed_to_the_end():
    server = MockServer(
        ok(
            {
                "value": [{"CustomerAccount": "1"}] * 2,
                "@odata.nextLink": "/data/CustomersV3?$skip=2",
            }
        ),
        ok({"value": [{"CustomerAccount": "3"}] * 2}),
    )
    async with d365(server) as erp:
        assert len(await erp.customers.get_all(page_size=2)) == 4


async def test_d365_lines_are_refused():
    async with d365(MockServer()) as erp:
        with pytest.raises(Exception, match="cannot write items"):
            await erp.sales_orders.create({"customer_id": "1", "items": [{"product_id": "A"}]})


async def test_d365_atomic_create():
    server = MockServer(
        batch((201, '{"CustomerAccount":"1"}')), batch((400, '{"error":{"message":"x"}}'))
    )
    async with d365(server) as erp:
        got = await erp.execute("create_atomic", entity="customers", payloads=[{"a": 1}])
        assert got == [{"CustomerAccount": "1"}]
        with pytest.raises(BusinessRuleError):
            await erp.execute("create_atomic", entity="customers", payloads=[{}, {}])


async def test_d365_metadata():
    server = MockServer(ok([{"Name": "CustomerV3"}]))
    async with d365(server) as erp:
        assert await erp.execute("list_entities") == [{"Name": "CustomerV3"}]
