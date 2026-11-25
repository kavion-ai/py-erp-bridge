"""Infor CloudSuite: M3 through the ION API gateway."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import ClassVar
from urllib.parse import quote

import httpx

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
    APIError,
    BusinessRuleError,
    ConfigurationError,
    NotFoundError,
    UniversalERPError,
)
from py_erp_bridge.observability import logger
from py_erp_bridge.transport.http import ErrorDetails
from py_erp_bridge.types import (
    JsonObject,
    JsonValue,
    RecordKey,
    as_int,
    as_object,
    as_objects,
    text,
)


def load_ionapi(path: str | Path) -> JsonObject:
    """Turn a backend-service ``.ionapi`` file into an adapter configuration."""
    data = as_object(json.loads(Path(path).read_text(encoding="utf-8")))
    missing = [k for k in ("ti", "ci", "cs", "iu", "pu", "ot", "saak", "sask") if not data.get(k)]
    if missing:
        raise ConfigurationError(
            f".ionapi file lacks {', '.join(missing)}; download a backend service "
            "credential with a service account"
        )
    return {
        "base_url": data["iu"],
        "tenant": data["ti"],
        "auth": {
            "type": "oauth2_password",
            "token_url": f"{text(data['pu']).rstrip('/')}/{text(data['ot']).lstrip('/')}",
            "client_id": data["ci"],
            "client_secret": data["cs"],
            "username": data["saak"],
            "password": data["sask"],
        },
    }


class InforConfig(ProviderConfig):
    """``base_url`` is the ION API gateway (``iu`` in the .ionapi file)."""

    tenant: str
    #: M3 company and division; the service account's defaults apply if unset.
    cono: int | None = None
    divi: str | None = None
    #: M3 lists cannot be paged, so this bounds a list.
    max_records: int = 10_000


class InforAdapter(ERPAdapter):
    """M3 MI transactions. ``extra`` names the transaction of each operation."""

    config_model = InforConfig
    config: InforConfig
    info = ProviderInfo(
        name="infor",
        title="Infor CloudSuite (M3)",
        protocols=frozenset({Protocol.REST}),
        auth_types=frozenset(
            {"oauth2_password", "oauth2_authorization_code", "oauth2_client_credentials", "bearer"}
        ),
        sorting=False,
        native_bulk=True,
        documentation=(
            "https://github.com/infor-cloud/ion-api-sdk",
            "https://docs.infor.com/crm/10.1.x/en-us/webclient/upi1639111995644.html",
            "https://docs.infor.com/",
        ),
        limitations=(
            "Entities cover CloudSuites built on M3. For LN or Industrial (SyteLine) use "
            "client.request() against their own ION API suites.",
            "The M3 wire format was not confirmed in Infor's documentation.",
            "Lists cannot be paged; max_records bounds them and truncation is an error.",
            "Filters are equality on MI input fields of the list transaction.",
            "No webhooks: use ION BODs or Event Hub.",
        ),
    )
    default_entities: ClassVar[Mapping[str, EntitySpec]] = {
        "customers": EntitySpec(
            path="CRS610MI",
            key_fields=("CUNO",),
            operations=READ | {Operation.CREATE, Operation.UPDATE},
            fields=mapped(
                ("name", "CUNM"),
                ("street", "CUA1"),
                ("city", "TOWN"),
                ("postal_code", "PONO"),
                ("country", "CSCD"),
                ("phone", "PHNO"),
                ("tax_id", "VRNO"),
            ),
            extra={
                "list": "LstByNumber",
                "get": "GetBasicData",
                "create": "Add",
                "update": "ChgBasicData",
            },
        ),
        "vendors": EntitySpec(
            path="CRS620MI",
            key_fields=("SUNO",),
            operations=READ,
            fields=mapped(("name", "SUNM"), ("phone", "PHNO"), ("country", "CSCD")),
            extra={"list": "LstSuppliers", "get": "GetBasicData"},
        ),
        "products": EntitySpec(
            path="MMS200MI",
            key_fields=("ITNO",),
            operations=READ,
            fields=mapped(
                ("sku", "ITNO"),
                ("name", "ITDS"),
                ("description", "FUDS"),
                ("unit", "UNMS"),
                ("category", "ITGR"),
            ),
            extra={"list": "LstItmByItm", "get": "GetItmBasic"},
        ),
        "sales_orders": EntitySpec(
            path="OIS100MI",
            key_fields=("ORNO",),
            operations=READ,
            fields=mapped(
                ("number", "ORNO"),
                ("customer_id", "CUNO"),
                ("order_date", "ORDT", "compact_date"),
                ("currency", "CUCD"),
                ("status", "ORST"),
            ),
            extra={"list": "LstHead", "get": "GetHead"},
            notes="order entry is a multi-step process; use execute('mi')",
        ),
        "purchase_orders": EntitySpec(
            path="PPS200MI",
            key_fields=("PUNO",),
            operations=READ,
            fields=mapped(
                ("number", "PUNO"),
                ("vendor_id", "SUNO"),
                ("order_date", "PUDT", "compact_date"),
                ("currency", "CUCD"),
                ("status", "PUST"),
            ),
            extra={"list": "LstHead", "get": "GetHead"},
        ),
    }

    @property
    def _root(self) -> str:
        return f"/{quote(self.config.tenant, safe='')}/M3/m3api-rest/v2/execute"

    def _transaction(self, spec: EntitySpec, name: str) -> str:
        if name not in spec.extra:
            raise self.unsupported(f"{name} on {spec.path} (no MI transaction configured)")
        return spec.extra[name]

    def _context(self) -> dict[str, str | int]:
        context: dict[str, str | int] = {}
        if self.config.cono is not None:
            context["cono"] = self.config.cono
        if self.config.divi is not None:
            context["divi"] = self.config.divi
        return context

    async def _read(
        self, program: str, transaction: str, inputs: Mapping[str, str], limit: int
    ) -> list[JsonObject]:
        response = await self.http.request(
            "GET",
            f"{self._root}/{quote(program, safe='')}/{quote(transaction, safe='')}",
            params={**self._context(), "maxrecs": limit, "dateformat": "YMD8", **inputs},
            headers={"Accept": "application/json"},
        )
        outcome = self._results(self.json_object(response), 1)[0]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def _results(self, body: JsonObject, expected: int) -> list[list[JsonObject] | APIError]:
        results = as_objects(body.get("results"))
        if len(results) != expected:
            raise APIError("infor: unexpected M3 response", provider=self.info.name)
        return [self._result(r) for r in results]

    def _result(self, result: JsonObject) -> list[JsonObject] | APIError:
        message = text(result.get("errorMessage")).strip()
        if message:
            missing = "does not exist" in message.lower()
            kind: type[APIError] = NotFoundError if missing else BusinessRuleError
            return kind(
                f"infor: {message}",
                provider=self.info.name,
                code=text(result.get("errorCode")) or None,
            )
        return [
            {k: v.strip() if isinstance(v, str) else v for k, v in r.items()}
            for r in as_objects(result.get("records"))
        ]

    async def _write(
        self, program: str, transaction: str, records: list[JsonObject]
    ) -> list[list[JsonObject] | APIError]:
        calls: list[JsonValue] = [{"transaction": transaction, "record": r} for r in records]
        body: JsonObject = {"program": program, "transactions": calls}
        body.update(self._context())
        response = await self.http.request("POST", self._root, json=body)
        return self._results(self.json_object(response), len(records))

    def _inputs(self, filters: tuple[Filter, ...]) -> dict[str, str]:
        inputs = {}
        for item in filters:
            if item.op is not FilterOp.EQ:
                raise self.unsupported_filter(item)
            inputs[item.field] = str(item.scalar)
        return inputs

    async def list_page(self, spec: EntitySpec, query: Query, cursor: str | None) -> RawPage:
        """Run the list transaction. Raises rather than returning a truncated list."""
        if query.sort:
            raise self.unsupported("sorting")
        limit = self.config.max_records
        inputs = {**self._inputs(query.filters), **query.params}
        if query.select:
            inputs["returncols"] = ",".join(dict.fromkeys([*spec.key_fields, *query.select]))
        records = await self._read(spec.path, self._transaction(spec, "list"), inputs, limit + 1)
        if len(records) > limit:
            logger.error("infor list exceeds max_records", extra={"program": spec.path})
            raise BusinessRuleError(
                f"infor: {spec.path} returned more than {limit} records; add filters or raise "
                "max_records",
                provider=self.info.name,
            )
        return RawPage(records=records, total=len(records))

    async def get(self, spec: EntitySpec, key: RecordKey) -> JsonObject:
        """Run the get transaction."""
        keys = self.key_values(spec, key)
        records = await self._read(spec.path, self._transaction(spec, "get"), keys, 1)
        if not records:
            raise NotFoundError(f"infor: {spec.path} not found", provider=self.info.name)
        return {**keys, **records[0]}

    async def create(
        self, spec: EntitySpec, payload: JsonObject, idempotency_key: str | None
    ) -> JsonObject:
        """Run the create transaction."""
        if idempotency_key is not None:
            raise self.unsupported("idempotency keys")
        outcome = (await self.bulk_create(spec, [payload]))[0]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    async def bulk_create(
        self, spec: EntitySpec, payloads: list[JsonObject]
    ) -> list[JsonObject | Exception]:
        """Run the create transactions, 100 per request; each stands alone."""
        transaction = self._transaction(spec, "create")
        outcomes: list[JsonObject | Exception] = []
        for start in range(0, len(payloads), 100):
            chunk = payloads[start : start + 100]
            try:
                results = await self._write(spec.path, transaction, chunk)
            except UniversalERPError as exc:
                # Earlier chunks were already committed; keep their outcome.
                outcomes += [exc] * len(chunk)
                continue
            for payload, result in zip(chunk, results, strict=True):
                if isinstance(result, APIError):
                    outcomes.append(result)
                else:
                    outcomes.append({**payload, **(result[0] if result else {})})
        return outcomes

    async def update(
        self, spec: EntitySpec, key: RecordKey, payload: JsonObject, etag: str | None
    ) -> JsonObject | None:
        """Run the update transaction."""
        if etag is not None:
            raise self.unsupported("etags")
        record: JsonObject = {**payload, **self.key_values(spec, key)}
        result = (await self._write(spec.path, self._transaction(spec, "update"), [record]))[0]
        if isinstance(result, APIError):
            raise result
        return None

    async def health_check(self) -> None:
        """Fetch one customer."""
        spec = self.entities["customers"]
        await self._read(spec.path, self._transaction(spec, "list"), {}, 1)

    @operation
    async def mi(
        self, *, program: str, transaction: str, record: JsonObject, read_only: bool = False
    ) -> list[JsonObject]:
        """Run any MI transaction. Only transactions flagged ``read_only`` are retried."""
        if read_only:
            inputs = {k: text(v) for k, v in record.items()}
            return await self._read(program, transaction, inputs, self.config.max_records)
        result = (await self._write(program, transaction, [record]))[0]
        if isinstance(result, APIError):
            raise result
        return result

    def parse_error(self, response: httpx.Response) -> ErrorDetails:
        """Extract gateway errors."""
        try:
            body = as_object(response.json())
        except ValueError:
            return ErrorDetails()
        message = text(body.get("message")) or text(body.get("error_description"))
        code = text(body.get("error")) or text(as_int(body.get("code")))
        return ErrorDetails(code=code or None, message=message or None)
