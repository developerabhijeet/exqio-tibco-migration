"""Tab 4, Design: generate the design record (job) and read it. G3 is decided at ``/gates``."""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from api.app.deps import RunnerDep, SessionDep, SettingsDep, UserDep
from api.app.models import Process
from api.app.schemas.job import JobCreated
from api.app.services import design as svc
from api.app.services import gates as gate_svc
from api.app.services.citations import cite_design, design_entities
from api.app.services.context import active_job

router = APIRouter(tags=["design"])


async def _cited(session, proc: Process, view: dict[str, Any]) -> dict[str, Any]:
    """``view`` plus ``narrative_cited`` and ``decisions[i].cited``. Their ``text`` is the MASKED
    narrative or ``"{choice}. {reason}"`` and the spans index into it (code points), so it can
    differ from ``narrative``/``choice``/``reason``: clients render ``cited.text`` when present."""
    entities = design_entities(proc, view, await gate_svc.load_rules(session, proc.id),
                               await gate_svc.load_findings(session, proc.id))
    return cite_design(view, entities)


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
                                 project_id=proc.project_id, process_id=process_id)
    return JobCreated(job_id=job_id)


@router.get("/processes/{process_id}/design")
async def get_design(process_id: str, session: SessionDep) -> dict[str, Any]:
    """The latest design record, with ``narrative_cited`` and ``decisions[i].cited`` (masked text,
    spans into it, refs to this process's activities, rules, findings, tables and queues); 404
    before the first design job."""
    proc = await session.get(Process, process_id)
    if proc is None:
        raise HTTPException(status_code=404, detail="process not found")
    view = await svc.design_view(session, proc)
    if view is None:
        raise HTTPException(status_code=404, detail="no design record yet")
    return await _cited(session, proc, view)


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
        view = await svc.answer_open_question(session, proc, index, body.decision, body.answer, user)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except IndexError as exc:
        raise HTTPException(status_code=404, detail="no open question at that index") from exc
    return await _cited(session, proc, view)
