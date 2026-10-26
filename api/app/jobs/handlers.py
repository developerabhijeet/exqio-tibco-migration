"""One handler per job kind. Each rebuilds the existing job factory from the stored payload.

The ``plan`` payload carries no credentials: the handler reads the Azure connection when it runs and
builds the Terraform env then (``deploy_creds.terraform_env``: Workload Identity, a Key Vault read
or the local decrypted secret), so a secret never sits in the ``jobs`` table.
"""
from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import select

from api.app.jobs.locks import scenario_lock
from api.app.jobs.registry import WorkerServices, job
from api.app.jobs.runner import JobContext
from api.app.models import AzureConnection, Build, Plan, Process
from api.app.services import analysis as analysis_svc
from api.app.services import build as build_svc
from api.app.services import confluence_publish as confluence_svc
from api.app.services import deploy_creds
from api.app.services import design as design_svc
from api.app.services import domains as domains_svc
from api.app.services import github_push as github_push_svc
from api.app.services import infra as infra_svc
from api.app.services import memory_ingest as memory_ingest_svc
from api.app.services import memory_migrate as memory_migrate_svc
from api.app.services import providers as provider_svc
from api.app.services import scan as scan_svc
from api.app.services import verify as verify_svc


@job("scan")
async def scan(ctx: JobContext, w: WorkerServices) -> dict[str, Any]:
    p = ctx.payload
    return await scan_svc.scan_job(p["project_id"], p["upload_id"])(ctx)


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
    return await verify_svc.run_verify(ctx, ctx.payload["build_id"], w.settings, w.hosting,
                                       scenario_lock(w.sessionmaker))


@job("service_start")
async def service_start(ctx: JobContext, w: WorkerServices) -> dict[str, Any]:
    """Start the build's generated service on this worker (``POST /builds/{id}/run`` waits for it);
    the build row then names this worker as ``run_host``/``run_owner``."""
    await ctx.stage("start")
    return verify_svc.run_status(await w.hosting.start(ctx.payload["build_id"]))


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
    environment = p.get("environment") or infra_svc.PLAN_ENV
    return await infra_svc.plan_job(p["build_id"], p["plan_id"], env, terragrunt, environment)(ctx)


async def _plan_inputs(ctx: JobContext, w: WorkerServices) -> tuple[dict[str, str], str]:
    """The Terraform env (Workload Identity, a Key Vault secret read now, or the local secret) and
    the ``terragrunt`` binary; the env goes to the child process only."""
    p = ctx.payload
    async with w.sessionmaker() as s:
        build_row = await s.get(Build, p["build_id"])
        proc = await s.get(Process, build_row.process_id) if build_row else None
        conn = (await s.scalar(select(AzureConnection).where(AzureConnection.project_id == proc.project_id))
                if proc else None)
    if conn is None:
        raise RuntimeError("no Azure connection is stored for this build's project")
    # the credentials follow the payload's environment, not whatever the row says now
    conn = deploy_creds.connection_for_plan(conn, w.settings, p.get("environment") or infra_svc.PLAN_ENV)
    env = await asyncio.to_thread(deploy_creds.terraform_env, conn, w.settings,
                                  secret_reader=deploy_creds.keyvault_secret, fernet=w.fernet)
    if w.settings.tf_cli_config_file:  # every credential mode; a setting from .env is not in os.environ
        env = {**env, "TF_CLI_CONFIG_FILE": w.settings.tf_cli_config_file}
    return env, build_svc.toolchain()["terragrunt"]["path"]


@job("eval")
async def eval_(ctx: JobContext, w: WorkerServices) -> dict[str, Any]:
    p = ctx.payload
    return await provider_svc.eval_job(p["eval_id"], p["process_ids"], w.settings)(ctx)


@job(memory_ingest_svc.INGEST_KIND, max_attempts="memory_ingest_max_attempts")
async def memory_ingest(ctx: JobContext, w: WorkerServices) -> dict[str, Any]:
    """Project one phase's Postgres state into the memory graph (``services.memory_ingest``)."""
    return await memory_ingest_svc.ingest(ctx, w)


@job(memory_migrate_svc.MIGRATE_KIND, max_attempts="memory_ingest_max_attempts")
async def memory_migrate(ctx: JobContext, w: WorkerServices) -> dict[str, Any]:
    """The one-time copy of the Postgres ``knowledge`` schema into the memory graph
    (``services.memory_migrate``)."""
    return await memory_migrate_svc.migrate_job(ctx, w)


@job(memory_migrate_svc.REEMBED_KIND, max_attempts="memory_ingest_max_attempts")
async def memory_reembed(ctx: JobContext, w: WorkerServices) -> dict[str, Any]:
    """Re-embed one project's memory with the active embedding backend."""
    return await memory_migrate_svc.reembed_job(ctx, w)


@job(github_push_svc.PUSH_KIND)
async def github_push(ctx: JobContext, w: WorkerServices) -> dict[str, Any]:
    """Push a build to an ``exiqo/`` branch of a GitHub repository and open its pull request
    (``services.github_push``); the token is read from its mounted file now, never stored."""
    return await github_push_svc.push_job(ctx, w)


@job(confluence_svc.PUBLISH_KIND)
async def confluence_publish(ctx: JobContext, w: WorkerServices) -> dict[str, Any]:
    """Create or update the process's architecture page in Confluence (``services.confluence_publish``);
    the Atlassian credential is read from its mounted file now, never stored."""
    return await confluence_svc.publish_job(ctx, w)
