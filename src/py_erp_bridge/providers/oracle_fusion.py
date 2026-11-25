"""Oracle Fusion Cloud ERP (REST)."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import ClassVar
from urllib.parse import quote, urlsplit

import httpx

from py_erp_bridge.config.settings import ProviderConfig
from py_erp_bridge.core.adapter import ERPAdapter, operation
from py_erp_bridge.core.capabilities import (
    READ,
    EntitySpec,
    Evidence,
    Operation,
    Protocol,
    ProviderInfo,
    mapped,
)
from py_erp_bridge.core.query import Filter, FilterOp, Query, RawPage
from py_erp_bridge.exceptions import ConfigurationError
from py_erp_bridge.transport.http import ErrorDetails
from py_erp_bridge.transport.xml import soap_call, to_json
from py_erp_bridge.types import (
    JsonObject,
    JsonValue,
    RecordKey,
    Scalar,
    as_int,
    as_object,
    as_objects,
    text,
)

_ITEM = "application/vnd.oracle.adf.resourceitem+json"
_OPERATORS = {
    FilterOp.EQ: "=",
    FilterOp.NE: "!=",
    FilterOp.GT: ">",
    FilterOp.GE: ">=",
    FilterOp.LT: "<",
    FilterOp.LE: "<=",
}
_WRITE = READ | {Operation.CREATE, Operation.UPDATE}
_MODIFIED = ("modified_at", "~LastUpdateDate")


class OracleFusionConfig(ProviderConfig):
    """``base_url`` is the pod URL, e.g. ``https://xxxx.fa.ocs.oraclecloud.com``."""

    version: str = "11.13.18.05"
    #: Sent as REST-Framework-Version; version 2+ is needed for IN and LIKE.
    framework_version: str = "4"


