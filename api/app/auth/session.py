"""The two signed cookies of the sign-in: ``exiqo_login`` (state, nonce and PKCE verifier between
``/auth/login`` and ``/auth/callback``, 10 minutes) and ``exiqo_session`` (the ``Principal``, 8 h).

Both are signed, not encrypted, with ``SESSION_SECRET`` (itsdangerous, a salt per cookie): the
session holds no token, only who the user is and their roles, so readability is harmless and a
changed byte invalidates it. Both are ``HttpOnly; Secure; SameSite=Lax; Path=/``. Lax lets the
cookie ride the top-level redirect back from Entra and the same-origin calls of the web, and keeps
it off cross-site sub-requests; :func:`api.app.deps.check_origin` covers the rest.
"""
from __future__ import annotations

import time
from typing import Any

from fastapi import Response
from itsdangerous import BadSignature, URLSafeTimedSerializer

from api.app.auth.roles import Principal

SESSION_COOKIE = "exiqo_session"
LOGIN_COOKIE = "exiqo_login"
SESSION_MAX_AGE = 8 * 3600
LOGIN_MAX_AGE = 600
_SESSION_SALT = "exiqo-session"
_LOGIN_SALT = "exiqo-login"


def _serializer(secret: str, salt: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(secret, salt=salt)


def dump_session(principal: Principal, secret: str) -> str:
    return _serializer(secret, _SESSION_SALT).dumps(
        {"p": principal.model_dump(mode="json"), "exp": int(time.time()) + SESSION_MAX_AGE})


def load_session(value: str, secret: str) -> Principal | None:
    """The principal of a valid, unexpired session cookie; ``None`` otherwise."""
    try:
        data = _serializer(secret, _SESSION_SALT).loads(value, max_age=SESSION_MAX_AGE)
        if int(data["exp"]) < time.time():
            return None
        return Principal.model_validate(data["p"])
    except (BadSignature, KeyError, TypeError, ValueError):
        return None


def dump_login(data: dict[str, str], secret: str) -> str:
    return _serializer(secret, _LOGIN_SALT).dumps(data)


def load_login(value: str, secret: str) -> dict[str, Any] | None:
    try:
        data = _serializer(secret, _LOGIN_SALT).loads(value, max_age=LOGIN_MAX_AGE)
    except BadSignature:
        return None
    return data if isinstance(data, dict) else None


def set_cookie(response: Response, name: str, value: str, max_age: int) -> None:
    response.set_cookie(name, value, max_age=max_age, path="/", secure=True, httponly=True,
                        samesite="lax")


def clear_cookie(response: Response, name: str) -> None:
    response.delete_cookie(name, path="/", secure=True, httponly=True, samesite="lax")
