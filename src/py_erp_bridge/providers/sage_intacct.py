"""Sage Intacct (REST API v1)."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime
from typing import ClassVar
from urllib.parse import quote

import httpx

from py_erp_bridge.config.settings import ProviderConfig
from py_erp_bridge.core.adapter import ERPAdapter, operation
from py_erp_bridge.core.capabilities import (
    CRUD,
    READ,
    EntitySpec,
    LineMap,
    Protocol,
    ProviderInfo,
    mapped,
)
from py_erp_bridge.core.query import Filter, FilterOp, Query, RawPage
from py_erp_bridge.core.transforms import to_json
from py_erp_bridge.exceptions import APIError
from py_erp_bridge.transport.http import ErrorDetails
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

_OPERATORS = {
    FilterOp.EQ: "$eq",
    FilterOp.NE: "$ne",
    FilterOp.GT: "$gt",
    FilterOp.GE: "$gte",
    FilterOp.LT: "$lt",
    FilterOp.LE: "$lte",
    FilterOp.IN: "$in",
    FilterOp.CONTAINS: "$contains",
    FilterOp.STARTSWITH: "$startsWith",
}
_MODIFIED = ("modified_at", "~audit.modifiedDateTime")


class SageIntacctConfig(ProviderConfig):
    """OAuth token endpoint: ``https://api.intacct.com/ia/api/v1/oauth2/token``."""

    base_url: str = "https://api.intacct.com/ia/api/v1"
    #: Entity (location) to work in, for multi-entity companies.
    entity_id: str | None = None


