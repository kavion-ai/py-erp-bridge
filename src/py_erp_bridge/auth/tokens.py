"""Token model and storage."""

from __future__ import annotations

import time
from typing import Protocol

from pydantic import BaseModel, SecretStr


class Token(BaseModel):
    """An OAuth token; ``expires_at`` is a Unix timestamp."""

    access_token: SecretStr
    refresh_token: SecretStr | None = None
    expires_at: float | None = None

    def valid(self, skew: float) -> bool:
        """Whether the token is usable for at least *skew* more seconds."""
        return self.expires_at is None or self.expires_at - skew > time.time()


class TokenStore(Protocol):
    """Persists tokens. Implement it to survive restarts with rotating refresh tokens."""

    async def load(self) -> Token | None:
        """Return the stored token."""

    async def save(self, token: Token) -> None:
        """Store a token."""


class InMemoryTokenStore:
    """Keeps the token in process memory."""

    def __init__(self) -> None:
        self._token: Token | None = None

    async def load(self) -> Token | None:
        """Return the stored token."""
        return self._token

    async def save(self, token: Token) -> None:
        """Store a token."""
        self._token = token
