"""Expert rules of a project: import a CSV (validated, previewed with ``dry_run``), load the bundled
GMF set, list (with the phases each rule was applied in), enable or disable one, clear them all.

Writes need a persona that may act at Upload (sme, architect, engineer, qa, ops; admin always);
a viewer reads."""
from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from starlette.datastructures import UploadFile

from api.app.auth.roles import Principal
from api.app.deps import SessionDep, require_roles
from api.app.models import AuditLog, ExpertRule, Project
from api.app.services import expert_rules as svc
from engine.agents import expert_rules as er

router = APIRouter(tags=["expert-rules"])

WRITERS = ("sme", "architect", "engineer", "qa", "ops")
WriterDep = Annotated[Principal, Depends(require_roles(*WRITERS))]


class RulePatch(BaseModel):
    enabled: bool


async def _project(session: SessionDep, project_id: str) -> Project:
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    return project


async def _import(session: SessionDep, project_id: str, data: bytes, *, mode: str, dry_run: bool,
                  source: str, actor: str, filename: str) -> dict[str, Any]:
    rep = er.parse_csv(data)
    if rep.fatal:
        raise HTTPException(status_code=422, detail={"message": rep.fatal, **rep.as_dict()})
    if not rep.rows:
        raise HTTPException(status_code=422, detail={"message": "no valid rules in the file", **rep.as_dict()})
    existing = set((await session.scalars(select(ExpertRule.rule_id)
                                          .where(ExpertRule.project_id == project_id))).all())
    ids = [r["rule_id"] for r in rep.rows]
    if mode == "replace":
        imported, updated = len(ids), 0
    else:
        updated = sum(1 for i in ids if i in existing)
        imported = len(ids) - updated
    if not dry_run:
        imported, updated = await svc.import_rows(session, project_id, rep.rows, mode=mode, source=source,
                                                  actor=actor)
        session.add(AuditLog(project_id=project_id, actor=actor, actor_kind="user",
                             action="expert rules imported",
                             detail={"file": filename, "mode": mode, "source": source, "imported": imported,
                                     "updated": updated, "skipped": len(rep.errors)}))
        await session.commit()
    skipped_ids = {e["row"] for e in rep.errors}
    return {"dry_run": dry_run, "mode": mode, "source": source, "total": rep.total, "imported": imported,
            "updated": updated, "skipped": len(skipped_ids), "removed": len(existing - set(ids))
            if mode == "replace" else 0, "errors": rep.errors, "warnings": rep.warnings,
            "rule_ids": ids}


@router.post("/projects/{project_id}/expert-rules/import")
async def import_rules(project_id: str, request: Request, session: SessionDep, principal: WriterDep,
                       mode: Literal["merge", "replace"] = "merge", dry_run: bool = False) -> dict[str, Any]:
    """Multipart field ``file``: the CSV (at most 2 MB). Rows with errors are skipped and listed."""
    await _project(session, project_id)
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > er.MAX_BYTES + 64 * 1024:
        raise HTTPException(status_code=413, detail="the file is larger than 2 MB")
    form = await request.form()
    file = form.get("file")
    if not isinstance(file, UploadFile):
        raise HTTPException(status_code=422, detail={"message": "multipart field 'file' (a CSV) is required"})
    data = await file.read(er.MAX_BYTES + 1)
    if len(data) > er.MAX_BYTES:
        raise HTTPException(status_code=413, detail="the file is larger than 2 MB")
    return await _import(session, project_id, data, mode=mode, dry_run=dry_run, source="upload",
                         actor=principal.actor, filename=file.filename or "rules.csv")


@router.post("/projects/{project_id}/expert-rules/defaults")
async def load_defaults(project_id: str, session: SessionDep, principal: WriterDep,
                        mode: Literal["merge", "replace"] = "merge", dry_run: bool = False) -> dict[str, Any]:
    """The bundled GMF TIBCO expert rules (``samples/rules/tibco-expert-rules.csv``)."""
    await _project(session, project_id)
    return await _import(session, project_id, svc.default_csv(), mode=mode, dry_run=dry_run, source="default",
                         actor=principal.actor, filename=svc.DEFAULT_CSV.name)


@router.get("/projects/{project_id}/expert-rules")
async def list_rules(project_id: str, session: SessionDep) -> dict[str, Any]:
    await _project(session, project_id)
    rows = [svc.rule_out(r) for r in await svc.list_rules(session, project_id)]
    by_severity = {s: sum(1 for r in rows if r["severity"] == s) for s in er.SEVERITIES}
    return {"rules": rows, "total": len(rows), "enabled": sum(1 for r in rows if r["enabled"]),
            "by_severity": by_severity, "phases": list(er.PHASES)}


@router.patch("/projects/{project_id}/expert-rules/{rule_id}")
async def patch_rule(project_id: str, rule_id: str, body: RulePatch, session: SessionDep,
                     principal: WriterDep) -> dict[str, Any]:
    row = await session.scalar(select(ExpertRule).where(ExpertRule.project_id == project_id,
                                                        ExpertRule.rule_id == rule_id))
    if row is None:
        raise HTTPException(status_code=404, detail="expert rule not found")
    if row.enabled != body.enabled:
        row.enabled = body.enabled
        session.add(AuditLog(project_id=project_id, actor=principal.actor, actor_kind="user",
                             action="expert rule " + ("enabled" if body.enabled else "disabled"),
                             detail={"rule_id": rule_id}))
        await session.commit()
        await session.refresh(row)
    return svc.rule_out(row)


@router.delete("/projects/{project_id}/expert-rules")
async def clear_rules(project_id: str, session: SessionDep, principal: WriterDep) -> dict[str, Any]:
    await _project(session, project_id)
    n = await session.scalar(select(func.count()).select_from(ExpertRule)
                             .where(ExpertRule.project_id == project_id)) or 0
    await session.execute(delete(ExpertRule).where(ExpertRule.project_id == project_id))
    session.add(AuditLog(project_id=project_id, actor=principal.actor, actor_kind="user",
                         action="expert rules cleared", detail={"removed": n}))
    await session.commit()
    return {"removed": n}


@router.get("/expert-rules/template.csv")
async def template() -> Response:
    return Response(er.template_csv(), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="expert-rules-template.csv"'})
