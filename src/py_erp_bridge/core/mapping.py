"""Conversion between canonical models and provider payloads."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Generic, TypeVar

from pydantic import BaseModel, ValidationError

from py_erp_bridge.core.capabilities import EntitySpec, FieldMap
from py_erp_bridge.core.query import Filter, Query, Sort
from py_erp_bridge.core.transforms import get_transform
from py_erp_bridge.exceptions import MappingError, PayloadValidationError
from py_erp_bridge.models import CustomRecord, ERPRecord
from py_erp_bridge.types import JsonObject, JsonValue

R = TypeVar("R", bound=ERPRecord)

_CONTROL = {"id", "provider", "raw", "provider_fields", "items"}


def get_path(data: JsonObject, path: str) -> JsonValue:
    """Read a dotted path. A literal key containing dots wins."""
    if path in data:
        return data[path]
    node: JsonValue = data
    for part in path.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


def set_path(data: JsonObject, path: str, value: JsonValue) -> None:
    """Write a dotted path, creating intermediate objects."""
    *parents, leaf = path.split(".")
    node = data
    for part in parents:
        child = node.setdefault(part, {})
        if not isinstance(child, dict):
            raise MappingError(f"cannot write '{path}': '{part}' is not an object")
        node = child
    node[leaf] = value


def _read_fields(fields: Sequence[FieldMap], raw: JsonObject) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {}
    for item in fields:
        value = get_path(raw, item.source)
        if value is False:
            # No canonical field is boolean; some ERPs use false for "empty".
            continue
        value = get_transform(item.transform).read(value)
        if value is not None and value != "":
            result[item.canonical] = value
    return result


def _write_fields(
    fields: Sequence[FieldMap], model: BaseModel, label: str, skip: set[str]
) -> JsonObject:
    by_name = {f.canonical: f for f in fields}
    payload: JsonObject = {}
    for name in model.model_fields_set - skip:
        item = by_name.get(name)
        if item is None:
            raise MappingError(
                f"'{name}' has no mapping for {label}; use provider_fields or an entity override"
            )
        if not item.writable:
            raise MappingError(f"'{name}' is read-only for {label}")
        set_path(payload, item.source, get_transform(item.transform).write(getattr(model, name)))
    return payload


class EntityMapper(Generic[R]):
    """Maps one entity of one provider."""

    def __init__(self, provider: str, entity: str, spec: EntitySpec, model: type[R]) -> None:
        self.provider = provider
        self.entity = entity
        self.spec = spec
        self.model = model
        self._label = f"{provider}.{entity}"

    def to_model(self, record_id: str | None, raw: JsonObject) -> R:
        """Build a canonical record; ``raw`` is preserved untouched."""
        data: dict[str, object] = dict(_read_fields(self.spec.fields, raw))
        if self.spec.lines and self.spec.lines.read_path:
            lines = get_path(raw, self.spec.lines.read_path)
            if isinstance(lines, list):
                data["items"] = [
                    {**_read_fields(self.spec.lines.fields, line), "raw": line}
                    for line in lines
                    if isinstance(line, dict)
                ]
        data |= {"id": record_id, "provider": self.provider, "raw": raw}
        try:
            return self.model.model_validate(data)
        except ValidationError as exc:
            # Pydantic messages embed input values; report only locations.
            where = ", ".join(".".join(map(str, e["loc"])) for e in exc.errors())
            raise MappingError(f"{self._label} record has unexpected data in: {where}") from exc

    def to_payload(self, data: Mapping[str, object] | ERPRecord, *, creating: bool) -> JsonObject:
        """Validate caller data and convert it to a provider payload."""
        if self.model is CustomRecord and isinstance(data, Mapping):
            try:
                model: ERPRecord = CustomRecord.model_validate({"provider_fields": dict(data)})
            except ValidationError as exc:
                raise PayloadValidationError(str(exc)) from exc
        elif isinstance(data, Mapping):
            try:
                model = self.model.model_validate(dict(data))
            except ValidationError as exc:
                raise PayloadValidationError(str(exc)) from exc
        elif isinstance(data, self.model):
            model = data
        else:
            raise PayloadValidationError(f"expected {self.model.__name__} or a mapping")

        payload: JsonObject = {}
        if creating:
            payload.update(self.spec.create_defaults)
        payload.update(_write_fields(self.spec.fields, model, self._label, _CONTROL))
        items = getattr(model, "items", None)
        if items and "items" in model.model_fields_set:
            lines = self.spec.lines
            if lines is None or lines.write_path is None:
                raise MappingError(f"{self._label} cannot write items with the header")
            out: list[JsonValue] = []
            for line in items:
                entry = _write_fields(lines.fields, line, f"{self._label} items", _CONTROL)
                entry.update(line.provider_fields)
                out.append(entry)
            set_path(payload, lines.write_path, out)
        payload.update(model.provider_fields)
        return payload

    def _source(self, name: str) -> FieldMap:
        item = self.spec.field(name)
        if item is not None:
            return item
        if name == "id" and len(self.spec.key_fields) == 1:
            return FieldMap(canonical="id", source=self.spec.key_fields[0])
        if name in self.model.model_fields:
            raise MappingError(f"'{name}' has no mapping for {self._label}")
        # Not canonical: assume a provider-native field name.
        return FieldMap(canonical=name, source=name)

    def translate(self, query: Query) -> Query:
        """Replace canonical field names and convert filter literals."""
        filters = []
        for item in query.filters:
            target = self._source(item.field)
            convert = get_transform(target.transform).filter
            value = (
                [convert(v) for v in item.value]
                if isinstance(item.value, list)
                else convert(item.value)
            )
            filters.append(Filter(field=target.source, op=item.op, value=value))
        return query.model_copy(
            update={
                "filters": tuple(filters),
                "sort": tuple(
                    Sort(field=self._source(s.field).source, descending=s.descending)
                    for s in query.sort
                ),
                "select": tuple(self._source(s).source for s in query.select),
            }
        )
