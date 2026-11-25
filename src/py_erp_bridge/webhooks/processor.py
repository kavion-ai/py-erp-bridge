"""Webhook parsing, deduplication and dispatch."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Protocol

from pydantic import BaseModel

from py_erp_bridge.exceptions import WebhookError
from py_erp_bridge.types import JsonObject
from py_erp_bridge.webhooks.verification import WebhookVerifier


class WebhookEvent(BaseModel):
    """A verified delivery."""

    id: str
    type: str
    payload: JsonObject


Handler = Callable[[WebhookEvent], Awaitable[None]]
Extractor = Callable[[Mapping[str, str], JsonObject], str | None]


class DeduplicationStore(Protocol):
    """Remembers processed deliveries. Use a shared store if you run several workers."""

    async def claim(self, event_id: str) -> bool:
        """Atomically claim an id; False if it was claimed before."""

    async def release(self, event_id: str) -> None:
        """Undo a claim so a redelivery is processed."""


class InMemoryDeduplication:
    """Single-process store with expiry."""

    def __init__(self, ttl: float = 86400, clock: Callable[[], float] = time.monotonic) -> None:
        self._ttl = ttl
        self._clock = clock
        self._seen: dict[str, float] = {}

    async def claim(self, event_id: str) -> bool:
        """Claim an id."""
        now = self._clock()
        self._seen = {k: t for k, t in self._seen.items() if now - t < self._ttl}
        if event_id in self._seen:
            return False
        self._seen[event_id] = now
        return True

    async def release(self, event_id: str) -> None:
        """Undo a claim."""
        self._seen.pop(event_id, None)


class WebhookProcessor:
    """Verifies, deduplicates and dispatches deliveries.

    ``event_type`` and ``event_id`` extract those values from headers and payload. Without
    an id, the body hash is used, which also drops legitimate identical deliveries within
    the store's retention; supply ``event_id`` if your ERP sends one.
    """

    def __init__(
        self,
        verifier: WebhookVerifier,
        *,
        event_type: Extractor,
        event_id: Extractor | None = None,
        store: DeduplicationStore | None = None,
        max_body: int = 5_000_000,
    ) -> None:
        self._verifier = verifier
        self._event_type = event_type
        self._event_id = event_id
        self._store = store or InMemoryDeduplication()
        self._max_body = max_body
        self._handlers: dict[str, list[Handler]] = {}

    def on(self, event_type: str) -> Callable[[Handler], Handler]:
        """Register a handler; ``"*"`` receives everything."""

        def decorate(handler: Handler) -> Handler:
            self._handlers.setdefault(event_type, []).append(handler)
            return handler

        return decorate

    async def process(self, headers: Mapping[str, str], body: bytes) -> WebhookEvent | None:
        """Handle a delivery; returns None for a duplicate.

        If a handler raises, the claim is released so that the ERP's retry is processed.
        """
        if len(body) > self._max_body:
            raise WebhookError("webhook body too large")
        self._verifier.verify(headers, body)
        try:
            payload = json.loads(body)
        except ValueError as exc:
            raise WebhookError("webhook body is not valid JSON") from exc
        if not isinstance(payload, dict):
            raise WebhookError("webhook body is not a JSON object")
        identifier = self._event_id(headers, payload) if self._event_id else None
        event = WebhookEvent(
            id=identifier or hashlib.sha256(body).hexdigest(),
            type=self._event_type(headers, payload) or "unknown",
            payload=payload,
        )
        if not await self._store.claim(event.id):
            return None
        try:
            for handler in [*self._handlers.get(event.type, []), *self._handlers.get("*", [])]:
                await handler(event)
        except BaseException:
            await self._store.release(event.id)
            raise
        return event
