"""Tab 5, Build: start a build (job), the build view, files, tests, reviews and metrics; and the
running service of a build (Verify uses it): start, status, logs, stop."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import select

from api.app.deps import RunnerDep, SessionDep, SettingsDep
from api.app.models import Build, BuildFile, Process, TestResult
from api.app.services import build as svc
from api.app.services import verify as verify_svc
from api.app.services.context import active_job

router = APIRouter(tags=["build"])

LANGUAGES = {".cs": "csharp", ".csproj": "xml", ".props": "xml", ".sln": "plaintext", ".json": "json",
             ".tf": "hcl", ".hcl": "hcl", ".yml": "yaml", ".yaml": "yaml", ".sql": "sql", ".md": "markdown",
             ".xml": "xml", ".config": "xml"}


class BuildRequest(BaseModel):
    runtime: Literal["container_app", "aks"] = "container_app"


class BuildCreated(BaseModel):
    job_id: str
    build_id: str


async def _build(session, build_id: str) -> Build:
    build = await session.get(Build, build_id)
    if build is None:
        raise HTTPException(status_code=404, detail="build not found")
    return build


@router.post("/processes/{process_id}/build", status_code=202, response_model=BuildCreated)
async def start_build(process_id: str, request: Request, session: SessionDep, runner: RunnerDep,
                      settings: SettingsDep, body: BuildRequest | None = None) -> BuildCreated:
    """Queue a build; 409 when G3 is not approved or a build of this process is in progress."""
    proc = await session.get(Process, process_id)
    if proc is None:
        raise HTTPException(status_code=404, detail="process not found")
    runtime = (body or BuildRequest()).runtime
    try:
        await svc.approved_design(session, proc)
    except svc.BuildRefused as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    running = await active_job(session, "build", process_id=process_id)
    if running is not None:
        raise HTTPException(status_code=409, detail=f"a build of this process is in progress: {running}")
    build = Build(process_id=process_id, runtime=runtime, status="queued")
    session.add(build)
    await session.commit()
    job_id = await runner.submit(
        "build", {"process_id": process_id, "build_id": build.id, "runtime": runtime},
        svc.build_job(build.id, settings, request.app.state.knowledge, request.app.state.scenario_lock),
        project_id=proc.project_id, process_id=process_id)
    build.job_id = job_id
    await session.commit()
    return BuildCreated(job_id=job_id, build_id=build.id)


@router.get("/processes/{process_id}/builds")
async def list_builds(process_id: str, session: SessionDep) -> list[dict[str, Any]]:
    rows = (await session.execute(select(Build).where(Build.process_id == process_id)
                                  .order_by(Build.created_at.desc()))).scalars().all()
    return [{"id": b.id, "status": b.status, "runtime": b.runtime, "build_hash": b.build_hash, "job_id": b.job_id,
             "created_at": b.created_at.isoformat() if b.created_at else None,
             "finished_at": b.finished_at.isoformat() if b.finished_at else None} for b in rows]


@router.get("/builds/{build_id}")
async def get_build(build_id: str, session: SessionDep) -> dict[str, Any]:
    return await svc.build_view(session, await _build(session, build_id))


@router.get("/builds/{build_id}/metrics")
async def get_metrics(build_id: str, session: SessionDep) -> dict[str, Any]:
    view = await svc.build_view(session, await _build(session, build_id))
    if view["metrics"] is None:
        raise HTTPException(status_code=404, detail="no metrics yet")
    return view["metrics"]


@router.get("/builds/{build_id}/files")
async def list_files(build_id: str, session: SessionDep,
                     origin: Annotated[str | None, Query(description="code|agent|ai|infra, comma separated")]
                     = None) -> dict[str, Any]:
    await _build(session, build_id)
    q = select(BuildFile).where(BuildFile.build_id == build_id)
    if origin:
        q = q.where(BuildFile.origin.in_([o.strip() for o in origin.split(",") if o.strip()]))
    rows = (await session.execute(q.order_by(BuildFile.path))).scalars().all()
    files = [svc.file_out(f) for f in rows]
    counts: dict[str, int] = {}
    for f in files:
        counts[f["origin"]] = counts.get(f["origin"], 0) + 1
    return {"build_id": build_id, "files": files, "counts": counts, "tree": svc.file_tree(files)}


@router.get("/builds/{build_id}/files/{path:path}")
async def get_file(build_id: str, path: str, session: SessionDep) -> dict[str, Any]:
    build = await _build(session, build_id)
    row = await session.scalar(select(BuildFile).where(BuildFile.build_id == build_id, BuildFile.path == path))
    if row is None or not build.output_dir:
        raise HTTPException(status_code=404, detail="file not found")
    root = Path(build.output_dir).resolve()
    target = (root / path).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    content = await asyncio.to_thread(target.read_text, encoding="utf-8", errors="replace")
    suffix = target.suffix if target.suffix else target.name
    return {**svc.file_out(row), "content": content,
            "language": LANGUAGES.get(suffix, "dockerfile" if target.name == "Dockerfile" else "plaintext")}


@router.get("/builds/{build_id}/tests")
async def get_tests(build_id: str, session: SessionDep) -> dict[str, Any]:
    await _build(session, build_id)
    rows = (await session.execute(select(TestResult).where(TestResult.build_id == build_id)
                                  .order_by(TestResult.file, TestResult.name))).scalars().all()
    cases = [{"name": t.name, "display_name": t.display_name, "kind": t.kind, "rule_key": t.rule_key,
              "ref": t.ref, "file": t.file, "outcome": t.outcome, "has_body": t.has_body, "message": t.message,
              "duration_ms": t.duration_ms} for t in rows]
    summary = {"total": len(cases), "passed": sum(1 for c in cases if c["outcome"] == "passed"),
               "failed": sum(1 for c in cases if c["outcome"] == "failed"),
               "not_run": sum(1 for c in cases if c["outcome"] not in ("passed", "failed")),
               "no_body": sum(1 for c in cases if not c["has_body"])}
    return {"build_id": build_id, "cases": cases, "summary": summary}


@router.get("/builds/{build_id}/reviews")
async def get_reviews(build_id: str, session: SessionDep) -> list[dict[str, Any]]:
    return list((await _build(session, build_id)).reviews or [])


# ------------------------------------------------------------------ the running service
@router.post("/builds/{build_id}/run")
async def run_service(build_id: str, request: Request, session: SessionDep,
                      settings: SettingsDep) -> dict[str, Any]:
    """Start the build's Api project (Development auth, in-memory bus, the scenario database) on a
    free port and wait for ``/health``. 409 when it cannot start, with the log tail."""
    build = await _build(session, build_id)
    services: dict[str, verify_svc.RunInfo] = request.app.state.services
    current = services.get(build_id)
    if current is not None and current.running:
        return verify_svc.run_status(current)
    proc = await session.get(Process, build.process_id)
    try:
        info = await verify_svc.start_service(settings, build, proc.project_id)
    except (RuntimeError, OSError, verify_svc.ScenarioDBUnavailable) as exc:
        raise HTTPException(status_code=409, detail=f"the service did not start: {exc}") from exc
    services[build_id] = info
    build.run_port, build.run_pid = info.port, info.handle.pid
    await session.commit()
    return verify_svc.run_status(info)


@router.get("/builds/{build_id}/run")
async def run_state(build_id: str, request: Request, session: SessionDep) -> dict[str, Any]:
    await _build(session, build_id)
    info = request.app.state.services.get(build_id)
    return {**verify_svc.run_status(info), "log_tail": verify_svc.read_log(info, 4000)}


@router.get("/builds/{build_id}/run/logs")
async def run_logs(build_id: str, request: Request, session: SessionDep) -> dict[str, Any]:
    await _build(session, build_id)
    info = request.app.state.services.get(build_id)
    return {"running": bool(info and info.running), "log": verify_svc.read_log(info)}


@router.delete("/builds/{build_id}/run")
async def stop_service(build_id: str, request: Request, session: SessionDep) -> dict[str, Any]:
    build = await _build(session, build_id)
    info = request.app.state.services.pop(build_id, None)
    if info is not None:
        await asyncio.to_thread(info.handle.stop)
    build.run_port = build.run_pid = None
    await session.commit()
    return {"stopped": info is not None, "running": False}
