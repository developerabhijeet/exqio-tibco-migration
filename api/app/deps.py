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


SessionDep = Annotated[AsyncSession, Depends(get_session)]
RunnerDep = Annotated[JobRunner, Depends(get_runner)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
UserDep = Annotated[str, Depends(require_user)]
