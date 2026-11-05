"""Sign-in routes, mounted at ``/auth`` in ``AUTH_MODE=oidc`` only (``/api/auth/...`` in the
cluster). The API is the web's OIDC backend-for-frontend: the browser never holds a token.

* ``GET /auth/login?return=<path>``: a fresh ``state``, ``nonce`` and PKCE (S256) verifier go
  into the short-lived signed ``exiqo_login`` cookie, and the browser is sent to Entra.
* ``GET /auth/callback``: checks ``state`` against that cookie, exchanges the code (with the
  verifier, and the client secret when one is configured), validates the ID token (issuer,
  audience = client id, signature, expiry, ``nonce``), sets ``exiqo_session`` and returns to
  ``return``.
* ``POST /auth/logout``: clears the session cookie (Entra's own session is left alone).
* ``GET /auth/me``: the principal, or 401. ``X-User`` is ignored.

``return`` must be a path on this origin (``/`` followed by anything but ``/`` or ``\\``); any
other value becomes ``/``, so the sign-in cannot be used as an open redirect. In ``AUTH_MODE=dev``
none of this exists and ``/auth/me`` is 404, which is how the web tells the two modes apart.
"""
from __future__ import annotations

import hmac
import logging
import secrets
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse

from api.app.auth import session as cookies
from api.app.auth.jwt import TokenInvalid
from api.app.auth.oidc import Oidc, OidcError, pkce_pair
from api.app.auth.roles import Principal, principal_from_claims
from api.app.config import Settings
from api.app.deps import current_principal, unauthorized

log = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


def safe_return(target: str | None) -> str:
    """``target`` when it is a same-origin relative path, else ``/``."""
    if not target or not target.startswith("/") or target[1:2] in ("/", "\\"):
        return "/"
    if any(c in target for c in "\r\n\t") or "\\" in target:
        return "/"
    return target


def _redirect_uri(request: Request, settings: Settings) -> str:
    return settings.oidc_redirect_uri or str(request.url_for("auth_callback"))


@router.get("/login")
async def login(request: Request, return_to: Annotated[str | None, Query(alias="return")] = None):
    settings: Settings = request.app.state.settings
    oidc: Oidc = request.app.state.oidc
    verifier, challenge = pkce_pair()
    state, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    try:
        url = await oidc.authorize_url(state=state, nonce=nonce, challenge=challenge,
                                       redirect_uri=_redirect_uri(request, settings))
    except OidcError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    response = RedirectResponse(url, status_code=302)
    cookies.set_cookie(response, cookies.LOGIN_COOKIE, cookies.dump_login(
        {"state": state, "nonce": nonce, "verifier": verifier, "return": safe_return(return_to)},
        settings.session_secret), cookies.LOGIN_MAX_AGE)
    return response


@router.get("/callback", name="auth_callback")
async def callback(request: Request, code: str | None = None, state: str | None = None,
                   error: str | None = None, error_description: str | None = None):
    settings: Settings = request.app.state.settings
    oidc: Oidc = request.app.state.oidc
    if error:
        # Entra's own words (no secret in them): "AADSTS50105: user not assigned to a role", ...
        raise HTTPException(status_code=400, detail=f"sign-in failed: {error}: {error_description or ''}"
                            .strip().rstrip(":"))
    pending = cookies.load_login(request.cookies.get(cookies.LOGIN_COOKIE, ""),
                                 settings.session_secret)
    if pending is None or not code or not state:
        raise HTTPException(status_code=400, detail="sign-in expired or was not started here: try again")
    if not hmac.compare_digest(str(pending.get("state", "")), state):
        raise HTTPException(status_code=400, detail="sign-in state does not match: try again")
    try:
        tokens = await oidc.exchange(code=code, verifier=str(pending["verifier"]),
                                     redirect_uri=_redirect_uri(request, settings))
        claims = await oidc.validate(str(tokens.get("id_token", "")), settings.oidc_client_id)
    except (OidcError, TokenInvalid) as exc:
        log.warning("sign-in refused: %s", exc)
        raise HTTPException(status_code=400, detail=f"sign-in refused: {exc}") from exc
    if not hmac.compare_digest(str(claims.get("nonce", "")), str(pending.get("nonce", ""))):
        raise HTTPException(status_code=400, detail="sign-in refused: nonce does not match")
    principal = principal_from_claims(claims, settings.role_groups)
    log.info("signed in oid=%s roles=%s", principal.oid, ",".join(sorted(principal.roles)) or "-")
    response = RedirectResponse(safe_return(pending.get("return")), status_code=303)
    cookies.set_cookie(response, cookies.SESSION_COOKIE,
                       cookies.dump_session(principal, settings.session_secret), cookies.SESSION_MAX_AGE)
    cookies.clear_cookie(response, cookies.LOGIN_COOKIE)
    return response


@router.post("/logout", status_code=204)
async def logout() -> Response:
    response = Response(status_code=204)
    cookies.clear_cookie(response, cookies.SESSION_COOKIE)
    return response


@router.get("/me", response_model=Principal)
async def me(request: Request) -> Principal:
    """Who is signed in, roles included (an account with none still gets an answer, so the web can
    say so). 401 when nobody is."""
    principal = await current_principal(request)
    if principal is None:
        raise unauthorized()
    return principal
