"""Copy changed customers since the last run, with a durable watermark."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path

from py_erp_bridge import ERPClient, IncrementalSync, config_from_env
from py_erp_bridge.models import Customer


class FileState:
    """Stores watermarks in a JSON file. Use a database if several workers sync."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def _read(self) -> dict[str, str]:
        return json.loads(self._path.read_text("utf-8")) if self._path.exists() else {}

    async def load(self, key: str) -> datetime | None:
        """Return the watermark."""
        value = self._read().get(key)
        return datetime.fromisoformat(value) if value else None

    async def save(self, key: str, watermark: datetime) -> None:
        """Replace the file atomically."""
        data = self._read() | {key: watermark.isoformat()}
        temporary = self._path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data), "utf-8")
        temporary.replace(self._path)


async def upsert(customer: Customer) -> None:
    """Handlers must be idempotent: records can be delivered more than once."""
    print("upsert", customer.id, customer.name)


async def main(state: Path = Path("sync-state.json")) -> None:
    """Run one synchronization."""
    async with ERPClient.create_async("erpnext", config_from_env("ERPNEXT")) as erp:
        report = await IncrementalSync(erp.customers, FileState(state)).run(upsert)
        print("processed", report.processed, "up to", report.watermark)


if __name__ == "__main__":
    asyncio.run(main())
