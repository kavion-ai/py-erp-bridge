"""Infor CloudSuite (M3): connect, read, page, filter, handle errors, write, go async.

Settings come from INFOR_* environment variables (see .env.example). Reading is the
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
from py_erp_bridge.providers.infor import load_ionapi
from py_erp_bridge.types import JsonObject

PROVIDER = "infor"


def load_config() -> JsonObject:
    """Read settings and tune the retry policy."""
    config = (
        load_ionapi(os.environ["INFOR_IONAPI_FILE"])
        if "INFOR_IONAPI_FILE" in os.environ
        else config_from_env("INFOR")
    )
    config.setdefault("retry", {"max_attempts": 5, "base_delay": 0.5, "max_delay": 20})
    return config


def read(erp: ERPClient) -> None:
    """Fetch customers and invoices."""
    for customer in erp.customers.iterate(limit=5):
        print("customer", customer.id, customer.name)
    try:
        erp.invoices.get_all(limit=5)
    except UnsupportedOperationError as exc:
        print("invoices:", exc)


def page_and_filter(erp: ERPClient) -> None:
    """Fetch single pages and filter."""
    page = erp.customers.list_page(page_size=10)
    print("first page:", len(page.items), "next cursor:", page.next_cursor)
    matching = erp.customers.get_all(filters={"country": "US"}, limit=10)
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
    print(
        erp.execute(
            "mi",
            program="MMS200MI",
            transaction="GetItmBasic",
            record={"ITNO": "A001"},
            read_only=True,
        )
    )


def write(erp: ERPClient) -> None:
    """Create, update, delete and bulk-create."""
    customer = erp.customers.create(
        {"name": "Example Ltd", "country": "US", "provider_fields": {"CUNO": "EX1"}}
    )
    if customer.id is not None:
        erp.customers.update(customer.id, {"phone": "5550100"})
    result = erp.customers.bulk_create(
        [{"name": f"Example {n}", "provider_fields": {"CUNO": f"EX{n}"}} for n in range(2, 4)]
    )
    print("failed:", [str(i.error) for i in result.failed])
    # Deletion is not mapped; use execute("mi", ...) with the program's delete transaction.


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
