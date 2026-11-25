"""Microsoft Dynamics 365 Finance and Operations (OData v4 data entities)."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime
from typing import ClassVar

from py_erp_bridge.config.settings import ProviderConfig
from py_erp_bridge.core.adapter import operation
from py_erp_bridge.core.capabilities import (
    CRUD,
    READ,
    EntitySpec,
    Evidence,
    Operation,
    Protocol,
    ProviderInfo,
    mapped,
)
from py_erp_bridge.core.query import Filter, FilterOp, Query, RawPage
from py_erp_bridge.providers._odata import ODataAdapter
from py_erp_bridge.transport.odata import ODataVersion, literal
from py_erp_bridge.types import JsonObject, RecordKey, as_objects

_NO_LINES = "lines are a separate entity; use custom_entities for SalesOrderLinesV2 etc."


class Dynamics365Config(ProviderConfig):
    """``base_url`` is the environment URL, e.g. ``https://org.operations.dynamics.com``."""

    #: Legal entity (dataAreaId) used for reads, keys and creates.
    company: str | None = None
    #: Read across all companies the user can access.
    cross_company: bool = False


class Dynamics365Adapter(ODataAdapter):
    """Finance and Operations apps. Business Central and Dataverse are different APIs."""

    version = ODataVersion.V4
    max_page_size = 10_000
    config_model = Dynamics365Config
    config: Dynamics365Config
    info = ProviderInfo(
        name="dynamics365",
        title="Microsoft Dynamics 365 Finance & Operations",
        protocols=frozenset({Protocol.ODATA_V4}),
        auth_types=frozenset({"oauth2_client_credentials", "oauth2_authorization_code", "bearer"}),
        native_bulk=True,
        documentation=(
            "https://learn.microsoft.com/dynamics365/fin-ops-core/dev-itpro/data-entities/odata",
            "https://learn.microsoft.com/dynamics365/fin-ops-core/dev-itpro/data-entities/"
            "services-home-page",
        ),
        limitations=(
            "Covers Finance & Operations only, not Business Central or Dataverse apps.",
            "Order lines are separate entities and are not embedded in headers.",
            "'contains' and 'startswith' use wildcard equality; strings with * are rejected.",
            "Business events are configured inside the ERP, not through this adapter.",
        ),
    )
    default_entities: ClassVar[Mapping[str, EntitySpec]] = {
        "customers": EntitySpec(
            path="CustomersV3",
            key_fields=("dataAreaId", "CustomerAccount"),
            operations=CRUD,
            fields=mapped(
                ("name", "OrganizationName"),
                ("email", "PrimaryContactEmail"),
                ("currency", "SalesCurrencyCode"),
            ),
            evidence=Evidence.OFFICIAL_DOCS,
            notes="create needs CustomerGroupId and PartyType via provider_fields",
        ),
        "vendors": EntitySpec(
            path="VendorsV2",
            key_fields=("dataAreaId", "VendorAccountNumber"),
            operations=CRUD,
            fields=mapped(
                ("name", "VendorOrganizationName"),
                ("currency", "CurrencyCode"),
            ),
            evidence=Evidence.SECONDARY,
        ),
        "products": EntitySpec(
            path="ReleasedProductsV2",
            key_fields=("dataAreaId", "ItemNumber"),
            operations=READ | {Operation.CREATE, Operation.UPDATE},
            fields=mapped(("sku", "ProductNumber"), ("name", "SearchName")),
        ),
        "sales_orders": EntitySpec(
            path="SalesOrderHeadersV2",
            key_fields=("dataAreaId", "SalesOrderNumber"),
            operations=CRUD,
            fields=mapped(
                ("number", "~SalesOrderNumber"),
                ("customer_id", "OrderingCustomerAccountNumber"),
                ("currency", "CurrencyCode"),
                ("status", "~SalesOrderStatus"),
            ),
            evidence=Evidence.OFFICIAL_DOCS,
            notes=_NO_LINES,
        ),
        "purchase_orders": EntitySpec(
            path="PurchaseOrderHeadersV2",
            key_fields=("dataAreaId", "PurchaseOrderNumber"),
            operations=CRUD,
            fields=mapped(
                ("number", "~PurchaseOrderNumber"),
                ("vendor_id", "OrderVendorAccountNumber"),
                ("currency", "CurrencyCode"),
                ("order_date", "~AccountingDate", "date"),
                ("status", "~PurchaseOrderStatus"),
            ),
            notes=_NO_LINES,
        ),
        "invoices": EntitySpec(
            path="SalesInvoiceHeadersV2",
            key_fields=("dataAreaId", "InvoiceNumber", "InvoiceDate", "LedgerVoucher"),
            operations=frozenset({Operation.LIST}),
            fields=mapped(
                ("number", "InvoiceNumber"),
                ("customer_id", "InvoiceCustomerAccountNumber"),
                ("invoice_date", "InvoiceDate", "date"),
                ("currency", "CurrencyCode"),
                ("total", "TotalInvoiceAmount", "decimal"),
            ),
            notes="confirm name and keys with execute('list_entities')",
        ),
        "financial_records": EntitySpec(
            path="LedgerJournalHeaders",
            key_fields=("dataAreaId", "JournalBatchNumber"),
            operations=CRUD,
            fields=mapped(("number", "~JournalBatchNumber"), ("description", "Description")),
            notes="journal headers; lines are LedgerJournalLines",
        ),
    }

    def collection_path(self, spec: EntitySpec) -> str:
        """Entity set path."""
        return f"/data/{spec.path}"

    def service_path(self, spec: EntitySpec) -> str:
        """Service root."""
        return "/data"

    def base_params(self) -> dict[str, str]:
        """Cross-company reads are needed for any company but the user's default."""
        if self.config.cross_company or self.config.company:
            return {"cross-company": "true"}
        return {}

    def key_values(self, spec: EntitySpec, key: RecordKey) -> dict[str, str]:
        """Accept a bare account if ``company`` is configured."""
        if (
            isinstance(key, (str, int))
            and len(spec.key_fields) == 2
            and spec.key_fields[0] == "dataAreaId"
            and "/" not in str(key)
            and self.config.company
        ):
            key = f"{self.config.company}/{key}"
        return super().key_values(spec, key)

    def render_filter(self, item: Filter, version: ODataVersion) -> str:
        """F&O documents wildcard equality and no string functions."""
        if item.op in {FilterOp.CONTAINS, FilterOp.STARTSWITH}:
            value = str(item.scalar)
            if "*" in value:
                raise self.unsupported_filter(item)
            pattern = f"*{value}*" if item.op is FilterOp.CONTAINS else f"{value}*"
            return f"{item.field} eq {literal(pattern, version)}"
        if item.op is FilterOp.IN and not item.values:
            raise self.unsupported_filter(item)
        if isinstance(item.value, date) and not isinstance(item.value, datetime):
            # F&O exposes dates as Edm.DateTimeOffset.
            midnight = datetime(*item.value.timetuple()[:3], tzinfo=UTC)
            item = item.model_copy(update={"value": midnight})
        return super().render_filter(item, version)

    async def list_page(self, spec: EntitySpec, query: Query, cursor: str | None) -> RawPage:
        """Restrict to the configured company unless cross-company is requested."""
        if (
            self.config.company
            and not self.config.cross_company
            and "dataAreaId" in spec.key_fields
            and not any(f.field == "dataAreaId" for f in query.filters)
        ):
            company = Filter(field="dataAreaId", value=self.config.company)
            query = query.model_copy(update={"filters": (company, *query.filters)})
        return await super().list_page(spec, query, cursor)

    async def create(
        self, spec: EntitySpec, payload: JsonObject, idempotency_key: str | None
    ) -> JsonObject:
        """Create in the configured company."""
        return await super().create(spec, self._with_company(spec, payload), idempotency_key)

    async def bulk_create(
        self, spec: EntitySpec, payloads: list[JsonObject]
    ) -> list[JsonObject | Exception] | None:
        """Create in the configured company."""
        return await super().bulk_create(spec, [self._with_company(spec, p) for p in payloads])

    def _with_company(self, spec: EntitySpec, payload: JsonObject) -> JsonObject:
        if self.config.company and "dataAreaId" in spec.key_fields:
            return {"dataAreaId": self.config.company, **payload}
        return payload

    async def health_check(self) -> None:
        """Read the service document."""
        await self.http.request("GET", "/data", headers={"Accept": "application/json"})

    @operation
    async def list_entities(self) -> list[JsonObject]:
        """List the environment's data entities; use it to confirm entity names."""
        response = await self.http.request("GET", "/Metadata/DataEntities")
        data = response.json()
        return as_objects(data if isinstance(data, list) else data.get("value"))
