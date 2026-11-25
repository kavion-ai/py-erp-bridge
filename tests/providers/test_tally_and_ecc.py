from __future__ import annotations

from datetime import date
from decimal import Decimal

import httpx
import pytest

from helpers import MockServer, config
from py_erp_bridge import AsyncERPClient
from py_erp_bridge.exceptions import (
    BusinessRuleError,
    ConfigurationError,
    NotFoundError,
    PayloadValidationError,
    UnsupportedOperationError,
)
from py_erp_bridge.transport import rfc
from py_erp_bridge.transport import xml as xmllib


def tally(server):
    return AsyncERPClient("tally", config("tally"), http_transport=server)


def collection(inner):
    return httpx.Response(
        200,
        text="<ENVELOPE><HEADER><STATUS>1</STATUS></HEADER><BODY><DATA><COLLECTION>"
        f"{inner}</COLLECTION></DATA></BODY></ENVELOPE>",
    )


LEDGERS = (
    '<LEDGER NAME="A &amp; B"><EMAIL TYPE="String">a@b.test</EMAIL><X>&#4;</X></LEDGER>'
    '<LEDGER NAME="Other"><EMAIL/></LEDGER>'
)


def imported(**counts):
    inner = "".join(f"<{k}>{v}</{k}>" for k, v in counts.items())
    return httpx.Response(200, text=f"<RESPONSE>{inner}</RESPONSE>")


async def test_tally_list_request_and_local_filter():
    server = MockServer(collection(LEDGERS), collection(LEDGERS))
    async with tally(server) as erp:
        got = await erp.customers.get_all(filters={"name__contains": "a &"})
        assert [(c.id, c.email) for c in got] == [("A & B", "a@b.test")]
        assert (await erp.customers.get("A & B")).name == "A & B"
    root = xmllib.parse(server.requests[0].content.decode())
    assert root.findtext("HEADER/TYPE") == "Collection"
    assert root.findtext(".//SVCURRENTCOMPANY") == "Acme & Co"
    node = root.find(".//COLLECTION")
    assert node.findtext("CHILDOF") == "Sundry Debtors"
    assert "EMAIL" in [m.text for m in node.findall("NATIVEMETHOD")]


async def test_tally_missing_record():
    async with tally(MockServer(collection(LEDGERS))) as erp:
        with pytest.raises(NotFoundError):
            await erp.customers.get("nope")


async def test_tally_vouchers_and_quantities():
    voucher = (
        "<VOUCHER><MASTERID> 12 </MASTERID><DATE>20240131</DATE><AMOUNT>-1180.00</AMOUNT></VOUCHER>"
    )
    stock = '<STOCKITEM NAME="Bolt"><CLOSINGBALANCE> 1,200 Nos</CLOSINGBALANCE></STOCKITEM>'
    server = MockServer(collection(voucher), collection(stock))
    async with tally(server) as erp:
        invoice = (
            await erp.invoices.get_all(params={"from_date": "20240101", "to_date": "20241231"})
        )[0]
        assert (invoice.id, invoice.invoice_date) == ("12", date(2024, 1, 31))
        assert invoice.total == Decimal("-1180.00")
        assert b"<SVFROMDATE>20240101</SVFROMDATE>" in server.last.content
        assert (await erp.inventory.get_all())[0].quantity == 1200
        with pytest.raises(PayloadValidationError):
            await erp.invoices.get_all(params={"from_date": "2024-01-01"})
        with pytest.raises(PayloadValidationError):
            await erp.invoices.get_all(params={"x": "1"})


async def test_tally_create_and_update():
    server = MockServer(imported(CREATED=1, ERRORS=0), imported(ALTERED=1), collection(LEDGERS))
    async with tally(server) as erp:
        created = await erp.customers.create({"name": "A & B", "email": "x@y.test"})
        assert created.id == "A & B"
        ledger = xmllib.parse(server.last.content.decode()).find(".//LEDGER")
        assert ledger.attrib == {"NAME": "A & B", "ACTION": "Create"}
        assert ledger.findtext("PARENT") == "Sundry Debtors"
        await erp.customers.update("A & B", {"email": "n@y.test"})
        assert b'ACTION="Alter"' in server.requests[1].content
        with pytest.raises(UnsupportedOperationError, match="renaming"):
            await erp.customers.update("A & B", {"name": "C"})


