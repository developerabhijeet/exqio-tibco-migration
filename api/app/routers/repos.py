"""GitHub repositories as a source (sub-project 6, spec section 4): connect a branch, list the
connected repositories and a repository's branches, pull the latest commit.

Connecting needs ``engineer`` or ``admin`` (an ``sme`` may connect an ``estate`` repository).
Every GitHub request goes through sub-project 7's connector: 409 when it is off or has no
credential, 502 with its (redacted) message when GitHub refuses.
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from api.app.auth.roles import Principal
from api.app.connectors.audit import audit_connector
from api.app.connectors.base import ConnectorDisabled, ConnectorError, ConnectorNotConfigured
from api.app.deps import SessionDep, SettingsDep, require_roles
from api.app.models import Project, ProjectRepo
from api.app.services import repos as svc

router = APIRouter(tags=["repos"])

SME_ESTATE_ONLY = "an SME may connect an estate repository only"
ConnectorDep = Annotated[Principal, Depends(require_roles("engineer", "admin", "sme"))]
PusherDep = Annotated[Principal, Depends(require_roles("engineer", "admin"))]


class RepoIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo: str = Field(pattern=svc.REPO_PATTERN, max_length=201)
    branch: str = Field(min_length=1, max_length=200)
    purpose: Literal["estate", "build"]
    process_id: str | None = None


class PullIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    branch: str | None = Field(default=None, min_length=1, max_length=200)


class PushIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    build_id: str = Field(min_length=1, max_length=64)
    open_pr: bool = True


class RepoOut(BaseModel):
    id: str
    project_id: str
    repo: str
    branch: str  # the connected branch (the push base)
    purpose: str
    process_id: str | None
    last_commit: str | None
    connected_by: str
    connected_at: datetime
    job_id: str | None = None

    @classmethod
    def of(cls, row: ProjectRepo, job_id: str | None = None) -> RepoOut:
        return cls(id=row.id, project_id=row.project_id, repo=svc.full_name(row), branch=row.default_branch,
                   purpose=row.purpose, process_id=row.process_id, last_commit=row.last_commit,
                   connected_by=row.connected_by, connected_at=row.connected_at, job_id=job_id)


def connector_errors(exc: Exception) -> HTTPException:
    if isinstance(exc, (ConnectorDisabled, ConnectorNotConfigured)):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, ConnectorError):
        return HTTPException(status_code=502, detail=exc.message)
    if isinstance(exc, svc.RepoRefused):
        return HTTPException(status_code=422, detail=str(exc))
    raise exc


def _sme_only(principal: Principal) -> bool:
    return not principal.roles & {"engineer", "admin"}


async def _repo(session, repo_id: str) -> ProjectRepo:
    row = await session.get(ProjectRepo, repo_id)
    if row is None:
        raise HTTPException(status_code=404, detail="repository not connected")
    return row


@router.post("/projects/{project_id}/repos", status_code=201, response_model=RepoOut)
async def connect(project_id: str, body: RepoIn, request: Request, principal: ConnectorDep, session: SessionDep,
                  settings: SettingsDep) -> RepoOut:
    """Connect ``owner/name`` at ``branch`` and queue its first fetch."""
    if _sme_only(principal) and body.purpose != "estate":
        raise HTTPException(status_code=403, detail=SME_ESTATE_ONLY)
    if await session.get(Project, project_id) is None:
        raise HTTPException(status_code=404, detail="project not found")
    try:
        row = await svc.connect_repo(session, settings, project_id, body.repo, body.branch, body.purpose,
                                     body.process_id, principal.actor)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ConnectorDisabled, ConnectorNotConfigured, ConnectorError, svc.RepoRefused) as exc:
        await session.commit()  # the connector's audit line
        raise connector_errors(exc) from exc
    await session.commit()
    job_id = await svc.queue_fetch(request.app.state.sessionmaker, row, row.default_branch, principal.actor)
    return RepoOut.of(row, job_id)


@router.get("/projects/{project_id}/repos", response_model=list[RepoOut])
async def list_repos(project_id: str, session: SessionDep) -> list[RepoOut]:
    rows = (await session.scalars(select(ProjectRepo).where(ProjectRepo.project_id == project_id)
                                  .order_by(ProjectRepo.connected_at))).all()
    return [RepoOut.of(r) for r in rows]


@router.get("/repos/{repo_id}/branches")
async def list_branches(repo_id: str, session: SessionDep, settings: SettingsDep) -> dict[str, list[str]]:
    row = await _repo(session, repo_id)
    try:
        return {"branches": await svc.branches(session, settings, row)}
    except (ConnectorDisabled, ConnectorNotConfigured, ConnectorError) as exc:
        raise connector_errors(exc) from exc


@router.post("/repos/{repo_id}/pull")
async def pull(repo_id: str, request: Request, principal: ConnectorDep, session: SessionDep,
               settings: SettingsDep, body: PullIn | None = None):
    """Fetch the branch again (``branch``, else the connected one): ``{"changed": false}`` when its
    head is the last commit fetched, else 202 with the fetch job."""
    row = await _repo(session, repo_id)
    if _sme_only(principal) and row.purpose != "estate":
        raise HTTPException(status_code=403, detail=SME_ESTATE_ONLY)
    branch = (body.branch if body else None) or row.default_branch
    try:
        svc.check_branch(branch)
        head = await svc.remote_head(session, settings, row, branch, principal.actor)
    except (ConnectorDisabled, ConnectorNotConfigured, ConnectorError, svc.RepoRefused) as exc:
        await session.commit()
        raise connector_errors(exc) from exc
    await session.commit()
    if head == row.last_commit:
        return {"changed": False}
    job_id = await svc.queue_fetch(request.app.state.sessionmaker, row, branch, principal.actor)
    return JSONResponse(status_code=202, content={"changed": True, "job_id": job_id, "commit": head})


@router.post("/repos/{repo_id}/push")
async def push(repo_id: str, body: PushIn, request: Request, principal: PusherDep, session: SessionDep,
               settings: SettingsDep) -> dict:
    """Push a build of the repository's process to ``exiqo/<process slug>/iteration-<n>`` (with
    ``EXIQO_BASELINE.json``, so a reviewer's commit there pulls back as an edit set) and open its pull
    request into the connected branch."""
    row = await _repo(session, repo_id)
    where = {"project_id": row.project_id, "process_id": row.process_id}
    target = svc.full_name(row)
    try:
        out = await svc.push_build(session, request.app.state.sessionmaker, settings, row, body.build_id,
                                   body.open_pr, principal.actor)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (svc.PushRefused, svc.PushBusy) as exc:
        await session.rollback()
        # X20: a refusal before anything is sent is audited "refused", as every connector's is
        await audit_connector(session, principal.actor, "github", "push", **where, target=target,
                              outcome="refused", detail={"build_id": body.build_id, "message": str(exc)})
        await session.commit()
        raise HTTPException(status_code=422 if isinstance(exc, svc.PushRefused) else 409, detail=str(exc)) from exc
    except (ConnectorDisabled, ConnectorNotConfigured, ConnectorError) as exc:
        await session.commit()  # the connector's audit line
        raise connector_errors(exc) from exc
    await session.commit()
    return out
