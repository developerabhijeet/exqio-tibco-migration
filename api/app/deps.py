"""FastAPI dependencies: database session, job runner, settings, and the user principal."""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from api.app.config import Settings
from api.app.jobs.runner import JobRunner


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.sessionmaker() as session:
        yield session


def get_runner(request: Request) -> JobRunner:
    return request.app.state.runner


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def require_user(x_user: Annotated[str | None, Header()] = None,
                 x_agent: Annotated[str | None, Header()] = None) -> str:
    """The human principal behind a gate decision. Agents never act on gates (spec 3.3)."""
    if x_agent is not None:
        raise HTTPException(status_code=403, detail="agents cannot perform this action")
    user = (x_user or "").strip()
    if not user:
        raise HTTPException(status_code=401, detail="X-User header required")
    return user


def require_browser_user(request: Request, x_user: Annotated[str | None, Header()] = None,
                         x_agent: Annotated[str | None, Header()] = None) -> str:
    """The principal behind a call the browser makes without the web app's fetch helper: the
    Swagger UI frame of the generated service, a new tab on it. Such a navigation carries no
    ``X-User``, which in ``AUTH_MODE=dev`` is an unverified claim anyway, so dev mode accepts the
    call as ``anonymous``; ``oidc`` requires a signed-in user like :func:`require_user`."""
    settings: Settings = request.app.state.settings
    if settings.auth_mode == "dev" and x_agent is None:
        return (x_user or "").strip() or "anonymous"
    return require_user(x_user, x_agent)


SessionDep = Annotated[AsyncSession, Depends(get_session)]
RunnerDep = Annotated[JobRunner, Depends(get_runner)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
UserDep = Annotated[str, Depends(require_user)]
BrowserUserDep = Annotated[str, Depends(require_browser_user)]
