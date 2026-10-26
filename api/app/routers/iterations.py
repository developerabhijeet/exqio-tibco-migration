"""Iterations (sub-project 6): re-enter Design for a process with what failed, uploaded logs and
updated requirements; list and read iterations; close or abandon one.

Opening an iteration resets G3 to G6 of the process (the architect approves the design again in the
light of the errors) and queues the design job stamped with the new iteration. Logs and requirements
are untrusted data: size capped while the body streams in, text only, masked before they are stored,
and handed to the designer inside ``<source>``.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import ValidationError
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.requests import ClientDisconnect

from api.app.auth.roles import Principal
from api.app.deps import RunnerDep, SessionDep, SettingsDep, require_roles
from api.app.models import AuditLog, Build, DesignRecord, Iteration, IterationInput, Process
from api.app.schemas.iteration import (
    InputText,
    IterationDetail,
    IterationInputOut,
    IterationNote,
    IterationOpen,
    IterationOut,
    IterationPreview,
    TimelineEntry,
)
from api.app.services import gates as gate_svc
from api.app.services import iterations as iteration_svc
from api.app.services import memory_ingest
from api.app.services.context import active_job
from api.app.services.failures import (
    MAX_UPLOAD_BYTES,
    FailureItem,
    LogTooLarge,
    clean,
    collect_failures,
    read_log_upload,
    store_log,
)
from api.app.services.text_safety import one_line
from api.app.services.uploads import UploadError
from engine.memory.model import KnowledgeError, guard_text

router = APIRouter(tags=["iterations"])

MAX_LOGS = 10
JSON_BODY_LIMIT = 256 * 1024
#: ten logs of 5 MB, the ``body`` field and the multipart framing
MULTIPART_BODY_LIMIT = MAX_LOGS * MAX_UPLOAD_BYTES + 1024 * 1024
SME_ONLY = "an SME may open an iteration with requirements only"
QUEUE_FAILED = "the design job could not be queued: the iteration was abandoned, open it again"
TOO_LARGE = "the request is larger than {mb} MB"
OPENERS = ("architect", "admin", "sme")
FULL_OPENERS = frozenset({"architect", "admin"})
LOG_FIELDS = ("logs", "logs[]")

OpenerDep = Annotated[Principal, Depends(require_roles(*OPENERS))]
EnderDep = Annotated[Principal, Depends(require_roles("architect", "admin"))]


# ------------------------------------------------------------------ request body
def _too_large(limit: int) -> HTTPException:
    return HTTPException(status_code=413, detail=TOO_LARGE.format(mb=round(limit / (1024 * 1024), 2)))


def _capped(request: Request, limit: int) -> Request:
    """``request`` whose body may not exceed ``limit`` bytes: refused on its ``Content-Length``
    before anything is read, and while it streams in (a chunked body declares none)."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise _too_large(limit)
    seen = 0
    receive = request.receive

    async def capped_receive() -> dict[str, Any]:
        nonlocal seen
        message = await receive()
        if message["type"] == "http.request":
            seen += len(message.get("body", b""))
            if seen > limit:
                raise _too_large(limit)
        return message

    return Request(request.scope, capped_receive)


def _invalid(exc: ValidationError) -> HTTPException:
    # never the input itself: requirements may hold secrets the masker has not seen yet
    return HTTPException(status_code=422, detail=exc.errors(include_url=False, include_context=False,
                                                            include_input=False))


def _read_upload(file: UploadFile) -> FailureItem:
    """One uploaded log (at most 5 MB + 1 byte is read, enough to refuse a larger one)."""
    file.file.seek(0)
    return read_log_upload(file.filename or "upload.log", file.file.read(MAX_UPLOAD_BYTES + 1))


async def _parse(request: Request) -> tuple[IterationOpen, list[FailureItem]]:
    """The JSON body, or the multipart ``body`` field (JSON) and ``logs``/``logs[]`` files, each
    log read and checked in a thread. 413 for a body or a log over its size, 422 otherwise."""
    content_type = request.headers.get("content-type", "")
    try:
        if not content_type.startswith("multipart/form-data"):
            try:
                return IterationOpen.model_validate(await _capped(request, JSON_BODY_LIMIT).json()), []
            except ValueError as exc:
                if isinstance(exc, ValidationError):
                    raise _invalid(exc) from exc
                raise HTTPException(status_code=422, detail="expected a JSON body or multipart 'body' and 'logs'") from exc
        form = await _capped(request, MULTIPART_BODY_LIMIT).form(max_files=100, max_fields=10)
    except ClientDisconnect as exc:
        raise HTTPException(status_code=400, detail="the request body ended early") from exc
    try:
        raw = form.get("body") or "{}"
        if not isinstance(raw, str):
            raise HTTPException(status_code=422, detail="multipart field 'body' must be JSON text")
        try:
            body = IterationOpen.model_validate_json(raw)
        except ValidationError as exc:
            raise _invalid(exc) from exc
        files = [f for name in LOG_FIELDS for f in form.getlist(name) if isinstance(f, UploadFile)]
        if len(files) > MAX_LOGS:
            raise HTTPException(status_code=422, detail=f"at most {MAX_LOGS} log files per iteration")
        logs: list[FailureItem] = []
        for f in files:
            try:
                logs.append(await run_in_threadpool(_read_upload, f))
            except UploadError as exc:
                raise HTTPException(status_code=413 if isinstance(exc, LogTooLarge) else 422,
                                    detail=str(exc)) from exc
        return body, logs
    finally:
        await form.close()


