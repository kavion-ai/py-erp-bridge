"""Authentication settings, discriminated by ``type``. Secrets are ``SecretStr``."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from py_erp_bridge.types import JsonObject


class _Auth(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class NoAuth(_Auth):
    """No HTTP authentication (TallyPrime, RFC-only connections)."""

    type: Literal["none"] = "none"


class BasicAuth(_Auth):
    """HTTP Basic."""

    type: Literal["basic"] = "basic"
    username: str
    password: SecretStr


class BearerAuth(_Auth):
    """Static bearer token or API key sent as a bearer token."""

    type: Literal["bearer"] = "bearer"
    token: SecretStr


class ApiKeyAuth(_Auth):
    """API key in a header or query parameter; ``prefix`` is prepended verbatim."""

    type: Literal["api_key"] = "api_key"
    key: SecretStr
    name: str = "Authorization"
    location: Literal["header", "query"] = "header"
    prefix: str = ""


class _OAuth2(_Auth):
    token_url: str
    client_id: str
    scope: str | None = None
    extra_params: dict[str, str] = Field(default_factory=dict)
    #: Refresh this many seconds before expiry.
    expiry_skew: float = Field(default=60, ge=0)

    @field_validator("token_url")
    @classmethod
    def _https_only(cls, value: str) -> str:
        if not value.startswith("https://"):
            raise ValueError("token_url must use https")
        return value


class OAuth2ClientCredentials(_OAuth2):
    """OAuth 2.0 client credentials."""

    type: Literal["oauth2_client_credentials"] = "oauth2_client_credentials"
    client_secret: SecretStr
    client_auth: Literal["body", "basic"] = "body"


class OAuth2Password(_OAuth2):
    """Resource-owner password grant. Used by Infor ION backend service accounts."""

    type: Literal["oauth2_password"] = "oauth2_password"
    client_secret: SecretStr
    username: SecretStr
    password: SecretStr
    client_auth: Literal["body", "basic"] = "body"


class OAuth2AuthorizationCode(_OAuth2):
    """Authorization code, or a refresh token obtained earlier."""

    type: Literal["oauth2_authorization_code"] = "oauth2_authorization_code"
    client_secret: SecretStr | None = None
    client_auth: Literal["body", "basic"] = "body"
    refresh_token: SecretStr | None = None
    code: SecretStr | None = None
    redirect_uri: str | None = None
    code_verifier: SecretStr | None = None


class JwtAssertionAuth(_OAuth2):
    """Client credentials authenticated by a signed JWT (RFC 7523), e.g. NetSuite M2M."""

    type: Literal["oauth2_jwt_assertion"] = "oauth2_jwt_assertion"
    private_key: SecretStr
    key_id: str
    algorithm: Literal["PS256", "PS384", "PS512", "ES256", "ES384", "ES512", "RS256"] = "PS256"
    scopes: list[str] = Field(default_factory=list)
    lifetime: int = Field(default=1800, gt=0, lt=3600)


class OAuth1Auth(_Auth):
    """OAuth 1.0a HMAC-SHA256 (NetSuite token-based authentication)."""

    type: Literal["oauth1"] = "oauth1"
    realm: str
    consumer_key: str
    consumer_secret: SecretStr
    token_id: str
    token_secret: SecretStr


class CustomAuth(_Auth):
    """Authentication implemented by a plugin registered with ``register_auth``."""

    type: Literal["custom"] = "custom"
    plugin: str
    options: JsonObject = Field(default_factory=dict, repr=False)


AuthConfig = Annotated[
    NoAuth
    | BasicAuth
    | BearerAuth
    | ApiKeyAuth
    | OAuth2ClientCredentials
    | OAuth2Password
    | OAuth2AuthorizationCode
    | JwtAssertionAuth
    | OAuth1Auth
    | CustomAuth,
    Field(discriminator="type"),
]
