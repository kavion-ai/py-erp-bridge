"""TallyPrime (XML over HTTP)."""

from __future__ import annotations

import re
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import ClassVar, Literal
from xml.etree.ElementTree import Element

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
from py_erp_bridge.core.query import Query, RawPage, matches
from py_erp_bridge.core.transforms import Transform, register_transform
from py_erp_bridge.exceptions import (
    APIError,
    BusinessRuleError,
    NotFoundError,
    PayloadValidationError,
    UnsupportedOperationError,
)
from py_erp_bridge.transport import xml
from py_erp_bridge.types import JsonObject, JsonValue, RecordKey, as_objects, text

_MASTER = READ | {Operation.CREATE, Operation.UPDATE}
_NUMBER = re.compile(r"^\s*(-?[\d,]*\.?\d+)")


def _quantity(value: JsonValue) -> JsonValue:
    """Parse amounts such as ``10 Nos`` or ``1,200.50/Nos``."""
    found = _NUMBER.match(text(value))
    return found[1].replace(",", "") if found else None


register_transform("tally_quantity", Transform(_quantity))

_PARTY = mapped(
    ("name", "NAME"),
    ("email", "EMAIL"),
    ("phone", "LEDGERPHONE"),
    ("region", "LEDSTATENAME"),
    ("postal_code", "PINCODE"),
    ("country", "COUNTRYNAME"),
)


class TallyConfig(ProviderConfig):
    """``base_url`` is the Tally gateway, e.g. ``http://localhost:9000``."""

    base_url: str = "http://localhost:9000"
    #: Company to use; must be loaded. Without it Tally uses the active company.
    company: str | None = None


