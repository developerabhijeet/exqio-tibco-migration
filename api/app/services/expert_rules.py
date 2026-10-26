"""Expert rules of a project: import from CSV (merge or replace), the bundled GMF set, and the job
scope that puts the enabled rules into the phase prompts and records which ones were used."""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.app.models import AnalysisRun, Build, ExpertRule, Job, Process
from engine.agents import expert_rules as er

log = logging.getLogger(__name__)

DEFAULT_CSV = Path(__file__).resolve().parents[3] / "samples" / "rules" / "tibco-expert-rules.csv"
APPLIED_KEEP = 20
MODES = ("merge", "replace")


def rule_out(r: ExpertRule) -> dict[str, Any]:
    applied = list(r.applied or [])
    phases = [p for p in er.PHASES if any(a.get("phase") == p for a in applied)]
    return {
        **{f: getattr(r, f) for f in er.FIELDS},
        "id": r.id, "enabled": r.enabled, "source": r.source, "uploaded_by": r.uploaded_by,
        "phases": list(er.phases_for(r.primary_agent)), "applied_in": phases,
        "last_applied": applied[-1] if applied else None,
        "created_at": r.created_at, "updated_at": r.updated_at,
    }


async def list_rules(session: AsyncSession, project_id: str) -> list[ExpertRule]:
    return list((await session.scalars(select(ExpertRule).where(ExpertRule.project_id == project_id)
                                       .order_by(ExpertRule.rule_id))).all())


async def import_rows(session: AsyncSession, project_id: str, rows: list[dict[str, str]], *, mode: str,
                      source: str, actor: str | None) -> tuple[int, int]:
    """Upsert ``rows`` by Rule ID (``replace``: the project's rules are removed first). Returns
    ``(imported, updated)``. A re-imported rule keeps its ``enabled`` flag and its usage."""
    if mode == "replace":
        await session.execute(delete(ExpertRule).where(ExpertRule.project_id == project_id))
        existing: dict[str, ExpertRule] = {}
    else:
        existing = {r.rule_id: r for r in await list_rules(session, project_id)}
    imported = updated = 0
    for row in rows:
        values = {f: row.get(f, "") for f in er.FIELDS}
        current = existing.get(row["rule_id"])
        if current is None:
            session.add(ExpertRule(project_id=project_id, source=source, uploaded_by=actor, enabled=True,
                                   applied=[], **values))
            imported += 1
        else:
            for k, v in values.items():
                setattr(current, k, v)
            current.source, current.uploaded_by = source, actor
            updated += 1
    await session.flush()
    return imported, updated


def default_csv() -> bytes:
    return DEFAULT_CSV.read_bytes()


# ------------------------------------------------------------------ the job scope
async def _project_of(s: AsyncSession, job_id: str, payload: dict[str, Any]) -> str | None:
    if payload.get("project_id"):
        return str(payload["project_id"])
    pid = await s.scalar(select(Job.project_id).where(Job.id == job_id))
    if pid:
        return pid
    process_id = payload.get("process_id")
    if not process_id and payload.get("build_id"):
        process_id = await s.scalar(select(Build.process_id).where(Build.id == payload["build_id"]))
    if process_id:
        return await s.scalar(select(Process.project_id).where(Process.id == process_id))
    return None


@asynccontextmanager
async def job_scope(sessionmaker: async_sessionmaker[AsyncSession], job_id: str, payload: dict[str, Any],
                    kind: str) -> AsyncIterator[er.ActiveRules | None]:
    """For the duration of a phase job: the enabled rules of its project in
    :data:`engine.agents.expert_rules.ACTIVE`. Afterwards each rule a prompt carried gets a usage
    entry, and an analysis job's summary names the rules it applied. Bookkeeping failures are logged,
    never raised: rules must not break a phase."""
    active: er.ActiveRules | None = None
    project_id: str | None = None
    try:
        async with sessionmaker() as s:
            project_id = await _project_of(s, job_id, payload)
            if project_id:
                rows = (await s.scalars(select(ExpertRule).where(ExpertRule.project_id == project_id,
                                                                 ExpertRule.enabled.is_(True)))).all()
                if rows:
                    active = er.ActiveRules([{f: getattr(r, f) for f in er.FIELDS} | {"enabled": True}
                                             for r in rows])
    except Exception:
        log.exception("expert rules: cannot load the rules for job %s", job_id)
    token = er.ACTIVE.set(active)
    try:
        yield active
    finally:
        er.ACTIVE.reset(token)
        if active is not None and active.used and project_id:
            try:
                await record_applied(sessionmaker, project_id, active, ref=job_id, kind=kind,
                                     process_id=payload.get("process_id"))
            except Exception:
                log.exception("expert rules: cannot record usage for job %s", job_id)


async def record_applied(sessionmaker: async_sessionmaker[AsyncSession], project_id: str,
                         active: er.ActiveRules, *, ref: str, kind: str, process_id: str | None) -> None:
    at = datetime.now(UTC).isoformat()
    by_rule: dict[str, list[str]] = {}
    for phase, ids in active.used.items():
        for rid in ids:
            by_rule.setdefault(rid, []).append(phase)
    async with sessionmaker() as s:
        rows = (await s.scalars(select(ExpertRule).where(ExpertRule.project_id == project_id,
                                                         ExpertRule.rule_id.in_(by_rule)))).all()
        for r in rows:
            entries = [{"phase": p, "at": at, "ref": ref, "job": kind} for p in by_rule[r.rule_id]]
            r.applied = [*(r.applied or []), *entries][-APPLIED_KEEP:]
        analysis_ids = sorted(active.used.get("analysis", set()))
        if kind == "analyse" and process_id and analysis_ids:
            run = await s.scalar(select(AnalysisRun).where(AnalysisRun.process_id == process_id)
                                 .order_by(AnalysisRun.created_at.desc()).limit(1))
            if run is not None:
                discovery = sorted(r.rule_id for r in rows if r.rule_id in analysis_ids
                                   and "discover" in er.phases_for(r.primary_agent))
                run.summary = {**(run.summary or {}), "expert_rules": {
                    "applied": analysis_ids, "phase": "analysis", "discovery_checked": discovery,
                    "job_id": ref}}
        await s.commit()
