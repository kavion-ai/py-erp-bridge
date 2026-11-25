import json
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from helpers import MockServer, config, ok
from py_erp_bridge import AsyncERPClient, Filter, FilterOp
from py_erp_bridge.exceptions import (
    APIError,
    AuthenticationError,
    BusinessRuleError,
    ERPConnectionError,
    MissingDependencyError,
)
from py_erp_bridge.transport import rfc, xml
from py_erp_bridge.transport.odata import (
    BatchOperation,
    ODataVersion,
    condition,
    decode_batch,
    encode_batch,
    key_predicate,
    literal,
    parse_error,
    parse_page,
)

V2, V4 = ODataVersion.V2, ODataVersion.V4


@pytest.mark.parametrize(
    ("value", "v2", "v4"),
    [
        ("O'Brien", "'O''Brien'", "'O''Brien'"),
        (True, "true", "true"),
        (5, "5", "5"),
        (Decimal("1.5"), "1.5M", "1.5"),
        (date(2024, 1, 2), "datetime'2024-01-02T00:00:00'", "2024-01-02"),
        (
            datetime(2024, 1, 2, 3, tzinfo=UTC),
            "datetimeoffset'2024-01-02T03:00:00Z'",
            "2024-01-02T03:00:00Z",
        ),
        (datetime(2024, 1, 2, 3), "datetime'2024-01-02T03:00:00'", "2024-01-02T03:00:00Z"),
    ],
)
def test_odata_literals(value, v2, v4):
    assert literal(value, V2) == v2
    assert literal(value, V4) == v4


def test_odata_conditions():
    def render(op, value, version):
        return condition(Filter(field="F", op=op, value=value), version)

    assert render(FilterOp.CONTAINS, "a", V2) == "substringof('a',F)"
    assert render(FilterOp.CONTAINS, "a", V4) == "contains(F,'a')"
    assert render(FilterOp.IN, ["a", "b"], V4) == "(F eq 'a' or F eq 'b')"
    assert render(FilterOp.GE, 3, V2) == "F ge 3"
    with pytest.raises(ValueError, match="single value"):
        render(FilterOp.EQ, ["a"], V4)


def test_odata_keys_are_escaped():
    assert key_predicate({"K": "a/b'c"}) == "(%27a%2Fb%27%27c%27)"
    assert key_predicate({"A": "1", "B": "2"}) == "(A=%271%27,B=%272%27)"


def test_odata_pages_and_errors():
    page, link = parse_page({"d": {"results": [{"a": 1}], "__next": "n", "__count": "9"}}, V2)
    assert (page.records, page.total, link) == ([{"a": 1}], 9, "n")
    page, link = parse_page({"value": [], "@odata.count": 0}, V4)
    assert (page.records, page.total, link) == ([], 0, None)
    with pytest.raises(APIError):
        parse_page({}, V4)
    assert parse_error({"error": {"code": "X", "message": {"value": "m"}}}) == ("X", "m")
    assert parse_error({"error": {"code": "X", "message": "m"}}) == ("X", "m")


def test_batch_encoding():
    ops = [BatchOperation(path="Set", payload={"n": "ü"}), BatchOperation(path="Set", payload={})]
    kind, body = encode_batch(ops, atomic=False)
    assert kind.startswith("multipart/mixed; boundary=batch_")
    assert body.count(b"boundary=changeset_") == 2
    assert b"POST Set HTTP/1.1\r\n" in body
    assert encode_batch(ops, atomic=True)[1].count(b"boundary=changeset_") == 1


def batch_response(*groups):
    """Build a response as servers do; a group is a list of (status, body)."""
    out = []
    for i, group in enumerate(groups):
        inner = []
        for status, body in group:
            content = json.dumps(body)
            inner.append(
                "Content-Type: application/http\r\nContent-Transfer-Encoding: binary\r\n\r\n"
                f"HTTP/1.1 {status} X\r\nContent-Type: application/json\r\n\r\n{content}\r\n"
            )
        if group[0][0] >= 300:
            out.append("--b\r\n" + inner[0])
        else:
            parts = "".join(f"--c{i}\r\n{p}" for p in inner)
            out.append(
                f"--b\r\nContent-Type: multipart/mixed; boundary=c{i}\r\n\r\n{parts}--c{i}--\r\n"
            )
    return "multipart/mixed; boundary=b", ("".join(out) + "--b--\r\n").encode()


def test_batch_decoding():
    groups = decode_batch(*batch_response([(201, {"a": 1})], [(400, {"error": {}})]))
    assert [[r.status for r in g] for g in groups] == [[201], [400]]
    assert groups[0][0].body == {"a": 1}
    with pytest.raises(APIError):
        decode_batch("application/json", b"{}")


# -- XML --------------------------------------------------------------------


