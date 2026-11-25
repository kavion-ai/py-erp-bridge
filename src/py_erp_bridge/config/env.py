"""Build configuration dictionaries from environment variables."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping

from py_erp_bridge.types import JsonObject, JsonValue, as_object


def _decode(value: str) -> JsonValue:
    if value.startswith("-----BEGIN"):
        return value.replace("\\n", "\n")
    if value[0] in "[{":
        try:
            decoded: JsonValue = json.loads(value)
        except ValueError:
            return value  # e.g. a password that starts with a bracket
        return decoded
    return value


def config_from_env(prefix: str, environ: Mapping[str, str] | None = None) -> JsonObject:
    """Collect ``PREFIX_*`` variables; ``__`` separates nesting levels.

    ``ODOO_BASE_URL`` and ``ODOO_AUTH__TOKEN`` with prefix ``ODOO`` become
    ``{"base_url": ..., "auth": {"token": ...}}``. Values that are JSON arrays or objects
    are decoded; everything else stays text and is converted by the config model.
    Empty values are ignored.
    """
    source = os.environ if environ is None else environ
    start = prefix.upper().rstrip("_") + "_"
    result: JsonObject = {}
    for name, value in source.items():
        if not name.upper().startswith(start) or not value:
            continue
        *parents, leaf = name[len(start) :].lower().split("__")
        node = result
        for parent in parents:
            child = as_object(node.get(parent))
            node[parent] = child
            node = child
        node[leaf] = _decode(value)
    return result
