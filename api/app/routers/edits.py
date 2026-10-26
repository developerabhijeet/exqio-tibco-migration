"""Hand edits (sub-project 6, spec section 3): upload an edited copy of a build's download, preview
what changed against the baseline build, read one file's two versions, and resume the iteration
(a build that applies the edits, or a design first when the approved design moved on).

The zip is untrusted: the request body is capped while it streams in, and the archive is checked
entry by entry before anything of it is written (:mod:`api.app.services.edits`).
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.datastructures import UploadFile
from starlette.requests import ClientDisconnect

from api.app.auth.roles import Principal
from api.app.db import new_id
from api.app.deps import RunnerDep, SessionDep, SettingsDep, require_roles
from api.app.jobs.locks import advisory_lock
from api.app.models import Build, EditSet, Iteration, Process, Project
from api.app.routers.iterations import _capped
from api.app.schemas.iteration import EditDiff, EditResumed, EditSetOut
from api.app.services import build as build_svc
from api.app.services import edits as svc
from api.app.services import gates as gate_svc
from api.app.services import iterations as iteration_svc
from api.app.services import memory_ingest
from api.app.services.context import active_job
from api.app.services.uploads import UploadError, UploadTooLarge

router = APIRouter(tags=["edits"])

EngineerDep = Annotated[Principal, Depends(require_roles("engineer", "admin"))]
MAX_DIFF_BYTES = 1024 * 1024
#: multipart framing around the zip
MULTIPART_SLACK = 1024 * 1024
ALREADY = "the edit set was already resumed"
DESIGN_MOVED = "the approved design changed since these edits were uploaded: upload them again"
DESIGN_QUEUE_FAILED = "the design job could not be queued: resume the edit set again"
QUEUE_FAILED = "the build could not be queued: resume the edit set again"
DESIGN_FIRST = "the design must be approved again (G3) before the edits are built"


# ------------------------------------------------------------------ upload
async def _spool(request: Request, settings, part: Path) -> None:
    """The multipart ``file`` field copied to ``part``; the body is capped at the upload limit."""
    try:
        form = await _capped(request, settings.upload_max_bytes + MULTIPART_SLACK).form(max_files=1, max_fields=5)
    except ClientDisconnect as exc:
        raise HTTPException(status_code=400, detail="the request body ended early") from exc
    try:
        file = form.get("file")
        if not isinstance(file, UploadFile):
            raise HTTPException(status_code=422, detail="multipart field 'file' (a zip) is required")

        def copy() -> None:
            part.parent.mkdir(parents=True, exist_ok=True)
            file.file.seek(0)
            with open(part, "wb") as out:
                while chunk := file.file.read(1024 * 1024):
                    out.write(chunk)

        await asyncio.to_thread(copy)
    finally:
        await form.close()


async def _out(session, row: EditSet) -> EditSetOut:
    iteration = await session.get(Iteration, row.iteration_id)
    return EditSetOut(id=row.id, iteration=iteration.n, iteration_id=iteration.id,
                      baseline_build_id=row.baseline_build_id, resume_phase=iteration.resume_phase,
                      status=row.status, files=row.files or [], counts=svc.change_counts(row.files or []))


async def _upload(request: Request, session, settings, principal: Principal, build_id: str | None,
                  project_id: str) -> EditSetOut:
    part = settings.data_dir.resolve() / "tmp" / f"{new_id()}.zip.part"
    try:
        await _spool(request, settings, part)
        try:
            row = await svc.create_edit_set(session, settings, build_id, project_id, part, principal.actor)
        except UploadTooLarge as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        except UploadError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except svc.EditRefused as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        try:
            await session.commit()
        except BaseException:
            await asyncio.to_thread(svc.remove_tree, svc.edits_dir(settings) / row.id)
            raise
    finally:
        part.unlink(missing_ok=True)
    iteration = await session.get(Iteration, row.iteration_id)
    await memory_ingest.enqueue_iteration_ingest(  # the edit set's iteration revision (spec section 5)
        request.app.state.sessionmaker, settings, project_id=project_id, iteration_id=iteration.id, n=iteration.n,
        process_id=(iteration.process_ids or [None])[0], actor=principal.actor)
    return await _out(session, row)


@router.post("/builds/{build_id}/edits", status_code=201, response_model=EditSetOut)
async def upload_build_edits(build_id: str, request: Request, principal: EngineerDep, session: SessionDep,
                             settings: SettingsDep) -> EditSetOut:
    """An edited copy of this build's download (multipart ``file``): the files that differ from
    the build, and the iteration (``kind = edits``) they open. Nothing is built until resumed."""
    build = await session.get(Build, build_id)
    process = await session.get(Process, build.process_id) if build is not None else None
    if build is None or process is None:
        raise HTTPException(status_code=404, detail="build not found")
    return await _upload(request, session, settings, principal, build_id, process.project_id)


@router.post("/projects/{project_id}/edits", status_code=201, response_model=EditSetOut)
async def upload_project_edits(project_id: str, request: Request, principal: EngineerDep, session: SessionDep,
                               settings: SettingsDep) -> EditSetOut:
    """Like ``POST /builds/{id}/edits``, the build named by the zip's ``EXIQO_BASELINE.json``
    (which must be a build of this project)."""
    if await session.get(Project, project_id) is None:
        raise HTTPException(status_code=404, detail="project not found")
    return await _upload(request, session, settings, principal, None, project_id)


# ------------------------------------------------------------------ read
async def _edit_set(session, edit_set_id: str) -> EditSet:
    row = await session.get(EditSet, edit_set_id)
    if row is None:
        raise HTTPException(status_code=404, detail="edit set not found")
    return row


@router.get("/edit-sets/{edit_set_id}", response_model=EditSetOut)
async def get_edit_set(edit_set_id: str, session: SessionDep) -> EditSetOut:
    return await _out(session, await _edit_set(session, edit_set_id))


def _read_text(root: Path | None, path: str) -> str:
    """The file at ``path`` under ``root`` as text ("" when absent); 413 over 1 MB, 422 if binary."""
    if root is None:
        return ""
    base = root.resolve()
    target = base / path
    if target.is_symlink() or not target.resolve().is_relative_to(base) or not target.is_file():
        return ""
    if target.stat().st_size > MAX_DIFF_BYTES:
        raise HTTPException(status_code=413, detail=f"{path} is larger than 1 MB: download the build to compare it")
    data = target.read_bytes()
    if b"\x00" in data:
        raise HTTPException(status_code=422, detail=f"{path} is not a text file")
    return data.decode("utf-8", errors="replace")


@router.get("/edit-sets/{edit_set_id}/diff/{path:path}", response_model=EditDiff)
async def get_edit_diff(edit_set_id: str, path: str, session: SessionDep) -> EditDiff:
    """The baseline build's and the edited version of one changed file (JSON text; the web shows
    them side by side, never as HTML)."""
    row = await _edit_set(session, edit_set_id)
    if path not in {f.get("path") for f in row.files or []}:
        raise HTTPException(status_code=404, detail="no such change in the edit set")
    build = await session.get(Build, row.baseline_build_id) if row.baseline_build_id else None
    base_root = Path(build.output_dir) if build is not None and build.output_dir else None
    baseline = await asyncio.to_thread(_read_text, base_root, path)
    edited = await asyncio.to_thread(_read_text, Path(row.root_dir), path)
    return EditDiff(baseline=baseline, edited=edited)


# ------------------------------------------------------------------ resume
async def _queue_design(session, runner, settings, principal: Principal, row: EditSet, iteration: Iteration,
                        process: Process) -> EditResumed:
    running = await active_job(session, "design", process_id=process.id)
    if running is not None:
        raise HTTPException(status_code=409, detail=f"a design job for this process is in progress: {running}")
    gates = await gate_svc.gate_rows(session, process.id)
    await gate_svc.reset_gates(session, process, gates, "G3", f"iteration {iteration.n}: edits on an earlier design",
                               principal.actor, "user", {"iteration": iteration.n, "edit_set_id": row.id})
    row.status = "designing"
    gate_svc.audit(session, process, principal.actor, "edit set resumed",
                   detail={"edit_set_id": row.id, "iteration": iteration.n, "phase": "design"})
    await session.commit()
    try:
        job_id = await runner.submit("design", {"process_id": process.id, "provider": settings.resolved_provider(),
                                                "iteration": iteration.n},
                                     project_id=process.project_id, process_id=process.id)
    except Exception as exc:
        # G3 stays reset (audited); the edit set goes back so resuming again queues the design
        await session.rollback()
        row.status = "previewed"
        await session.commit()
        raise HTTPException(status_code=503, detail=DESIGN_QUEUE_FAILED) from exc
    return EditResumed(job_id=job_id, kind="design", edit_set_id=row.id)


async def _queue_build(session, runner, principal: Principal, row: EditSet, iteration: Iteration,
                       process: Process, baseline: Build) -> EditResumed:
    try:
        design = await build_svc.approved_design(session, process)
    except build_svc.BuildRefused as exc:
        raise HTTPException(status_code=409, detail=DESIGN_FIRST if row.status == "designing" else str(exc)) from exc
    if row.status == "designing":
        if await active_job(session, "design", process_id=process.id) is not None:
            raise HTTPException(status_code=409, detail=DESIGN_FIRST)
    elif design.design_hash != baseline.design_hash:
        raise HTTPException(status_code=409, detail=DESIGN_MOVED)
    running = await active_job(session, "build", process_id=process.id)
    if running is not None:
        raise HTTPException(status_code=409, detail=f"a build of this process is in progress: {running}")
    build = Build(process_id=process.id, runtime=baseline.runtime, status="queued", edit_set_id=row.id,
                  iteration=iteration.n)
    session.add(build)
    row.status = "resumed"
    gate_svc.audit(session, process, principal.actor, "edit set resumed",
                   detail={"edit_set_id": row.id, "iteration": iteration.n, "build_id": build.id,
                           "phase": iteration.resume_phase})
    await session.commit()
    previous = "designing" if iteration.resume_phase == "design" else "previewed"
    try:
        job_id = await runner.submit("build", {"process_id": process.id, "build_id": build.id,
                                               "runtime": build.runtime, "edit_set_id": row.id,
                                               "iteration": iteration.n},
                                     project_id=process.project_id, process_id=process.id)
    except Exception as exc:
        # no resumed edit set without its build: it can be resumed again
        await session.rollback()
        row.status, build.status, build.error = previous, "failed", QUEUE_FAILED
        await session.commit()
        raise HTTPException(status_code=503, detail=QUEUE_FAILED) from exc
    build.job_id = job_id
    await session.commit()
    return EditResumed(job_id=job_id, kind="build", edit_set_id=row.id, build_id=build.id)


@router.post("/edit-sets/{edit_set_id}/resume", status_code=202, response_model=EditResumed)
async def resume(edit_set_id: str, request: Request, principal: EngineerDep, session: SessionDep,
                 runner: RunnerDep, settings: SettingsDep) -> EditResumed:
    """Build the edits (``resume_phase`` build or infra), or, when the approved design moved on
    since the baseline build, re-enter Design first (G3-G6 reset, design job queued); once G3 is
    approved again, resuming once more queues the build. 409 when already resumed. One resume per
    edit set at a time (a double click queues one job)."""
    await _edit_set(session, edit_set_id)
    async with advisory_lock(request.app.state.sessionmaker, f"edit-set:{edit_set_id}"):
        row = await _edit_set(session, edit_set_id)
        await session.refresh(row)  # read again under the lock: another resume may have committed
        return await _resume(session, runner, settings, principal, row)


async def _resume(session, runner, settings, principal: Principal, row: EditSet) -> EditResumed:
    if row.status == "resumed":
        raise HTTPException(status_code=409, detail=ALREADY)
    iteration = await session.get(Iteration, row.iteration_id)
    if iteration is None or iteration.status in iteration_svc.ENDED_STATUSES:
        raise HTTPException(status_code=409, detail=f"iteration {iteration.n if iteration else '?'} has ended")
    baseline = await session.get(Build, row.baseline_build_id) if row.baseline_build_id else None
    process = await session.get(Process, baseline.process_id) if baseline is not None else None
    if baseline is None or process is None:
        raise HTTPException(status_code=409, detail="the baseline build no longer exists")
    if iteration.resume_phase == "design" and row.status == "previewed":
        return await _queue_design(session, runner, settings, principal, row, iteration, process)
    return await _queue_build(session, runner, principal, row, iteration, process, baseline)