def _requirements(text: str | None) -> str | None:
    """Masked requirements without control characters or citation markers; raw TIBCO process XML
    is refused (422)."""
    if text is None or not text.strip():
        return None
    try:
        guard_text(text)
    except KnowledgeError as exc:
        raise HTTPException(status_code=422, detail="the requirements contain raw TIBCO process XML, "
                                                    "which is not accepted") from exc
    return clean(text).strip() or None


def _note(text: str | None) -> str | None:
    return clean(text).strip() or None if text else None


# ------------------------------------------------------------------ open
async def _process(session, process_id: str) -> Process:
    row = await session.get(Process, process_id)
    if row is None:
        raise HTTPException(status_code=404, detail="process not found")
    return row


@router.get("/processes/{process_id}/iterations/preview", response_model=IterationPreview)
async def preview(process_id: str, session: SessionDep) -> IterationPreview:
    """The collected failures of the process's latest build that an iteration would include."""
    await _process(session, process_id)
    return IterationPreview(items=await collect_failures(session, process_id))


@router.post("/processes/{process_id}/iterations", status_code=201, response_model=IterationOut,
             openapi_extra={"requestBody": {"content": {
                 "application/json": {"schema": IterationOpen.model_json_schema()},
                 "multipart/form-data": {"schema": {"type": "object", "properties": {
                     "body": {"type": "string", "description": "IterationOpen as JSON"},
                     "logs": {"type": "array", "items": {"type": "string", "format": "binary"}}}}}}}})
async def open_iteration(process_id: str, request: Request, principal: OpenerDep, session: SessionDep,
                         runner: RunnerDep, settings: SettingsDep) -> IterationOut:
    """Re-enter Design: a new iteration with the chosen failures, uploaded logs and requirements;
    G3-G6 reset; the design job queued. An SME may give requirements only."""
    sme_only = not principal.roles & FULL_OPENERS
    if sme_only and (request.headers.get("content-type", "").startswith("multipart/form-data")):
        raise HTTPException(status_code=403, detail=SME_ONLY)  # before a log byte is read
    body, logs = await _parse(request)
    requirements = _requirements(body.requirements)
    if sme_only and (body.include or logs or requirements is None):
        raise HTTPException(status_code=403, detail=SME_ONLY)

    process = await _process(session, process_id)
    if not process.spec_hash:
        raise HTTPException(status_code=409, detail="the process has no spec hash yet: run the analysis first")
    for kind in iteration_svc.BUSY_JOB_KINDS:
        running = await active_job(session, kind, process_id=process_id)
        if running is not None:
            raise HTTPException(status_code=409, detail=f"a {kind} job for this process is in progress: {running}")

    collected = await collect_failures(session, process_id)
    if body.include is not None:
        by_key = {i.key: i for i in collected}
        unknown = [k for k in body.include if k not in by_key]
        if unknown:
            raise HTTPException(status_code=422, detail=f"unknown failure: {one_line(unknown[0], 120)}")
        collected = [by_key[k] for k in dict.fromkeys(body.include)]
    unique_logs = list({item.sha256: item for item in logs}.values())

    try:
        row = await iteration_svc.open_iteration(
            session, process.project_id, [process_id], "reentry", actor=principal.actor,
            requirements=requirements, note=_note(body.note),
            audit_detail={"inputs": len(collected) + len(unique_logs), "uploaded_logs": len(unique_logs),
                          "requirements": requirements is not None})
    except iteration_svc.IterationBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    for item in [*collected, *unique_logs]:
        session.add(IterationInput(iteration_id=row.id, kind=item.kind, process_id=process_id, ref=item.ref,
                                   title=item.title, text=item.text, sha256=item.sha256))
    for item in unique_logs:
        await run_in_threadpool(store_log, settings, row.id, item)
    rows = await gate_svc.gate_rows(session, process_id)
    await gate_svc.reset_gates(session, process, rows, "G3", f"iteration {row.n} re-entered design",
                               principal.actor, "user", {"iteration": row.n, "iteration_id": row.id})
    await session.commit()
    try:
        job_id = await runner.submit("design", {"process_id": process_id, "provider": settings.resolved_provider(),
                                                "iteration": row.n},
                                     project_id=process.project_id, process_id=process_id)
    except Exception as exc:
        # No open iteration without its design job: it is abandoned (the gate reset stands, audited).
        await session.rollback()
        await iteration_svc.abandon(session, row.id, iteration_svc.INITIAL_ACTOR, QUEUE_FAILED)
        await session.commit()
        raise HTTPException(status_code=503, detail=QUEUE_FAILED) from exc
    # what the iteration brought in, as one memory revision of that iteration (no-op without Neo4j)
    await memory_ingest.enqueue_ingest(
        request.app.state.sessionmaker, settings, project_id=process.project_id, phase="iteration",
        scope=f"iteration:{row.n}", source={"iteration_id": row.id, "process_id": process_id},
        actor=principal.actor, ingest_key=f"iteration:{row.id}", iteration=row.n)
    return IterationOut.model_validate(row).model_copy(update={"job_id": job_id})


