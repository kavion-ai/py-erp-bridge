"""Odoo: JSON-2 (19+) and legacy JSON-RPC (older releases)."""

from __future__ import annotations

import base64
from collections.abc import Mapping
from datetime import UTC, date, datetime
from typing import ClassVar, Literal

import httpx
from pydantic import Field

from py_erp_bridge.auth.static import NoAuthStrategy
from py_erp_bridge.config.auth import BasicAuth
from py_erp_bridge.config.settings import ProviderConfig
from py_erp_bridge.core.adapter import ERPAdapter, FileUpload, operation
from py_erp_bridge.core.capabilities import (
    READ,
    EntitySpec,
    LineMap,
    Protocol,
    ProviderInfo,
    mapped,
)
from py_erp_bridge.core.query import Filter, FilterOp, Query, RawPage
from py_erp_bridge.core.transforms import to_json
from py_erp_bridge.exceptions import (
    APIError,
    AuthenticationError,
    BusinessRuleError,
    ConfigurationError,
    NotFoundError,
    PayloadValidationError,
    UniversalERPError,
)
from py_erp_bridge.transport.http import ErrorDetails, HttpTransport
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
    FilterOp.EQ: "=",
    FilterOp.NE: "!=",
    FilterOp.GT: ">",
    FilterOp.GE: ">=",
    FilterOp.LT: "<",
    FilterOp.LE: "<=",
    FilterOp.IN: "in",
    FilterOp.CONTAINS: "ilike",
    FilterOp.STARTSWITH: "=ilike",
}
_MODIFIED = ("modified_at", "~write_date")
_READ_METHODS = frozenset({"search_read", "read", "search", "search_count", "fields_get"})
_PARTY = (
    ("name", "name"),
    ("email", "email"),
    ("phone", "phone"),
    ("tax_id", "vat"),
    ("street", "street"),
    ("city", "city"),
    ("postal_code", "zip"),
    ("country", "country_id", "relation"),
    ("region", "state_id", "relation"),
    _MODIFIED,
)
_LINES_NOTE = "items can be written; reads return only line ids in raw['order_line']"


class OdooConfig(ProviderConfig):
    """``api='jsonrpc'`` needs ``database`` and ``basic`` auth (login + API key)."""

    api: Literal["json2", "jsonrpc"] = "json2"
    database: str | None = None
    context: JsonObject = Field(default_factory=dict)


