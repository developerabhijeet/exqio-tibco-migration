"""Projects, upload, scan and the project-level reads tab 1 and tab 2 need."""
from __future__ import annotations

import asyncio
import logging
import shutil
import zipfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import UploadFile

from api.app.config import Settings
from api.app.db import new_id
from api.app.deps import RunnerDep, SessionDep, SettingsDep
from api.app.models import Application, Domain, File, Job, Process, Project, ScanReport, Upload
from api.app.schemas.job import JobCreated
from api.app.schemas.process import ProcessSummary
from api.app.schemas.project import (
    FileEntryOut,
    ProjectCreate,
    ProjectOut,
    ReportOut,
    ScanIn,
    UploadOut,
    UploadPathIn,
)
from api.app.services import uploads
from api.app.services.knowledge import Knowledge, KnowledgeUnavailable
from api.app.services.scan import scan_job

router = APIRouter(tags=["projects"])
log = logging.getLogger(__name__)


async def _project(session: AsyncSession, project_id: str) -> Project:
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    return project


@router.post("/projects", status_code=201, response_model=ProjectOut)
async def create_project(body: ProjectCreate, request: Request, session: SessionDep) -> Project:
    """Create a project and, in the background, seed its migration memory with the R Systems
    TIBCO-to-.NET patterns (``source: rsi-seed``). Seeding failures are logged, never fatal."""
    project = Project(name=body.name.strip())
    session.add(project)
    await session.commit()
    await session.refresh(project)
    task = asyncio.create_task(_seed(request.app.state.knowledge, project.id))
    request.app.state.background.add(task)
    task.add_done_callback(request.app.state.background.discard)
    return project


async def _seed(knowledge: Knowledge, project_id: str) -> None:
    try:
        n = await knowledge.seed_patterns(project_id)
        log.info("project %s: %d RSI seed patterns loaded into migration memory", project_id, n)
    except KnowledgeUnavailable as exc:
        log.warning("project %s: RSI seed patterns not loaded: %s", project_id, exc)


@router.get("/projects", response_model=list[ProjectOut])
async def list_projects(session: SessionDep) -> list[Project]:
    return list((await session.execute(select(Project).order_by(Project.created_at.desc()))).scalars())


@router.get("/projects/{project_id}", response_model=ProjectOut)
async def get_project(project_id: str, session: SessionDep) -> Project:
    return await _project(session, project_id)


async def _save_multipart(request: Request, part_path: Path) -> str:
    form = await request.form()
    file = form.get("file")
    if not isinstance(file, UploadFile):
        raise uploads.UploadError("multipart field 'file' (a zip) is required")
    name = file.filename or "upload.zip"
    try:
        await asyncio.to_thread(_spool_to, file.file, part_path)
    finally:
        await form.close()
    return name


def _spool_to(src, part_path: Path) -> None:
    part_path.parent.mkdir(parents=True, exist_ok=True)
    src.seek(0)
    with open(part_path, "wb") as out:
        shutil.copyfileobj(src, out, 1024 * 1024)


def _allowed_source(settings: Settings, project_id: str, raw: str) -> Path:
    """The resolved server path, if it lies under UPLOAD_ALLOWED_ROOTS and not in another
    project's upload folder."""
    src = Path(raw).expanduser().resolve()
    allowed = any(src.is_relative_to(root) for root in settings.allowed_upload_roots)
    store = settings.uploads_dir
    if src.is_relative_to(store) and not src.is_relative_to(store / project_id):
        allowed = False
    if not allowed:
        raise uploads.UploadError(f"path not allowed: {raw}")
    return src


@router.post("/projects/{project_id}/upload", status_code=201, response_model=UploadOut,
             openapi_extra={"requestBody": {"content": {
                 "multipart/form-data": {"schema": {"type": "object", "properties": {
                     "file": {"type": "string", "format": "binary"}}}},
                 "application/json": {"schema": UploadPathIn.model_json_schema()}}}})
