"""Entra ID token validation: issuer, audience, RS256 signature against the tenant's JWKS, and
``exp``/``nbf`` with 60 s of clock leeway.

The JWKS is fetched once and cached. A token signed with a ``kid`` the cache does not hold makes
it fetch again, once (Entra rotates its signing keys without notice); a further unknown ``kid``
within ``REFETCH_COOLDOWN_S`` is refused without a fetch, so a stream of forged tokens cannot turn
into a stream of requests to Entra.

The issuer is the v2.0 one, so an app registration whose bearer tokens are meant for this API
must issue v2.0 access tokens (``"accessTokenAcceptedVersion": 2`` in its manifest); v1.0 tokens
(issuer ``https://sts.windows.net/<tenant>/``) are refused.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

import jwt
from jwt import PyJWK

LEEWAY_S = 60
REFETCH_COOLDOWN_S = 60.0


class TokenInvalid(Exception):
    """The token is not acceptable; the message says why (never the token itself)."""


def issuer_of(tenant_id: str) -> str:
    return f"https://login.microsoftonline.com/{tenant_id}/v2.0"


class JwksCache:
    """The tenant's signing keys by ``kid``; ``fetch`` returns the JWKS document."""

    def __init__(self, fetch: Callable[[], Awaitable[dict[str, Any]]]) -> None:
        self._fetch = fetch
        self._keys: dict[str, PyJWK] | None = None
        self._refetched_at = float("-inf")
        self._lock = asyncio.Lock()

    async def _load(self) -> None:
        doc = await self._fetch()
        keys: dict[str, PyJWK] = {}
        for jwk in doc.get("keys", []):
            if jwk.get("kty") != "RSA" or not jwk.get("kid"):
                continue
            try:
                keys[jwk["kid"]] = PyJWK(jwk, algorithm="RS256")
            except jwt.PyJWTError:
                continue  # a key we cannot use; the others still count
        self._keys = keys

    async def key(self, kid: str) -> PyJWK:
        async with self._lock:
            if self._keys is None:
                await self._load()
            if kid not in self._keys and time.monotonic() - self._refetched_at >= REFETCH_COOLDOWN_S:
                self._refetched_at = time.monotonic()
                await self._load()
            found = self._keys.get(kid)
        if found is None:
            raise TokenInvalid(f"unknown signing key {kid!r}")
        return found


async def validate_jwt(token: str, *, tenant_id: str, audience: str | Sequence[str],
                       jwks: JwksCache) -> dict[str, Any]:
    """The token's claims, or ``TokenInvalid`` naming the failed check."""
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise TokenInvalid(f"malformed token: {exc}") from exc
    if header.get("alg") != "RS256":
        raise TokenInvalid(f"signature algorithm {header.get('alg')!r} is not RS256")
    key = await jwks.key(str(header.get("kid") or ""))
    try:
        return jwt.decode(token, key=key.key, algorithms=["RS256"],
                          audience=[audience] if isinstance(audience, str) else list(audience),
                          issuer=issuer_of(tenant_id), leeway=LEEWAY_S,
                          options={"require": ["exp", "iss", "aud"]})
    except jwt.ExpiredSignatureError as exc:
        raise TokenInvalid("token expired") from exc
    except jwt.ImmatureSignatureError as exc:
        raise TokenInvalid("token not yet valid (nbf)") from exc
    except jwt.InvalidIssuerError as exc:
        raise TokenInvalid("wrong issuer") from exc
    except jwt.InvalidAudienceError as exc:
        raise TokenInvalid("wrong audience") from exc
    except jwt.InvalidSignatureError as exc:
        raise TokenInvalid("bad signature") from exc
    except jwt.MissingRequiredClaimError as exc:
        raise TokenInvalid(f"missing claim: {exc.claim}") from exc
    except jwt.PyJWTError as exc:
        raise TokenInvalid(f"invalid token: {type(exc).__name__}") from exc