class TallyAdapter(ERPAdapter):
    """TallyPrime. Entity paths are Tally collection types."""

    config_model = TallyConfig
    config: TallyConfig
    info = ProviderInfo(
        name="tally",
        title="TallyPrime",
        protocols=frozenset({Protocol.XML}),
        auth_types=frozenset({"none", "basic"}),
        sorting=False,
        documentation=(
            "https://help.tallysolutions.com/xml-interface/",
            "https://help.tallysolutions.com/sample-xml/",
            "https://help.tallysolutions.com/pre-requisites-for-integrations/",
        ),
        limitations=(
            "Tally documents no authentication; never expose its port to a network.",
            "The company must be open in a running TallyPrime.",
            "No server paging: every list reads the whole collection, filters run locally.",
            "Vouchers are written with execute('import_voucher').",
            "Deletion of masters is not implemented (not documented in the pages reviewed).",
            "No webhooks.",
        ),
    )
    default_entities: ClassVar[Mapping[str, EntitySpec]] = {
        "customers": EntitySpec(
            path="Ledger",
            key_fields=("NAME",),
            operations=_MASTER,
            fields=_PARTY,
            create_defaults={"PARENT": "Sundry Debtors"},
            extra={"child_of": "Sundry Debtors"},
            evidence=Evidence.OFFICIAL_DOCS,
        ),
        "vendors": EntitySpec(
            path="Ledger",
            key_fields=("NAME",),
            operations=_MASTER,
            fields=_PARTY,
            create_defaults={"PARENT": "Sundry Creditors"},
            extra={"child_of": "Sundry Creditors"},
            evidence=Evidence.OFFICIAL_DOCS,
        ),
        "products": EntitySpec(
            path="StockItem",
            key_fields=("NAME",),
            operations=_MASTER,
            fields=mapped(("name", "NAME"), ("unit", "BASEUNITS"), ("category", "PARENT")),
            evidence=Evidence.OFFICIAL_DOCS,
        ),
        "inventory": EntitySpec(
            path="StockItem",
            key_fields=("NAME",),
            operations=READ,
            fields=mapped(
                ("product_id", "NAME"),
                ("quantity", "CLOSINGBALANCE", "tally_quantity"),
                ("unit", "BASEUNITS"),
            ),
        ),
        "sales_orders": EntitySpec(
            path="Vouchers : VoucherType",
            key_fields=("MASTERID",),
            operations=READ,
            fields=mapped(
                ("number", "VOUCHERNUMBER"),
                ("order_date", "DATE", "compact_date"),
                ("customer_id", "PARTYLEDGERNAME"),
            ),
            extra={"child_of": "Sales Order"},
        ),
        "purchase_orders": EntitySpec(
            path="Vouchers : VoucherType",
            key_fields=("MASTERID",),
            operations=READ,
            fields=mapped(
                ("number", "VOUCHERNUMBER"),
                ("order_date", "DATE", "compact_date"),
                ("vendor_id", "PARTYLEDGERNAME"),
            ),
            extra={"child_of": "Purchase Order"},
        ),
        "invoices": EntitySpec(
            path="Vouchers : VoucherType",
            key_fields=("MASTERID",),
            operations=READ,
            fields=mapped(
                ("number", "VOUCHERNUMBER"),
                ("invoice_date", "DATE", "compact_date"),
                ("customer_id", "PARTYLEDGERNAME"),
                ("total", "AMOUNT", "decimal"),
            ),
            extra={"child_of": "Sales"},
            notes="amount signs follow Tally's convention (debits are negative)",
        ),
        "payments": EntitySpec(
            path="Vouchers : VoucherType",
            key_fields=("MASTERID",),
            operations=READ,
            fields=mapped(
                ("number", "VOUCHERNUMBER"),
                ("payment_date", "DATE", "compact_date"),
                ("party_id", "PARTYLEDGERNAME"),
                ("amount", "AMOUNT", "decimal"),
            ),
            extra={"child_of": "Receipt"},
            notes="receipts; outgoing payments are the 'Payment' voucher type",
        ),
        "financial_records": EntitySpec(
            path="Vouchers : VoucherType",
            key_fields=("MASTERID",),
            operations=READ,
            fields=mapped(
                ("number", "VOUCHERNUMBER"),
                ("posting_date", "DATE", "compact_date"),
                ("description", "NARRATION"),
                ("amount", "AMOUNT", "decimal"),
            ),
            extra={"child_of": "Journal"},
        ),
    }

    # -- envelopes ------------------------------------------------------------

    def _static(self, **values: str) -> str:
        if self.config.company:
            values["SVCURRENTCOMPANY"] = self.config.company
        return xml.element("STATICVARIABLES", "".join(xml.tag(k, v) for k, v in values.items()))

    async def _send(self, envelope: str, *, safe: bool) -> Element:
        response = await self.http.request(
            "POST",
            "/",
            content=envelope.encode(),
            headers={"Content-Type": "text/xml; charset=utf-8"},
            idempotent=safe,
        )
        root = xml.parse(response.text)
        errors = [e.text or "" for e in root.iter("LINEERROR")]
        status = root.findtext("HEADER/STATUS")
        if not errors and status is not None and status.strip() != "1":
            data = root.find("BODY/DATA")
            errors = ["".join(data.itertext()).strip() if data is not None else "unknown error"]
        if root.tag == "RESPONSE" and not len(root) and (root.text or "").strip():
            errors.append((root.text or "").strip())
        count = next((e.text for e in root.iter("ERRORS")), None)
        exceptions = next((e.text for e in root.iter("EXCEPTIONS")), None)
        if not errors and any(v and v.strip() not in {"", "0"} for v in (count, exceptions)):
            errors = [f"{count or 0} errors, {exceptions or 0} exceptions"]
        if errors:
            raise BusinessRuleError(f"tally: {'; '.join(errors)}", provider=self.info.name)
        return root

    async def _collection(
        self, spec: EntitySpec, fields: list[str], params: Mapping[str, str]
    ) -> list[JsonObject]:
        static = {"SVEXPORTFORMAT": "$$SysName:XML"}
        for name, variable in (("from_date", "SVFROMDATE"), ("to_date", "SVTODATE")):
            if name in params:
                if not re.fullmatch(r"\d{8}", params[name]):
                    raise PayloadValidationError(f"{name} must be YYYYMMDD")
                static[variable] = params[name]
        unknown = set(params) - {"from_date", "to_date"}
        if unknown:
            raise PayloadValidationError(f"unknown tally params: {', '.join(sorted(unknown))}")
        parts = [xml.tag("TYPE", spec.path)]
        if "child_of" in spec.extra:
            parts += [xml.tag("CHILDOF", spec.extra["child_of"]), xml.tag("BELONGSTO", "Yes")]
        parts += [xml.tag("NATIVEMETHOD", f) for f in fields]
        name = "PyErpBridgeCollection"
        collection = xml.element("COLLECTION", "".join(parts), {"NAME": name, "ISMODIFY": "No"})
        header = "".join(
            [
                xml.tag("VERSION", "1"),
                xml.tag("TALLYREQUEST", "Export"),
                xml.tag("TYPE", "Collection"),
                xml.tag("ID", name),
            ]
        )
        description = self._static(**static) + xml.element(
            "TDL", xml.element("TDLMESSAGE", collection)
        )
        body = xml.element("DESC", description)
        root = await self._send(
            xml.element("ENVELOPE", xml.element("HEADER", header) + xml.element("BODY", body)),
            safe=True,
        )
        node = root.find(".//COLLECTION")
        if node is None:
            raise APIError("tally: response has no collection", provider=self.info.name)
        return [self._record(child) for child in node]

    @staticmethod
    def _record(node: Element) -> JsonObject:
        content = xml.to_json(node)
        record: JsonObject = content if isinstance(content, dict) else {}
        for key, value in node.attrib.items():
            record.setdefault(key, value)
        if node.get("NAME"):
            # The NAME child may be a list of aliases; the attribute is the primary name.
            record["NAME"] = node.get("NAME")
        return record

    async def _all(self, spec: EntitySpec, params: Mapping[str, str]) -> list[JsonObject]:
        fields = list(dict.fromkeys([*spec.key_fields, *(f.source for f in spec.fields)]))
        return await self._collection(spec, fields, params)

    # -- operations -----------------------------------------------------------

    async def list_page(self, spec: EntitySpec, query: Query, cursor: str | None) -> RawPage:
        """Read the collection, filter locally and return everything as one page."""
        if query.sort:
            raise self.unsupported("sorting")
        records = [r for r in await self._all(spec, query.params) if matches(r, query.filters)]
        return RawPage(records=records, total=len(records))

    async def get(self, spec: EntitySpec, key: RecordKey) -> JsonObject:
        """Find one record in its collection."""
        wanted = self.key_values(spec, key)
        for record in await self._all(spec, {}):
            if all(text(record.get(k)) == v for k, v in wanted.items()):
                return record
        raise NotFoundError(f"tally: {spec.path} not found", provider=self.info.name)

    def key_values(self, spec: EntitySpec, key: RecordKey) -> dict[str, str]:
        """Names may contain any character, including slashes."""
        if len(spec.key_fields) == 1 and not isinstance(key, Mapping):
            return {spec.key_fields[0]: str(key)}
        return super().key_values(spec, key)

    def _encode(self, data: JsonObject) -> str:
        parts = []
        for name, value in data.items():
            for item in value if isinstance(value, list) else [value]:
                if isinstance(item, dict):
                    parts.append(xml.element(name, self._encode(item)))
                else:
                    rendered = (
                        {True: "Yes", False: "No"}.get(item)
                        if isinstance(item, bool)
                        else text(item)
                    )
                    parts.append(xml.tag(name, rendered or ""))
        return "".join(parts)

    async def _import(self, report: str, message: str) -> Element:
        header = "".join(
            [
                xml.tag("VERSION", "1"),
                xml.tag("TALLYREQUEST", "Import"),
                xml.tag("TYPE", "Data"),
                xml.tag("ID", report),
            ]
        )
        data = xml.element("TALLYMESSAGE", message, {"xmlns:UDF": "TallyUDF"})
        body = xml.element("DESC", self._static()) + xml.element("DATA", data)
        return await self._send(
            xml.element("ENVELOPE", xml.element("HEADER", header) + xml.element("BODY", body)),
            safe=False,
        )

    @staticmethod
    def _count(root: Element, name: str) -> int:
        value = next((e.text for e in root.iter(name)), None)
        return int(value) if value and value.strip().isdigit() else 0

    def _tag(self, spec: EntitySpec) -> str:
        if not spec.path.isalpha():
            raise UnsupportedOperationError(
                "tally vouchers are written with execute('import_voucher')"
            )
        return spec.path.upper()

    async def create(
        self, spec: EntitySpec, payload: JsonObject, idempotency_key: str | None
    ) -> JsonObject:
        """Create a master."""
        if idempotency_key is not None:
            raise self.unsupported("idempotency keys")
        name = text(payload.get("NAME"))
        if not name:
            raise PayloadValidationError("tally masters need a name")
        message = xml.element(
            self._tag(spec), self._encode(payload), {"NAME": name, "ACTION": "Create"}
        )
        root = await self._import("All Masters", message)
        if self._count(root, "CREATED") + self._count(root, "ALTERED") == 0:
            raise BusinessRuleError("tally: nothing was created", provider=self.info.name)
        return dict(payload)

    async def update(
        self, spec: EntitySpec, key: RecordKey, payload: JsonObject, etag: str | None
    ) -> JsonObject | None:
        """Alter a master. Renaming is refused because it changes the key."""
        if etag is not None:
            raise self.unsupported("etags")
        name = self.key_values(spec, key)[spec.key_fields[0]]
        if text(payload.get("NAME", name)) != name:
            raise self.unsupported("renaming masters")
        message = xml.element(
            self._tag(spec), self._encode(payload), {"NAME": name, "ACTION": "Alter"}
        )
        root = await self._import("All Masters", message)
        if self._count(root, "ALTERED") == 0:
            raise BusinessRuleError("tally: nothing was altered", provider=self.info.name)
        return None

    async def health_check(self) -> None:
        """Export the list of companies."""
        await self._collection(EntitySpec(path="Company", key_fields=("NAME",)), ["NAME"], {})

    @operation
    async def import_voucher(
        self,
        *,
        voucher_type: str,
        date: str,
        fields: JsonObject,
        action: Literal["Create"] = "Create",
    ) -> JsonObject:
        """Import a voucher given its native fields; ``date`` is YYYYMMDD.

        Accounting entries must balance, which is checked before sending.
        """
        if not re.fullmatch(r"\d{8}", date):
            raise PayloadValidationError("date must be YYYYMMDD")
        self._check_balance(fields)
        content = self._encode({"DATE": date, "VOUCHERTYPENAME": voucher_type, **fields})
        message = xml.element("VOUCHER", content, {"VCHTYPE": voucher_type, "ACTION": action})
        root = await self._import("Vouchers", message)
        if self._count(root, "CREATED") == 0:
            raise BusinessRuleError("tally: voucher was not created", provider=self.info.name)
        return {"LASTVCHID": next((e.text or "" for e in root.iter("LASTVCHID")), "")}

    @staticmethod
    def _check_balance(fields: JsonObject) -> None:
        if "ALLINVENTORYENTRIES.LIST" in fields:
            return  # Inventory allocations also carry amounts; Tally validates those.
        entries = [
            *as_objects(fields.get("LEDGERENTRIES.LIST")),
            *as_objects(fields.get("ALLLEDGERENTRIES.LIST")),
        ]
        try:
            total = sum((Decimal(text(e.get("AMOUNT"))) for e in entries), Decimal(0))
        except InvalidOperation:
            raise PayloadValidationError("every ledger entry needs a numeric AMOUNT") from None
        if total != 0:
            raise PayloadValidationError(f"voucher entries do not balance (difference {total})")
