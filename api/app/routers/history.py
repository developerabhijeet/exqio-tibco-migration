"""History and tokenomics: jobs, model calls and decisions of a project on one timeline."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from api.app.deps import SessionDep
from api.app.models import Project
from api.app.services import history as svc

router = APIRouter(tags=["history"])


@router.get("/history")
async def get_history(project_id: str, session: SessionDep) -> dict[str, Any]:
    if await session.get(Project, project_id) is None:
        raise HTTPException(status_code=404, detail="project not found")
    return await svc.history(session, project_id)