class SageIntacctAdapter(ERPAdapter):
    """Intacct. Paths are REST object names such as ``accounts-payable/vendor``."""

    config_model = SageIntacctConfig
    config: SageIntacctConfig
    max_page_size = 4000
    info = ProviderInfo(
        name="sage_intacct",
        title="Sage Intacct",
        protocols=frozenset({Protocol.REST}),
        auth_types=frozenset({"oauth2_client_credentials", "oauth2_authorization_code", "bearer"}),
        documentation=(
            "https://developer.sage.com/intacct/docs/1/sage-intacct-rest-api/"
            "authorization-and-security/oauth2",
            "https://developer.sage.com/intacct/docs/1/sage-intacct-rest-api/"
            "api-essentials/query-service",
            "https://developer.sage.com/intacct/apis/intacct/1/intacct-openapi",
        ),
        limitations=(
            "Sage's reference could not be read for this release: object names, fields and "
            "the query format are unconfirmed.",
            "Records are addressed by 'key' (the system id), not by 'id' (the user-facing id).",
            "The query service cannot select all fields, so lists contain mapped fields only.",
            "The legacy XML API is not implemented.",
        ),
    )
    default_entities: ClassVar[Mapping[str, EntitySpec]] = {
        "customers": EntitySpec(
            path="accounts-receivable/customer",
            key_fields=("key",),
            fields=mapped(
                ("name", "name"),
                ("tax_id", "taxId"),
                ("currency", "currency"),
                _MODIFIED,
            ),
            modified_field="audit.modifiedDateTime",
        ),
        "vendors": EntitySpec(
            path="accounts-payable/vendor",
            key_fields=("key",),
            fields=mapped(
                ("name", "name"),
                ("tax_id", "taxId"),
                ("currency", "currency"),
                _MODIFIED,
            ),
            modified_field="audit.modifiedDateTime",
        ),
        "products": EntitySpec(
            path="inventory-control/item",
            key_fields=("key",),
            fields=mapped(("sku", "id"), ("name", "name"), _MODIFIED),
            modified_field="audit.modifiedDateTime",
        ),
        "invoices": EntitySpec(
            path="accounts-receivable/invoice",
            key_fields=("key",),
            fields=mapped(
                ("number", "invoiceNumber"),
                ("customer_id", "customer.id"),
                ("invoice_date", "invoiceDate", "date"),
                ("due_date", "dueDate", "date"),
                ("status", "~state"),
                _MODIFIED,
            ),
            lines=LineMap(
                fields=mapped(
                    ("description", "memo"),
                    ("amount", "txnAmount", "decimal_string"),
                ),
                write_path="lines",
                read_path="lines",
            ),
            modified_field="audit.modifiedDateTime",
        ),
        "payments": EntitySpec(
            path="accounts-receivable/payment",
            key_fields=("key",),
            operations=READ,
            fields=mapped(
                ("party_id", "customer.id"),
                ("payment_date", "paymentDate", "date"),
                ("method", "paymentMethod"),
                _MODIFIED,
            ),
            modified_field="audit.modifiedDateTime",
        ),
        "employees": EntitySpec(
            path="company-config/employee",
            key_fields=("key",),
            fields=mapped(("number", "id"), ("title", "jobTitle"), _MODIFIED),
            modified_field="audit.modifiedDateTime",
        ),
        "financial_records": EntitySpec(
            path="general-ledger/journal-entry",
            key_fields=("key",),
            operations=CRUD,
            fields=mapped(
                ("number", "~txnNumber"),
                ("posting_date", "postingDate", "date"),
                ("description", "description"),
                _MODIFIED,
            ),
            modified_field="audit.modifiedDateTime",
            notes="create needs glJournal and lines via provider_fields",
        ),
    }

    @staticmethod
    def _path(spec: EntitySpec) -> str:
        return f"/objects/{quote(spec.path, safe='/')}"

    def _record_path(self, spec: EntitySpec, key: RecordKey) -> str:
        value = self.key_values(spec, key)[spec.key_fields[0]]
        return f"{self._path(spec)}/{quote(value, safe='')}"

    def _headers(self) -> dict[str, str]:
        return {"X-IA-API-Param-Entity": self.config.entity_id} if self.config.entity_id else {}

    def record_id(self, spec: EntitySpec, raw: JsonObject) -> str | None:
        """Query results flatten references to dotted names; keys stay plain."""
        return text(raw.get(spec.key_fields[0])) or None

    @staticmethod
    def _literal(value: Scalar) -> JsonValue:
        if isinstance(value, datetime):
            utc = value.astimezone(UTC) if value.tzinfo else value
            return utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        return value.isoformat() if isinstance(value, date) else to_json(value)

    def _filter(self, item: Filter) -> JsonObject:
        value: JsonValue = (
            [self._literal(v) for v in item.values]
            if item.op is FilterOp.IN
            else self._literal(item.scalar)
        )
        return {_OPERATORS[item.op]: {item.field: value}}

    def _result(self, body: JsonObject) -> JsonObject:
        result = body.get("ia::result")
        if not isinstance(result, dict):
            raise APIError("sage_intacct: response has no ia::result", provider=self.info.name)
        return result

    async def list_page(self, spec: EntitySpec, query: Query, cursor: str | None) -> RawPage:
        """Query a page; positions are 1-based."""
        wanted = query.select or tuple(f.source for f in spec.fields)
        fields: list[JsonValue] = list(dict.fromkeys([*spec.key_fields, "id", *wanted]))
        start = int(cursor) if cursor else 1
        order: list[JsonValue] = [
            {s.field: "desc" if s.descending else "asc"} for s in query.sort
        ] or [{spec.key_fields[0]: "asc"}]
        body: JsonObject = {
            "object": spec.path,
            "fields": fields,
            "orderBy": order,
            "start": start,
            "size": self.page_size(query),
        }
        if query.filters:
            body["filters"] = [self._filter(f) for f in query.filters]
        body.update(query.params)
        response = await self.http.request(
            "POST", "/services/core/query", json=body, headers=self._headers(), idempotent=True
        )
        data = self.json_object(response)
        records = data.get("ia::result")
        if not isinstance(records, list):
            raise APIError("sage_intacct: query returned no list", provider=self.info.name)
        meta = as_object(data.get("ia::meta"))
        following = as_int(meta.get("next"))
        return RawPage(
            records=as_objects(records),
            next_cursor=str(following) if following and following > start else None,
            total=as_int(meta.get("totalCount")),
        )

    async def get(self, spec: EntitySpec, key: RecordKey) -> JsonObject:
        """Fetch an object."""
        response = await self.http.request(
            "GET", self._record_path(spec, key), headers=self._headers()
        )
        return self._result(self.json_object(response))

    async def create(
        self, spec: EntitySpec, payload: JsonObject, idempotency_key: str | None
    ) -> JsonObject:
        """Create an object; the response holds its key, id and href."""
        response = await self.http.request(
            "POST",
            self._path(spec),
            json=payload,
            headers=self._headers(),
            idempotency_key=idempotency_key,
        )
        return self._result(self.json_object(response))

    async def update(
        self, spec: EntitySpec, key: RecordKey, payload: JsonObject, etag: str | None
    ) -> JsonObject | None:
        """Patch an object."""
        if etag is not None:
            raise self.unsupported("etags")
        await self.http.request(
            "PATCH",
            self._record_path(spec, key),
            json=payload,
            headers=self._headers(),
            idempotent=True,
        )
        return None

    async def delete(self, spec: EntitySpec, key: RecordKey, etag: str | None) -> None:
        """Delete an object."""
        if etag is not None:
            raise self.unsupported("etags")
        await self.http.request("DELETE", self._record_path(spec, key), headers=self._headers())

    async def health_check(self) -> None:
        """Query one customer."""
        await self.list_page(self.entities["customers"], Query(page_size=1), None)

    @operation
    async def query(self, *, body: JsonObject) -> JsonObject:
        """Send a native query-service request."""
        response = await self.http.request(
            "POST", "/services/core/query", json=body, headers=self._headers(), idempotent=True
        )
        return self.json_object(response)

    def parse_error(self, response: httpx.Response) -> ErrorDetails:
        """Extract ``ia::error``."""
        try:
            body = as_object(response.json())
        except ValueError:
            return ErrorDetails()
        error = as_object(as_object(body.get("ia::result")).get("ia::error"))
        return ErrorDetails(
            code=text(error.get("code")) or None, message=text(error.get("message")) or None
        )
