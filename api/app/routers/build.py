"""Tab 5, Build: start a build (job), the build view, files, tests, reviews and metrics; and the
running service of a build (Verify uses it): start, status, logs, stop. The service runs on a
worker (:mod:`api.app.services.hosting`); these routes reach it through that worker."""
from __future__ import annotations

import asyncio
import io
import re
import zipfile
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel
from sqlalchemy import select

from api.app.deps import RunnerDep, SessionDep, SettingsDep, UserDep
from api.app.jobs.locks import advisory_lock
from api.app.models import Build, BuildFile, Job, Process, TestResult
from api.app.routers.run_proxy import public_urls
from api.app.services import build as svc
from api.app.services import hosting
from api.app.services import verify as verify_svc
from api.app.services.build_tree import (
    DOWNLOAD_EXCLUDED,
    build_tree_files,
    download_excluded,
    export_note,
)
from api.app.services.citations import cite_review, mask_lines
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
        project_id=proc.project_id, process_id=process_id)
    build.job_id = job_id
    await session.commit()
    return BuildCreated(job_id=job_id, build_id=build.id)


@router.get("/processes/{process_id}/builds")
async def list_builds(process_id: str, session: SessionDep) -> list[dict[str, Any]]:
    rows = (await session.execute(select(Build).where(Build.process_id == process_id)
                                  .order_by(Build.created_at.desc()))).scalars().all()
    return [{"id": b.id, "status": b.status, "runtime": b.runtime, "stack": b.stack or "dotnet",
             "build_hash": b.build_hash, "job_id": b.job_id,
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
    # content_masked: the same lines, each masked with the configured sensitive fields, for text
    # quoted from the file elsewhere (the Deploy evidence previews); the viewer shows content.
    return {**svc.file_out(row), "content": content, "content_masked": mask_lines(content),
            "language": LANGUAGES.get(suffix, "dockerfile" if target.name == "Dockerfile" else "plaintext")}


#: Zip entries that have to arrive executable. A zip stores the mode in the external attributes, and
#: a generator writes every file 0644, so without this ``bash run-local.sh`` is the only way to start
#: the exported bundle and ``./run-local.sh`` answers "permission denied".
_EXECUTABLE = (".sh",)

_export_note = export_note  # the name earlier callers and tests import


def _zip_solution(root: Path, note: str | None = None,
                  excluded: frozenset[str] = DOWNLOAD_EXCLUDED) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in build_tree_files(root, excluded):
            path = root / name
            if path.suffix in _EXECUTABLE:
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = (0o100755 << 16)
                zf.writestr(info, path.read_bytes())
            else:
                zf.write(path, name)
        if note is not None:
            zf.writestr("EXPORTED_FROM.md", note)
    return buf.getvalue()


@router.get("/builds/{build_id}/download")
async def download(build_id: str, session: SessionDep) -> Response:
    """The solution, its infrastructure and its local-run scripts as a zip, ready to run on another
    machine (without the stack's build output: bin, obj, TestResults and .exiqo for .NET)."""
    build = await _build(session, build_id)
    if not build.output_dir or not Path(build.output_dir).is_dir():
        raise HTTPException(status_code=404, detail="the build has no solution on disk")
    proc = await session.get(Process, build.process_id)
    note = export_note(build, proc)
    data = await asyncio.to_thread(_zip_solution, Path(build.output_dir), note, download_excluded(build.stack))
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", proc.name) if proc and proc.name else "build"
    return Response(content=data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{stem}-{build_id[:12]}.zip"'})


@router.get("/builds/{build_id}/tests")
async def get_tests(build_id: str, session: SessionDep) -> dict[str, Any]:
    await _build(session, build_id)
    rows = (await session.execute(select(TestResult).where(TestResult.build_id == build_id)
                                  .order_by(TestResult.file, TestResult.name))).scalars().all()
    cases = [{"name": t.name, "display_name": t.display_name, "kind": t.kind, "rule_key": t.rule_key,
              "ref": t.ref, "file": t.file, "outcome": t.outcome, "has_body": t.has_body, "message": t.message,
              "duration_ms": t.duration_ms} for t in rows]
    # Three things get counted apart from a failure, because none of them says the code is wrong.
    # A test with no body was never written. A test blocked by an un-migrated sub-process could not
    # run: the dependency has no approved design, so no code was derived from its TIBCO source and
    # the caller throws before reaching anything this build produced.
    written = [c for c in cases if c["has_body"]]
    for c in cases:
        c["blocked_by"] = svc.blocking_subprocess(c.get("message"))
    runnable = [c for c in written if not c["blocked_by"]]
    blocked = [c for c in written if c["blocked_by"]]
    summary = {"total": len(cases), "passed": sum(1 for c in cases if c["outcome"] == "passed"),
               "failed": sum(1 for c in runnable if c["outcome"] == "failed"),
               "not_run": sum(1 for c in runnable if c["outcome"] not in ("passed", "failed")),
               "no_body": sum(1 for c in cases if not c["has_body"]),
               "blocked": len(blocked),
               "blocked_by": sorted({c["blocked_by"] for c in blocked}),
               "written": len(written)}
    return {"build_id": build_id, "cases": cases, "summary": summary}


async def _cited_reviews(session, build: Build, findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each finding plus ``cited``: a ``file`` ref to its file and line (``line`` ``None`` when the
    reviewer gave none) and refs to the activities of that file it names; ``null`` for a finding with
    no text (no sentence to carry the ref). ``cited.text`` is the
    MASKED finding text and the spans index into it (code points), so it can differ from ``text``:
    clients render ``cited.text`` when present."""
    proc = await session.get(Process, build.process_id)
    paths = {f.get("file") for f in findings if f.get("file")}
    refs = {r.path: r.tibco_ref for r in (await session.execute(
        select(BuildFile).where(BuildFile.build_id == build.id, BuildFile.path.in_(paths)))).scalars()} \
        if paths else {}
    out = []
    for f in findings:
        ct = cite_review(f, build.id, proc, refs.get(f.get("file")))
        out.append({**f, "cited": ct.model_dump(mode="json") if ct is not None else None})
    return out


@router.get("/builds/{build_id}/reviews")
async def get_reviews(build_id: str, session: SessionDep) -> list[dict[str, Any]]:
    build = await _build(session, build_id)
    return await _cited_reviews(session, build, list(build.reviews or []))


class ReviewDecision(BaseModel):
    """``keep`` means the next build applies this finding; ``reject`` means it never will.
    ``null`` puts the finding back to undecided."""

    decision: Literal["keep", "reject"] | None
    note: str | None = None


class ReviewDecisionAll(BaseModel):
    """One verdict for the whole panel. ``only_undecided`` keeps verdicts already given."""

    decision: Literal["keep", "reject"] | None
    only_undecided: bool = True


@router.patch("/builds/{build_id}/reviews")
async def decide_all_reviews(build_id: str, body: ReviewDecisionAll, session: SessionDep,
                             user: UserDep) -> list[dict[str, Any]]:
    """Keep or reject every review finding at once; a finding a later build already applied is
    left as it is."""
    build = await _build(session, build_id)
    findings = await svc.decide_all_review_findings(session, build, body.decision, body.only_undecided, user)
    return await _cited_reviews(session, build, findings)


@router.patch("/builds/{build_id}/reviews/{index}")
async def decide_review(build_id: str, index: int, body: ReviewDecision, session: SessionDep,
                        user: UserDep) -> list[dict[str, Any]]:
    """Keep or reject one review finding. The index is its position in the build's review list,
    which is written once when the build runs and never reordered."""
    build = await _build(session, build_id)
    try:
        findings = await svc.decide_review_finding(session, build, index, body.decision, body.note, user)
    except IndexError as exc:
        raise HTTPException(status_code=404, detail="no review finding at that index") from exc
    return await _cited_reviews(session, build, findings)


# ------------------------------------------------------------------ the running service
#: How long POST /builds/{id}/run waits for its ``service_start`` job: the service's own start
#: timeout (``dotnet run`` until ``/health`` answers) plus the claim and the scenario seed.
START_WAIT_S = verify_svc.START_TIMEOUT_S + 30.0


async def _wait_job(sessionmaker, job_id: str, timeout_s: float) -> Job | None:
    """The job once it is done or failed; ``None`` when it is still going after ``timeout_s``."""
    deadline = asyncio.get_running_loop().time() + timeout_s
    while True:
        async with sessionmaker() as s:
            job = await s.get(Job, job_id)
        if job is None or job.status in ("done", "failed"):
            return job
        if asyncio.get_running_loop().time() >= deadline:
            return None
        await asyncio.sleep(0.2)


def _host_failure(exc: hosting.HostError) -> HTTPException:
    return HTTPException(status_code=502, detail=str(exc))


async def _state(request: Request, session, build: Build) -> dict[str, Any]:
    try:
        state = await hosting.service_state(request.app.state, session, build)
    except hosting.HostError as exc:
        raise _host_failure(exc) from exc
    return public_urls(request, build.id, state)


@router.post("/builds/{build_id}/run")
async def run_service(build_id: str, request: Request, session: SessionDep, runner: RunnerDep) -> dict[str, Any]:
    """Start the build's Api project (Development auth, in-memory bus, the scenario database) on a
    worker (the ``service_start`` job) and wait for ``/health``. 409 when it cannot start, with the
    log tail."""
    build = await _build(session, build_id)
    sessionmaker = request.app.state.sessionmaker
    async with advisory_lock(sessionmaker, f"service:{build_id}"):  # one start per build at a time
        # read again under the lock: another replica's start may have committed since the load
        await session.refresh(build)
        state = await _state(request, session, build)
        if state["running"]:
            state.pop("log_tail", None)
            return state
        job_id = await active_job(session, "service_start", key="build_id", value=build_id)
        if job_id is None:
            proc = await session.get(Process, build.process_id)
            # a worker still named on the row (its service exited) restarts it itself, so a build
            # never has services on two workers; a gone worker was cleared by _state above
            job_id = await runner.submit("service_start", {"build_id": build_id},
                                         project_id=proc.project_id if proc else None,
                                         process_id=build.process_id, affinity=build.run_owner)
    job = await _wait_job(sessionmaker, job_id, START_WAIT_S)
    if job is None:
        raise HTTPException(status_code=409, detail=f"the service did not start within {START_WAIT_S:.0f} s "
                                                    f"(start job {job_id} is still going)")
    if job.status == "failed":
        # the job's error is "<ExceptionType>: <message>"; the message carries the log tail
        reason = re.sub(r"^[A-Za-z_][\w.]*: ", "", job.error or "", count=1)
        raise HTTPException(status_code=409, detail=f"the service did not start: {reason}")
    await session.refresh(build)
    state = await _state(request, session, build)
    state.pop("log_tail", None)
    return state


@router.get("/builds/{build_id}/run")
async def run_state(build_id: str, request: Request, session: SessionDep) -> dict[str, Any]:
    """The service's status and log tail, from the worker that hosts it. When that worker no longer
    answers, the run is cleared and ``reason`` says so."""
    return await _state(request, session, await _build(session, build_id))


@router.get("/builds/{build_id}/run/logs")
async def run_logs(build_id: str, request: Request, session: SessionDep) -> dict[str, Any]:
    build = await _build(session, build_id)
    if not build.run_owner:
        return {"running": False, "log": ""}
    try:
        return await hosting.host_client(request.app.state, build).logs(build_id)
    except hosting.HostGone:
        await hosting.clear_run(session, build)
        return {"running": False, "log": "", "reason": hosting.GONE_REASON}
    except hosting.HostError as exc:
        raise _host_failure(exc) from exc


@router.delete("/builds/{build_id}/run")
async def stop_service(build_id: str, request: Request, session: SessionDep) -> dict[str, Any]:
    """Stop the build's service. A start in progress is waited for first, so the stop stops the
    service it registered and clears the host, pid and port it wrote."""
    build = await _build(session, build_id)
    sessionmaker = request.app.state.sessionmaker
    async with advisory_lock(sessionmaker, f"service:{build_id}"):
        pending = await active_job(session, "service_start", key="build_id", value=build_id)
    if pending is not None:
        await _wait_job(sessionmaker, pending, START_WAIT_S)
    await session.refresh(build)  # the start committed host, pid and port
    stopped = False
    if build.run_owner:
        try:
            stopped = await hosting.host_client(request.app.state, build).stop(build_id)
        except hosting.HostGone:
            await hosting.clear_run(session, build)
        except hosting.HostError as exc:
            raise _host_failure(exc) from exc
    return {"stopped": stopped, "running": False}
