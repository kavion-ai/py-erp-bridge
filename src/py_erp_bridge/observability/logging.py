"""Standard-library logging with optional JSON output.

The SDK never logs bodies, query strings or Authorization headers: in ERP traffic those
routinely contain personal and financial data.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

logger = logging.getLogger("py_erp_bridge")
logger.addHandler(logging.NullHandler())

_STANDARD = frozenset(vars(logging.makeLogRecord({}))) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    """Render records, including ``extra`` fields, as one JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        """Render one record."""
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        payload.update({k: v for k, v in vars(record).items() if k not in _STANDARD})
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: int | str = logging.INFO) -> None:
    """Attach a JSON stream handler to the SDK logger. Safe to call repeatedly."""
    if not any(isinstance(h.formatter, JsonFormatter) for h in logger.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
    logger.setLevel(level)