@pytest.mark.parametrize(
    "response",
    [
        imported(CREATED=0, LINEERROR="Ledger exists"),
        imported(CREATED=0, ERRORS=1),
        imported(CREATED=0, EXCEPTIONS=1),
        imported(CREATED=0),
        httpx.Response(200, text="<RESPONSE>Unknown Request, cannot be processed</RESPONSE>"),
        httpx.Response(
            200,
            text="<ENVELOPE><HEADER><STATUS>0</STATUS></HEADER><BODY><DATA>DESC not found</DATA>"
            "</BODY></ENVELOPE>",
        ),
    ],
)
async def test_tally_failures_inside_http_200(response):
    async with tally(MockServer(response)) as erp:
        with pytest.raises(BusinessRuleError):
            await erp.customers.create({"name": "x"})


async def test_tally_voucher_import():
    server = MockServer(imported(CREATED=1, LASTVCHID=77))
    entries = [
        {"LEDGERNAME": "Cash", "ISDEEMEDPOSITIVE": True, "AMOUNT": "-100"},
        {"LEDGERNAME": "Sales", "ISDEEMEDPOSITIVE": False, "AMOUNT": "100"},
    ]
    async with tally(server) as erp:
        args = {"voucher_type": "Receipt", "date": "20240131"}
        result = await erp.execute(
            "import_voucher", fields={"ALLLEDGERENTRIES.LIST": entries}, **args
        )
        assert result == {"LASTVCHID": "77"}
        node = xmllib.parse(server.last.content.decode()).find(".//VOUCHER")
        assert len(node.findall("ALLLEDGERENTRIES.LIST")) == 2
        assert node.findtext("ALLLEDGERENTRIES.LIST/ISDEEMEDPOSITIVE") == "Yes"
        with pytest.raises(PayloadValidationError, match="balance"):
            await erp.execute(
                "import_voucher", fields={"ALLLEDGERENTRIES.LIST": entries[:1]}, **args
            )
        with pytest.raises(PayloadValidationError, match="YYYYMMDD"):
            await erp.execute("import_voucher", voucher_type="X", date="1", fields={})
    assert len(server.requests) == 1


async def test_tally_import_is_not_retried():
    server = MockServer(httpx.Response(503))
    async with tally(server) as erp:
        with pytest.raises(Exception, match="503"):
            await erp.customers.create({"name": "x"})
    assert len(server.requests) == 1


# -- SAP ECC ----------------------------------------------------------------


class Sap:
    def __init__(self, **results):
        self.results = results
        self.calls = []

    def call(self, func_name, **params):
        self.calls.append((func_name, params))
        result = self.results.get(func_name, {})
        return result(params) if callable(result) else result

    def ping(self):
        self.calls.append(("ping", {}))

    def close(self):
        self.calls.append(("close", {}))

    def names(self):
        return [c[0] for c in self.calls]


@pytest.fixture
def connect(monkeypatch):
    def use(**results):
        sap = Sap(**results)
        monkeypatch.setattr(rfc, "_factory", lambda params: sap)
        return sap

    return use


def table(*rows):
    def read(params):
        names = [f["FIELDNAME"] for f in params["FIELDS"]]
        data = [
            {"WA": params["DELIMITER"].join(str(r.get(n, "")).ljust(10) for n in names)}
            for r in rows
        ]
        return {"FIELDS": params["FIELDS"], "DATA": data[: params["ROWCOUNT"]]}

    return read


def ecc(**kw):
    return AsyncERPClient("sap_ecc", config("sap_ecc", **kw))


