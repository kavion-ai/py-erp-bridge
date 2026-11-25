"""TallyPrime: connect, read, page, filter, handle errors, write, go async.

Settings come from TALLY_* environment variables (see .env.example). Reading is the
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

PROVIDER = "tally"


def load_config() -> JsonObject:
    """Read settings and tune the retry policy."""
    config = config_from_env("TALLY")
    config.setdefault("retry", {"max_attempts": 5, "base_delay": 0.5, "max_delay": 20})
    return config


def read(erp: ERPClient) -> None:
    """Fetch customers and invoices."""
    for customer in erp.customers.iterate(limit=5):
        print("customer", customer.id, customer.name)
    for invoice in erp.invoices.iterate(
        limit=5, params={"from_date": "20240401", "to_date": "20250331"}
    ):
        print("invoice", invoice.number, invoice.invoice_date, invoice.total)


def page_and_filter(erp: ERPClient) -> None:
    """Fetch single pages and filter."""
    page = erp.customers.list_page()
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
    # Every Tally collection type is reachable as an ad-hoc entity.
    groups = erp.entity("groups", spec=EntitySpec(path="Group", key_fields=("NAME",)))
    print("groups:", [g.id for g in groups.get_all(limit=5)])


def write(erp: ERPClient) -> None:
    """Create, update, delete and bulk-create."""
    erp.customers.create({"name": "Example Ltd", "email": "ap@example.com"})
    erp.customers.update("Example Ltd", {"phone": "5550100"})
    erp.execute(
        "import_voucher",
        voucher_type="Receipt",
        date="20240401",
        fields={
            "NARRATION": "Example receipt",
            "ALLLEDGERENTRIES.LIST": [
                {"LEDGERNAME": "Example Ltd", "ISDEEMEDPOSITIVE": False, "AMOUNT": "100.00"},
                {"LEDGERNAME": "Cash", "ISDEEMEDPOSITIVE": True, "AMOUNT": "-100.00"},
            ],
        },
    )
    result = erp.customers.bulk_create([{"name": "Example A"}, {"name": "Example B"}])
    print("failed:", [str(i.error) for i in result.failed])
    # Deleting masters is not implemented.


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
