"""Run every example against a real (local) HTTP server."""

from __future__ import annotations

import base64
import hashlib
import hmac
import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from py_erp_bridge import ERPClient
from py_erp_bridge.transport import rfc

EXAMPLES = Path(__file__).parent.parent / "examples"
XML = (
    "<ENVELOPE><HEADER><STATUS>1</STATUS></HEADER>"
    "<BODY><DATA><COLLECTION/></DATA></BODY></ENVELOPE>"
)
PROVIDERS = {
    "sap_s4hana": ("S4", {"d": {"results": []}}),
    "oracle_fusion": ("FUSION", {"items": []}),
    "netsuite": ("NETSUITE", {"items": []}),
    "dynamics365": ("D365", {"value": []}),
    "odoo": ("ODOO", []),
    "erpnext": ("ERPNEXT", {"data": [], "message": "user"}),
    "tally": ("TALLY", XML),
    "infor": ("INFOR", {"results": [{"records": []}]}),
    "sage_intacct": ("INTACCT", {"ia::result": [], "ia::meta": {}}),
}


def load(name):
    spec = importlib.util.spec_from_file_location(f"example_{name}", EXAMPLES / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def serve():
    servers = []

    def start(answer):
        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def respond(self):
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                body = (answer if isinstance(answer, str) else json.dumps(answer)).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = respond

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return f"http://127.0.0.1:{server.server_port}"

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize("provider", PROVIDERS)
def test_provider_example_reads(provider, serve, monkeypatch, capsys):
    prefix, answer = PROVIDERS[provider]
    monkeypatch.setenv(f"{prefix}_BASE_URL", serve(answer))
    monkeypatch.delenv("ERP_EXAMPLE_WRITE", raising=False)
    if provider != "tally":
        monkeypatch.setenv(f"{prefix}_AUTH__TYPE", "bearer")
        monkeypatch.setenv(f"{prefix}_AUTH__TOKEN", "test")
    monkeypatch.setenv("INFOR_TENANT", "T")
    monkeypatch.setenv("D365_COMPANY", "usmf")
    load(provider).main()
    out = capsys.readouterr().out
    assert "healthy: True" in out
    assert "async count: 0" in out


def test_ecc_example(monkeypatch, capsys):
    class Connection:
        def call(self, name, **params):
            return {"FIELDS": params.get("FIELDS", []), "DATA": []}

        def ping(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(rfc, "_factory", lambda params: Connection())
    for name, value in {
        "CLIENT": "100",
        "USER": "u",
        "PASSWD": "p",
        "ASHOST": "h",
        "SYSNR": "00",
    }.items():
        monkeypatch.setenv(f"ECC_RFC__{name}", value)
    load("sap_ecc").main()
    assert "no such customer" in capsys.readouterr().out


async def test_sync_example(serve, monkeypatch, tmp_path, capsys):
    url = serve({"data": [{"name": "C", "modified": "2024-01-01 10:00:00"}]})
    for name, value in {"BASE_URL": url, "AUTH__TYPE": "bearer", "AUTH__TOKEN": "t"}.items():
        monkeypatch.setenv(f"ERPNEXT_{name}", value)
    await load("incremental_sync").main(tmp_path / "s.json")
    assert "2024-01-01T10:00" in (tmp_path / "s.json").read_text()
    assert "processed 1" in capsys.readouterr().out


async def test_webhook_example(capsys):
    body = b'{"doctype": "Customer", "name": "C", "modified": "x"}'
    signature = base64.b64encode(hmac.new(b"s", body, hashlib.sha256).digest()).decode()
    await (
        load("webhooks")
        .build_processor("s")
        .process({"X-Frappe-Webhook-Signature": signature}, body)
    )
    assert "customer changed: C" in capsys.readouterr().out


def test_custom_provider_example(serve):
    load("custom_provider")
    config = {
        "base_url": serve({"data": [{"id": 1, "displayName": "Acme"}]}),
        "auth": {"type": "bearer", "token": "t"},
    }
    with ERPClient("example_erp", config) as erp:
        assert erp.customers.get_all()[0].name == "Acme"


def test_env_example_contains_no_values_that_look_like_secrets():
    for line in (EXAMPLES.parent / ".env.example").read_text("utf-8").splitlines():
        name, _, value = line.partition("=")
        if any(word in name for word in ("SECRET", "PASSW", "TOKEN", "KEY")) and "URL" not in name:
            assert value in {"", "PS256"} or "ID" in name, name
