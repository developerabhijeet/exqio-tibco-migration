"""Tab 4, Design: generate the design record (job) and read it. G3 is decided at ``/gates``."""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from api.app.deps import RunnerDep, SessionDep, SettingsDep, UserDep
from api.app.models import Process
from api.app.schemas.job import JobCreated
from api.app.services import design as svc
from api.app.services.context import active_job

router = APIRouter(tags=["design"])


@router.post("/processes/{process_id}/design", status_code=202, response_model=JobCreated)
async def generate_design(process_id: str, request: Request, session: SessionDep, runner: RunnerDep,
                          settings: SettingsDep) -> JobCreated:
    """Queue the design job (skeleton by code, narrative by the designer); 409 while one runs or
    before the process has been analyzed."""
    proc = await session.get(Process, process_id)
    if proc is None:
        raise HTTPException(status_code=404, detail="process not found")
    if not proc.spec_hash:
        raise HTTPException(status_code=409, detail="the process has no spec hash yet: run the analysis first")
    running = await active_job(session, "design", process_id=process_id)
    if running is not None:
        raise HTTPException(status_code=409, detail=f"a design job for this process is in progress: {running}")
    job_id = await runner.submit("design", {"process_id": process_id, "provider": settings.resolved_provider()},
                                 svc.design_job(process_id, settings, request.app.state.knowledge),
                                 project_id=proc.project_id, process_id=process_id)
    return JobCreated(job_id=job_id)


@router.get("/processes/{process_id}/design")
async def get_design(process_id: str, session: SessionDep) -> dict[str, Any]:
    """The latest design record; 404 before the first design job."""
    proc = await session.get(Process, process_id)
    if proc is None:
        raise HTTPException(status_code=404, detail="process not found")
    view = await svc.design_view(session, proc)
    if view is None:
        raise HTTPException(status_code=404, detail="no design record yet")
    return view


class QuestionAnswer(BaseModel):
    """``answered`` records what the architect decided; ``rejected`` records that the question does
    not apply. ``null`` reopens it."""

    decision: Literal["answered", "rejected"] | None
    answer: str | None = None


@router.patch("/processes/{process_id}/design/questions/{index}")
async def answer_question(process_id: str, index: int, body: QuestionAnswer, session: SessionDep,
                          user: UserDep) -> dict[str, Any]:
    """Answer or reject one open question. The design hash does not move, so an approved G3 stays
    approved: the answer is a note on the design, not a change to it."""
    proc = await session.get(Process, process_id)
    if proc is None:
        raise HTTPException(status_code=404, detail="process not found")
    if body.decision == "answered" and not (body.answer or "").strip():
        raise HTTPException(status_code=422, detail="an answered question needs an answer")
    try:
        return await svc.answer_open_question(session, proc, index, body.decision, body.answer, user)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except IndexError as exc:
        raise HTTPException(status_code=404, detail="no open question at that index") from exc
