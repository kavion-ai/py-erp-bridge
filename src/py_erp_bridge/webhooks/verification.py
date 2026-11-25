"""Webhook authenticity checks."""

from __future__ import annotations

import base64
import hashlib
import hmac
from collections.abc import Mapping
from typing import Literal, Protocol

from pydantic import SecretStr

from py_erp_bridge.exceptions import SignatureVerificationError


class WebhookVerifier(Protocol):
    """Raises SignatureVerificationError unless the request is authentic."""

    def verify(self, headers: Mapping[str, str], body: bytes) -> None:
        """Check the raw, undecoded body."""


def _header(headers: Mapping[str, str], name: str) -> str:
    for key, value in headers.items():
        if key.lower() == name.lower():
            return value
    raise SignatureVerificationError(f"missing header {name}")


class HmacVerifier:
    """HMAC over the raw body."""

    def __init__(
        self,
        secret: SecretStr,
        header: str,
        *,
        algorithm: Literal["sha256", "sha384", "sha512"] = "sha256",
        encoding: Literal["hex", "base64"] = "hex",
        prefix: str = "",
    ) -> None:
        if not secret.get_secret_value():
            raise ValueError("webhook secret must not be empty")
        self._secret = secret
        self._header = header
        self._algorithm = algorithm
        self._encoding = encoding
        self._prefix = prefix

    def verify(self, headers: Mapping[str, str], body: bytes) -> None:
        """Compare signatures in constant time."""
        received = _header(headers, self._header)
        key = self._secret.get_secret_value().encode()
        digest = hmac.new(key, body, getattr(hashlib, self._algorithm)).digest()
        encoded = digest.hex() if self._encoding == "hex" else base64.b64encode(digest).decode()
        if not hmac.compare_digest((self._prefix + encoded).encode(), received.encode()):
            raise SignatureVerificationError("signature mismatch")


class SharedSecretVerifier:
    """A secret header, for ERPs that can add headers but do not sign payloads."""

    def __init__(self, secret: SecretStr, header: str) -> None:
        if not secret.get_secret_value():
            raise ValueError("webhook secret must not be empty")
        self._secret = secret
        self._header = header

    def verify(self, headers: Mapping[str, str], body: bytes) -> None:
        """Compare the header in constant time."""
        expected = self._secret.get_secret_value().encode()
        if not hmac.compare_digest(expected, _header(headers, self._header).encode()):
            raise SignatureVerificationError("shared secret mismatch")


def frappe_verifier(secret: SecretStr) -> HmacVerifier:
    """Verifier for Frappe / ERPNext webhooks."""
    return HmacVerifier(secret, "X-Frappe-Webhook-Signature", encoding="base64")
