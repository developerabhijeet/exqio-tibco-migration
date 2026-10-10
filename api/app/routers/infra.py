"""Tab 7, Deploy: generated infrastructure, the Azure connection (encrypted) and the plan."""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from api.app.deps import RunnerDep, SessionDep, UserDep
from api.app.models import AzureConnection, Build, Process, Project
from api.app.services import build as build_svc
from api.app.services import evidence
from api.app.services import infra as svc
from api.app.services.context import active_job
from api.app.services.gates import audit

router = APIRouter(tags=["deploy"])


class AzureConnect(BaseModel):
    tenant: str = Field(min_length=1)
    subscription: str = Field(min_length=1)
    resource_group: str | None = None
    container_registry: str | None = None
    runtime: Literal["container_app", "aks"] | None = None
    service_bus_namespace: str | None = None
    key_vault: str | None = None
    log_analytics: str | None = None
    location: str | None = None
    client_id: str | None = None
    client_secret: str | None = None


class Acknowledge(BaseModel):
    note: str = Field(min_length=1, max_length=2000)


async def _build(session, build_id: str) -> Build:
    build = await session.get(Build, build_id)
    if build is None:
        raise HTTPException(status_code=404, detail="build not found")
    return build


@router.get("/builds/{build_id}/infra")
async def get_infra(build_id: str, session: SessionDep) -> dict[str, Any]:
    return await svc.infra_view(session, await _build(session, build_id))


@router.post("/projects/{project_id}/azure")
async def connect_azure(project_id: str, body: AzureConnect, request: Request, session: SessionDep,
                        user: UserDep) -> dict[str, Any]:
    """Store the Azure connection; the client secret is Fernet-encrypted and never returned."""
    if await session.get(Project, project_id) is None:
        raise HTTPException(status_code=404, detail="project not found")
    conn = await svc.save_connection(session, request.app.state.fernet, project_id, body.model_dump())
    audit(session, None, user, "azure connected", project_id=project_id,
          detail={"tenant": body.tenant, "subscription": body.subscription,
                  "secret_stored": bool(body.client_secret)})
    await session.commit()
    return svc.connection_out(conn, request.app.state.fernet_generated)


@router.get("/projects/{project_id}/azure")
async def get_azure(project_id: str, request: Request, session: SessionDep) -> dict[str, Any]:
    conn = await session.scalar(select(AzureConnection).where(AzureConnection.project_id == project_id))
    return svc.connection_out(conn, request.app.state.fernet_generated)


@router.delete("/projects/{project_id}/azure")
async def disconnect_azure(project_id: str, request: Request, session: SessionDep, user: UserDep) -> dict[str, Any]:
    conn = await session.scalar(select(AzureConnection).where(AzureConnection.project_id == project_id))
    if conn is not None:
        await session.delete(conn)
        audit(session, None, user, "azure disconnected", project_id=project_id)
        await session.commit()
    return {"connected": False}


@router.post("/builds/{build_id}/plan")
async def plan(build_id: str, request: Request, session: SessionDep, runner: RunnerDep) -> dict[str, Any]:
    """Connected (and ``terragrunt``, ``terraform``, ``az`` present): queue ``terragrunt run --all --
    plan`` and return ``{connected: true, job_id, plan_id}``. Otherwise record the connect-later state
    and return ``{connected: false, reason, last_validation, plan}``."""
    build = await _build(session, build_id)
    proc = await session.get(Process, build.process_id)
    conn = await session.scalar(select(AzureConnection).where(AzureConnection.project_id == proc.project_id))
    tools = build_svc.toolchain()
    ready, reason = svc.plan_ready(conn, tools)
    if not ready:
        latest = await evidence.latest_plan(session, build_id)
        if latest is not None and latest.status == "acknowledged":
            # connect-later was acknowledged by operations: keep that decision, do not supersede it
            return {"connected": False, "reason": reason, "last_validation": build.infra_validation,
                    "plan": svc.plan_out(latest), "acknowledged": True}
        p = await svc.record_connect_later(session, build, reason or "")
        await session.commit()
        return {"connected": False, "reason": reason, "last_validation": build.infra_validation,
                "plan": svc.plan_out(p), "acknowledged": False}
    running = await active_job(session, "plan", key="build_id", value=build_id)
    if running is not None:
        raise HTTPException(status_code=409, detail=f"a plan of this build is in progress: {running}")
    try:
        secret = svc.decrypt_secret(request.app.state.fernet, conn)
    except svc.SecretUnreadable as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    p = await svc.record_connect_later(session, build, "")
    p.connected, p.status, p.note = True, "running", None
    await session.commit()
    job_id = await runner.submit("plan", {"build_id": build_id, "plan_id": p.id},
                                 svc.plan_job(build_id, p.id, svc.plan_env(conn, secret),
                                              tools["terragrunt"]["path"]),
                                 project_id=proc.project_id, process_id=proc.id)
    p.job_id = job_id
    await session.commit()
    return {"connected": True, "job_id": job_id, "plan_id": p.id}


@router.get("/builds/{build_id}/plan")
async def get_plan(build_id: str, session: SessionDep) -> dict[str, Any]:
    await _build(session, build_id)
    out = svc.plan_out(await evidence.latest_plan(session, build_id))
    if out is None:
        raise HTTPException(status_code=404, detail="no plan for this build yet")
    return out


@router.post("/builds/{build_id}/plan/acknowledge")
async def acknowledge(build_id: str, body: Acknowledge, session: SessionDep, user: UserDep) -> dict[str, Any]:
    """An operations user acknowledges connect-later with a note (enables G6 without a plan)."""
    build = await _build(session, build_id)
    proc = await session.get(Process, build.process_id)
    p = await svc.acknowledge(session, build, proc, user, body.note.strip())
    await session.commit()
    return svc.plan_out(p)