def test_xml_escaping_and_names():
    assert xml.tag("A", "<&>") == "<A>&lt;&amp;&gt;</A>"
    assert xml.element("A", "", {"n": 'x"<'}) == '<A n="x&quot;&lt;"></A>'
    with pytest.raises(ValueError, match="invalid XML name"):
        xml.tag("A><B", "x")


def test_xml_parse_tolerates_control_characters_and_rejects_attacks():
    assert xml.parse("<?xml version='1.0' encoding='utf-16'?><a>x&#4;y\x04&#65;</a>").text == "xyA"
    bomb = '<!DOCTYPE a [<!ENTITY x SYSTEM "file:///etc/passwd">]><a>&x;</a>'
    with pytest.raises(APIError):
        xml.parse(bomb)
    with pytest.raises(APIError):
        xml.parse("<a>")


def test_xml_to_json():
    node = xml.parse("<r xmlns='urn:x'><a>1</a><b>x</b><b>y</b><b>z</b><c><d/></c></r>")
    assert xml.to_json(node) == {"a": "1", "b": ["x", "y", "z"], "c": {"d": ""}}


async def test_soap():
    env = '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body>{}</s:Body></s:Envelope>'
    fault = "<s:Fault><faultcode>s:Server</faultcode><faultstring>boom</faultstring></s:Fault>"
    server = MockServer(ok(text=env.format("<r><v>1</v></r>")), ok(text=env.format(fault)))
    async with AsyncERPClient(
        "oracle_fusion", config("oracle_fusion"), http_transport=server
    ) as erp:
        result = await erp.execute("soap", path="/svc", body_xml="<q/>", action="urn:a")
        assert result == {"r": {"v": "1"}}
        assert server.last.headers["SOAPAction"] == '"urn:a"'
        with pytest.raises(BusinessRuleError, match="boom"):
            await erp.execute("soap", path="/svc", body_xml="<q/>", action="urn:a")
        with pytest.raises(ValueError, match="action"):
            await erp.execute("soap", path="/svc", body_xml="", action='a"\r\nX: y')


# -- RFC --------------------------------------------------------------------

CFG = rfc.RfcConfig(client="100", user="u", passwd="p", ashost="h", sysnr="00")


class LogonError(Exception):
    pass


class CommunicationError(Exception):
    pass


class ABAPApplicationError(Exception):
    key = "NOT_FOUND"


@pytest.mark.parametrize(
    ("raised", "expected"),
    [
        (LogonError, AuthenticationError),
        (CommunicationError, ERPConnectionError),
        (ABAPApplicationError, BusinessRuleError),
        (KeyError, KeyError),
    ],
)
async def test_rfc_errors(monkeypatch, raised, expected):
    class Connection:
        def call(self, name, **params):
            raise raised("x")

        def close(self):
            pass

    monkeypatch.setattr(rfc, "_factory", lambda params: Connection())
    transport = rfc.RfcTransport(CFG)
    with pytest.raises(expected):
        await transport.call("F")
    await transport.aclose()


async def test_rfc_reconnects_after_communication_failure(monkeypatch):
    opened = []

    class Connection:
        def call(self, name, **params):
            if len(opened) == 1:
                raise CommunicationError
            return {"ok": 1}

        def close(self):
            pass

    def factory(params):
        opened.append(params)
        return Connection()

    monkeypatch.setattr(rfc, "_factory", factory)
    transport = rfc.RfcTransport(CFG)
    with pytest.raises(ERPConnectionError):
        await transport.call("F")
    assert await transport.call("F") == {"ok": 1}
    assert opened[0]["passwd"] == "p"


async def test_rfc_without_pyrfc():
    with pytest.raises(MissingDependencyError):
        await rfc.RfcTransport(CFG).ping()


@pytest.mark.parametrize(
    "bad",
    [
        {"client": "1", "user": "u", "passwd": "p"},
        {"client": "1", "ashost": "h", "sysnr": "0"},
        {
            "client": "1",
            "user": "u",
            "passwd": "p",
            "ashost": "h",
            "sysnr": "0",
            "mshost": "m",
            "sysid": "s",
            "group": "g",
        },
    ],
)
def test_rfc_config(bad):
    with pytest.raises(ValueError, match="set"):
        rfc.RfcConfig(**bad)
    assert "SECRET" not in repr(
        rfc.RfcConfig(client="1", user="u", passwd="SECRET", ashost="h", sysnr="0")
    )


async def test_rfc_cancelled_call_abandons_its_connection(monkeypatch):
    import asyncio
    import threading

    release, opened = threading.Event(), []

    class Connection:
        def call(self, name, **params):
            if name == "SLOW":
                release.wait(5)
            return {}

        def close(self):
            pass

    def factory(params):
        opened.append(Connection())
        return opened[-1]

    monkeypatch.setattr(rfc, "_factory", factory)
    transport = rfc.RfcTransport(CFG)
    task = asyncio.create_task(transport.call("SLOW"))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await transport.call("FAST")
    assert len(opened) == 2
    release.set()
