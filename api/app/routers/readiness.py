"""Readiness: expectations from the SME review with status computed from stored evidence."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from api.app.deps import SessionDep, SettingsDep
from api.app.models import Project
from api.app.services import readiness as svc

router = APIRouter(tags=["readiness"])


@router.get("/projects/{project_id}/readiness")
async def get_readiness(project_id: str, request: Request, session: SessionDep,
                        settings: SettingsDep) -> dict[str, Any]:
    if await session.get(Project, project_id) is None:
        raise HTTPException(status_code=404, detail="project not found")
    return await svc.readiness(session, settings, request.app.state.knowledge, project_id)
