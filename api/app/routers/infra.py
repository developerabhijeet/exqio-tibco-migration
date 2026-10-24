"""Tab 7, Deploy: generated infrastructure, the Azure connection and the plan.

``DEPLOY_CREDENTIAL_MODE=local`` keeps the Connect Azure form (the secret Fernet-encrypted). In an
install (``workload_identity``) the body names a declared deploy environment and nothing else: the
server copies its ids and Key Vault reference, and no secret can be posted.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select

from api.app.deps import RunnerDep, SessionDep, UserDep
from api.app.models import AzureConnection, Build, Process, Project
from api.app.services import build as build_svc
from api.app.services import deploy_creds, evidence
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
    environment: str | None = Field(None, pattern=r"^[A-Za-z0-9_-]+$")  # infra/live/<env>; None: dev


class EnvironmentConnect(BaseModel):
    """Workload Identity install: only the declared environment's name."""

    model_config = ConfigDict(extra="forbid")
    environment: str = Field(min_length=1)


def _validated(model: type[BaseModel], body: Any) -> Any:
    try:
        return model.model_validate(body)
    except ValidationError as exc:
        raise RequestValidationError(exc.errors(include_url=False)) from exc


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


@router.get("/deploy/environments")
async def deploy_environments(request: Request) -> dict[str, Any]:
    """The declared deploy environments: ids and where a secret lives, never a secret value."""
    return deploy_creds.environments_out(request.app.state.settings)


@router.api_route("/projects/{project_id}/azure", methods=["POST", "PUT"])
async def connect_azure(project_id: str, request: Request, session: SessionDep, user: UserDep,
                        body: Annotated[Any, Body()]) -> dict[str, Any]:
    """Store the Azure connection. Local mode: the form, the client secret Fernet-encrypted and
    never returned. Otherwise ``{environment}``: copied from the declared environment (422 when it
    is not declared), no secret stored."""
    settings = request.app.state.settings
    if await session.get(Project, project_id) is None:
        raise HTTPException(status_code=404, detail="project not found")
    if settings.deploy_credential_mode == "local":
        form: AzureConnect = _validated(AzureConnect, body)
        conn = await svc.save_connection(session, request.app.state.fernet, project_id, form.model_dump())
        detail = {"tenant": form.tenant, "subscription": form.subscription,
                  "secret_stored": bool(form.client_secret), "environment": form.environment}
    else:
        name = _validated(EnvironmentConnect, body).environment
        env = deploy_creds.declared_environment(settings, name)
        if env is None:
            declared = ", ".join(e.name for e in settings.deploy_environments) or "none"
            raise HTTPException(status_code=422,
                                detail=f"deploy environment {name!r} is not declared (declared: {declared})")
        conn = await svc.save_environment_connection(session, project_id, env)
        detail = {"tenant": env.tenant, "subscription": env.subscription, "environment": env.name,
                  "auth_mode": env.auth, "secret_stored": False}
    audit(session, None, user, "azure connected", project_id=project_id, detail=detail)
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
        svc.decrypt_secret(request.app.state.fernet, conn)  # readable now; the job decrypts it when it runs
    except svc.SecretUnreadable as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    p = await svc.record_connect_later(session, build, "")
    p.connected, p.status, p.note = True, "running", None
    await session.commit()
    environment = conn.environment or svc.PLAN_ENV  # credentials and infra/live/<environment>
    job_id = await runner.submit("plan", {"build_id": build_id, "plan_id": p.id, "environment": environment},
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
