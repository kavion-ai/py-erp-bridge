"""Named, reversible value transforms used by field maps."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation

from py_erp_bridge.exceptions import ConfigurationError
from py_erp_bridge.types import JsonValue, Scalar, text


def to_json(value: object) -> JsonValue:
    """Default canonical-to-provider conversion."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"unsupported value type {type(value).__name__}")


@dataclass(frozen=True)
class Transform:
    """A pair of conversion functions."""

    read: Callable[[JsonValue], JsonValue]
    write: Callable[[object], JsonValue] = to_json
    #: Conversion for filter literals. Defaults to leaving the value alone.
    filter: Callable[[Scalar], Scalar] = lambda value: value


def _identity(value: JsonValue) -> JsonValue:
    return value


def _date(value: JsonValue) -> JsonValue:
    raw = text(value).strip()
    return raw[:10] or None


def _compact_date(value: JsonValue) -> JsonValue:
    raw = text(value).strip()
    if len(raw) != 8 or not raw.isdigit() or raw == "00000000":
        return None
    return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"


def _compact_date_write(value: object) -> JsonValue:
    return value.strftime("%Y%m%d") if isinstance(value, date) else to_json(value)


def _compact_date_filter(value: Scalar) -> Scalar:
    return value.strftime("%Y%m%d") if isinstance(value, date) else value


_V2_DATE = re.compile(r"/Date\((-?\d+)(?:[+-]\d+)?\)/")


def odata_v2_datetime(value: JsonValue) -> JsonValue:
    """Convert ``/Date(ms)/`` to ISO 8601 (UTC)."""
    found = _V2_DATE.fullmatch(text(value))
    if not found:
        return value or None
    return datetime.fromtimestamp(int(found[1]) / 1000, UTC).isoformat()


def _odata_v2_date(value: JsonValue) -> JsonValue:
    return _date(odata_v2_datetime(value))


def _odata_v2_write(value: object) -> JsonValue:
    if isinstance(value, date):
        moment = value if isinstance(value, datetime) else datetime(*value.timetuple()[:3])
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        return f"/Date({int(moment.timestamp() * 1000)})/"
    return to_json(value)


def _decimal_read(value: JsonValue) -> JsonValue:
    """Parse decimals that may be blank, padded, or have a trailing minus sign."""
    raw = text(value).strip().replace(",", "")
    if not raw:
        return None
    if raw.endswith("-"):
        raw = "-" + raw[:-1]
    try:
        return str(Decimal(raw))
    except InvalidOperation:
        return None


def _decimal_string(value: object) -> JsonValue:
    return None if value is None else str(value)


def _alpha(width: int) -> Transform:
    def pad(value: object) -> str:
        raw = str(value)
        return raw.zfill(width) if raw.isdigit() else raw

    def strip(value: JsonValue) -> JsonValue:
        raw = text(value).strip()
        return (raw.lstrip("0") or "0") if raw.isdigit() else (raw or None)

    return Transform(strip, pad, pad)


def _flag_read(value: JsonValue) -> JsonValue:
    return value[0] if isinstance(value, list) and value else (value or None)


def _int_write(value: object) -> JsonValue:
    raw = str(value)
    return int(raw) if raw.isdigit() else to_json(value)


def _int_filter(value: Scalar) -> Scalar:
    return int(value) if isinstance(value, str) and value.isdigit() else value


_TRANSFORMS: dict[str, Transform] = {
    "identity": Transform(_identity),
    "date": Transform(_date),
    "compact_date": Transform(_compact_date, _compact_date_write, _compact_date_filter),
    "odata_v2_date": Transform(_odata_v2_date, _odata_v2_write),
    "odata_v2_datetime": Transform(odata_v2_datetime, _odata_v2_write),
    "decimal": Transform(_decimal_read),
    "decimal_string": Transform(_decimal_read, _decimal_string),
    "alpha10": _alpha(10),
    "alpha18": _alpha(18),
    "strip": Transform(lambda v: text(v).strip() or None),
    "relation": Transform(_flag_read, _int_write, _int_filter),
    "int_id": Transform(_identity, _int_write, _int_filter),
}


def get_transform(name: str) -> Transform:
    """Look up a transform."""
    try:
        return _TRANSFORMS[name]
    except KeyError:
        raise ConfigurationError(f"unknown transform '{name}'") from None


def register_transform(name: str, transform: Transform) -> None:
    """Register a custom transform. Built-in names cannot be replaced."""
    if name in _TRANSFORMS:
        raise ConfigurationError(f"transform '{name}' already exists")
    _TRANSFORMS[name] = transform
