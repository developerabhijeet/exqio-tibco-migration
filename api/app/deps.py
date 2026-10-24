"""FastAPI dependencies: database session, job runner, settings, and the user principal."""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from api.app.auth.jwt import TokenInvalid
from api.app.auth.oidc import Oidc
from api.app.auth.roles import Principal, dev_principal, principal_from_claims
from api.app.auth.session import SESSION_COOKIE, load_session
from api.app.config import Settings
from api.app.jobs.runner import JobRunner


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.sessionmaker() as session:
        yield session


def get_runner(request: Request) -> JobRunner:
    return request.app.state.runner


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


UNAUTHENTICATED = "sign in required"
NO_ROLE = "your account holds no ExiQO Migrate role: ask for one of the configured groups"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def unauthorized(detail: str = UNAUTHENTICATED) -> HTTPException:
    return HTTPException(status_code=401, detail=detail, headers={"WWW-Authenticate": "Bearer"})


def check_origin(request: Request) -> None:
    """CSRF guard for cookie-authenticated writes, on top of ``SameSite=Lax``: an ``Origin`` that
    is neither this host nor a configured CORS origin is refused. A request without ``Origin``
    (curl, a same-origin navigation) passes; browsers send it on every cross-origin write."""
    origin = request.headers.get("origin")
    if request.method in SAFE_METHODS or origin is None:
        return
    settings: Settings = request.app.state.settings
    # Host, not scheme: behind the TLS-terminating ingress this process may see http.
    if origin in settings.cors_origin_list or urlsplit(origin).netloc == request.headers.get("host"):
        return
    raise HTTPException(status_code=403, detail=f"cross-origin request from {origin} refused")


async def current_principal(request: Request, *, bearer: bool = True) -> Principal | None:
    """``AUTH_MODE=oidc``: the principal of the session cookie, else (when ``bearer``) of a valid
    ``Authorization: Bearer`` Entra token, else ``None``. ``X-User`` is never read here."""
    cached = getattr(request.state, "principal", None)
    if cached is not None:
        return cached
    settings: Settings = request.app.state.settings
    principal = None
    cookie = request.cookies.get(SESSION_COOKIE)
    if cookie:
        principal = load_session(cookie, settings.session_secret)
        if principal is not None:
            check_origin(request)
    if principal is None and bearer:
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() == "bearer" and token.strip():
            oidc: Oidc = request.app.state.oidc
            try:
                claims = await oidc.validate(token.strip(), oidc.audiences)
            except TokenInvalid as exc:
                raise unauthorized(f"invalid bearer token: {exc}") from exc
            principal = principal_from_claims(claims, settings.role_groups)
    if principal is not None:
        request.state.principal = principal
    return principal


def _with_role(principal: Principal | None) -> Principal:
    if principal is None:
        raise unauthorized()
    if not principal.roles:
        raise HTTPException(status_code=403, detail=NO_ROLE)
    return principal


async def require_signed_in(request: Request) -> None:
    """Router guard: in ``AUTH_MODE=oidc`` every call needs a signed-in user holding a role. In
    ``dev`` it lets everything through, as before (routes that record an actor use
    :func:`require_user`)."""
    if request.app.state.settings.auth_mode == "oidc":
        _with_role(await current_principal(request))


async def require_user(request: Request, x_user: Annotated[str | None, Header()] = None,
                       x_agent: Annotated[str | None, Header()] = None) -> Principal:
    """The human principal behind an action. Agents never act (spec 3.3), in either mode.
    ``dev``: the persona named by ``X-User`` (401 without it). ``oidc``: the session cookie or
    a bearer token; ``X-User`` is ignored."""
    if x_agent is not None:
        raise HTTPException(status_code=403, detail="agents cannot perform this action")
    if request.app.state.settings.auth_mode == "oidc":
        return _with_role(await current_principal(request))
    user = (x_user or "").strip()
    if not user:
        raise HTTPException(status_code=401, detail="X-User header required")
    return dev_principal(user)


async def require_actor(principal: Annotated[Principal, Depends(require_user)]) -> str:
    """The name gate rows and audit lines record: the persona id in dev, the UPN in oidc."""
    return principal.actor


async def require_admin(principal: Annotated[Principal, Depends(require_user)]) -> Principal:
    if "admin" not in principal.roles:
        raise HTTPException(status_code=403, detail="this needs the admin role")
    return principal


async def require_browser_user(request: Request, x_user: Annotated[str | None, Header()] = None,
                               x_agent: Annotated[str | None, Header()] = None) -> str:
    """The principal behind a call the browser makes without the web app's fetch helper: the
    Swagger UI frame of the generated service, a new tab on it. Such a navigation carries no
    custom header, so ``AUTH_MODE=dev`` accepts the call as ``anonymous`` (``X-User`` is an
    unverified claim there anyway) and ``oidc`` reads the session cookie only: the
    ``Authorization`` header on these calls is the generated service's own token, not ours."""
    if x_agent is not None:
        raise HTTPException(status_code=403, detail="agents cannot perform this action")
    settings: Settings = request.app.state.settings
    if settings.auth_mode == "dev":
        return (x_user or "").strip() or "anonymous"
    return _with_role(await current_principal(request, bearer=False)).actor


SessionDep = Annotated[AsyncSession, Depends(get_session)]
RunnerDep = Annotated[JobRunner, Depends(get_runner)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
UserDep = Annotated[str, Depends(require_actor)]
PrincipalDep = Annotated[Principal, Depends(require_user)]
AdminDep = Annotated[Principal, Depends(require_admin)]
BrowserUserDep = Annotated[str, Depends(require_browser_user)]
