"""SAP ECC (RFC): connect, read, page, filter, handle errors, write, go async.

Settings come from ECC_* environment variables (see .env.example). Reading is the
default; writes run only with ERP_EXAMPLE_WRITE=1. Use a sandbox system.
"""

from __future__ import annotations

import asyncio
import os

from py_erp_bridge import AsyncERPClient, ERPClient, config_from_env
from py_erp_bridge.exceptions import (
    APIError,
    AuthenticationError,
    NotFoundError,
    RateLimitError,
    TransportError,
    UnsupportedOperationError,
)
from py_erp_bridge.types import JsonObject

PROVIDER = "sap_ecc"


def load_config() -> JsonObject:
    """Read settings and tune the retry policy."""
    config = config_from_env("ECC")
    config.setdefault("retry", {"max_attempts": 5, "base_delay": 0.5, "max_delay": 20})
    return config


def read(erp: ERPClient) -> None:
    """Fetch customers and invoices."""
    for customer in erp.customers.iterate(limit=5):
        print("customer", customer.id, customer.name)
    for invoice in erp.invoices.iterate(limit=5):
        print("invoice", invoice.number, invoice.invoice_date, invoice.total)


def page_and_filter(erp: ERPClient) -> None:
    """Fetch single pages and filter."""
    page = erp.customers.list_page(page_size=10)
    print("first page:", len(page.items), "next cursor:", page.next_cursor)
    matching = erp.customers.get_all(filters={"name__contains": "a"}, limit=10)
    print("matching:", len(matching))


def errors(erp: ERPClient) -> None:
    """Handle the standard exceptions."""
    try:
        erp.customers.get("9999999999")
    except NotFoundError:
        print("no such customer")
    except RateLimitError as exc:
        print("slow down for", exc.retry_after, "seconds")
    except AuthenticationError as exc:
        print("check the credentials:", exc)
    except APIError as exc:
        print("rejected:", exc.status_code, exc.code, "request", exc.request_id)
    except TransportError as exc:
        print("network problem:", exc, "sent:", exc.request_sent)


def specific(erp: ERPClient) -> None:
    """Use what only this provider offers."""
    print(erp.execute("call", function="STFC_CONNECTION", params={"REQUTEXT": "hello"}))


def write(erp: ERPClient) -> None:
    """Create, update, delete and bulk-create."""
    # BAPI parameters are passed through; commit and rollback are automatic.
    order = erp.sales_orders.create(
        {
            "provider_fields": {
                "ORDER_HEADER_IN": {
                    "DOC_TYPE": "TA",
                    "SALES_ORG": "1000",
                    "DISTR_CHAN": "10",
                    "DIVISION": "00",
                },
                "ORDER_ITEMS_IN": [{"ITM_NUMBER": "000010", "MATERIAL": "TG11", "TARGET_QTY": "1"}],
                "ORDER_PARTNERS": [{"PARTN_ROLE": "AG", "PARTN_NUMB": "0000001001"}],
            }
        }
    )
    print("created", order.id)
    # Other BAPIs, e.g. to change an order:
    erp.execute(
        "call_bapi",
        function="BAPI_SALESORDER_CHANGE",
        params={"SALESDOCUMENT": order.id, "ORDER_HEADER_INX": {"UPDATEFLAG": "D"}},
    )


async def read_async() -> None:
    """The same API, awaited."""
    async with ERPClient.create_async(PROVIDER, load_config()) as erp:
        await count(erp)


async def count(erp: AsyncERPClient) -> None:
    """Stream records without holding them in memory."""
    total = 0
    async for _ in erp.customers.iterate(limit=200):
        total += 1
    print("async count:", total)


def main() -> None:
    """Run the example."""
    with ERPClient(PROVIDER, load_config()) as erp:
        status = erp.test_connection()
        print("healthy:", status.healthy, status.detail)
        if not status.healthy:
            return
        read(erp)
        page_and_filter(erp)
        errors(erp)
        specific(erp)
        if os.environ.get("ERP_EXAMPLE_WRITE") == "1":
            try:
                write(erp)
            except UnsupportedOperationError as exc:
                print("not supported:", exc)
    asyncio.run(read_async())


if __name__ == "__main__":
    main()