async def test_ecc_read_table(connect):
    sap = connect(RFC_READ_TABLE=table({"KUNNR": "0000001001", "NAME1": "Acme|Co"}))
    async with ecc() as erp:
        got = await erp.customers.get_all(
            filters={"id": "1001", "name__startswith": "O'", "country": ["DE", "FR"]}
        )
        assert (got[0].id, got[0].name) == ("1001", "Acme|Co")
        assert (await erp.customers.get("1001")).id == "1001"
        assert (await erp.test_connection()).healthy
    params = sap.calls[0][1]
    assert [o["TEXT"] for o in params["OPTIONS"]] == [
        "KUNNR = '0000001001'",
        "AND NAME1 LIKE 'O''%'",
        "AND ( LAND1 = 'DE'",
        "OR LAND1 = 'FR' )",
    ]
    assert params["QUERY_TABLE"] == "KNA1"
    assert "close" in sap.names()


async def test_ecc_paging_and_dates(connect):
    sap = connect(
        RFC_READ_TABLE=lambda p: (
            table({"VBELN": "1", "ERDAT": "20240131", "NETWR": "10.00"})(p)
            if p["ROWSKIPS"] == 0
            else {"FIELDS": [], "DATA": []}
        )
    )
    async with ecc() as erp:
        orders = await erp.sales_orders.get_all(
            page_size=1, filters={"order_date__ge": date(2024, 1, 1)}
        )
    assert orders[0].order_date == date(2024, 1, 31)
    assert sap.calls[0][1]["OPTIONS"] == [{"TEXT": "ERDAT >= '20240101'"}]
    assert sap.calls[1][1]["ROWSKIPS"] == 1


async def test_ecc_refuses_bad_filters_and_sorting(connect):
    connect()
    async with ecc() as erp:
        with pytest.raises(UnsupportedOperationError):
            await erp.customers.get_all(sort=["name"])
        with pytest.raises(UnsupportedOperationError):
            await erp.customers.get_all(filters={"name__contains": "50%"})
        with pytest.raises(ConfigurationError, match="72"):
            await erp.customers.get_all(filters={"name": "x" * 80})


async def test_ecc_not_found(connect):
    connect(RFC_READ_TABLE=table())
    async with ecc() as erp:
        with pytest.raises(NotFoundError):
            await erp.customers.get("1")


async def test_ecc_create_commits(connect):
    sap = connect(
        BAPI_PO_CREATE1={"EXPPURCHASEORDER": "4500000001", "RETURN": [{"TYPE": "S"}]},
        BAPI_TRANSACTION_COMMIT={"RETURN": {"TYPE": ""}},
    )
    async with ecc() as erp:
        order = await erp.purchase_orders.create({"provider_fields": {"POHEADER": {"VENDOR": "1"}}})
    assert order.id == "4500000001"
    assert sap.names()[:2] == ["BAPI_PO_CREATE1", "BAPI_TRANSACTION_COMMIT"]


async def test_ecc_business_error_rolls_back(connect):
    sap = connect(
        BAPI_PO_CREATE1={
            "RETURN": [{"TYPE": "E", "ID": "06", "NUMBER": "001", "MESSAGE": "No vendor"}]
        }
    )
    async with ecc() as erp:
        with pytest.raises(BusinessRuleError, match="No vendor") as info:
            await erp.purchase_orders.create({"provider_fields": {"POHEADER": {}}})
    assert info.value.code == "06001"
    assert sap.names()[:2] == ["BAPI_PO_CREATE1", "BAPI_TRANSACTION_ROLLBACK"]


async def test_ecc_does_not_pretend_to_translate_creates(connect):
    sap = connect()
    async with ecc() as erp:
        with pytest.raises(ConfigurationError, match="provider_fields"):
            await erp.purchase_orders.create({"vendor_id": "1"})
    assert sap.calls == []


async def test_ecc_raw_call(connect):
    connect(STFC_CONNECTION={"ECHOTEXT": "hi"})
    async with ecc() as erp:
        assert await erp.execute("call", function="STFC_CONNECTION", params={"REQUTEXT": "hi"}) == {
            "ECHOTEXT": "hi"
        }
