"""OAuth 2.0 grants."""

from __future__ import annotations

import asyncio
import time
import uuid
from abc import abstractmethod

import httpx
from pydantic import SecretStr

from py_erp_bridge.auth.base import AuthStrategy
from py_erp_bridge.auth.static import basic_header
from py_erp_bridge.auth.tokens import InMemoryTokenStore, Token, TokenStore
from py_erp_bridge.config.auth import (
    JwtAssertionAuth,
    OAuth2AuthorizationCode,
    OAuth2ClientCredentials,
    OAuth2Password,
)
from py_erp_bridge.exceptions import (
    AuthenticationError,
    ERPConnectionError,
    ERPTimeoutError,
    MissingDependencyError,
)
from py_erp_bridge.types import as_int, as_object, text

OAuth2Config = OAuth2ClientCredentials | OAuth2Password | OAuth2AuthorizationCode | JwtAssertionAuth


class OAuth2Strategy(AuthStrategy):
    """Caches, refreshes and applies access tokens. Concurrent refreshes are coalesced."""

    def __init__(
        self, config: OAuth2Config, client: httpx.AsyncClient, store: TokenStore | None = None
    ) -> None:
        self._config = config
        self._client = client
        self._store = store or InMemoryTokenStore()
        self._lock = asyncio.Lock()
        self._token: Token | None = None
        self._loaded = False

    @abstractmethod
    def grant(self) -> dict[str, str]:
        """Form fields of the initial grant."""

    def client_fields(self) -> tuple[dict[str, str], dict[str, str]]:
        """Client authentication as (form fields, headers)."""
        secret: SecretStr | None = getattr(self._config, "client_secret", None)
        if secret is not None and getattr(self._config, "client_auth", "body") == "basic":
            header = basic_header(self._config.client_id, secret.get_secret_value())
            return {}, {"Authorization": header}
        fields = {"client_id": self._config.client_id}
        if secret is not None:
            fields["client_secret"] = secret.get_secret_value()
        return fields, {}

    async def apply(self, request: httpx.Request) -> None:
        """Set the Authorization header."""
        token = await self._current()
        request.headers["Authorization"] = f"Bearer {token.access_token.get_secret_value()}"

    async def refresh(self, rejected: httpx.Request) -> bool:
        """Expire the rejected token, unless another request already replaced it."""
        if self._token is not None and rejected.headers.get("Authorization") == (
            f"Bearer {self._token.access_token.get_secret_value()}"
        ):
            self._token = self._token.model_copy(update={"expires_at": 0.0})
        return True

    async def _current(self) -> Token:
        async with self._lock:
            if not self._loaded:
                self._token = await self._store.load()
                self._loaded = True
            if self._token is None or not self._token.valid(self._config.expiry_skew):
                self._token = await self._obtain(self._token)
                await self._store.save(self._token)
            return self._token

    async def _obtain(self, old: Token | None) -> Token:
        if old is not None and old.refresh_token is not None:
            try:
                return await self._exchange(self._refresh_grant(old.refresh_token), old)
            except AuthenticationError:
                # Fall back to the original grant, e.g. client credentials.
                pass
        return await self._exchange(self.grant(), old)

    @staticmethod
    def _refresh_grant(token: SecretStr) -> dict[str, str]:
        return {"grant_type": "refresh_token", "refresh_token": token.get_secret_value()}

    async def _exchange(self, fields: dict[str, str], old: Token | None) -> Token:
        extra, headers = self.client_fields()
        data = {**fields, **extra, **self._config.extra_params}
        if self._config.scope and fields["grant_type"] != "refresh_token":
            data["scope"] = self._config.scope
        try:
            response = await self._client.post(
                self._config.token_url,
                data=data,
                headers={"Accept": "application/json", **headers},
            )
        except httpx.TimeoutException as exc:
            raise ERPTimeoutError("token endpoint timed out", request_sent=False) from exc
        except httpx.TransportError as exc:
            raise ERPConnectionError("token endpoint unreachable", request_sent=False) from exc
        try:
            body = as_object(response.json())
        except ValueError:
            body = {}
        access = text(body.get("access_token"))
        if not response.is_success or not access:
            reason = text(body.get("error")) or f"HTTP {response.status_code}"
            raise AuthenticationError(f"token request failed: {reason}")
        expires = as_int(body.get("expires_in"))
        refresh = text(body.get("refresh_token"))
        return Token(
            access_token=SecretStr(access),
            # Servers that do not rotate refresh tokens omit it from the response.
            refresh_token=SecretStr(refresh) if refresh else (old.refresh_token if old else None),
            expires_at=time.time() + expires if expires is not None else None,
        )


class ClientCredentialsStrategy(OAuth2Strategy):
    """Client credentials grant."""

    _config: OAuth2ClientCredentials

    def grant(self) -> dict[str, str]:
        """Form fields."""
        return {"grant_type": "client_credentials"}


class PasswordStrategy(OAuth2Strategy):
    """Resource-owner password grant."""

    _config: OAuth2Password

    def grant(self) -> dict[str, str]:
        """Form fields."""
        return {
            "grant_type": "password",
            "username": self._config.username.get_secret_value(),
            "password": self._config.password.get_secret_value(),
        }


class AuthorizationCodeStrategy(OAuth2Strategy):
    """Authorization code grant, then refresh tokens."""

    _config: OAuth2AuthorizationCode

    def grant(self) -> dict[str, str]:
        """Form fields."""
        cfg = self._config
        if cfg.refresh_token is not None:
            return self._refresh_grant(cfg.refresh_token)
        if cfg.code is None or cfg.redirect_uri is None:
            raise AuthenticationError("no usable refresh_token, and no code with redirect_uri")
        fields = {
            "grant_type": "authorization_code",
            "code": cfg.code.get_secret_value(),
            "redirect_uri": cfg.redirect_uri,
        }
        if cfg.code_verifier is not None:
            fields["code_verifier"] = cfg.code_verifier.get_secret_value()
        return fields


class JwtAssertionStrategy(OAuth2Strategy):
    """Client credentials with a private-key JWT."""

    _config: JwtAssertionAuth

    def client_fields(self) -> tuple[dict[str, str], dict[str, str]]:
        """The assertion identifies the client, so nothing else is sent."""
        return {}, {}

    def grant(self) -> dict[str, str]:
        """Form fields with a fresh assertion."""
        try:
            import jwt
        except ImportError as exc:
            raise MissingDependencyError(
                "JWT authentication needs: pip install 'py-erp-bridge[jwt]'"
            ) from exc
        cfg = self._config
        now = int(time.time())
        claims: dict[str, object] = {
            "iss": cfg.client_id,
            "aud": cfg.token_url,
            "iat": now,
            "exp": now + cfg.lifetime,
            "jti": uuid.uuid4().hex,
        }
        if cfg.scopes:
            claims["scope"] = cfg.scopes
        assertion = jwt.encode(
            claims,
            cfg.private_key.get_secret_value(),
            algorithm=cfg.algorithm,
            headers={"kid": cfg.key_id},
        )
        return {
            "grant_type": "client_credentials",
            "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
            "client_assertion": assertion,
        }