class OdooAdapter(ERPAdapter):
    """Odoo. Entity paths are model names."""

    config_model = OdooConfig
    config: OdooConfig
    info = ProviderInfo(
        name="odoo",
        title="Odoo",
        protocols=frozenset({Protocol.REST, Protocol.JSON_RPC}),
        auth_types=frozenset({"bearer", "basic"}),
        native_bulk=True,
        file_upload=True,
        documentation=(
            "https://www.odoo.com/documentation/19.0/developer/reference/external_api.html",
        ),
        limitations=(
            "The external API is only available on Custom pricing plans.",
            "JSON-2 needs Odoo 19+; JSON-RPC is deprecated and will be removed.",
            "Each call is one transaction; calls cannot share one.",
            "Native bulk create is all-or-nothing.",
            "Outgoing webhooks are configured as automation rules inside Odoo.",
        ),
    )
    default_entities: ClassVar[Mapping[str, EntitySpec]] = {
        "customers": EntitySpec(
            path="res.partner",
            fields=mapped(*_PARTY),
            create_defaults={"customer_rank": 1},
            modified_field="write_date",
            extra={"domain": "customer_rank>0"},
        ),
        "vendors": EntitySpec(
            path="res.partner",
            fields=mapped(*_PARTY),
            create_defaults={"supplier_rank": 1},
            modified_field="write_date",
            extra={"domain": "supplier_rank>0"},
        ),
        "products": EntitySpec(
            path="product.product",
            fields=mapped(
                ("sku", "default_code"),
                ("name", "name"),
                ("description", "description_sale"),
                ("unit", "uom_id", "relation"),
                ("price", "list_price"),
                ("category", "categ_id", "relation"),
                _MODIFIED,
            ),
            modified_field="write_date",
        ),
        "inventory": EntitySpec(
            path="stock.quant",
            operations=READ,
            fields=mapped(
                ("product_id", "product_id", "relation"),
                ("location_id", "location_id", "relation"),
                ("quantity", "quantity"),
                ("unit", "product_uom_id", "relation"),
                _MODIFIED,
            ),
            modified_field="write_date",
        ),
        "sales_orders": EntitySpec(
            path="sale.order",
            fields=mapped(
                ("number", "~name"),
                ("customer_id", "partner_id", "relation"),
                ("order_date", "date_order", "date"),
                ("currency", "~currency_id", "relation"),
                ("status", "~state"),
                ("total", "~amount_total"),
                _MODIFIED,
            ),
            lines=LineMap(
                fields=mapped(
                    ("product_id", "product_id", "relation"),
                    ("description", "name"),
                    ("quantity", "product_uom_qty"),
                    ("unit_price", "price_unit"),
                ),
                write_path="order_line",
            ),
            modified_field="write_date",
            notes=_LINES_NOTE,
        ),
        "purchase_orders": EntitySpec(
            path="purchase.order",
            fields=mapped(
                ("number", "~name"),
                ("vendor_id", "partner_id", "relation"),
                ("order_date", "date_order", "date"),
                ("currency", "~currency_id", "relation"),
                ("status", "~state"),
                ("total", "~amount_total"),
                _MODIFIED,
            ),
            lines=LineMap(
                fields=mapped(
                    ("product_id", "product_id", "relation"),
                    ("description", "name"),
                    ("quantity", "product_qty"),
                    ("unit_price", "price_unit"),
                ),
                write_path="order_line",
            ),
            modified_field="write_date",
            notes=_LINES_NOTE,
        ),
        "invoices": EntitySpec(
            path="account.move",
            fields=mapped(
                ("number", "~name"),
                ("customer_id", "partner_id", "relation"),
                ("invoice_date", "invoice_date", "date"),
                ("due_date", "invoice_date_due", "date"),
                ("currency", "~currency_id", "relation"),
                ("status", "~state"),
                ("total", "~amount_total"),
                ("balance", "~amount_residual"),
                _MODIFIED,
            ),
            lines=LineMap(
                fields=mapped(
                    ("product_id", "product_id", "relation"),
                    ("description", "name"),
                    ("quantity", "quantity"),
                    ("unit_price", "price_unit"),
                ),
                write_path="invoice_line_ids",
            ),
            create_defaults={"move_type": "out_invoice"},
            modified_field="write_date",
            extra={"domain": "move_type=out_invoice"},
            notes="drafts must be posted with execute('call', method='action_post')",
        ),
        "payments": EntitySpec(
            path="account.payment",
            fields=mapped(
                ("number", "~name"),
                ("party_id", "partner_id", "relation"),
                ("payment_date", "date", "date"),
                ("amount", "amount"),
                ("currency", "~currency_id", "relation"),
                ("reference", "memo"),
                _MODIFIED,
            ),
            modified_field="write_date",
            notes="'memo' was called 'ref' before Odoo 18; override if needed",
        ),
        "employees": EntitySpec(
            path="hr.employee",
            fields=mapped(
                ("name", "name"),
                ("email", "work_email"),
                ("department", "department_id", "relation"),
                ("title", "job_title"),
                _MODIFIED,
            ),
            modified_field="write_date",
        ),
        "financial_records": EntitySpec(
            path="account.move.line",
            operations=READ,
            fields=mapped(
                ("number", "move_name"),
                ("account_id", "account_id", "relation"),
                ("posting_date", "date", "date"),
                ("debit", "debit"),
                ("credit", "credit"),
                ("amount", "balance"),
                ("currency", "currency_id", "relation"),
                ("description", "name"),
                _MODIFIED,
            ),
            modified_field="write_date",
        ),
    }

    def __init__(self, config: ProviderConfig, http: HttpTransport) -> None:
        super().__init__(config, http)
        self._uid: int | None = None
        auth = self.config.auth
        if self.config.api == "jsonrpc":
            if not isinstance(auth, BasicAuth) or not self.config.database:
                raise ConfigurationError("odoo jsonrpc needs 'database' and basic auth")
            # JSON-RPC carries credentials in the body.
            http.auth = NoAuthStrategy()
        elif isinstance(auth, BasicAuth):
            raise ConfigurationError("odoo json2 needs bearer auth with an API key")

    # -- RPC ------------------------------------------------------------------

    async def _rpc(
        self, service: str, method: str, args: list[JsonValue], *, safe: bool
    ) -> JsonValue:
        response = await self.http.request(
            "POST",
            "/jsonrpc",
            json={
                "jsonrpc": "2.0",
                "method": "call",
                "params": {"service": service, "method": method, "args": args},
                "id": 1,
            },
            idempotent=safe,
        )
        body = self.json_object(response)
        error = as_object(body.get("error"))
        if error:
            data = as_object(error.get("data"))
            name = text(data.get("name"))
            message = text(data.get("message")) or text(error.get("message"))
            kind: type[APIError] = BusinessRuleError
            if name.endswith("MissingError"):
                kind = NotFoundError
            raise kind(f"odoo: {message}", provider="odoo", code=name or None)
        return body.get("result")

    async def _execute_kw(self, model: str, method: str, kwargs: JsonObject) -> JsonValue:
        auth = self.config.auth
        if not isinstance(auth, BasicAuth) or self.config.database is None:
            raise ConfigurationError("odoo jsonrpc needs 'database' and basic auth")
        secret = auth.password.get_secret_value()
        database = self.config.database
        if self._uid is None:
            uid = await self._rpc("common", "login", [database, auth.username, secret], safe=True)
            if not isinstance(uid, int) or uid is False:
                raise AuthenticationError("odoo rejected the login")
            self._uid = uid
        named = dict(kwargs)
        positional: list[JsonValue] = [named.pop("ids")] if "ids" in named else []
        return await self._rpc(
            "object",
            "execute_kw",
            [database, self._uid, secret, model, method, positional, named],
            safe=method in _READ_METHODS,
        )

    @operation
    async def call(self, *, model: str, method: str, params: JsonObject | None = None) -> JsonValue:
        """Call a model method with named arguments (``ids`` selects records)."""
        payload: JsonObject = dict(params or {})
        if self.config.context and "context" not in payload:
            payload["context"] = self.config.context
        if self.config.api == "jsonrpc":
            return await self._execute_kw(model, method, payload)
        headers = {"X-Odoo-Database": self.config.database} if self.config.database else {}
        response = await self.http.request(
            "POST",
            f"/json/2/{model}/{method}",
            json=payload,
            headers=headers,
            idempotent=method in _READ_METHODS,
        )
        try:
            result: JsonValue = response.json()
        except ValueError:
            raise APIError("odoo: response is not JSON", provider="odoo") from None
        return result

    # -- CRUD -----------------------------------------------------------------

    @staticmethod
    def _literal(value: Scalar) -> JsonValue:
        if isinstance(value, datetime):
            utc = value.astimezone(UTC) if value.tzinfo else value
            return utc.strftime("%Y-%m-%d %H:%M:%S")
        return value.isoformat() if isinstance(value, date) else to_json(value)

    def _term(self, item: Filter) -> JsonValue:
        value: JsonValue
        if item.op is FilterOp.IN:
            value = [self._literal(v) for v in item.values]
        elif item.op is FilterOp.STARTSWITH:
            raw = str(item.scalar)
            if "%" in raw or "_" in raw:
                raise self.unsupported_filter(item)
            value = f"{raw}%"
        else:
            value = self._literal(item.scalar)
        return [item.field, _OPERATORS[item.op], value]

    @staticmethod
    def _base_domain(spec: EntitySpec) -> list[JsonValue]:
        rule = spec.extra.get("domain", "")
        if ">" in rule:
            name, _, number = rule.partition(">")
            return [[name, ">", int(number)]]
        if "=" in rule:
            name, _, value = rule.partition("=")
            return [[name, "=", value]]
        return []

    def _field_names(self, spec: EntitySpec, select: tuple[str, ...] = ()) -> list[JsonValue]:
        names = list(select) or [f.source for f in spec.fields]
        if not names:
            return []  # Odoo returns every readable field.
        if not select and spec.lines and spec.lines.write_path:
            names.append(spec.lines.write_path)
        return list(dict.fromkeys(["id", *names]))

    async def list_page(self, spec: EntitySpec, query: Query, cursor: str | None) -> RawPage:
        """Fetch a page by offset, ordered by id unless sorted."""
        offset = int(cursor) if cursor else 0
        size = self.page_size(query)
        order = ", ".join(f"{s.field} {'desc' if s.descending else 'asc'}" for s in query.sort)
        result = await self.call(
            model=spec.path,
            method="search_read",
            params={
                "domain": [*self._base_domain(spec), *(self._term(f) for f in query.filters)],
                "fields": self._field_names(spec, query.select),
                "offset": offset,
                "limit": size,
                "order": order or "id asc",
            },
        )
        records = self.record_list(result)
        more = len(records) >= size
        return RawPage(records=records, next_cursor=str(offset + len(records)) if more else None)

    def _ids(self, spec: EntitySpec, key: RecordKey) -> list[JsonValue]:
        value = as_int(self.key_values(spec, key)["id"])
        if value is None:
            raise PayloadValidationError("odoo record ids are integers")
        return [value]

    async def get(self, spec: EntitySpec, key: RecordKey) -> JsonObject:
        """Fetch a record. Uses a search so that missing ids are detected reliably."""
        result = self.record_list(
            await self.call(
                model=spec.path,
                method="search_read",
                params={
                    "domain": [["id", "=", self._ids(spec, key)[0]]],
                    "fields": self._field_names(spec),
                    "limit": 1,
                },
            )
        )
        if not result:
            raise NotFoundError(f"{spec.path} {key} not found", provider="odoo")
        return result[0]

    def _commands(self, spec: EntitySpec, payload: JsonObject, *, replace: bool) -> JsonObject:
        """Wrap lines in one2many commands."""
        path = spec.lines.write_path if spec.lines else None
        if path is None or path not in payload:
            return payload
        commands: list[JsonValue] = [[5, 0, 0]] if replace else []
        commands += [[0, 0, line] for line in as_objects(payload[path])]
        return {**payload, path: commands}

    async def create(
        self, spec: EntitySpec, payload: JsonObject, idempotency_key: str | None
    ) -> JsonObject:
        """Create a record; returns its id."""
        if idempotency_key is not None:
            raise self.unsupported("idempotency keys")
        created = await self._create_many(spec, [payload])
        return created[0]

    async def _create_many(self, spec: EntitySpec, payloads: list[JsonObject]) -> list[JsonObject]:
        values: list[JsonValue] = [self._commands(spec, p, replace=False) for p in payloads]
        result = await self.call(model=spec.path, method="create", params={"vals_list": values})
        ids = result if isinstance(result, list) else [result]
        if len(ids) != len(payloads) or not all(isinstance(i, int) for i in ids):
            raise APIError("odoo: unexpected create response", provider="odoo")
        return [{"id": i} for i in ids]

    async def bulk_create(
        self, spec: EntitySpec, payloads: list[JsonObject]
    ) -> list[JsonObject | Exception] | None:
        """Create all records in one transaction."""
        try:
            return list(await self._create_many(spec, payloads))
        except UniversalERPError as exc:
            return [exc] * len(payloads)

    async def update(
        self, spec: EntitySpec, key: RecordKey, payload: JsonObject, etag: str | None
    ) -> JsonObject | None:
        """Write fields. Given lines replace the existing ones."""
        if etag is not None:
            raise self.unsupported("etags")
        await self.call(
            model=spec.path,
            method="write",
            params={
                "ids": self._ids(spec, key),
                "vals": self._commands(spec, payload, replace=True),
            },
        )
        return None

    async def delete(self, spec: EntitySpec, key: RecordKey, etag: str | None) -> None:
        """Delete a record."""
        if etag is not None:
            raise self.unsupported("etags")
        await self.call(model=spec.path, method="unlink", params={"ids": self._ids(spec, key)})

    async def health_check(self) -> None:
        """Run a harmless search."""
        await self.call(
            model="res.partner", method="search_read", params={"fields": ["id"], "limit": 1}
        )

    async def upload_file(self, upload: FileUpload) -> JsonObject:
        """Create an attachment."""
        values: JsonObject = {
            "name": upload.filename,
            "datas": base64.b64encode(upload.content).decode(),
            "mimetype": upload.content_type,
        }
        if upload.entity and upload.record_id:
            spec = self.entities.get(upload.entity)
            values["res_model"] = spec.path if spec else upload.entity
            values["res_id"] = as_int(upload.record_id)
        return (await self._create_many(EntitySpec(path="ir.attachment"), [values]))[0]

    def parse_error(self, response: httpx.Response) -> ErrorDetails:
        """Extract JSON-2 errors."""
        try:
            body = as_object(response.json())
        except ValueError:
            return ErrorDetails()
        code = text(body.get("name")) or None
        return ErrorDetails(
            code=code,
            message=text(body.get("message")) or None,
            not_found=bool(code and code.endswith("MissingError")),
        )
