"""ERPNext (Frappe REST API)."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date, datetime
from typing import ClassVar
from urllib.parse import quote

import httpx

from py_erp_bridge.config.settings import ProviderConfig
from py_erp_bridge.core.adapter import (
    ERPAdapter,
    FileUpload,
    WebhookRegistration,
    operation,
)
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
from py_erp_bridge.exceptions import ConfigurationError
from py_erp_bridge.transport.http import ErrorDetails
from py_erp_bridge.types import (
    JsonObject,
    JsonValue,
    RecordKey,
    Scalar,
    as_object,
    text,
)

_OPERATORS = {
    FilterOp.EQ: "=",
    FilterOp.NE: "!=",
    FilterOp.GT: ">",
    FilterOp.GE: ">=",
    FilterOp.LT: "<",
    FilterOp.LE: "<=",
    FilterOp.IN: "in",
    FilterOp.CONTAINS: "like",
    FilterOp.STARTSWITH: "like",
}
_LINES = LineMap(
    fields=mapped(
        ("line_number", "~idx"),
        ("product_id", "item_code"),
        ("description", "description"),
        ("quantity", "qty"),
        ("unit", "uom"),
        ("unit_price", "rate"),
        ("amount", "~amount"),
    ),
    write_path="items",
    read_path="items",
)
_MODIFIED = ("modified_at", "~modified")
_LIST_NOTE = "lists do not include items; get() does"


class ERPNextConfig(ProviderConfig):
    """``base_url`` is the site URL."""


class ERPNextAdapter(ERPAdapter):
    """ERPNext. Entity paths are DocType names."""

    config_model = ERPNextConfig
    info = ProviderInfo(
        name="erpnext",
        title="ERPNext",
        protocols=frozenset({Protocol.REST}),
        auth_types=frozenset(
            {"api_key", "bearer", "oauth2_authorization_code", "oauth2_client_credentials"}
        ),
        webhook_registration=True,
        file_upload=True,
        documentation=(
            "https://docs.frappe.io/framework/user/en/api/rest",
            "https://docs.frappe.io/framework/user/en/guides/integration/webhooks",
        ),
        limitations=(
            "List responses omit child tables (items).",
            "Submitted documents must be cancelled before they can be deleted.",
            "Modification timestamps are in the site's time zone, without an offset.",
        ),
    )
    default_entities: ClassVar[Mapping[str, EntitySpec]] = {
        "customers": EntitySpec(
            path="Customer",
            key_fields=("name",),
            fields=mapped(
                ("name", "customer_name"),
                ("email", "email_id"),
                ("phone", "mobile_no"),
                ("tax_id", "tax_id"),
                ("currency", "default_currency"),
                _MODIFIED,
            ),
            modified_field="modified",
        ),
        "vendors": EntitySpec(
            path="Supplier",
            key_fields=("name",),
            fields=mapped(
                ("name", "supplier_name"),
                ("email", "email_id"),
                ("phone", "mobile_no"),
                ("tax_id", "tax_id"),
                ("currency", "default_currency"),
                ("country", "country"),
                _MODIFIED,
            ),
            modified_field="modified",
        ),
        "products": EntitySpec(
            path="Item",
            key_fields=("name",),
            fields=mapped(
                ("sku", "item_code"),
                ("name", "item_name"),
                ("description", "description"),
                ("unit", "stock_uom"),
                ("price", "standard_rate"),
                ("category", "item_group"),
                _MODIFIED,
            ),
            modified_field="modified",
        ),
        "inventory": EntitySpec(
            path="Bin",
            key_fields=("name",),
            operations=READ,
            fields=mapped(
                ("product_id", "item_code"),
                ("location_id", "warehouse"),
                ("quantity", "actual_qty"),
                ("unit", "stock_uom"),
                _MODIFIED,
            ),
            modified_field="modified",
            notes="stock changes through Stock Entry documents",
        ),
        "sales_orders": EntitySpec(
            path="Sales Order",
            key_fields=("name",),
            fields=mapped(
                ("number", "~name"),
                ("customer_id", "customer"),
                ("order_date", "transaction_date", "date"),
                ("currency", "currency"),
                ("status", "~status"),
                ("total", "~grand_total"),
                _MODIFIED,
            ),
            lines=_LINES,
            modified_field="modified",
            notes=_LIST_NOTE,
        ),
        "purchase_orders": EntitySpec(
            path="Purchase Order",
            key_fields=("name",),
            fields=mapped(
                ("number", "~name"),
                ("vendor_id", "supplier"),
                ("order_date", "transaction_date", "date"),
                ("currency", "currency"),
                ("status", "~status"),
                ("total", "~grand_total"),
                _MODIFIED,
            ),
            lines=_LINES,
            modified_field="modified",
            notes=_LIST_NOTE,
        ),
        "invoices": EntitySpec(
            path="Sales Invoice",
            key_fields=("name",),
            fields=mapped(
                ("number", "~name"),
                ("customer_id", "customer"),
                ("invoice_date", "posting_date", "date"),
                ("due_date", "due_date", "date"),
                ("currency", "currency"),
                ("status", "~status"),
                ("total", "~grand_total"),
                ("balance", "~outstanding_amount"),
                _MODIFIED,
            ),
            lines=_LINES,
            modified_field="modified",
            notes=_LIST_NOTE,
        ),
        "payments": EntitySpec(
            path="Payment Entry",
            key_fields=("name",),
            fields=mapped(
                ("number", "~name"),
                ("party_id", "party"),
                ("payment_date", "posting_date", "date"),
                ("amount", "paid_amount"),
                ("currency", "paid_from_account_currency"),
                ("method", "mode_of_payment"),
                ("reference", "reference_no"),
                _MODIFIED,
            ),
            modified_field="modified",
            notes="create needs payment_type, party_type and accounts via provider_fields",
        ),
        "employees": EntitySpec(
            path="Employee",
            key_fields=("name",),
            fields=mapped(
                ("number", "~name"),
                ("name", "~employee_name"),
                ("first_name", "first_name"),
                ("last_name", "last_name"),
                ("email", "company_email"),
                ("department", "department"),
                ("title", "designation"),
                _MODIFIED,
            ),
            modified_field="modified",
        ),
        "financial_records": EntitySpec(
            path="GL Entry",
            key_fields=("name",),
            operations=READ,
            fields=mapped(
                ("number", "voucher_no"),
                ("account_id", "account"),
                ("posting_date", "posting_date", "date"),
                ("debit", "debit"),
                ("credit", "credit"),
                ("currency", "account_currency"),
                ("description", "remarks"),
                _MODIFIED,
            ),
            modified_field="modified",
            notes="write Journal Entry documents through a custom entity",
        ),
    }

    @staticmethod
    def _path(spec: EntitySpec) -> str:
        return f"/api/resource/{quote(spec.path, safe='')}"

    def _record_path(self, spec: EntitySpec, key: RecordKey) -> str:
        name = self.key_values(spec, key)[spec.key_fields[0]]
        return f"{self._path(spec)}/{quote(name, safe='')}"

    @staticmethod
    def _literal(value: Scalar) -> JsonValue:
        if isinstance(value, datetime):
            return value.strftime("%Y-%m-%d %H:%M:%S")
        return value.isoformat() if isinstance(value, date) else to_json(value)

    def _filter(self, item: Filter) -> JsonValue:
        value: JsonValue
        if item.op is FilterOp.IN:
            value = [self._literal(v) for v in item.values]
        elif item.op in {FilterOp.CONTAINS, FilterOp.STARTSWITH}:
            raw = str(item.scalar)
            if "%" in raw or "_" in raw:
                raise self.unsupported_filter(item)
            value = f"%{raw}%" if item.op is FilterOp.CONTAINS else f"{raw}%"
        else:
            value = self._literal(item.scalar)
        return [item.field, _OPERATORS[item.op], value]

    async def list_page(self, spec: EntitySpec, query: Query, cursor: str | None) -> RawPage:
        """Fetch a page by offset, ordered by name unless sorted."""
        start = int(cursor) if cursor else 0
        size = self.page_size(query)
        fields = list(dict.fromkeys([*spec.key_fields, *query.select])) if query.select else ["*"]
        order = ", ".join(f"{s.field} {'desc' if s.descending else 'asc'}" for s in query.sort)
        params: dict[str, str] = {
            "fields": json.dumps(fields),
            "limit_start": str(start),
            "limit_page_length": str(size),
            "order_by": order or "name asc",
        }
        if query.filters:
            params["filters"] = json.dumps([self._filter(f) for f in query.filters])
        response = await self.http.request(
            "GET", self._path(spec), params={**params, **query.params}
        )
        records = self.record_list(self.json_object(response).get("data"))
        more = len(records) >= size
        return RawPage(records=records, next_cursor=str(start + len(records)) if more else None)

    async def get(self, spec: EntitySpec, key: RecordKey) -> JsonObject:
        """Fetch a document with child tables."""
        response = await self.http.request("GET", self._record_path(spec, key))
        return as_object(self.json_object(response).get("data"))

    async def create(
        self, spec: EntitySpec, payload: JsonObject, idempotency_key: str | None
    ) -> JsonObject:
        """Create a document (as draft, for submittable types)."""
        response = await self.http.request(
            "POST", self._path(spec), json=payload, idempotency_key=idempotency_key
        )
        return as_object(self.json_object(response).get("data"))

    async def update(
        self, spec: EntitySpec, key: RecordKey, payload: JsonObject, etag: str | None
    ) -> JsonObject | None:
        """Update a document."""
        if etag is not None:
            raise self.unsupported("etags")
        response = await self.http.request("PUT", self._record_path(spec, key), json=payload)
        return as_object(self.json_object(response).get("data"))

    async def delete(self, spec: EntitySpec, key: RecordKey, etag: str | None) -> None:
        """Delete a document."""
        if etag is not None:
            raise self.unsupported("etags")
        await self.http.request("DELETE", self._record_path(spec, key))

    async def health_check(self) -> None:
        """Check the credentials."""
        await self.http.request("GET", "/api/method/frappe.auth.get_logged_user")

    @operation
    async def call_method(
        self, *, method: str, params: JsonObject | None = None, read_only: bool = False
    ) -> JsonValue:
        """Call a whitelisted server method."""
        path = f"/api/method/{quote(method, safe='.')}"
        if read_only:
            response = await self.http.request(
                "GET", path, params={k: text(v) for k, v in (params or {}).items()}
            )
        else:
            response = await self.http.request("POST", path, json=params or {})
        return self.json_object(response).get("message")

    @operation
    async def submit(self, *, entity: str, key: str) -> JsonObject:
        """Submit a draft document."""
        return await self._set_status(entity, key, 1)

    @operation
    async def cancel(self, *, entity: str, key: str) -> JsonObject:
        """Cancel a submitted document."""
        return await self._set_status(entity, key, 2)

    async def _set_status(self, entity: str, key: str, status: int) -> JsonObject:
        spec = self.entities.get(entity)
        if spec is None:
            raise ConfigurationError(f"unknown entity '{entity}'")
        return await self.update(spec, key, {"docstatus": status}, None) or {}

    async def upload_file(self, upload: FileUpload) -> JsonObject:
        """Upload a private file, optionally attached to a document."""
        data = {"is_private": "1"}
        if upload.entity and upload.record_id:
            spec = self.entities.get(upload.entity)
            data |= {"doctype": spec.path if spec else upload.entity, "docname": upload.record_id}
        response = await self.http.request(
            "POST",
            "/api/method/upload_file",
            data=data,
            files={"file": (upload.filename, upload.content, upload.content_type)},
        )
        return as_object(self.json_object(response).get("message"))

    async def register_webhook(self, registration: WebhookRegistration) -> JsonObject:
        """Create a Webhook document; ``event`` is a doc event such as ``on_update``."""
        spec = self.entities.get(registration.entity)
        payload: JsonObject = {
            "webhook_doctype": spec.path if spec else registration.entity,
            "webhook_docevent": registration.event,
            "request_url": registration.url,
            "request_method": "POST",
            "request_structure": "JSON",
            "webhook_json": "{{ doc | json }}",
            "enabled": 1,
            "enable_security": 1 if registration.secret else 0,
        }
        if registration.secret:
            payload["webhook_secret"] = registration.secret
        return await self.create(EntitySpec(path="Webhook", operations=CRUD), payload, None)

    def parse_error(self, response: httpx.Response) -> ErrorDetails:
        """Extract ``exc_type`` and server messages."""
        try:
            body = as_object(response.json())
        except ValueError:
            return ErrorDetails()
        code = text(body.get("exc_type")) or None
        message = text(body.get("exception")) or text(body.get("message")) or code
        return ErrorDetails(code=code, message=message, not_found=code == "DoesNotExistError")
