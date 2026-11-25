"""Authentication contract."""

from __future__ import annotations

from abc import ABC, abstractmethod

import httpx


class AuthStrategy(ABC):
    """Adds credentials to outgoing requests."""

    @abstractmethod
    async def apply(self, request: httpx.Request) -> None:
        """Modify *request* in place. Called for every attempt."""

    async def refresh(self, rejected: httpx.Request) -> bool:
        """React to a 401 for *rejected*. True if a resend is worthwhile."""
        return False
