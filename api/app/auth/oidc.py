"""The tenant's OpenID Connect endpoints and the authorization-code exchange.

Endpoints come from the tenant's v2.0 discovery document, fetched once per process and cached;
the JWKS from its ``jwks_uri`` (:class:`~api.app.auth.jwt.JwksCache`). Only
``login.microsoftonline.com`` is contacted, which is the Entra entry in the egress allowlist.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import secrets
from typing import Any
from urllib.parse import urlencode

import httpx

from api.app.auth.jwt import JwksCache, TokenInvalid, validate_jwt
from api.app.config import Settings

DISCOVERY_URL = "https://login.microsoftonline.com/{tenant}/v2.0/.well-known/openid-configuration"
SCOPES = "openid profile email"
TIMEOUT = httpx.Timeout(10.0, connect=5.0)


class OidcError(Exception):
    """Entra could not be reached or answered something unusable; the message is safe to show."""


def pkce_pair() -> tuple[str, str]:
    """``(verifier, S256 challenge)`` per RFC 7636."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


class Oidc:
    def __init__(self, settings: Settings, http: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self.http = http or httpx.AsyncClient(timeout=TIMEOUT)
        self._metadata: dict[str, Any] | None = None
        self._lock = asyncio.Lock()
        self.jwks = JwksCache(self._fetch_jwks)

    @property
    def audiences(self) -> list[str]:
        """Bearer tokens may be issued for the app's client id or its ``api://`` App ID URI."""
        cid = self.settings.oidc_client_id
        return [cid, f"api://{cid}"]

    async def _get_json(self, url: str) -> dict[str, Any]:
        try:
            r = await self.http.get(url)
            r.raise_for_status()
            return r.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise OidcError(f"Entra ID did not answer usably: {type(exc).__name__}") from exc

    async def metadata(self) -> dict[str, Any]:
        async with self._lock:
            if self._metadata is None:
                self._metadata = await self._get_json(
                    DISCOVERY_URL.format(tenant=self.settings.oidc_tenant_id))
            return self._metadata

    async def _fetch_jwks(self) -> dict[str, Any]:
        return await self._get_json((await self.metadata())["jwks_uri"])

    async def validate(self, token: str, audience: str | list[str]) -> dict[str, Any]:
        try:
            return await validate_jwt(token, tenant_id=self.settings.oidc_tenant_id,
                                      audience=audience, jwks=self.jwks)
        except OidcError as exc:  # the JWKS could not be read: the token cannot be trusted
            raise TokenInvalid(str(exc)) from exc

    async def authorize_url(self, *, state: str, nonce: str, challenge: str, redirect_uri: str) -> str:
        query = urlencode({
            "client_id": self.settings.oidc_client_id, "response_type": "code",
            "redirect_uri": redirect_uri, "response_mode": "query", "scope": SCOPES,
            "state": state, "nonce": nonce, "code_challenge": challenge,
            "code_challenge_method": "S256",
        })
        return f"{(await self.metadata())['authorization_endpoint']}?{query}"

    async def exchange(self, *, code: str, verifier: str, redirect_uri: str) -> dict[str, Any]:
        """The token response for an authorization code (``OidcError`` when Entra refuses it)."""
        form = {"grant_type": "authorization_code", "client_id": self.settings.oidc_client_id,
                "code": code, "redirect_uri": redirect_uri, "code_verifier": verifier,
                "scope": SCOPES}
        if self.settings.oidc_client_secret:
            form["client_secret"] = self.settings.oidc_client_secret
        try:
            r = await self.http.post((await self.metadata())["token_endpoint"], data=form)
        except httpx.HTTPError as exc:
            raise OidcError(f"Entra ID token endpoint did not answer: {type(exc).__name__}") from exc
        if r.status_code != 200:
            try:
                error = r.json().get("error", "")
            except ValueError:
                error = ""
            raise OidcError(f"Entra ID refused the code ({r.status_code} {error})".strip())
        return r.json()

    async def aclose(self) -> None:
        await self.http.aclose()
