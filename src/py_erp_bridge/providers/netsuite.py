"""Oracle NetSuite (SuiteTalk REST web services)."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from typing import ClassVar
from urllib.parse import quote, urlsplit

import httpx
from pydantic import Field

from py_erp_bridge.config.settings import ProviderConfig, RateLimitConfig
from py_erp_bridge.core.adapter import ERPAdapter, operation
from py_erp_bridge.core.capabilities import (
    CRUD,
    EntitySpec,
    Evidence,
    LineMap,
    Protocol,
    ProviderInfo,
    mapped,
)
from py_erp_bridge.core.query import Filter, FilterOp, Query, RawPage
from py_erp_bridge.exceptions import APIError, NotFoundError
from py_erp_bridge.transport.http import ErrorDetails
from py_erp_bridge.types import JsonObject, RecordKey, Scalar, as_object, as_objects, text

_STRING = {
    FilterOp.EQ: "IS",
    FilterOp.NE: "IS_NOT",
    FilterOp.CONTAINS: "CONTAIN",
    FilterOp.STARTSWITH: "START_WITH",
}
_NUMBER = {
    FilterOp.EQ: "EQUAL",
    FilterOp.NE: "EQUAL_NOT",
    FilterOp.GT: "GREATER",
    FilterOp.GE: "GREATER_OR_EQUAL",
    FilterOp.LT: "LESS",
    FilterOp.LE: "LESS_OR_EQUAL",
}
_DATE = {
    FilterOp.EQ: "ON",
    FilterOp.NE: "ON_NOT",
    FilterOp.GT: "AFTER",
    FilterOp.GE: "ON_OR_AFTER",
    FilterOp.LT: "BEFORE",
    FilterOp.LE: "ON_OR_BEFORE",
}
_MODIFIED = ("modified_at", "~lastModifiedDate")
_LINES = LineMap(
    fields=mapped(
        ("line_number", "~line"),
        ("product_id", "item.id"),
        ("description", "description"),
        ("quantity", "quantity"),
        ("unit_price", "rate"),
        ("amount", "amount"),
    ),
    write_path="item.items",
    read_path="item.items",
)


class NetSuiteConfig(ProviderConfig):
    """``base_url`` is ``https://<account>.suitetalk.api.netsuite.com``."""

    rate_limit: RateLimitConfig = Field(default_factory=lambda: RateLimitConfig(max_concurrency=4))
    #: Collections return only ids; expanding issues one GET per record.
    expand_lists: bool = True
    #: Date literals in filters follow the account's format.
    filter_date_format: str = "%m/%d/%Y"


