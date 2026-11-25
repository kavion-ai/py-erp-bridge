"""Shared JSON type aliases and narrowing helpers."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import TypeAlias

from pydantic import JsonValue as JsonValue

JsonObject: TypeAlias = dict[str, JsonValue]
RecordKey: TypeAlias = str | int | Mapping[str, str]
Scalar: TypeAlias = str | int | float | bool | Decimal | date | datetime


def as_object(value: JsonValue) -> JsonObject:
    """Return *value* if it is a JSON object, otherwise an empty object."""
    return value if isinstance(value, dict) else {}


def as_objects(value: JsonValue) -> list[JsonObject]:
    """Return every JSON object in *value* if it is an array, else an empty list."""
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def as_int(value: JsonValue) -> int | None:
    """Parse an int from a JSON number or numeric string."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        return int(value)
    except ValueError:
        return None


def text(value: JsonValue) -> str:
    """Render a scalar JSON value as text (empty string for null and containers)."""
    if value is None or isinstance(value, (dict, list)):
        return ""
    return str(value)
