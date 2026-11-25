# Authentication

| `type` | Meaning |
|---|---|
| `none` | No HTTP authentication |
| `basic` | `username`, `password` |
| `bearer` | Static `token` |
| `api_key` | `key` in a header or query parameter, with optional `prefix` |
| `oauth2_client_credentials` | Client id and secret |
| `oauth2_password` | Resource owner password (Infor service accounts) |
| `oauth2_authorization_code` | Code, then refresh tokens |
| `oauth2_jwt_assertion` | Client authenticated by a signed JWT (NetSuite) |
| `oauth1` | OAuth 1.0a HMAC-SHA256 (NetSuite TBA) |
| `custom` | A registered plugin |

A provider accepts only the types it supports: see [capabilities](capabilities.md) and the
[provider pages](providers.md).

Tokens are cached, renewed before they expire, and renewed once after a 401. Concurrent requests
share one renewal.

## Authorization code

```python
from py_erp_bridge.auth import build_authorization_request

request = build_authorization_request(
    "https://login.example.com/authorize", "client-id", "https://app.example.com/callback"
)
# 1. Store request.state and request.code_verifier in the user's session; redirect to request.url.
# 2. On the callback compare state, then use the code:
auth = {
    "type": "oauth2_authorization_code",
    "token_url": "https://login.example.com/token",
    "client_id": "client-id",
    "client_secret": "...",
    "code": "code-from-callback",
    "redirect_uri": "https://app.example.com/callback",
    "code_verifier": request.code_verifier,
}
```

Refresh tokens often rotate. Persist them with a `token_store` (`load` and `save` coroutines), or a
restart loses access:

```python
erp = ERPClient("sage_intacct", config, token_store=DatabaseTokenStore(tenant="acme"))
```

## Plugins

```python
from py_erp_bridge.auth import AuthStrategy, register_auth


class VaultAuth(AuthStrategy):
    def __init__(self, path):
        self.path = path

    async def apply(self, request):
        request.headers["Authorization"] = await read_from_vault(self.path)


register_auth("vault", lambda options, client: VaultAuth(options["path"]))
auth = {"type": "custom", "plugin": "vault", "options": {"path": "erp/token"}}
```
