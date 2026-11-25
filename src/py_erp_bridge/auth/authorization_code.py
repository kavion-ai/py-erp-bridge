"""Interactive part of the authorization code flow (with PKCE)."""

from __future__ import annotations

import base64
import hashlib
import secrets

import httpx
from pydantic import BaseModel, SecretStr


class AuthorizationRequest(BaseModel):
    """Redirect the user to ``url``; keep ``state`` and ``code_verifier`` in their session."""

    url: str
    state: str
    code_verifier: SecretStr


def build_authorization_request(
    authorize_url: str,
    client_id: str,
    redirect_uri: str,
    *,
    scope: str | None = None,
    extra_params: dict[str, str] | None = None,
) -> AuthorizationRequest:
    """Create an authorization URL. Verify ``state`` on the callback before using the code."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    state = secrets.token_urlsafe(32)
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": base64.urlsafe_b64encode(digest).rstrip(b"=").decode(),
        "code_challenge_method": "S256",
        **(extra_params or {}),
    }
    if scope:
        params["scope"] = scope
    url = httpx.URL(authorize_url).copy_merge_params(params)
    return AuthorizationRequest(url=str(url), state=state, code_verifier=SecretStr(verifier))
