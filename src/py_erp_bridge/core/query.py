"""Provider-neutral queries."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from py_erp_bridge.types import JsonObject, JsonValue, Scalar, text


class FilterOp(StrEnum):
    """Comparison operators. Providers may support only a subset."""

    EQ = "eq"
    NE = "ne"
    GT = "gt"
    GE = "ge"
    LT = "lt"
    LE = "le"
    IN = "in"
    CONTAINS = "contains"
    STARTSWITH = "startswith"


class Filter(BaseModel):
    """One condition; conditions are AND-ed."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    field: str
    op: FilterOp = FilterOp.EQ
    value: Scalar | list[Scalar]

    @property
    def values(self) -> list[Scalar]:
        """The value as a list."""
        return self.value if isinstance(self.value, list) else [self.value]

    @property
    def scalar(self) -> Scalar:
        """The value, which must not be a list."""
        if isinstance(self.value, list):
            raise ValueError(f"operator '{self.op}' needs a single value")
        return self.value


class Sort(BaseModel):
    """Sort key."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    field: str
    descending: bool = False


class Query(BaseModel):
    """A list request. Field names are canonical until translated by the mapper."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    filters: tuple[Filter, ...] = ()
    sort: tuple[Sort, ...] = ()
    select: tuple[str, ...] = ()
    page_size: int = Field(default=100, ge=1)
    #: Provider-native query parameters, passed through unchanged.
    params: dict[str, str] = Field(default_factory=dict)


FilterInput = Mapping[str, Scalar | list[Scalar]] | Sequence[Filter]


def parse_filters(filters: FilterInput | None) -> tuple[Filter, ...]:
    """Accept ``Filter`` objects or ``{"name": x, "total__gt": y}`` shorthand."""
    if not filters:
        return ()
    if not isinstance(filters, Mapping):
        return tuple(filters)
    result = []
    for key, value in filters.items():
        name, _, op = key.rpartition("__")
        if name and op in set(FilterOp):
            result.append(Filter(field=name, op=FilterOp(op), value=value))
        else:
            default = FilterOp.IN if isinstance(value, list) else FilterOp.EQ
            result.append(Filter(field=key, op=default, value=value))
    return tuple(result)


def parse_sort(sort: Sequence[str | Sort] | None) -> tuple[Sort, ...]:
    """Accept ``Sort`` objects or names; a leading ``-`` means descending."""
    return tuple(
        Sort(field=s.removeprefix("-"), descending=s.startswith("-")) if isinstance(s, str) else s
        for s in sort or ()
    )


class RawPage(BaseModel):
    """One page of provider records."""

    records: list[JsonObject]
    next_cursor: str | None = None
    total: int | None = None


T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    """One page of canonical records; pass ``next_cursor`` back to continue."""

    items: list[T]
    next_cursor: str | None = None
    total: int | None = None


def _compare(actual: JsonValue, expected: Scalar, op: FilterOp) -> bool:
    left: str | Decimal = text(actual)
    right: str | Decimal
    if isinstance(expected, (int, float, Decimal)) and not isinstance(expected, bool):
        try:
            left, right = Decimal(left), Decimal(str(expected))
        except InvalidOperation:
            return op is FilterOp.NE
    else:
        right = expected.isoformat() if isinstance(expected, (date, datetime)) else str(expected)
    match op:
        case FilterOp.NE:
            return left != right
        case FilterOp.GT:
            return left > right  # type: ignore[operator]
        case FilterOp.GE:
            return left >= right  # type: ignore[operator]
        case FilterOp.LT:
            return left < right  # type: ignore[operator]
        case FilterOp.LE:
            return left <= right  # type: ignore[operator]
        case FilterOp.CONTAINS:
            return str(right).casefold() in str(left).casefold()
        case FilterOp.STARTSWITH:
            return str(left).casefold().startswith(str(right).casefold())
        case _:
            return left == right


def matches(record: JsonObject, filters: Sequence[Filter]) -> bool:
    """Evaluate provider-field filters locally, for providers without server filtering."""
    for item in filters:
        value = record.get(item.field)
        if item.op is FilterOp.IN:
            if not any(_compare(value, v, FilterOp.EQ) for v in item.values):
                return False
        elif not _compare(value, item.scalar, item.op):
            return False
    return True
