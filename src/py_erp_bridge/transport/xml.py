"""Safe XML helpers and a minimal SOAP 1.1 caller."""

from __future__ import annotations

import re
from collections.abc import Mapping
from xml.etree.ElementTree import Element, ParseError
from xml.sax.saxutils import escape

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException

from py_erp_bridge.exceptions import APIError, BusinessRuleError
from py_erp_bridge.transport.http import HttpTransport
from py_erp_bridge.types import JsonObject, JsonValue

_CHAR_REF = re.compile(r"&#(x[0-9a-fA-F]+|[0-9]+);")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")


def _valid_ref(match: re.Match[str]) -> str:
    ref = match[1]
    code = int(ref[1:], 16) if ref.startswith("x") else int(ref)
    ok = code in {9, 10, 13} or 0x20 <= code <= 0xD7FF or 0xE000 <= code <= 0xFFFD
    return match[0] if ok or 0x10000 <= code <= 0x10FFFF else ""


def sanitize(document: str) -> str:
    """Drop characters that XML 1.0 forbids; some ERPs emit them."""
    return _CONTROL.sub("", _CHAR_REF.sub(_valid_ref, document))


def parse(document: str) -> Element:
    """Parse untrusted XML (DTDs and entities are rejected)."""
    cleaned = re.sub(r"^\s*<\?xml[^>]*\?>", "", sanitize(document).lstrip("\ufeff"))
    try:
        root: Element = ElementTree.fromstring(cleaned)
    except (ParseError, DefusedXmlException) as exc:
        raise APIError(f"invalid XML response: {type(exc).__name__}") from exc
    return root


def tag(name: str, value: str, attributes: Mapping[str, str] | None = None) -> str:
    """Build an element whose text is escaped."""
    return element(name, escape(value), attributes)


def element(name: str, inner: str, attributes: Mapping[str, str] | None = None) -> str:
    """Build an element from already encoded inner XML."""
    if not re.fullmatch(r"[A-Za-z_][\w.:-]*", name):
        raise ValueError(f"invalid XML name '{name}'")
    attrs = "".join(
        f' {k}="{escape(v, {chr(34): "&quot;"})}"' for k, v in (attributes or {}).items()
    )
    return f"<{name}{attrs}>{inner}</{name}>"


def to_json(node: Element) -> JsonValue:
    """Convert an element to JSON: leaves become text, repeated children become arrays."""
    if len(node) == 0:
        return (node.text or "").strip()
    result: JsonObject = {}
    for child in node:
        name = child.tag.rpartition("}")[2]
        value = to_json(child)
        if name in result:
            existing = result[name]
            if isinstance(existing, list):
                existing.append(value)
            else:
                result[name] = [existing, value]
        else:
            result[name] = value
    return result


async def soap_call(
    http: HttpTransport,
    path: str,
    body_xml: str,
    *,
    action: str,
    idempotent: bool = False,
) -> Element:
    """Send a SOAP 1.1 body and return the response Body; faults raise."""
    if '"' in action or "\n" in action or "\r" in action:
        raise ValueError("invalid SOAP action")
    envelope = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">'
        f"<soapenv:Body>{body_xml}</soapenv:Body></soapenv:Envelope>"
    )
    response = await http.request(
        "POST",
        path,
        content=envelope.encode(),
        headers={"Content-Type": "text/xml; charset=utf-8", "SOAPAction": f'"{action}"'},
        idempotent=idempotent,
    )
    root = parse(response.text)
    body = root.find("{*}Body")
    if body is None:
        raise APIError("SOAP response has no Body", provider=http.provider)
    fault = body.find("{*}Fault")
    if fault is not None:
        raise BusinessRuleError(
            f"SOAP fault: {fault.findtext('faultstring') or 'unknown'}",
            provider=http.provider,
            code=fault.findtext("faultcode"),
        )
    return body
