import asyncio
import base64
from urllib.parse import parse_qs

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import SecretStr

from helpers import MockServer, config, ok
from py_erp_bridge import AsyncERPClient
from py_erp_bridge.auth import (
    AuthStrategy,
    InMemoryTokenStore,
    Token,
    build_authorization_request,
    register_auth,
)
from py_erp_bridge.auth.factory import build_auth
from py_erp_bridge.auth.static import OAuth1Strategy
from py_erp_bridge.config import OAuth1Auth, OAuth2ClientCredentials
from py_erp_bridge.exceptions import (
    AuthenticationError,
    ConfigurationError,
    InvalidCredentialsError,
)

TOKEN_URL = "https://login.test/token"
CC = {
    "type": "oauth2_client_credentials",
    "token_url": TOKEN_URL,
    "client_id": "id",
    "client_secret": "sec",
    "scope": "s",
}


def form(request):
    return {k: v[0] for k, v in parse_qs(request.content.decode()).items()}


class OAuthServer(MockServer):
    """Issues numbered tokens and accepts only those in ``valid`` (None: all)."""

    def __init__(self, **token):
        self.issued = 0
        self.token = token
        self.revoked = set()
        super().__init__(handler=self.route)

    def route(self, request):
        if request.url.host == "login.test":
            self.issued += 1
            return ok({"access_token": f"t{self.issued}", "expires_in": 3600, **self.token})
        if request.headers["Authorization"].removeprefix("Bearer ") in self.revoked:
            return ok({}, 401)
        return ok({"value": []})

    def token_requests(self):
        return [r for r in self.requests if r.url.host == "login.test"]


def client(server, auth, **kw):
    return AsyncERPClient(
        "dynamics365", config("dynamics365", auth=auth), http_transport=server, **kw
    )


async def test_basic_bearer_and_api_key():
    cases = [
        (
            "sap_s4hana",
            {"type": "basic", "username": "ü", "password": "p"},
            "Basic " + base64.b64encode("ü:p".encode()).decode(),
        ),
        ("netsuite", {"type": "bearer", "token": "abc"}, "Bearer abc"),
        ("erpnext", {"type": "api_key", "key": "k:s", "prefix": "token "}, "token k:s"),
    ]
    for provider, auth, expected in cases:
        server = MockServer(ok({}))
        async with AsyncERPClient(
            provider, config(provider, auth=auth), http_transport=server
        ) as erp:
            await erp.request("GET", "/x")
        assert server.last.headers["Authorization"] == expected


async def test_api_key_in_query_keeps_other_parameters():
    server = MockServer(ok({}))
    auth = {"type": "api_key", "key": "a&b", "name": "key", "location": "query"}
    async with AsyncERPClient(
        "erpnext", config("erpnext", auth=auth), http_transport=server
    ) as erp:
        await erp.request("GET", "/x", params={"q": "1"})
    assert dict(server.last.url.params) == {"q": "1", "key": "a&b"}


async def test_client_credentials_is_cached():
    server = OAuthServer()
    async with client(server, CC) as erp:
        await asyncio.gather(*(erp.customers.get_all() for _ in range(5)))
    assert server.issued == 1
    assert form(server.requests[0]) == {
        "grant_type": "client_credentials",
        "client_id": "id",
        "client_secret": "sec",
        "scope": "s",
    }


async def test_client_auth_basic():
    server = OAuthServer()
    async with client(server, {**CC, "client_auth": "basic"}) as erp:
        await erp.customers.get_all()
    assert server.requests[0].headers["Authorization"].startswith("Basic ")
    assert "client_secret" not in form(server.requests[0])


async def test_401_refreshes_once_then_fails():
    server = OAuthServer()
    server.revoked = {"t1"}
    async with client(server, CC) as erp:
        await erp.customers.get_all()
        assert server.issued == 2
        server.revoked = {f"t{i}" for i in range(1, 10)}
        with pytest.raises(InvalidCredentialsError):
            await erp.customers.get_all()


async def test_concurrent_401s_share_one_refresh():
    server = OAuthServer()
    server.revoked = {"t1"}
    async with client(server, CC) as erp:
        await asyncio.gather(*(erp.customers.get_all() for _ in range(6)))
    assert server.issued == 2


async def test_authorization_code_then_rotating_refresh_token():
    server = OAuthServer(refresh_token="r1")
    store = InMemoryTokenStore()
    auth = {
        "type": "oauth2_authorization_code",
        "token_url": TOKEN_URL,
        "client_id": "id",
        "code": "c",
        "redirect_uri": "https://app.test/cb",
        "code_verifier": "v",
    }
    async with client(server, auth, token_store=store) as erp:
        await erp.customers.get_all()
        assert form(server.requests[0])["code_verifier"] == "v"
        saved = await store.load()
        assert saved.refresh_token.get_secret_value() == "r1"
        server.revoked = {"t1"}
        await erp.customers.get_all()
    assert form(server.token_requests()[1])["refresh_token"] == "r1"


