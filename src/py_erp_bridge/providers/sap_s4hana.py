"""SAP S/4HANA (OData v2 APIs)."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import ClassVar

import httpx

from py_erp_bridge.config.settings import ProviderConfig
from py_erp_bridge.core.adapter import operation
from py_erp_bridge.core.capabilities import (
    CRUD,
    READ,
    EntitySpec,
    Evidence,
    LineMap,
    Operation,
    Protocol,
    ProviderInfo,
    mapped,
)
from py_erp_bridge.exceptions import ConfigurationError, PermissionDeniedError
from py_erp_bridge.providers._odata import ODataAdapter
from py_erp_bridge.transport.http import HttpTransport
from py_erp_bridge.transport.odata import ODataVersion
from py_erp_bridge.transport.xml import soap_call, to_json
from py_erp_bridge.types import JsonObject, JsonValue

_READ_UPDATE = READ | {Operation.UPDATE}


class SapS4HanaConfig(ProviderConfig):
    """``base_url`` is the API host, e.g. ``https://my300000-api.s4hana.ondemand.com``."""

    #: SAP client (Mandant); needed on premise if the system default is not wanted.
    sap_client: str | None = None
    language: str | None = None


class SapS4HanaAdapter(ODataAdapter):
    """S/4HANA Cloud and on-premise. Entity paths are ``SERVICE/EntitySet``."""

    version = ODataVersion.V2
    config_model = SapS4HanaConfig
    config: SapS4HanaConfig
    info = ProviderInfo(
        name="sap_s4hana",
        title="SAP S/4HANA",
        protocols=frozenset({Protocol.ODATA_V2, Protocol.SOAP}),
        auth_types=frozenset(
            {"basic", "oauth2_client_credentials", "oauth2_authorization_code", "bearer"}
        ),
        native_bulk=True,
        documentation=(
            "https://api.sap.com/products/SAPS4HANACloud/apis",
            "https://help.sap.com/docs/SAP_S4HANA_CLOUD",
        ),
        limitations=(
            "Customers and suppliers are created through A_BusinessPartner, not directly.",
            "Billing documents, stock and journal items are read-only APIs.",
            "No HTTP webhooks: events are published through SAP Event Mesh.",
            "Employees belong to SuccessFactors and are not mapped.",
        ),
    )
    default_entities: ClassVar[Mapping[str, EntitySpec]] = {
        "customers": EntitySpec(
            path="API_BUSINESS_PARTNER/A_Customer",
            key_fields=("Customer",),
            operations=_READ_UPDATE,
            fields=mapped(
                ("name", "~CustomerName"),
                ("tax_id", "~TaxNumber1"),
            ),
            notes="create through API_BUSINESS_PARTNER/A_BusinessPartner",
        ),
        "vendors": EntitySpec(
            path="API_BUSINESS_PARTNER/A_Supplier",
            key_fields=("Supplier",),
            operations=_READ_UPDATE,
            fields=mapped(("name", "~SupplierName"), ("tax_id", "~TaxNumber1")),
            notes="create through API_BUSINESS_PARTNER/A_BusinessPartner",
        ),
        "products": EntitySpec(
            path="API_PRODUCT_SRV/A_Product",
            key_fields=("Product",),
            operations=READ | {Operation.CREATE, Operation.UPDATE},
            fields=mapped(
                ("sku", "Product"),
                ("unit", "BaseUnit"),
                ("category", "ProductGroup"),
                ("modified_at", "~LastChangeDateTime", "odata_v2_datetime"),
            ),
            modified_field="LastChangeDateTime",
        ),
        "inventory": EntitySpec(
            path="API_MATERIAL_STOCK_SRV/A_MatlStkInAcctMod",
            key_fields=(
                "Material",
                "Plant",
                "StorageLocation",
                "Batch",
                "Supplier",
                "Customer",
                "WBSElementInternalID",
                "SDDocument",
                "SDDocumentItem",
                "InventorySpecialStockType",
                "InventoryStockType",
            ),
            operations=frozenset({Operation.LIST}),
            fields=mapped(
                ("product_id", "Material"),
                ("location_id", "StorageLocation"),
                ("quantity", "MatlWrhsStkQtyInMatlBaseUnit", "decimal"),
                ("unit", "MaterialBaseUnit"),
            ),
            notes="keys may be blank, so record ids are usually absent",
        ),
        "sales_orders": EntitySpec(
            path="API_SALES_ORDER_SRV/A_SalesOrder",
            key_fields=("SalesOrder",),
            operations=CRUD,
            fields=mapped(
                ("number", "~SalesOrder"),
                ("customer_id", "SoldToParty"),
                ("order_date", "SalesOrderDate", "odata_v2_date"),
                ("currency", "TransactionCurrency"),
                ("status", "~OverallSDProcessStatus"),
                ("total", "~TotalNetAmount", "decimal"),
                ("modified_at", "~LastChangeDateTime", "odata_v2_datetime"),
            ),
            lines=LineMap(
                fields=mapped(
                    ("line_number", "SalesOrderItem"),
                    ("product_id", "Material"),
                    ("description", "SalesOrderItemText"),
                    ("quantity", "RequestedQuantity", "decimal_string"),
                    ("unit", "RequestedQuantityUnit"),
                    ("amount", "~NetAmount", "decimal"),
                ),
                write_path="to_Item",
                read_path="to_Item.results",
            ),
            modified_field="LastChangeDateTime",
            extra={"expand": "to_Item"},
            notes="create needs SalesOrderType and sales area via provider_fields",
            evidence=Evidence.SECONDARY,
        ),
        "purchase_orders": EntitySpec(
            path="API_PURCHASEORDER_PROCESS_SRV/A_PurchaseOrder",
            key_fields=("PurchaseOrder",),
            operations=CRUD,
            fields=mapped(
                ("number", "~PurchaseOrder"),
                ("vendor_id", "Supplier"),
                ("order_date", "PurchaseOrderDate", "odata_v2_date"),
                ("currency", "DocumentCurrency"),
                ("modified_at", "~LastChangeDateTime", "odata_v2_datetime"),
            ),
            lines=LineMap(
                fields=mapped(
                    ("line_number", "PurchaseOrderItem"),
                    ("product_id", "Material"),
                    ("description", "PurchaseOrderItemText"),
                    ("quantity", "OrderQuantity", "decimal_string"),
                    ("unit", "PurchaseOrderQuantityUnit"),
                    ("unit_price", "NetPriceAmount", "decimal_string"),
                ),
                write_path="to_PurchaseOrderItem",
                read_path="to_PurchaseOrderItem.results",
            ),
            modified_field="LastChangeDateTime",
            extra={"expand": "to_PurchaseOrderItem"},
        ),
        "invoices": EntitySpec(
            path="API_BILLING_DOCUMENT_SRV/A_BillingDocument",
            key_fields=("BillingDocument",),
            operations=READ,
            fields=mapped(
                ("number", "BillingDocument"),
                ("customer_id", "SoldToParty"),
                ("invoice_date", "BillingDocumentDate", "odata_v2_date"),
                ("currency", "TransactionCurrency"),
                ("total", "TotalNetAmount", "decimal"),
                ("status", "AccountingPostingStatus"),
                ("modified_at", "LastChangeDateTime", "odata_v2_datetime"),
            ),
            modified_field="LastChangeDateTime",
        ),
        "financial_records": EntitySpec(
            path="API_JOURNALENTRYITEMBASIC_SRV/A_JournalEntryItemBasic",
            key_fields=(
                "Ledger",
                "CompanyCode",
                "FiscalYear",
                "AccountingDocument",
                "LedgerGLLineItem",
            ),
            operations=READ,
            fields=mapped(
                ("number", "AccountingDocument"),
                ("account_id", "GLAccount"),
                ("posting_date", "PostingDate", "odata_v2_date"),
                ("amount", "AmountInCompanyCodeCurrency", "decimal"),
                ("currency", "CompanyCodeCurrency"),
                ("description", "DocumentItemText"),
            ),
        ),
    }

    def __init__(self, config: ProviderConfig, http: HttpTransport) -> None:
        super().__init__(config, http)
        self._csrf: dict[str, str] = {}
        self._csrf_lock = asyncio.Lock()

    def collection_path(self, spec: EntitySpec) -> str:
        """Entity set path."""
        return f"/sap/opu/odata/sap/{spec.path}"

    def service_path(self, spec: EntitySpec) -> str:
        """Service root."""
        service, separator, _ = spec.path.partition("/")
        if not separator:
            raise ConfigurationError("SAP entity paths must look like SERVICE/EntitySet")
        return f"/sap/opu/odata/sap/{service}"

    def _context(self) -> dict[str, str]:
        headers = {}
        if self.config.sap_client:
            headers["sap-client"] = self.config.sap_client
        if self.config.language:
            headers["sap-language"] = self.config.language
        return headers

    def base_params(self) -> dict[str, str]:
        """SAP context parameters."""
        return self._context()

    async def _token(self, service: str, stale: str | None = None) -> str:
        """Return a CSRF token; the session cookie lives in the HTTP client's jar."""
        async with self._csrf_lock:
            if service not in self._csrf or self._csrf[service] == stale:
                response = await self.http.request(
                    "GET",
                    f"{service}/",
                    headers={"X-CSRF-Token": "Fetch", "Accept": "application/json"},
                    params=self._context(),
                )
                self._csrf[service] = response.headers.get("x-csrf-token", "")
            return self._csrf[service]

    async def write(
        self,
        method: str,
        spec: EntitySpec,
        path: str,
        *,
        json: JsonObject | None = None,
        content: bytes | None = None,
        headers: Mapping[str, str] | None = None,
        idempotency_key: str | None = None,
    ) -> httpx.Response:
        """Send with a CSRF token, renewing it once if SAP rejects it."""
        service = self.service_path(spec)
        token = await self._token(service)
        base = {**self._context(), **(headers or {})}
        try:
            return await super().write(
                method,
                spec,
                path,
                json=json,
                content=content,
                headers={**base, "X-CSRF-Token": token},
                idempotency_key=idempotency_key,
            )
        except PermissionDeniedError as exc:
            if "csrf" not in (exc.body or "").lower():
                raise
        return await super().write(
            method,
            spec,
            path,
            json=json,
            content=content,
            headers={**base, "X-CSRF-Token": await self._token(service, stale=token)},
            idempotency_key=idempotency_key,
        )

    async def health_check(self) -> None:
        """Read one record of the first configured entity."""
        spec = self.entities["customers"]
        await self.http.request(
            "GET",
            self.collection_path(spec),
            params={**self._params(spec), "$top": "1"},
            headers={"Accept": "application/json"},
        )

    @operation
    async def soap(self, *, path: str, body_xml: str, action: str) -> JsonValue:
        """Call a SOAP service; ``body_xml`` is the (already escaped) Body content."""
        return to_json(await soap_call(self.http, path, body_xml, action=action))
