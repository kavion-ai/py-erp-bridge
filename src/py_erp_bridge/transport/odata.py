"""OData v2 / v4 syntax: literals, filters, keys, pages and multipart batches."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from enum import StrEnum
from typing import cast
from urllib.parse import quote

from pydantic import BaseModel

from py_erp_bridge.core.query import Filter, FilterOp, Query, RawPage
from py_erp_bridge.exceptions import APIError
from py_erp_bridge.types import JsonObject, JsonValue, Scalar, as_int, as_object, as_objects


class ODataVersion(StrEnum):
    """Protocol versions."""

    V2 = "v2"
    V4 = "v4"


def literal(value: Scalar, version: ODataVersion) -> str:
    """Render a literal, escaping strings."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, datetime):
        utc = value.astimezone(UTC) if value.tzinfo else value
        stamp = utc.replace(tzinfo=None).isoformat(timespec="seconds")
        if version is ODataVersion.V4:
            return stamp + "Z"
        return f"datetimeoffset'{stamp}Z'" if value.tzinfo else f"datetime'{stamp}'"
    if isinstance(value, date):
        iso = value.isoformat()
        return iso if version is ODataVersion.V4 else f"datetime'{iso}T00:00:00'"
    if isinstance(value, Decimal):
        return f"{value}M" if version is ODataVersion.V2 else str(value)
    if isinstance(value, (int, float)):
        return str(value)
    escaped = value.replace("'", "''")
    return f"'{escaped}'"


def condition(item: Filter, version: ODataVersion) -> str:
    """Render one filter."""
    match item.op:
        case FilterOp.IN:
            if not item.values:
                return "false"
            parts = [f"{item.field} eq {literal(v, version)}" for v in item.values]
            return "(" + " or ".join(parts) + ")"
        case FilterOp.CONTAINS if version is ODataVersion.V2:
            return f"substringof({literal(item.scalar, version)},{item.field})"
        case FilterOp.CONTAINS:
            return f"contains({item.field},{literal(item.scalar, version)})"
        case FilterOp.STARTSWITH:
            return f"startswith({item.field},{literal(item.scalar, version)})"
        case _:
            return f"{item.field} {item.op} {literal(item.scalar, version)}"


def build_params(
    query: Query,
    version: ODataVersion,
    *,
    top: int,
    skip: int,
    key_fields: Sequence[str],
    default_sort: bool,
    render: Callable[[Filter, ODataVersion], str] = condition,
) -> dict[str, str]:
    """Build system query options. Without a sort, keys are used so paging is stable."""
    params = {"$top": str(top)}
    if skip:
        params["$skip"] = str(skip)
    if query.filters:
        params["$filter"] = " and ".join(render(f, version) for f in query.filters)
    if query.sort:
        params["$orderby"] = ",".join(
            f"{s.field} desc" if s.descending else s.field for s in query.sort
        )
    elif default_sort:
        params["$orderby"] = ",".join(key_fields)
    if query.select:
        params["$select"] = ",".join(dict.fromkeys([*key_fields, *query.select]))
    return {**params, **query.params}


def key_predicate(keys: Mapping[str, str]) -> str:
    """Render a URL-encoded key predicate with string-typed keys."""
    quoted = {k: quote("'" + v.replace("'", "''") + "'", safe="") for k, v in keys.items()}
    if len(quoted) == 1:
        return f"({next(iter(quoted.values()))})"
    return "(" + ",".join(f"{k}={v}" for k, v in quoted.items()) + ")"


def parse_page(body: JsonObject, version: ODataVersion) -> tuple[RawPage, str | None]:
    """Return the page and the server's continuation link."""
    if version is ODataVersion.V4:
        link = body.get("@odata.nextLink")
        records = body.get("value")
        total = as_int(body.get("@odata.count"))
    else:
        data = body.get("d")
        inner = as_object(data)
        link = inner.get("__next")
        records = data if isinstance(data, list) else inner.get("results")
        total = as_int(inner.get("__count"))
    if not isinstance(records, list):
        raise APIError("OData response has no collection")
    return (
        RawPage(records=as_objects(cast("JsonValue", records)), total=total),
        link if isinstance(link, str) else None,
    )


def parse_error(body: JsonObject) -> tuple[str | None, str | None]:
    """Extract (code, message) from v2 or v4 error bodies."""
    error = as_object(body.get("error"))
    message = error.get("message")
    if isinstance(message, dict):
        message = message.get("value")
    code = error.get("code")
    return (code if isinstance(code, str) else None, message if isinstance(message, str) else None)


class BatchOperation(BaseModel):
    """One write in a batch; ``path`` is relative to the service root."""

    method: str = "POST"
    path: str
    payload: JsonObject


class BatchResponse(BaseModel):
    """Response to one operation."""

    status: int
    body: JsonObject


def encode_batch(operations: Sequence[BatchOperation], *, atomic: bool) -> tuple[str, bytes]:
    """Encode as multipart/mixed; returns (content type, body).

    ``atomic`` puts everything into one changeset; otherwise each write has its own.
    """
    batch = f"batch_{uuid.uuid4().hex}"
    groups = [list(operations)] if atomic else [[op] for op in operations]
    lines: list[str] = []
    content_id = 0
    for group in groups:
        changeset = f"changeset_{uuid.uuid4().hex}"
        lines += [f"--{batch}", f"Content-Type: multipart/mixed; boundary={changeset}", ""]
        for op in group:
            content_id += 1
            body = json.dumps(op.payload, ensure_ascii=True)
            lines += [
                f"--{changeset}",
                "Content-Type: application/http",
                "Content-Transfer-Encoding: binary",
                f"Content-ID: {content_id}",
                "",
                f"{op.method} {op.path} HTTP/1.1",
                "Content-Type: application/json",
                "Accept: application/json",
                f"Content-Length: {len(body)}",
                "",
                body,
            ]
        lines += [f"--{changeset}--"]
    lines += [f"--{batch}--", ""]
    return f"multipart/mixed; boundary={batch}", "\r\n".join(lines).encode("ascii")


def decode_batch(content_type: str, content: bytes) -> list[list[BatchResponse]]:
    """Decode a batch response into one list per changeset.

    A failed changeset yields a single response regardless of its number of operations.
    """
    head = f"Content-Type: {content_type}\r\n\r\n".encode("latin-1")
    root = BytesParser(policy=policy.HTTP).parsebytes(head + content)
    if not root.is_multipart():
        raise APIError("batch response is not multipart")
    groups = []
    for part in root.iter_parts():
        members = list(part.iter_parts()) if part.is_multipart() else [part]
        groups.append([_decode_part(cast("EmailMessage", m)) for m in members])
    return groups


def _decode_part(part: EmailMessage) -> BatchResponse:
    raw = part.get_payload(decode=True)
    content = raw if isinstance(raw, bytes) else b""
    head, _, body = content.replace(b"\r\n", b"\n").lstrip(b"\n").partition(b"\n\n")
    status_line = head.split(b"\n", 1)[0].decode("latin-1").split(" ")
    if len(status_line) < 2 or not status_line[1].isdigit():
        raise APIError("malformed batch part")
    try:
        data = json.loads(body) if body.strip() else {}
    except ValueError:
        data = {}
    return BatchResponse(status=int(status_line[1]), body=data if isinstance(data, dict) else {})