class OracleFusionAdapter(ERPAdapter):
    """Fusion. Paths are ``family/resource``; ids come from each record's self link."""

    config_model = OracleFusionConfig
    config: OracleFusionConfig
    max_page_size = 500
    info = ProviderInfo(
        name="oracle_fusion",
        title="Oracle Fusion Cloud ERP",
        protocols=frozenset({Protocol.REST, Protocol.SOAP}),
        auth_types=frozenset(
            {"basic", "bearer", "oauth2_client_credentials", "oauth2_authorization_code"}
        ),
        documentation=(
            "https://docs.oracle.com/en/cloud/saas/financials/",
            "https://docs.oracle.com/en/cloud/saas/procurement/26c/fapra/Quick_Start.html",
            "https://docs.oracle.com/en/cloud/saas/supply-chain-and-manufacturing/26b/fasrp/"
            "Quick_Start.html",
        ),
        limitations=(
            "Resource names and attributes could not be read in Oracle's reference for this "
            "release; confirm them with execute('describe').",
            "At most 500 records per request; deep offsets may fail on some resources.",
            "Which attributes can be filtered or sorted varies per resource.",
            "Filter values containing quotes or semicolons are rejected.",
            "Bulk loads use FBDI files through other services, which are not implemented.",
            "No webhooks: use Oracle Integration business events.",
        ),
    )
    default_entities: ClassVar[Mapping[str, EntitySpec]] = {
        "customers": EntitySpec(
            path="crmRestApi/accounts",
            key_fields=("PartyNumber",),
            operations=_WRITE,
            fields=mapped(
                ("name", "OrganizationName"),
                ("email", "EmailAddress"),
                ("street", "AddressLine1"),
                ("city", "City"),
                ("region", "State"),
                ("postal_code", "PostalCode"),
                ("country", "Country"),
                _MODIFIED,
            ),
            modified_field="LastUpdateDate",
        ),
        "vendors": EntitySpec(
            path="fscmRestApi/suppliers",
            key_fields=("SupplierId",),
            operations=_WRITE,
            fields=mapped(
                ("name", "Supplier"),
                ("tax_id", "TaxpayerId"),
                _MODIFIED,
            ),
            modified_field="LastUpdateDate",
            evidence=Evidence.SECONDARY,
        ),
        "products": EntitySpec(
            path="fscmRestApi/itemsV2",
            key_fields=("ItemId",),
            operations=_WRITE,
            fields=mapped(
                ("sku", "ItemNumber"),
                ("description", "ItemDescription"),
                ("unit", "PrimaryUOMValue"),
                _MODIFIED,
            ),
            modified_field="LastUpdateDate",
            notes="create needs OrganizationCode and ItemClass via provider_fields",
        ),
        "inventory": EntitySpec(
            path="fscmRestApi/inventoryOnhandBalances",
            operations=frozenset({Operation.LIST}),
            fields=mapped(
                ("product_id", "ItemNumber"),
                ("location_id", "SubinventoryCode"),
                ("quantity", "PrimaryQuantity"),
                ("unit", "PrimaryUOMCode"),
            ),
        ),
        "sales_orders": EntitySpec(
            path="fscmRestApi/salesOrdersForOrderHub",
            key_fields=("OrderKey",),
            operations=_WRITE,
            fields=mapped(
                ("number", "~OrderNumber"),
                ("customer_id", "BuyingPartyId"),
                ("currency", "TransactionalCurrencyCode"),
                ("status", "~StatusCode"),
                _MODIFIED,
            ),
            modified_field="LastUpdateDate",
            notes="create needs source transaction fields and lines via provider_fields",
        ),
        "purchase_orders": EntitySpec(
            path="fscmRestApi/purchaseOrders",
            key_fields=("POHeaderId",),
            operations=READ,
            fields=mapped(
                ("number", "OrderNumber"),
                ("vendor_id", "SupplierId"),
                ("currency", "CurrencyCode"),
                ("status", "Status"),
                ("total", "Total"),
                _MODIFIED,
            ),
            modified_field="LastUpdateDate",
            evidence=Evidence.SECONDARY,
            notes="create through the draftPurchaseOrders resource",
        ),
        "invoices": EntitySpec(
            path="fscmRestApi/receivablesInvoices",
            key_fields=("CustomerTransactionId",),
            operations=_WRITE,
            fields=mapped(
                ("number", "TransactionNumber"),
                ("customer_id", "BillToCustomerNumber"),
                ("invoice_date", "TransactionDate", "date"),
                ("due_date", "~DueDate", "date"),
                ("currency", "InvoiceCurrencyCode"),
                ("status", "~InvoiceStatus"),
                ("total", "~EnteredAmount"),
                ("balance", "~InvoiceBalanceAmount"),
                _MODIFIED,
            ),
            modified_field="LastUpdateDate",
        ),
        "payments": EntitySpec(
            path="fscmRestApi/payablesPayments",
            key_fields=("CheckId",),
            operations=READ,
            fields=mapped(
                ("number", "PaymentNumber"),
                ("party_id", "PayeeId"),
                ("payment_date", "PaymentDate", "date"),
                ("amount", "PaymentAmount"),
                ("currency", "PaymentCurrency"),
                ("method", "PaymentMethod"),
                _MODIFIED,
            ),
            modified_field="LastUpdateDate",
            notes="supplier payments; customer receipts are standardReceipts",
        ),
        "employees": EntitySpec(
            path="hcmRestApi/workers",
            key_fields=("PersonId",),
            operations=READ,
            fields=mapped(("number", "PersonNumber"), _MODIFIED),
            modified_field="LastUpdateDate",
            notes="names and emails are child resources; pass params={'expand': 'names,emails'}",
        ),
        "financial_records": EntitySpec(
            path="fscmRestApi/journalBatches",
            key_fields=("JeBatchId",),
            operations=READ,
            fields=mapped(
                ("number", "BatchName"),
                ("description", "BatchDescription"),
                _MODIFIED,
            ),
            modified_field="LastUpdateDate",
        ),
    }

    def _path(self, spec: EntitySpec) -> str:
        family, separator, resource = spec.path.partition("/")
        if not separator:
            raise ConfigurationError("fusion entity paths must look like family/resource")
        return f"/{family}/resources/{self.config.version}/{resource}"

    def _headers(self) -> dict[str, str]:
        return {"REST-Framework-Version": self.config.framework_version}

    def key_values(self, spec: EntitySpec, key: RecordKey) -> dict[str, str]:
        """Keys are opaque: some resources use hashed ids."""
        if isinstance(key, Mapping):
            return super().key_values(spec, key)
        return {spec.key_fields[0]: str(key)}

    def _record_path(self, spec: EntitySpec, key: RecordKey) -> str:
        value = self.key_values(spec, key)[spec.key_fields[0]]
        return f"{self._path(spec)}/{quote(value, safe='')}"

    def record_id(self, spec: EntitySpec, raw: JsonObject) -> str | None:
        """Prefer the self link, whose last segment addresses the record."""
        for link in as_objects(raw.get("links")):
            if link.get("rel") == "self":
                segment = urlsplit(text(link.get("href"))).path.rstrip("/").rpartition("/")[2]
                if segment:
                    return httpx.URL(f"/{segment}").path.lstrip("/")
        return super().record_id(spec, raw)

    def _literal(self, item: Filter, value: Scalar) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, date):
            return f"'{value.isoformat()}'"
        if not isinstance(value, str):
            return str(value)
        if "'" in value or ";" in value or '"' in value:
            raise self.unsupported_filter(item)
        return f"'{value}'"

    def _condition(self, item: Filter) -> str:
        if item.op is FilterOp.IN:
            if not item.values:
                raise self.unsupported_filter(item)
            values = ",".join(self._literal(item, v) for v in item.values)
            return f"{item.field} in ({values})"
        if item.op in {FilterOp.CONTAINS, FilterOp.STARTSWITH}:
            value = str(item.scalar)
            if "%" in value or "_" in value:
                raise self.unsupported_filter(item)
            pattern = f"%{value}%" if item.op is FilterOp.CONTAINS else f"{value}%"
            return f"{item.field} LIKE {self._literal(item, pattern)}"
        return f"{item.field}{_OPERATORS[item.op]}{self._literal(item, item.scalar)}"

    async def list_page(self, spec: EntitySpec, query: Query, cursor: str | None) -> RawPage:
        """Fetch a page. Sort explicitly: Oracle does not order pages by default."""
        offset = int(cursor) if cursor else 0
        params: dict[str, str | int] = {"limit": self.page_size(query), "offset": offset}
        if query.filters:
            params["q"] = ";".join(self._condition(f) for f in query.filters)
        if query.sort:
            params["orderBy"] = ",".join(
                f"{s.field}:{'desc' if s.descending else 'asc'}" for s in query.sort
            )
        elif spec.key_fields != ("id",):
            params["orderBy"] = ",".join(spec.key_fields)
        if query.select:
            params["fields"] = ",".join(dict.fromkeys([*spec.key_fields, *query.select]))
        response = await self.http.request(
            "GET", self._path(spec), params={**params, **query.params}, headers=self._headers()
        )
        body = self.json_object(response)
        records = self.record_list(body.get("items"))
        more = body.get("hasMore") is True and records
        return RawPage(
            records=records,
            next_cursor=str(offset + len(records)) if more else None,
            total=as_int(body.get("totalResults")),
        )

    async def get(self, spec: EntitySpec, key: RecordKey) -> JsonObject:
        """Fetch a record."""
        response = await self.http.request(
            "GET", self._record_path(spec, key), headers=self._headers()
        )
        return self.json_object(response)

    async def create(
        self, spec: EntitySpec, payload: JsonObject, idempotency_key: str | None
    ) -> JsonObject:
        """Create a record."""
        response = await self.http.request(
            "POST",
            self._path(spec),
            json=payload,
            headers={**self._headers(), "Content-Type": _ITEM},
            idempotency_key=idempotency_key,
        )
        return self.json_object(response)

    async def update(
        self, spec: EntitySpec, key: RecordKey, payload: JsonObject, etag: str | None
    ) -> JsonObject | None:
        """Patch a record."""
        headers = {**self._headers(), "Content-Type": _ITEM}
        if etag is not None:
            headers["If-Match"] = etag
        response = await self.http.request(
            "PATCH", self._record_path(spec, key), json=payload, headers=headers, idempotent=True
        )
        return self.json_object(response) if response.content else None

    async def delete(self, spec: EntitySpec, key: RecordKey, etag: str | None) -> None:
        """Delete a record (only for entities that declare it)."""
        headers = {**self._headers(), **({"If-Match": etag} if etag is not None else {})}
        await self.http.request("DELETE", self._record_path(spec, key), headers=headers)

    async def health_check(self) -> None:
        """Read one supplier."""
        await self.http.request(
            "GET",
            self._path(self.entities["vendors"]),
            params={"limit": 1},
            headers=self._headers(),
        )

    @operation
    async def describe(self, *, entity: str) -> JsonObject:
        """Return a resource's metadata; use it to confirm attribute names."""
        spec = self.entities.get(entity)
        if spec is None:
            raise ConfigurationError(f"unknown entity '{entity}'")
        response = await self.http.request(
            "GET", f"{self._path(spec)}/describe", headers=self._headers()
        )
        return self.json_object(response)

    @operation
    async def soap(self, *, path: str, body_xml: str, action: str) -> JsonValue:
        """Call a SOAP service; ``body_xml`` is the (already escaped) Body content."""
        return to_json(await soap_call(self.http, path, body_xml, action=action))

    def parse_error(self, response: httpx.Response) -> ErrorDetails:
        """Extract ``o:errorDetails``; some services answer in plain text."""
        try:
            body = as_object(response.json())
        except ValueError:
            kind = response.headers.get("Content-Type", "")
            return ErrorDetails(message=response.text[:300] if "text/plain" in kind else None)
        details = as_objects(body.get("o:errorDetails"))
        return ErrorDetails(
            code=text(body.get("o:errorCode"))
            or (text(details[0].get("o:errorCode")) if details else "")
            or None,
            message="; ".join(text(d.get("detail")) for d in details)
            or text(body.get("detail"))
            or text(body.get("title"))
            or None,
        )
