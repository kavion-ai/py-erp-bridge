"""SAP S/4HANA: connect, read, page, filter, handle errors, write, go async.

Settings come from S4_* environment variables (see .env.example). Reading is the
default; writes run only with ERP_EXAMPLE_WRITE=1. Use a sandbox system.
"""

from __future__ import annotations

import asyncio
import os

from py_erp_bridge import AsyncERPClient, EntitySpec, ERPClient, config_from_env
from py_erp_bridge.exceptions import (
    APIError,
    AuthenticationError,
    NotFoundError,
    RateLimitError,
    TransportError,
    UnsupportedOperationError,
)
from py_erp_bridge.types import JsonObject

PROVIDER = "sap_s4hana"


def load_config() -> JsonObject:
    """Read settings and tune the retry policy."""
    config = config_from_env("S4")
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
    page = erp.customers.list_page(page_size=10, sort=["name"])
    print("first page:", len(page.items), "next cursor:", page.next_cursor)
    matching = erp.customers.get_all(filters={"name__contains": "a"}, limit=10)
    print("matching:", len(matching))


def errors(erp: ERPClient) -> None:
    """Handle the standard exceptions."""
    try:
        erp.customers.get("does-not-exist")
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
    # Any OData service works through an ad-hoc entity. Its records expose ``raw``.
    partners = erp.entity(
        "business_partners",
        spec=EntitySpec(
            path="API_BUSINESS_PARTNER/A_BusinessPartner", key_fields=("BusinessPartner",)
        ),
    )
    print("partners:", len(partners.get_all(limit=3)))


def write(erp: ERPClient) -> None:
    """Create, update, delete and bulk-create."""
    # Customers are created through the business partner.
    partners = erp.entity(
        "business_partners",
        spec=EntitySpec(
            path="API_BUSINESS_PARTNER/A_BusinessPartner", key_fields=("BusinessPartner",)
        ),
    )
    partner = partners.create(
        {
            "BusinessPartnerCategory": "2",
            "OrganizationBPName1": "Example Ltd",
            "to_BusinessPartnerRole": [{"BusinessPartnerRole": "FLCU01"}],
        }
    )
    print("created", partner.id)
    order = erp.sales_orders.create(
        {
            "customer_id": "1001",
            "items": [{"product_id": "TG11", "quantity": "1"}],
            "provider_fields": {
                "SalesOrderType": "OR",
                "SalesOrganization": "1010",
                "DistributionChannel": "10",
                "OrganizationDivision": "00",
            },
        }
    )
    if order.id is not None:
        erp.sales_orders.update(order.id, {"provider_fields": {"PurchaseOrderByCustomer": "PO-1"}})
        erp.sales_orders.delete(order.id)
    result = erp.products.bulk_create(
        [{"sku": "EX-1", "unit": "PC"}, {"sku": "EX-2", "unit": "PC"}]
    )
    print("created:", len(result.succeeded), "failed:", [str(i.error) for i in result.failed])


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
