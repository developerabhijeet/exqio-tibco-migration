"""Domains and the estate graph for tab 2 (Discover).

Code groups processes into domains during the scan; the AI only names them (the ``discover``
job). The SME can rename a domain or merge one into another: both are stored on the domain
row (``renamed_by``, ``merged_into``), audited at project level, and recorded as migration
memory. A merged domain is hidden from the list and a rescan keeps its processes in the domain
it was merged into.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.app.config import Settings
from api.app.jobs.runner import JobContext
from api.app.models import Domain, GraphEdge, GraphNode, Process
from api.app.services import gates as gate_svc
from api.app.services.knowledge import Knowledge, KnowledgeUnavailable
from api.app.services.providers import build_provider, model_call_recorder
from engine.agents.domain_namer import name_domains
from engine.domains.models import DomainCandidate, Evidence

log = logging.getLogger(__name__)


class Conflict(Exception):
    """HTTP 409."""


async def _processes_by_domain(session: AsyncSession, project_id: str) -> dict[str, list[Process]]:
    rows = (await session.execute(select(Process).where(Process.project_id == project_id)
                                  .order_by(Process.ir_id))).scalars().all()
    out: dict[str, list[Process]] = {}
    for p in rows:
        if p.domain_id:
            out.setdefault(p.domain_id, []).append(p)
    return out


def domain_out(d: Domain, procs: list[Process]) -> dict[str, Any]:
    return {"id": d.id, "key": d.key, "name": d.name, "suggested_name": d.suggested_name,
            "process_ids": [p.id for p in procs], "ir_process_ids": [p.ir_id for p in procs],
            "evidence": d.evidence or [], "renamed_by": d.renamed_by}


async def list_domains(session: AsyncSession, project_id: str) -> list[dict[str, Any]]:
    rows = (await session.execute(select(Domain).where(Domain.project_id == project_id,
                                                       Domain.merged_into.is_(None))
                                  .order_by(Domain.created_at, Domain.key))).scalars().all()
    by_domain = await _processes_by_domain(session, project_id)
    return [domain_out(d, by_domain.get(d.id, [])) for d in rows]


async def get_domain_out(session: AsyncSession, domain: Domain) -> dict[str, Any]:
    by_domain = await _processes_by_domain(session, domain.project_id)
    return domain_out(domain, by_domain.get(domain.id, []))


async def _remember(knowledge: Knowledge, project_id: str, text: str, context: dict[str, Any],
                    user: str) -> None:
    try:
        await knowledge.record_decision(project_id, text, context, user)
    except KnowledgeUnavailable as exc:
        log.warning("domain decision not recorded in migration memory: %s", exc)


async def rename(session: AsyncSession, knowledge: Knowledge, domain: Domain, name: str,
                 user: str) -> Domain:
    old = domain.name
    if name == old:
        return domain
    domain.name, domain.renamed_by = name, user
    gate_svc.audit(session, None, user, "domain renamed", project_id=domain.project_id,
                   detail={"domain_id": domain.id, "key": domain.key, "old_name": old, "new_name": name})
    await session.commit()
    await _remember(knowledge, domain.project_id,
                    f"SME {user} renamed the domain \"{old}\" to \"{name}\" (suggested name "
                    f"\"{domain.suggested_name}\").",
                    {"domain_id": domain.id, "domain_key": domain.key, "old_name": old, "new_name": name,
                     "suggested_name": domain.suggested_name, "user": user, "decision": "renamed"},
                    user)
    return domain


async def merge(session: AsyncSession, knowledge: Knowledge, source: Domain, target: Domain,
                user: str) -> Domain:
    """Merge ``source`` into ``target``: its processes move, ``target`` survives."""
    if source.id == target.id:
        raise Conflict("a domain cannot be merged into itself")
    if source.project_id != target.project_id:
        raise Conflict("domains belong to different projects")
    if source.merged_into or target.merged_into:
        raise Conflict("domain already merged")
    procs = (await session.execute(select(Process).where(Process.domain_id == source.id))).scalars().all()
    for p in procs:
        p.domain_id = target.id
    target.process_ids = list(dict.fromkeys([*(target.process_ids or []), *(source.process_ids or [])]))
    target.evidence = [*(target.evidence or []), *(source.evidence or [])]
    source.merged_into = target.id
    gate_svc.audit(session, None, user, "domain merged", project_id=target.project_id,
                   detail={"source_id": source.id, "source_name": source.name, "target_id": target.id,
                           "target_name": target.name, "processes": [p.ir_id for p in procs]})
    await session.commit()
    await _remember(knowledge, target.project_id,
                    f"SME {user} merged the domain \"{source.name}\" into \"{target.name}\".",
                    {"source_id": source.id, "source_name": source.name, "target_id": target.id,
                     "target_name": target.name, "user": user, "decision": "merged"}, user)
    return target


async def graph_view(session: AsyncSession, project_id: str) -> dict[str, Any]:
    """The estate graph (Cytoscape) with process nodes carrying the API process id, complexity
    band and migration status for the UI filters."""
    procs = {f"process:{p.ir_id}": p for p in (await session.execute(
        select(Process).where(Process.project_id == project_id))).scalars()}
    nodes = []
    for n in (await session.execute(select(GraphNode).where(GraphNode.project_id == project_id)
                                    .order_by(GraphNode.id))).scalars():
        attrs = dict(n.attrs or {})
        p = procs.get(n.node_id)
        if n.kind == "process" and p is not None:
            attrs.update(process_id=p.id, complexity_band=p.complexity_band,
                         complexity_score=p.complexity_score, migration_status=p.migration_status,
                         domain_id=p.domain_id)
        nodes.append({"id": n.node_id, "kind": n.kind, "name": n.name, "attrs": attrs})
    edges = [{"src": e.src, "dst": e.dst, "kind": e.kind, "attrs": e.attrs or {}}
             for e in (await session.execute(select(GraphEdge).where(GraphEdge.project_id == project_id)
                                             .order_by(GraphEdge.id))).scalars()]
    return {"nodes": nodes, "edges": edges}


# ------------------------------------------------------------------ AI naming job
def naming_job(project_id: str, settings: Settings):
    async def run(ctx: JobContext) -> dict[str, Any]:
        return await run_naming(ctx, project_id, settings)

    return run


async def run_naming(ctx: JobContext, project_id: str, settings: Settings) -> dict[str, Any]:
    await ctx.stage("name")
    async with ctx.sessionmaker() as s:
        rows = (await s.execute(select(Domain).where(Domain.project_id == project_id,
                                                     Domain.merged_into.is_(None))
                                .order_by(Domain.created_at, Domain.key))).scalars().all()
        by_domain = await _processes_by_domain(s, project_id)
        candidates = [DomainCandidate(id=d.key, process_ids=[p.ir_id for p in by_domain.get(d.id, [])],
                                      evidence=[Evidence.model_validate(e) for e in d.evidence or []],
                                      suggested_name=d.suggested_name) for d in rows]
    choice = build_provider(settings)
    if choice.provider is None:
        await ctx.log(f"skipped: code-only naming: {choice.reason}", tag="ai")
        return {"named": 0, "ai_available": False, "ai_reason": choice.reason}
    choice.provider.recorder = model_call_recorder(
        ctx.sessionmaker, job_id=ctx.job_id, project_id=project_id, process_id=None,
        primary_name=choice.provider.primary.name)
    named = await name_domains(candidates, choice.provider)
    changed = 0
    async with ctx.sessionmaker() as s:
        rows = {d.key: d for d in (await s.execute(select(Domain).where(Domain.project_id == project_id)))
                .scalars()}
        for c in named:
            row = rows.get(c.id)
            if row is None or row.suggested_name == c.suggested_name:
                continue
            await ctx.log(f"{row.suggested_name} -> {c.suggested_name}", tag="ai")
            row.suggested_name = c.suggested_name
            if row.renamed_by is None:
                row.name = c.suggested_name
            changed += 1
        await s.commit()
    await ctx.log(f"{changed} of {len(candidates)} domain name(s) changed by the model", tag="ai")
    return {"named": changed, "ai_available": True, "ai_reason": None}