class NetSuiteAdapter(ERPAdapter):
    """NetSuite. Entity paths are REST record types."""

    config_model = NetSuiteConfig
    config: NetSuiteConfig
    info = ProviderInfo(
        name="netsuite",
        title="Oracle NetSuite",
        protocols=frozenset({Protocol.REST}),
        auth_types=frozenset(
            {"oauth2_jwt_assertion", "oauth2_authorization_code", "oauth1", "bearer"}
        ),
        sorting=False,
        documentation=(
            "https://docs.oracle.com/en/cloud/saas/netsuite/ns-online-help/chapter_1540391670.html",
        ),
        limitations=(
            "Record collections return ids only; reads cost one request per record. "
            "Use execute('suiteql') for large reads.",
            "Collections cannot be sorted; SuiteQL can.",
            "No bulk create in the record API.",
            "Creates and updates return no body, only the record's location.",
            "Filter dates use the account's date format (filter_date_format).",
            "Webhooks need SuiteScript or a SuiteApp in the account.",
        ),
    )
    default_entities: ClassVar[Mapping[str, EntitySpec]] = {
        "customers": EntitySpec(
            path="customer",
            fields=mapped(
                ("name", "companyName"),
                ("email", "email"),
                ("phone", "phone"),
                ("tax_id", "vatRegNumber"),
                ("currency", "currency.id"),
                _MODIFIED,
            ),
            modified_field="lastModifiedDate",
            evidence=Evidence.OFFICIAL_DOCS,
            notes="OneWorld accounts need subsidiary via provider_fields",
        ),
        "vendors": EntitySpec(
            path="vendor",
            fields=mapped(
                ("name", "companyName"),
                ("email", "email"),
                ("phone", "phone"),
                ("tax_id", "vatRegNumber"),
                ("currency", "currency.id"),
                _MODIFIED,
            ),
            modified_field="lastModifiedDate",
        ),
        "products": EntitySpec(
            path="inventoryItem",
            fields=mapped(
                ("sku", "itemId"),
                ("name", "displayName"),
                ("description", "salesDescription"),
                _MODIFIED,
            ),
            modified_field="lastModifiedDate",
            notes="other item types are separate records, e.g. nonInventorySaleItem",
        ),
        "sales_orders": EntitySpec(
            path="salesOrder",
            fields=mapped(
                ("number", "~tranId"),
                ("customer_id", "entity.id"),
                ("order_date", "tranDate", "date"),
                ("currency", "currency.id"),
                ("status", "~status.refName"),
                ("total", "~total"),
                _MODIFIED,
            ),
            lines=_LINES,
            modified_field="lastModifiedDate",
        ),
        "purchase_orders": EntitySpec(
            path="purchaseOrder",
            fields=mapped(
                ("number", "~tranId"),
                ("vendor_id", "entity.id"),
                ("order_date", "tranDate", "date"),
                ("currency", "currency.id"),
                ("status", "~status.refName"),
                ("total", "~total"),
                _MODIFIED,
            ),
            lines=_LINES,
            modified_field="lastModifiedDate",
        ),
        "invoices": EntitySpec(
            path="invoice",
            fields=mapped(
                ("number", "~tranId"),
                ("customer_id", "entity.id"),
                ("invoice_date", "tranDate", "date"),
                ("due_date", "dueDate", "date"),
                ("currency", "currency.id"),
                ("status", "~status.refName"),
                ("total", "~total"),
                ("balance", "~amountRemaining"),
                _MODIFIED,
            ),
            lines=_LINES,
            modified_field="lastModifiedDate",
        ),
        "payments": EntitySpec(
            path="customerPayment",
            fields=mapped(
                ("number", "~tranId"),
                ("party_id", "customer.id"),
                ("payment_date", "tranDate", "date"),
                ("amount", "payment"),
                ("currency", "currency.id"),
                ("method", "paymentMethod.id"),
                ("reference", "memo"),
                _MODIFIED,
            ),
            modified_field="lastModifiedDate",
        ),
        "employees": EntitySpec(
            path="employee",
            fields=mapped(
                ("number", "entityId"),
                ("first_name", "firstName"),
                ("last_name", "lastName"),
                ("email", "email"),
                ("title", "title"),
                ("department", "department.id"),
                _MODIFIED,
            ),
            modified_field="lastModifiedDate",
        ),
        "financial_records": EntitySpec(
            path="journalEntry",
            operations=CRUD,
            fields=mapped(
                ("number", "~tranId"),
                ("posting_date", "tranDate", "date"),
                ("currency", "currency.id"),
                ("description", "memo"),
                _MODIFIED,
            ),
            modified_field="lastModifiedDate",
            notes="create needs subsidiary and line via provider_fields",
        ),
    }

    @staticmethod
    def _path(spec: EntitySpec) -> str:
        return f"/services/rest/record/v1/{quote(spec.path, safe='')}"

    def _record_path(self, spec: EntitySpec, key: RecordKey) -> str:
        value = self.key_values(spec, key)[spec.key_fields[0]]
        return f"{self._path(spec)}/{quote(value, safe=':')}"

    def _value(self, item: Filter, value: Scalar) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, date):
            return f'"{value.strftime(self.config.filter_date_format)}"'
        if isinstance(value, (int, float, Decimal)):
            return str(value)
        if '"' in value:
            # The documentation defines no escape sequence.
            raise self.unsupported_filter(item)
        return f'"{value}"'

    def _condition(self, item: Filter) -> str:
        name = item.field.removesuffix(".id")
        if name != item.field or name == "id":
            if item.op not in {FilterOp.EQ, FilterOp.NE, FilterOp.IN}:
                raise self.unsupported_filter(item)
            ids = [str(v) for v in item.values]
            if not ids or not all(v.isdigit() for v in ids):
                raise self.unsupported_filter(item)
            operator = "ANY_OF_NOT" if item.op is FilterOp.NE else "ANY_OF"
            return f"{name} {operator} [{', '.join(ids)}]"
        if item.op is FilterOp.IN:
            parts = [self._condition(Filter(field=item.field, value=v)) for v in item.values]
            if not parts:
                raise self.unsupported_filter(item)
            return f"({' OR '.join(parts)})"
        value = item.scalar
        if isinstance(value, bool):
            table = {FilterOp.EQ: "IS", FilterOp.NE: "IS_NOT"}
        elif isinstance(value, date):
            table = _DATE
        elif isinstance(value, (int, float, Decimal)):
            table = _NUMBER
        else:
            table = _STRING
        if item.op not in table:
            raise self.unsupported_filter(item)
        return f"{name} {table[item.op]} {self._value(item, value)}"

    async def list_page(self, spec: EntitySpec, query: Query, cursor: str | None) -> RawPage:
        """Fetch a page of ids and, by default, the records behind them."""
        if query.sort:
            raise self.unsupported("sorting; use execute('suiteql')")
        if query.select:
            raise self.unsupported("field selection on lists; use execute('suiteql')")
        offset = int(cursor) if cursor else 0
        size = self.page_size(query)
        params: dict[str, str | int] = {"limit": size, "offset": offset}
        if query.filters:
            params["q"] = " AND ".join(self._condition(f) for f in query.filters)
        response = await self.http.request(
            "GET", self._path(spec), params={**params, **query.params}
        )
        body = self.json_object(response)
        stubs = self.record_list(body.get("items"))
        records = stubs
        if self.config.expand_lists:
            found = await asyncio.gather(*(self._expand(spec, s) for s in stubs))
            records = [r for r in found if r is not None]
        total = body.get("totalResults")
        return RawPage(
            records=records,
            # Offsets must be multiples of the limit.
            next_cursor=str(offset + size) if body.get("hasMore") is True else None,
            total=total if isinstance(total, int) else None,
        )

    async def _expand(self, spec: EntitySpec, stub: JsonObject) -> JsonObject | None:
        try:
            return await self.get(spec, text(stub.get("id")))
        except NotFoundError:
            return None  # Deleted since the list was read.

    async def get(self, spec: EntitySpec, key: RecordKey) -> JsonObject:
        """Fetch a record; ``eid:<external id>`` is accepted as key."""
        params = {"expandSubResources": "true"} if spec.lines else None
        response = await self.http.request("GET", self._record_path(spec, key), params=params)
        return self.json_object(response)

    async def create(
        self, spec: EntitySpec, payload: JsonObject, idempotency_key: str | None
    ) -> JsonObject:
        """Create a record; the id comes from the Location header."""
        response = await self.http.request(
            "POST", self._path(spec), json=payload, idempotency_key=idempotency_key
        )
        location = urlsplit(response.headers.get("Location", "")).path
        identifier = location.rstrip("/").rpartition("/")[2]
        if not identifier:
            raise APIError(
                "netsuite: record was created but no Location was returned",
                provider=self.info.name,
                status_code=response.status_code,
            )
        return {"id": identifier}

    async def update(
        self, spec: EntitySpec, key: RecordKey, payload: JsonObject, etag: str | None
    ) -> JsonObject | None:
        """Patch a record."""
        if etag is not None:
            raise self.unsupported("etags")
        await self.http.request(
            "PATCH", self._record_path(spec, key), json=payload, idempotent=True
        )
        return None

    async def delete(self, spec: EntitySpec, key: RecordKey, etag: str | None) -> None:
        """Delete a record."""
        if etag is not None:
            raise self.unsupported("etags")
        await self.http.request("DELETE", self._record_path(spec, key))

    async def health_check(self) -> None:
        """Fetch one customer id."""
        await self.http.request("GET", self._path(self.entities["customers"]), params={"limit": 1})

    @operation
    async def suiteql(self, *, query: str, limit: int = 1000, offset: int = 0) -> JsonObject:
        """Run a SuiteQL query and return one page (``items``, ``hasMore``, ...)."""
        response = await self.http.request(
            "POST",
            "/services/rest/query/v1/suiteql",
            params={"limit": limit, "offset": offset},
            json={"q": query},
            headers={"Prefer": "transient"},
            idempotent=True,
        )
        return self.json_object(response)

    def parse_error(self, response: httpx.Response) -> ErrorDetails:
        """Extract ``o:errorDetails``."""
        try:
            body = as_object(response.json())
        except ValueError:
            return ErrorDetails()
        details = as_objects(body.get("o:errorDetails"))
        first = details[0] if details else {}
        return ErrorDetails(
            code=text(first.get("o:errorCode")) or None,
            message="; ".join(text(d.get("detail")) for d in details) or text(body.get("title")),
        )
