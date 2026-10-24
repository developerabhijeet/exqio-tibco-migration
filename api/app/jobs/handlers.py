"""One handler per job kind. Each rebuilds the existing job factory from the stored payload.

The ``plan`` payload carries no credentials: the handler reads the Azure connection and decrypts
the client secret when it runs, so a secret never sits in the ``jobs`` table.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select

from api.app.jobs.locks import scenario_lock
from api.app.jobs.registry import WorkerServices, job
from api.app.jobs.runner import JobContext
from api.app.models import AzureConnection, Build, Plan, Process
from api.app.services import analysis as analysis_svc
from api.app.services import build as build_svc
from api.app.services import design as design_svc
from api.app.services import domains as domains_svc
from api.app.services import infra as infra_svc
from api.app.services import providers as provider_svc
from api.app.services import scan as scan_svc
from api.app.services import verify as verify_svc


@job("scan")
async def scan(ctx: JobContext, w: WorkerServices) -> dict[str, Any]:
    p = ctx.payload
    return await scan_svc.scan_job(p["project_id"], p["upload_id"], w.knowledge)(ctx)


@job("discover")
async def discover(ctx: JobContext, w: WorkerServices) -> dict[str, Any]:
    return await domains_svc.naming_job(ctx.payload["project_id"], w.settings)(ctx)


@job("analyse")
async def analyse(ctx: JobContext, w: WorkerServices) -> dict[str, Any]:
    return await analysis_svc.analyse_job(ctx.payload["process_id"], w.settings, w.knowledge)(ctx)


@job("design")
async def design(ctx: JobContext, w: WorkerServices) -> dict[str, Any]:
    return await design_svc.design_job(ctx.payload["process_id"], w.settings, w.knowledge)(ctx)


@job("build")
async def build(ctx: JobContext, w: WorkerServices) -> dict[str, Any]:
    return await build_svc.build_job(ctx.payload["build_id"], w.settings, w.knowledge,
                                     scenario_lock(w.sessionmaker))(ctx)


@job("verify")
async def verify(ctx: JobContext, w: WorkerServices) -> dict[str, Any]:
    return await verify_svc.run_verify(ctx, ctx.payload["build_id"], w.settings, w.services,
                                       scenario_lock(w.sessionmaker))


@job("plan")
async def plan(ctx: JobContext, w: WorkerServices) -> dict[str, Any]:
    p = ctx.payload
    try:
        env, terragrunt = await _plan_inputs(ctx, w)
    except Exception as exc:
        async with w.sessionmaker() as s:  # the plan row never stays "running"
            row = await s.get(Plan, p["plan_id"])
            if row is not None and row.status == "running":
                row.status = "failed"
                row.output = f"{type(exc).__name__}: {exc}"
                await s.commit()
        raise
    return await infra_svc.plan_job(p["build_id"], p["plan_id"], env, terragrunt)(ctx)


async def _plan_inputs(ctx: JobContext, w: WorkerServices) -> tuple[dict[str, str], str]:
    p = ctx.payload
    async with w.sessionmaker() as s:
        build_row = await s.get(Build, p["build_id"])
        proc = await s.get(Process, build_row.process_id) if build_row else None
        conn = (await s.scalar(select(AzureConnection).where(AzureConnection.project_id == proc.project_id))
                if proc else None)
    if conn is None:
        raise RuntimeError("no Azure connection is stored for this build's project")
    env = infra_svc.plan_env(conn, infra_svc.decrypt_secret(w.fernet, conn))
    return env, build_svc.toolchain()["terragrunt"]["path"]


@job("eval")
async def eval_(ctx: JobContext, w: WorkerServices) -> dict[str, Any]:
    p = ctx.payload
    return await provider_svc.eval_job(p["eval_id"], p["process_ids"], w.settings)(ctx)
