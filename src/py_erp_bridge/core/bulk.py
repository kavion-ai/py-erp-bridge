"""Bulk operation results."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from py_erp_bridge.models import ERPRecord


class BulkItemResult(BaseModel):
    """Outcome of one item. ``skipped`` means it was never attempted."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    index: int
    record: ERPRecord | None = None
    error: Exception | None = None
    skipped: bool = False

    @property
    def ok(self) -> bool:
        """Whether the item succeeded."""
        return self.error is None and not self.skipped


class BulkResult(BaseModel):
    """Outcome of a bulk operation, in input order."""

    items: list[BulkItemResult]

    @property
    def succeeded(self) -> list[BulkItemResult]:
        """Successful items."""
        return [i for i in self.items if i.ok]

    @property
    def failed(self) -> list[BulkItemResult]:
        """Failed or skipped items."""
        return [i for i in self.items if not i.ok]