# ------------------------------------------------------------------ read
async def _iteration(session, iteration_id: str) -> Iteration:
    row = await session.get(Iteration, iteration_id)
    if row is None:
        raise HTTPException(status_code=404, detail="iteration not found")
    return row


@router.get("/projects/{project_id}/iterations", response_model=list[IterationOut])
async def list_iterations(project_id: str, session: SessionDep) -> list[Iteration]:
    try:
        await iteration_svc.ensure_initial(session, project_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="project not found") from exc
    await session.commit()  # iteration 1 may have just been created
    return list((await session.scalars(select(Iteration).where(Iteration.project_id == project_id)
                                       .order_by(Iteration.n))).all())


async def _timeline(session, row: Iteration) -> list[TimelineEntry]:
    """Design records and builds stamped with the iteration, and gate decisions on its processes
    while it was open."""
    pids = list(row.process_ids or [])
    if row.kind == "initial":
        pids = list((await session.scalars(select(Process.id).where(Process.project_id == row.project_id))).all())
    if not pids:
        return []
    out: list[TimelineEntry] = []
    for d in (await session.scalars(select(DesignRecord).where(DesignRecord.process_id.in_(pids),
                                                               DesignRecord.iteration == row.n))).all():
        out.append(TimelineEntry(kind="design", at=d.created_at, process_id=d.process_id, id=d.id,
                                 iteration=d.iteration, summary=f"design record v{d.version}"))
    for b in (await session.scalars(select(Build).where(Build.process_id.in_(pids),
                                                        Build.iteration == row.n))).all():
        out.append(TimelineEntry(kind="build", at=b.created_at, process_id=b.process_id, id=b.id,
                                 iteration=b.iteration, summary=f"build {b.status}"))
    q = select(AuditLog).where(AuditLog.process_id.in_(pids), AuditLog.gate.is_not(None),
                               AuditLog.ts >= row.opened_at)
    if row.closed_at is not None:
        q = q.where(AuditLog.ts <= row.closed_at)
    for a in (await session.scalars(q.order_by(AuditLog.ts, AuditLog.id))).all():
        out.append(TimelineEntry(kind="gate", at=a.ts, process_id=a.process_id, id=str(a.id),
                                 summary=f"{a.gate} {a.action.removeprefix('gate ')} by {a.actor}"))
    far = datetime.max.replace(tzinfo=UTC)
    return sorted(out, key=lambda e: e.at or far)


@router.get("/iterations/{iteration_id}", response_model=IterationDetail)
async def get_iteration(iteration_id: str, session: SessionDep) -> IterationDetail:
    """The iteration, its inputs (without their text) and its timeline."""
    row = await _iteration(session, iteration_id)
    inputs = (await session.scalars(select(IterationInput).where(IterationInput.iteration_id == row.id)
                                    .order_by(IterationInput.created_at, IterationInput.id))).all()
    return IterationDetail(**IterationOut.model_validate(row).model_dump(),
                           inputs=[IterationInputOut.model_validate(i) for i in inputs],
                           timeline=await _timeline(session, row))


@router.get("/iterations/{iteration_id}/inputs/{input_id}", response_model=InputText)
async def get_input_text(iteration_id: str, input_id: str, session: SessionDep) -> InputText:
    """The masked excerpt of one input (JSON; the web shows it in a ``pre``, never as HTML)."""
    row = await session.get(IterationInput, input_id)
    if row is None or row.iteration_id != iteration_id:
        raise HTTPException(status_code=404, detail="input not found")
    return InputText(text=row.text)


# ------------------------------------------------------------------ end
async def _end(session, iteration_id: str, action, principal: Principal, body: IterationNote | None) -> Iteration:
    await _iteration(session, iteration_id)
    try:
        row = await action(session, iteration_id, principal.actor, _note(body.note if body else None))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await session.commit()
    return row


@router.post("/iterations/{iteration_id}/close", response_model=IterationOut)
async def close_iteration(iteration_id: str, principal: EnderDep, session: SessionDep,
                          body: IterationNote | None = None) -> Iteration:
    return await _end(session, iteration_id, iteration_svc.close, principal, body)


@router.post("/iterations/{iteration_id}/abandon", response_model=IterationOut)
async def abandon_iteration(iteration_id: str, principal: EnderDep, session: SessionDep,
                            body: IterationNote | None = None) -> Iteration:
    return await _end(session, iteration_id, iteration_svc.abandon, principal, body)