async def upload(project_id: str, request: Request, session: SessionDep,
                 settings: SettingsDep) -> UploadOut:
    """A zip (multipart field ``file``) or ``{"path": "..."}`` naming a folder or zip on the server."""
    await _project(session, project_id)
    upload_id = new_id()
    dest = settings.uploads_dir / project_id / upload_id
    part = dest.parent / f"{upload_id}.zip.part"
    content_type = request.headers.get("content-type", "")
    try:
        if content_type.startswith("multipart/form-data"):
            source_kind, source_name = "zip", await _save_multipart(request, part)
            await asyncio.to_thread(uploads.extract_zip, part, dest, settings.upload_max_bytes)
        else:
            try:
                body = UploadPathIn.model_validate(await request.json())
            except (ValueError, ValidationError) as exc:
                raise HTTPException(status_code=422, detail="expected multipart 'file' or JSON {\"path\"}") from exc
            src = _allowed_source(settings, project_id, body.path)
            source_kind, source_name = "path", body.path
            if src.is_dir():
                await asyncio.to_thread(uploads.copy_folder, src, dest, settings.upload_max_bytes)
            elif src.is_file() and zipfile.is_zipfile(src):
                await asyncio.to_thread(uploads.extract_zip, src, dest, settings.upload_max_bytes)
            else:
                raise uploads.UploadError(f"path not found: {body.path}")
        scan_root = uploads.single_root(dest)
        entries = await asyncio.to_thread(uploads.inventory, dest / scan_root if scan_root else dest)
    except uploads.UploadError as exc:
        uploads.remove_tree(dest)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        uploads.remove_tree(dest)
        raise HTTPException(status_code=400, detail=f"cannot read upload: {exc.strerror or exc}") from exc
    finally:
        part.unlink(missing_ok=True)

    row = Upload(id=upload_id, project_id=project_id, source_kind=source_kind, source_name=source_name,
                 root_dir=str(dest), scan_root=scan_root, file_count=len(entries),
                 total_bytes=sum(e["size"] for e in entries))
    session.add(row)
    await session.flush()
    files = [File(project_id=project_id, upload_id=upload_id, **e) for e in entries]
    session.add_all(files)
    await session.commit()
    return UploadOut(upload_id=upload_id, scan_root=scan_root,
                     files=[FileEntryOut.model_validate(f) for f in files])


async def _latest_upload(session: AsyncSession, project_id: str) -> Upload | None:
    return await session.scalar(select(Upload).where(Upload.project_id == project_id)
                                .order_by(Upload.created_at.desc()).limit(1))


@router.get("/projects/{project_id}/files", response_model=list[FileEntryOut])
async def list_files(project_id: str, session: SessionDep,
                     upload_id: str | None = None) -> list[File]:
    """Files of one upload (default: the latest)."""
    await _project(session, project_id)
    if upload_id is None:
        latest = await _latest_upload(session, project_id)
        if latest is None:
            return []
        upload_id = latest.id
    q = select(File).where(File.project_id == project_id, File.upload_id == upload_id).order_by(File.path)
    return list((await session.execute(q)).scalars())


@router.post("/projects/{project_id}/scan", status_code=202, response_model=JobCreated)
async def scan(project_id: str, request: Request, session: SessionDep, runner: RunnerDep,
               body: ScanIn | None = None) -> JobCreated:
    """Queue a scan of an upload; 409 while a scan of this project is queued or running."""
    await _project(session, project_id)
    if body and body.upload_id:
        upload_row = await session.get(Upload, body.upload_id)
        if upload_row is None or upload_row.project_id != project_id:
            raise HTTPException(status_code=404, detail="upload not found")
    else:
        upload_row = await _latest_upload(session, project_id)
        if upload_row is None:
            raise HTTPException(status_code=409, detail="nothing uploaded yet")
    async with request.app.state.scan_lock:  # the check and the submit are one step
        active = await session.scalar(select(Job.id).where(
            Job.project_id == project_id, Job.kind == "scan", Job.status.in_(("queued", "running"))).limit(1))
        if active is not None:
            raise HTTPException(status_code=409, detail=f"a scan of this project is already in progress: {active}")
        job_id = await runner.submit("scan", {"project_id": project_id, "upload_id": upload_row.id},
                                     scan_job(project_id, upload_row.id, request.app.state.knowledge),
                                     project_id=project_id)
    return JobCreated(job_id=job_id)


@router.get("/projects/{project_id}/report", response_model=ReportOut)
async def report(project_id: str, session: SessionDep) -> ReportOut:
    await _project(session, project_id)
    row = await session.scalar(select(ScanReport).where(ScanReport.project_id == project_id)
                               .order_by(ScanReport.created_at.desc()).limit(1))
    if row is None:
        raise HTTPException(status_code=404, detail="not scanned yet")
    return ReportOut(**{**row.report, "upload_id": row.upload_id, "job_id": row.job_id,
                        "synthetic": row.synthetic, "sql_files": row.sql_files, "created_at": row.created_at})


@router.get("/projects/{project_id}/processes", response_model=list[ProcessSummary])
async def list_processes(project_id: str, session: SessionDep) -> list[ProcessSummary]:
    await _project(session, project_id)
    activity_count = func.coalesce(func.jsonb_array_length(Process.ir["activities"]), 0)
    q = (select(Process, Application.app_key, Domain.name, activity_count)
         .join(Application, Application.id == Process.application_id)
         .outerjoin(Domain, Domain.id == Process.domain_id)
         .where(Process.project_id == project_id)
         .order_by(Process.ir_id))
    out = []
    for p, app_key, domain_name, n in (await session.execute(q)).all():
        out.append(ProcessSummary(
            id=p.id, ir_id=p.ir_id, name=p.name, application=app_key, module=p.module, package=p.package,
            dialect=p.dialect, source_path=p.source_path, starter_kind=p.starter_kind, spec_hash=p.spec_hash,
            complexity_score=p.complexity_score, complexity_band=p.complexity_band, domain_id=p.domain_id,
            domain_name=domain_name, migration_status=p.migration_status, activity_count=n))
    return out
