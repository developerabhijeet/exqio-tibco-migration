"""Engine objects rebuilt from the API tables for the design, build and verify jobs.

The scan stores IR with paths relative to the upload's scan root (upload-independent hashes).
The engine's generators and the parity evaluator read files next to the estate (``Database/*.sql``,
``Stubs/*.json``, schema files), so :func:`engine_app` rebuilds the ``Application`` with every
process ``source_path`` and contract ``path`` made absolute again under the stored upload.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.app.models import Application, Process, Upload
from api.app.services import gates as gate_svc
from api.app.services.scan import upload_scan_root
from engine.analysis.models import Citation
from engine.analysis.models import Finding as EngineFinding
from engine.analysis.models import Rule as EngineRule
from engine.ir.models import Application as IRApplication
from engine.ir.models import Process as IRProcess


class NotFound(Exception):
    """A row the request names does not exist (HTTP 404)."""


def _absolute(path: str, root: Path) -> str:
    if not path:
        return path
    p = Path(path)
    return str(p if p.is_absolute() else (root / p))


async def scan_root_of(session: AsyncSession, proc: Process) -> Path | None:
    upload = await session.get(Upload, proc.upload_id) if proc.upload_id else None
    return upload_scan_root(upload).resolve() if upload is not None else None


async def engine_app(session: AsyncSession, proc: Process) -> IRApplication:
    """The engine ``Application`` of ``proc`` with all its sibling processes, paths absolute."""
    app_row = await session.get(Application, proc.application_id)
    siblings = (await session.execute(
        select(Process.ir).where(Process.application_id == proc.application_id))).scalars().all()
    data: dict[str, Any] = {**(app_row.ir if app_row else {}), "processes": list(siblings)}
    app = IRApplication.model_validate(data)
    root = await scan_root_of(session, proc)
    if root is not None:
        for p in app.processes:
            p.source_path = _absolute(p.source_path, root)
        for c in app.contracts:
            c.path = _absolute(c.path, root)
    return app


def engine_process(app: IRApplication, proc: Process) -> IRProcess:
    ir_id = (proc.ir or {}).get("id")
    found = next((p for p in app.processes if p.id == ir_id), None)
    return found or IRProcess.model_validate(proc.ir)


def _citations(raw: list[dict[str, Any]] | None) -> list[Citation]:
    out = []
    for c in raw or []:
        try:
            out.append(Citation(activity_id=c.get("activity_id") or "", kind=c.get("kind") or "expression",
                                text=c.get("text") or ""))
        except ValueError:
            continue
    return out


async def engine_rules(session: AsyncSession, process_id: str) -> list[EngineRule]:
    """API rule rows as engine rules (``id`` is the rule key, ``R1``)."""
    out = []
    for r in await gate_svc.load_rules(session, process_id):
        out.append(EngineRule(id=r.rule_key, text=r.text, citations=_citations(r.citations),
                              confidence=float(r.confidence or 0.0), status=r.status, owner=r.owner,
                              memory_refs=list(r.memory_refs or [])))
    return out


async def engine_findings(session: AsyncSession, process_id: str) -> list[EngineFinding]:
    """API finding rows as engine findings (``id`` is the finding key)."""
    out = []
    for f in await gate_svc.load_findings(session, process_id):
        out.append(EngineFinding(id=f.finding_key, severity=f.severity, text=f.text,
                                 citations=_citations(f.citations), source=f.source,
                                 decision=f.decision, note=f.note, business_impact=f.business_impact))
    return out


async def active_job(session: AsyncSession, kind: str, *, process_id: str | None = None,
                     key: str | None = None, value: str | None = None) -> str | None:
    """The id of a queued or running job of ``kind`` for a process (or with ``payload[key] == value``)."""
    from api.app.models import Job

    q = select(Job.id).where(Job.kind == kind, Job.status.in_(("queued", "running")))
    if process_id is not None:
        q = q.where(Job.process_id == process_id)
    if key is not None:
        q = q.where(Job.payload[key].astext == value)
    return await session.scalar(q.limit(1))
