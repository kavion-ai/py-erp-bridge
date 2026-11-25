"""Receive ERPNext webhooks with the standard library only.

Run with WEBHOOK_SECRET set, behind a TLS-terminating proxy. With a real web framework,
pass its raw request body and headers to ``WebhookProcessor.process`` in the same way.
"""

from __future__ import annotations

import asyncio
import os
from http.server import BaseHTTPRequestHandler, HTTPServer

from pydantic import SecretStr

from py_erp_bridge.exceptions import SignatureVerificationError, WebhookError
from py_erp_bridge.types import text
from py_erp_bridge.webhooks import WebhookEvent, WebhookProcessor, frappe_verifier

MAX_BODY = 1_000_000


def build_processor(secret: str) -> WebhookProcessor:
    """Create a processor with handlers."""
    processor = WebhookProcessor(
        frappe_verifier(SecretStr(secret)),
        event_type=lambda headers, payload: text(payload.get("doctype")),
        event_id=lambda headers, payload: f"{payload.get('name')}:{payload.get('modified')}",
        max_body=MAX_BODY,
    )

    @processor.on("Customer")
    async def customer_changed(event: WebhookEvent) -> None:
        print("customer changed:", event.payload.get("name"))

    return processor


def serve(processor: WebhookProcessor, port: int = 8080) -> None:
    """Serve until interrupted."""

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                self.send_error(413)
                return
            try:
                asyncio.run(processor.process(dict(self.headers), self.rfile.read(length)))
            except SignatureVerificationError:
                self.send_error(401)
            except WebhookError:
                self.send_error(400)
            else:
                self.send_response(204)
                self.end_headers()

    HTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    serve(build_processor(os.environ["WEBHOOK_SECRET"]))
