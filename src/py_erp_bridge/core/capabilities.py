"""Declarative descriptions of what a provider can do."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from py_erp_bridge.types import JsonObject


class Operation(StrEnum):
    """Standard operations on an entity."""

    LIST = "list"
    GET = "get"
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


READ = frozenset({Operation.LIST, Operation.GET})
CRUD = frozenset(Operation)


class Protocol(StrEnum):
    """Wire protocols."""

    REST = "rest"
    ODATA_V2 = "odata_v2"
    ODATA_V4 = "odata_v4"
    RFC = "rfc"
    SOAP = "soap"
    XML = "xml_http"
    JSON_RPC = "json_rpc"


class Evidence(StrEnum):
    """How well a wire-level detail is backed. No provider has been live-tested."""

    #: Read in the vendor's own documentation while writing this release.
    OFFICIAL_DOCS = "official_docs"
    #: Reported by third-party material only.
    SECONDARY = "secondary"
    #: Established convention, but not confirmed; verify against your system's metadata.
    UNCONFIRMED = "unconfirmed"


class FieldMap(BaseModel):
    """Maps one canonical field to a provider field (dots address nested objects)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    canonical: str
    source: str
    transform: str = "identity"
    writable: bool = True


class LineMap(BaseModel):
    """Maps document lines."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    fields: tuple[FieldMap, ...]
    #: Where lines are written. None means lines cannot be written with the header.
    write_path: str | None = None
    #: Where lines appear in responses. None means they are not embedded.
    read_path: str | None = None


class EntitySpec(BaseModel):
    """How one entity is represented by a provider."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    key_fields: tuple[str, ...] = ("id",)
    operations: frozenset[Operation] = CRUD
    fields: tuple[FieldMap, ...] = ()
    lines: LineMap | None = None
    modified_field: str | None = None
    #: Added to create payloads unless already present.
    create_defaults: JsonObject = Field(default_factory=dict)
    #: Adapter-specific hints.
    extra: dict[str, str] = Field(default_factory=dict)
    evidence: Evidence = Evidence.UNCONFIRMED
    notes: str = ""

    def field(self, canonical: str) -> FieldMap | None:
        """Return the mapping for a canonical field, if there is one."""
        return next((f for f in self.fields if f.canonical == canonical), None)


class ProviderInfo(BaseModel):
    """Public description of a provider."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    title: str
    protocols: frozenset[Protocol]
    auth_types: frozenset[str]
    filtering: bool = True
    sorting: bool = True
    native_bulk: bool = False
    webhook_registration: bool = False
    file_upload: bool = False
    documentation: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()


def mapped(*items: tuple[str, str] | tuple[str, str, str]) -> tuple[FieldMap, ...]:
    """Build field maps from ``(canonical, source[, transform])`` tuples.

    A source starting with ``~`` is read-only.
    """
    result = []
    for item in items:
        source = item[1]
        result.append(
            FieldMap(
                canonical=item[0],
                source=source.removeprefix("~"),
                transform=item[2] if len(item) == 3 else "identity",
                writable=not source.startswith("~"),
            )
        )
    return tuple(result)
