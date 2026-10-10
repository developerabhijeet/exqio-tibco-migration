"""Tab 3: run analysis (job), the analysis view, rule and finding decisions, the audit trail."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.app.deps import RunnerDep, SessionDep, SettingsDep, UserDep
from api.app.models import AuditLog, Job, Process, Project
from api.app.schemas.analysis import AnalysisOut, AuditOut, FindingPatch, RulePatch
from api.app.schemas.job import JobCreated
from api.app.services import analysis as svc

router = APIRouter(tags=["analysis"])


async def _process(session: AsyncSession, process_id: str) -> Process:
    proc = await session.get(Process, process_id)
    if proc is None:
        raise HTTPException(status_code=404, detail="process not found")
    return proc


@router.post("/processes/{process_id}/analyse", status_code=202, response_model=JobCreated)
async def analyse(process_id: str, request: Request, session: SessionDep, runner: RunnerDep,
                  settings: SettingsDep) -> JobCreated:
    """Queue the analysis job; 409 while one for this process is queued or running."""
    proc = await _process(session, process_id)
    async with request.app.state.analyse_lock:
        active = await session.scalar(select(Job.id).where(
            Job.process_id == process_id, Job.kind == "analyse", Job.status.in_(("queued", "running")))
            .limit(1))
        if active is not None:
            raise HTTPException(status_code=409, detail=f"an analysis of this process is already in "
                                                        f"progress: {active}")
        job_id = await runner.submit(
            "analyse", {"process_id": process_id, "provider": settings.resolved_provider()},
            svc.analyse_job(process_id, settings, request.app.state.knowledge),
            project_id=proc.project_id, process_id=process_id)
    return JobCreated(job_id=job_id)


@router.get("/processes/{process_id}/analysis", response_model=AnalysisOut)
async def get_analysis(process_id: str, session: SessionDep) -> dict:
    return await svc.analysis_view(session, await _process(session, process_id))


@router.patch("/rules/{rule_id}", response_model=AnalysisOut)
async def patch_rule(rule_id: str, body: RulePatch, request: Request, session: SessionDep,
                     user: UserDep) -> dict:
    """``{status}`` approves, rejects or reopens; ``{text}`` edits (status becomes ``edited``,
    citations re-verified, spec hash recomputed, G1 to G6 reset). Returns the analysis view."""
    if body.text is None and body.status is None:
        raise HTTPException(status_code=422, detail="give status or text")
    try:
        proc = await svc.update_rule(session, request.app.state.knowledge, rule_id, body.status,
                                     body.text, user)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except svc.Conflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return await svc.analysis_view(session, proc)


@router.patch("/findings/{finding_id}", response_model=AnalysisOut)
async def patch_finding(finding_id: str, body: FindingPatch, request: Request, session: SessionDep,
                        user: UserDep) -> dict:
    """Decide a finding: keep behavior, fix, or ask the business, with a note."""
    try:
        proc = await svc.decide_finding(session, request.app.state.knowledge, finding_id, body.decision,
                                        body.note, user)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return await svc.analysis_view(session, proc)


@router.get("/processes/{process_id}/audit", response_model=list[AuditOut])
async def process_audit(process_id: str, session: SessionDep) -> list[AuditLog]:
    await _process(session, process_id)
    q = select(AuditLog).where(AuditLog.process_id == process_id).order_by(AuditLog.id)
    return list((await session.execute(q)).scalars())


@router.get("/projects/{project_id}/audit", response_model=list[AuditOut])
async def project_audit(project_id: str, session: SessionDep) -> list[AuditLog]:
    if await session.get(Project, project_id) is None:
        raise HTTPException(status_code=404, detail="project not found")
    q = select(AuditLog).where(AuditLog.project_id == project_id).order_by(AuditLog.id)
    return list((await session.execute(q)).scalars())
