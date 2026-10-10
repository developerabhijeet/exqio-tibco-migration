"""Tab 2: domains (list, rename, merge, AI naming job) and the estate graph."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from api.app.deps import RunnerDep, SessionDep, SettingsDep, UserDep
from api.app.models import Domain, Project
from api.app.schemas.analysis import DomainOut, DomainRename, GraphOut
from api.app.schemas.job import JobCreated
from api.app.services import domains as svc

router = APIRouter(tags=["domains"])


async def _project(session: AsyncSession, project_id: str) -> Project:
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    return project


async def _domain(session: AsyncSession, domain_id: str) -> Domain:
    domain = await session.get(Domain, domain_id)
    if domain is None or domain.merged_into:
        raise HTTPException(status_code=404, detail="domain not found")
    return domain


@router.get("/projects/{project_id}/domains", response_model=list[DomainOut])
async def list_domains(project_id: str, session: SessionDep) -> list[dict]:
    await _project(session, project_id)
    return await svc.list_domains(session, project_id)


@router.post("/projects/{project_id}/domains/name", status_code=202, response_model=JobCreated)
async def name_domains(project_id: str, session: SessionDep, runner: RunnerDep,
                       settings: SettingsDep) -> JobCreated:
    """Queue the ``discover`` job: the model proposes a business name per domain. A name the
    SME set is kept; ``suggested_name`` always takes the model's proposal."""
    await _project(session, project_id)
    job_id = await runner.submit("discover", {"project_id": project_id},
                                 svc.naming_job(project_id, settings), project_id=project_id)
    return JobCreated(job_id=job_id)


@router.patch("/domains/{domain_id}", response_model=DomainOut)
async def rename_domain(domain_id: str, body: DomainRename, request: Request, session: SessionDep,
                        user: UserDep) -> dict:
    domain = await _domain(session, domain_id)
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="name must not be blank")
    domain = await svc.rename(session, request.app.state.knowledge, domain, name, user)
    return await svc.get_domain_out(session, domain)


@router.post("/domains/{domain_id}/merge/{target_id}", response_model=DomainOut)
async def merge_domains(domain_id: str, target_id: str, request: Request, session: SessionDep,
                        user: UserDep) -> dict:
    """Merge domain ``domain_id`` into ``target_id``; returns the surviving target."""
    source = await _domain(session, domain_id)
    target = await _domain(session, target_id)
    try:
        target = await svc.merge(session, request.app.state.knowledge, source, target, user)
    except svc.Conflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return await svc.get_domain_out(session, target)


@router.get("/projects/{project_id}/graph", response_model=GraphOut)
async def graph(project_id: str, session: SessionDep) -> dict:
    await _project(session, project_id)
    return await svc.graph_view(session, project_id)
