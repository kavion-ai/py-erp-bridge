"""Add an ERP without touching the SDK."""

from __future__ import annotations

from collections.abc import Mapping
from typing import ClassVar
from urllib.parse import quote

from py_erp_bridge import (
    EntitySpec,
    ERPAdapter,
    FieldMap,
    Operation,
    ProviderInfo,
    register_provider,
)
from py_erp_bridge.core.capabilities import Protocol
from py_erp_bridge.core.query import FilterOp, Query, RawPage
from py_erp_bridge.types import JsonObject, RecordKey


class ExampleAdapter(ERPAdapter):
    """A hypothetical REST ERP."""

    info = ProviderInfo(
        name="example_erp",
        title="Example ERP",
        protocols=frozenset({Protocol.REST}),
        auth_types=frozenset({"bearer"}),
        sorting=False,
        documentation=("https://erp.example.com/docs",),
        limitations=("Only equality filters.",),
    )
    default_entities: ClassVar[Mapping[str, EntitySpec]] = {
        "customers": EntitySpec(
            path="clients",
            operations=frozenset({Operation.LIST, Operation.GET}),
            fields=(FieldMap(canonical="name", source="displayName"),),
        ),
    }

    async def list_page(self, spec: EntitySpec, query: Query, cursor: str | None) -> RawPage:
        """GET /clients?page=N."""
        if query.sort:
            raise self.unsupported("sorting")
        page = int(cursor or "1")
        params: dict[str, str | int] = {"page": page, "size": self.page_size(query)}
        for item in query.filters:
            if item.op is not FilterOp.EQ:
                raise self.unsupported_filter(item)
            params[item.field] = str(item.scalar)
        body = self.json_object(await self.http.request("GET", f"/{spec.path}", params=params))
        more = body.get("hasMore") is True
        return RawPage(
            records=self.record_list(body.get("data")), next_cursor=str(page + 1) if more else None
        )

    async def get(self, spec: EntitySpec, key: RecordKey) -> JsonObject:
        """GET /clients/{id}."""
        identifier = quote(self.key_values(spec, key)["id"], safe="")
        return self.json_object(await self.http.request("GET", f"/{spec.path}/{identifier}"))

    async def health_check(self) -> None:
        """GET /ping."""
        await self.http.request("GET", "/ping")


# An installed package can use the entry point group "py_erp_bridge.providers" instead.
register_provider("example_erp", ExampleAdapter, replace=True)