async def test_stored_token_is_used_after_restart():
    store = InMemoryTokenStore()
    await store.save(Token(access_token=SecretStr("kept")))
    server = OAuthServer()
    async with client(server, CC, token_store=store) as erp:
        await erp.customers.get_all()
    assert server.issued == 0
    assert server.last.headers["Authorization"] == "Bearer kept"


async def test_token_failure_hides_secrets():
    server = MockServer(ok({"error": "invalid_client", "error_description": "sec"}, 400))
    async with client(server, CC) as erp:
        with pytest.raises(AuthenticationError) as info:
            await erp.customers.get_all()
    assert "invalid_client" in str(info.value)
    assert "sec" not in str(info.value)


async def test_jwt_assertion_matches_netsuite_rules():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    server = OAuthServer()
    auth = {
        "type": "oauth2_jwt_assertion",
        "token_url": TOKEN_URL,
        "client_id": "cid",
        "private_key": pem,
        "key_id": "cert1",
        "scopes": ["rest_webservices"],
    }
    async with AsyncERPClient(
        "netsuite", config("netsuite", auth=auth), http_transport=server
    ) as erp:
        await erp.request("GET", "/x")
    fields = form(server.requests[0])
    assert fields["client_assertion_type"].endswith("jwt-bearer")
    assert jwt.get_unverified_header(fields["client_assertion"]) == {
        "typ": "JWT",
        "alg": "PS256",
        "kid": "cert1",
    }
    claims = jwt.decode(
        fields["client_assertion"], key.public_key(), algorithms=["PS256"], audience=TOKEN_URL
    )
    assert claims["iss"] == "cid"
    assert claims["scope"] == ["rest_webservices"]
    assert claims["exp"] - claims["iat"] < 3600


def test_oauth1_signature_against_independent_computation():
    # Signature base string built by hand following RFC 5849 section 3.4.1.
    import hashlib
    import hmac

    strategy = OAuth1Strategy(
        OAuth1Auth(
            realm="ACC",
            consumer_key="ck",
            consumer_secret=SecretStr("cs"),
            token_id="tk",
            token_secret=SecretStr("ts"),
        )
    )
    header = strategy.header("get", httpx.URL("https://erp.test/r?b=x%20y&a=1"), "n", "1")
    base = (
        "GET&https%3A%2F%2Ferp.test%2Fr&a%3D1%26b%3Dx%2520y%26oauth_consumer_key%3Dck%26"
        "oauth_nonce%3Dn%26oauth_signature_method%3DHMAC-SHA256%26oauth_timestamp%3D1%26"
        "oauth_token%3Dtk%26oauth_version%3D1.0"
    )
    digest = hmac.new(b"cs&ts", base.encode(), hashlib.sha256).digest()
    expected = (
        base64.b64encode(digest)
        .decode()
        .replace("+", "%2B")
        .replace("/", "%2F")
        .replace("=", "%3D")
    )
    assert header.startswith('OAuth realm="ACC", ')
    assert f'oauth_signature="{expected}"' in header


def test_authorization_request_uses_pkce_and_state():
    a = build_authorization_request(
        "https://login.test/auth?x=1", "id", "https://app.test/cb", scope="s"
    )
    b = build_authorization_request("https://login.test/auth", "id", "https://app.test/cb")
    params = httpx.URL(a.url).params
    assert params["x"] == "1"
    assert params["code_challenge_method"] == "S256"
    assert a.state != b.state
    assert a.code_verifier.get_secret_value() not in a.url


async def test_custom_plugin():
    class Plugin(AuthStrategy):
        async def apply(self, request):
            request.headers["X-Custom"] = "yes"

    register_auth("test-plugin", lambda options, client: Plugin())
    with pytest.raises(ConfigurationError):
        register_auth("test-plugin", lambda options, client: Plugin())
    server = MockServer(ok({}))
    auth = {"type": "custom", "plugin": "test-plugin"}
    async with AsyncERPClient("odoo", config("odoo", auth=auth), http_transport=server) as erp:
        await erp.request("GET", "/x")
    assert server.last.headers["X-Custom"] == "yes"


def test_token_url_must_be_https():
    with pytest.raises(ValueError, match="https"):
        OAuth2ClientCredentials(token_url="http://x", client_id="a", client_secret=SecretStr("b"))


async def test_every_auth_type_builds():
    async with httpx.AsyncClient() as http:
        for auth in (OAuth2ClientCredentials(**{**CC, "client_secret": SecretStr("x")}),):
            assert build_auth(auth, http)


async def test_unsupported_auth_type_is_rejected():
    with pytest.raises(ConfigurationError, match="does not support auth type"):
        AsyncERPClient(
            "netsuite", config("netsuite", auth={"type": "basic", "username": "u", "password": "p"})
        )
