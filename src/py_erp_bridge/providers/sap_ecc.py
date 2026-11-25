"""SAP ECC over RFC: table reads plus BAPIs for writes."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import ClassVar, cast

from pydantic import Field

from py_erp_bridge.config.settings import ProviderConfig
from py_erp_bridge.core.adapter import ERPAdapter, operation
from py_erp_bridge.core.capabilities import (
    READ,
    EntitySpec,
    Operation,
    Protocol,
    ProviderInfo,
    mapped,
)
from py_erp_bridge.core.query import Filter, FilterOp, Query, RawPage
from py_erp_bridge.exceptions import (
    BusinessRuleError,
    ConfigurationError,
    NotFoundError,
)
from py_erp_bridge.transport.http import HttpTransport
from py_erp_bridge.transport.rfc import RfcCall, RfcConfig, RfcTransport
from py_erp_bridge.types import JsonObject, RecordKey, Scalar, as_object, as_objects, text

_LIST_ONLY = frozenset({Operation.LIST})
_OPERATORS = {
    FilterOp.EQ: "=",
    FilterOp.NE: "<>",
    FilterOp.GT: ">",
    FilterOp.GE: ">=",
    FilterOp.LT: "<",
    FilterOp.LE: "<=",
}
_OPTION_WIDTH = 72
_DELIMITER = "\x1f"


class SapEccConfig(ProviderConfig):
    """RFC settings. ``base_url`` only matters for custom SAP Gateway services."""

    base_url: str = "https://sap-gateway.invalid"
    rfc: RfcConfig
    #: Replace with your own released table reader; see docs/sap-rfc.md.
    read_table_function: str = "RFC_READ_TABLE"
    page_size: int = Field(default=500, ge=1)


class SapEccAdapter(ERPAdapter):
    """ECC 6.0. Entity paths are transparent table names."""

    config_model = SapEccConfig
    config: SapEccConfig
    max_page_size = 10_000
    info = ProviderInfo(
        name="sap_ecc",
        title="SAP ECC",
        protocols=frozenset({Protocol.RFC}),
        auth_types=frozenset({"none", "basic"}),
        sorting=False,
        documentation=(
            "https://github.com/SAP-archive/PyRFC",
            "https://support.sap.com/en/product/connectors/nwrfcsdk.html",
        ),
        limitations=(
            "Needs the SAP NW RFC SDK and a pyrfc-compatible module; SAP archived PyRFC.",
            "RFC_READ_TABLE is not released by SAP for customer use and limits row width.",
            "Built-in writes: sales orders and purchase orders. Use execute('call_bapi').",
            "Rows are read without a guaranteed order; sorting is not available.",
            "No webhooks: use IDocs, or poll.",
        ),
    )
    default_entities: ClassVar[Mapping[str, EntitySpec]] = {
        "customers": EntitySpec(
            path="KNA1",
            key_fields=("KUNNR",),
            operations=READ,
            fields=mapped(
                ("id", "KUNNR", "alpha10"),
                ("name", "NAME1", "strip"),
                ("street", "STRAS", "strip"),
                ("city", "ORT01", "strip"),
                ("region", "REGIO", "strip"),
                ("postal_code", "PSTLZ", "strip"),
                ("country", "LAND1", "strip"),
                ("phone", "TELF1", "strip"),
                ("tax_id", "STCD1", "strip"),
            ),
            extra={"alpha": "10"},
        ),
        "vendors": EntitySpec(
            path="LFA1",
            key_fields=("LIFNR",),
            operations=READ,
            fields=mapped(
                ("id", "LIFNR", "alpha10"),
                ("name", "NAME1", "strip"),
                ("street", "STRAS", "strip"),
                ("city", "ORT01", "strip"),
                ("region", "REGIO", "strip"),
                ("postal_code", "PSTLZ", "strip"),
                ("country", "LAND1", "strip"),
                ("phone", "TELF1", "strip"),
                ("tax_id", "STCD1", "strip"),
            ),
            extra={"alpha": "10"},
        ),
        "products": EntitySpec(
            path="MARA",
            key_fields=("MATNR",),
            operations=READ,
            fields=mapped(
                ("id", "MATNR", "alpha18"),
                ("sku", "MATNR", "alpha18"),
                ("unit", "MEINS", "strip"),
                ("category", "MATKL", "strip"),
            ),
            extra={"alpha": "18"},
            notes="descriptions are in MAKT",
        ),
        "inventory": EntitySpec(
            path="MARD",
            key_fields=("MATNR", "WERKS", "LGORT"),
            operations=_LIST_ONLY,
            fields=mapped(
                ("product_id", "MATNR", "alpha18"),
                ("location_id", "LGORT", "strip"),
                ("quantity", "LABST", "decimal"),
            ),
        ),
        "sales_orders": EntitySpec(
            path="VBAK",
            key_fields=("VBELN",),
            operations=READ | {Operation.CREATE},
            fields=mapped(
                ("id", "VBELN", "alpha10"),
                ("number", "VBELN", "alpha10"),
                ("customer_id", "KUNNR", "alpha10"),
                ("order_date", "ERDAT", "compact_date"),
                ("currency", "WAERK", "strip"),
                ("total", "NETWR", "decimal"),
            ),
            extra={"alpha": "10"},
            notes="create takes BAPI_SALESORDER_CREATEFROMDAT2 parameters in provider_fields",
        ),
        "purchase_orders": EntitySpec(
            path="EKKO",
            key_fields=("EBELN",),
            operations=READ | {Operation.CREATE},
            fields=mapped(
                ("id", "EBELN", "alpha10"),
                ("number", "EBELN", "alpha10"),
                ("vendor_id", "LIFNR", "alpha10"),
                ("order_date", "BEDAT", "compact_date"),
                ("currency", "WAERS", "strip"),
            ),
            extra={"alpha": "10"},
            notes="create takes BAPI_PO_CREATE1 parameters in provider_fields",
        ),
        "invoices": EntitySpec(
            path="VBRK",
            key_fields=("VBELN",),
            operations=READ,
            fields=mapped(
                ("id", "VBELN", "alpha10"),
                ("number", "VBELN", "alpha10"),
                ("customer_id", "KUNAG", "alpha10"),
                ("invoice_date", "FKDAT", "compact_date"),
                ("currency", "WAERK", "strip"),
                ("total", "NETWR", "decimal"),
            ),
            extra={"alpha": "10"},
        ),
        "financial_records": EntitySpec(
            path="BKPF",
            key_fields=("BUKRS", "BELNR", "GJAHR"),
            operations=READ,
            fields=mapped(
                ("number", "BELNR", "alpha10"),
                ("posting_date", "BUDAT", "compact_date"),
                ("currency", "WAERS", "strip"),
                ("description", "BKTXT", "strip"),
            ),
            notes="document headers; line items are in the cluster table BSEG",
        ),
    }
    _CREATE: ClassVar[Mapping[str, tuple[str, str]]] = {
        "VBAK": ("BAPI_SALESORDER_CREATEFROMDAT2", "SALESDOCUMENT"),
        "EKKO": ("BAPI_PO_CREATE1", "EXPPURCHASEORDER"),
    }

    def __init__(self, config: ProviderConfig, http: HttpTransport) -> None:
        super().__init__(config, http)
        self.rfc = RfcTransport(self.config.rfc)

    async def aclose(self) -> None:
        """Close the RFC connection."""
        await self.rfc.aclose()

    async def health_check(self) -> None:
        """Ping the system."""
        await self.rfc.ping()

    # -- reads ----------------------------------------------------------------

    @staticmethod
    def _literal(value: Scalar) -> str:
        if isinstance(value, date):
            value = value.strftime("%Y%m%d")
        escaped = str(value).replace("'", "''")
        return f"'{escaped}'"

    def _condition(self, item: Filter) -> list[str]:
        match item.op:
            case FilterOp.IN:
                if not item.values:
                    raise self.unsupported_filter(item)
                parts = [f"{item.field} = {self._literal(v)}" for v in item.values]
                lines = [f"( {parts[0]}", *(f"OR {p}" for p in parts[1:])]
                lines[-1] += " )"
            case FilterOp.CONTAINS | FilterOp.STARTSWITH:
                value = str(item.scalar)
                if "%" in value or "_" in value:
                    raise self.unsupported_filter(item)
                pattern = f"%{value}%" if item.op is FilterOp.CONTAINS else f"{value}%"
                lines = [f"{item.field} LIKE {self._literal(pattern)}"]
            case _:
                lines = [f"{item.field} {_OPERATORS[item.op]} {self._literal(item.scalar)}"]
        return lines

    def _options(self, filters: tuple[Filter, ...]) -> list[dict[str, str]]:
        lines: list[str] = []
        for item in filters:
            block = self._condition(item)
            if lines:
                block[0] = f"AND {block[0]}"
            lines += block
        too_long = next((line for line in lines if len(line) > _OPTION_WIDTH), None)
        if too_long:
            raise ConfigurationError(
                f"RFC filter line exceeds {_OPTION_WIDTH} characters; shorten the value"
            )
        return [{"TEXT": line} for line in lines]

    async def _read_table(
        self, table: str, columns: list[str], filters: tuple[Filter, ...], size: int, skip: int
    ) -> list[JsonObject]:
        result = await self.rfc.call(
            self.config.read_table_function,
            QUERY_TABLE=table,
            DELIMITER=_DELIMITER,
            ROWCOUNT=size,
            ROWSKIPS=skip,
            FIELDS=[{"FIELDNAME": c} for c in columns],
            OPTIONS=self._options(filters),
        )
        names = [text(f.get("FIELDNAME")) for f in as_objects(result.get("FIELDS"))]
        rows: list[JsonObject] = []
        for row in as_objects(result.get("DATA")):
            values = text(row.get("WA")).split(_DELIMITER)
            if len(values) != len(names):
                raise BusinessRuleError(
                    f"unexpected row layout reading {table}", provider=self.info.name
                )
            rows.append({n: v.strip() for n, v in zip(names, values, strict=True)})
        return rows

    def _columns(self, spec: EntitySpec, select: tuple[str, ...] = ()) -> list[str]:
        wanted = select or tuple(f.source for f in spec.fields)
        return list(dict.fromkeys([*spec.key_fields, *wanted]))

    async def list_page(self, spec: EntitySpec, query: Query, cursor: str | None) -> RawPage:
        """Read a window of rows."""
        if query.sort:
            raise self.unsupported("sorting")
        skip = int(cursor) if cursor else 0
        size = self.page_size(query)
        rows = await self._read_table(
            spec.path, self._columns(spec, query.select), query.filters, size, skip
        )
        more = len(rows) >= size
        return RawPage(records=rows, next_cursor=str(skip + len(rows)) if more else None)

    def key_values(self, spec: EntitySpec, key: RecordKey) -> dict[str, str]:
        """Pad numeric keys to their internal length (ALPHA conversion)."""
        values = super().key_values(spec, key)
        width = int(spec.extra.get("alpha", "0"))
        return {k: v.zfill(width) if v.isdigit() else v for k, v in values.items()}

    def record_id(self, spec: EntitySpec, raw: JsonObject) -> str | None:
        """Return the external form of a padded key."""
        value = super().record_id(spec, raw)
        if value and "alpha" in spec.extra and value.isdigit():
            return value.lstrip("0") or "0"
        return value

    async def get(self, spec: EntitySpec, key: RecordKey) -> JsonObject:
        """Read one row by key."""
        filters = tuple(Filter(field=k, value=v) for k, v in self.key_values(spec, key).items())
        rows = await self._read_table(spec.path, self._columns(spec), filters, 1, 0)
        if not rows:
            raise NotFoundError(f"{spec.path}: record not found", provider=self.info.name)
        return rows[0]

    # -- writes ---------------------------------------------------------------

    async def create(
        self, spec: EntitySpec, payload: JsonObject, idempotency_key: str | None
    ) -> JsonObject:
        """Create through the BAPI that belongs to the table."""
        if idempotency_key is not None:
            raise self.unsupported("idempotency keys")
        if spec.path not in self._CREATE:
            raise self.unsupported(f"create on {spec.path}")
        mapped_fields = {f.source for f in spec.fields} & set(payload)
        if mapped_fields:
            raise ConfigurationError(
                f"{self.info.name} creates need BAPI parameters in provider_fields; "
                f"canonical fields are not translated: {', '.join(sorted(mapped_fields))}"
            )
        function, result_field = self._CREATE[spec.path]
        result = await self.call_bapi(function=function, params=payload)
        number = text(result.get(result_field))
        if not number:
            raise BusinessRuleError(f"{function} returned no number", provider=self.info.name)
        return {spec.key_fields[0]: number, "BAPI": result}

    @operation
    async def call(self, *, function: str, params: JsonObject | None = None) -> JsonObject:
        """Call any remote-enabled function module, without a commit."""
        return await self.rfc.call(function, **cast("dict[str, object]", params or {}))

    @operation
    async def call_bapi(
        self, *, function: str, params: JsonObject | None = None, commit: bool = True
    ) -> JsonObject:
        """Call a BAPI; commit on success, roll back if RETURN reports an error."""

        async def work(call: RfcCall) -> JsonObject:
            try:
                result = await call(function, **cast("dict[str, object]", params or {}))
                self._check_return(function, result)
                if commit:
                    done = await call("BAPI_TRANSACTION_COMMIT", WAIT="X")
                    self._check_return("BAPI_TRANSACTION_COMMIT", done)
            except BusinessRuleError:
                await call("BAPI_TRANSACTION_ROLLBACK")
                raise
            return result

        return await self.rfc.session(work)

    def _check_return(self, function: str, result: JsonObject) -> None:
        returned = result.get("RETURN")
        messages = as_objects(returned) if isinstance(returned, list) else [as_object(returned)]
        errors = [m for m in messages if text(m.get("TYPE")) in {"E", "A", "X"}]
        if errors:
            detail = "; ".join(text(m.get("MESSAGE")) for m in errors)
            raise BusinessRuleError(
                f"{function}: {detail}",
                provider=self.info.name,
                code=text(errors[0].get("ID")) + text(errors[0].get("NUMBER")),
            )
