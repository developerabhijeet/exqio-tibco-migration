"""History and tokenomics (design spec 2.9): every job, every model call and every human decision of
a project on one timeline, with totals by phase and per workflow (and per complexity band).

Phases, by job kind: ``discovery`` (scan, discover, analyse, and Ask calls made outside a job),
``design`` (design), ``build`` (build, generate, test, infra), ``verify`` (verify, plan) and ``demo``
(provider evaluations and anything else not on the migration path).
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import false, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.app.models import AuditLog, Gate, Job, ModelCall, Process

PHASES = ("demo", "discovery", "design", "build", "verify")
PHASE_OF_JOB = {"scan": "discovery", "discover": "discovery", "analyse": "discovery", "design": "design",
                "build": "build", "generate": "build", "test": "build", "infra": "build",
                "verify": "verify", "plan": "verify", "eval": "demo"}


def _zero() -> dict[str, Any]:
    return {"jobs": 0, "model_calls": 0, "failed_calls": 0, "tokens_in": 0, "tokens_out": 0, "tokens_cached": 0,
            "cost_usd": 0.0, "latency_ms": 0.0, "decisions": 0}


def _add_call(t: dict[str, Any], c: ModelCall) -> None:
    t["model_calls"] += 1
    t["failed_calls"] += 0 if c.ok else 1
    t["tokens_in"] += c.tokens_in or 0
    t["tokens_out"] += c.tokens_out or 0
    t["tokens_cached"] += c.tokens_cached or 0
    t["cost_usd"] = round(t["cost_usd"] + (c.cost_usd or 0.0), 6)
    t["latency_ms"] += c.latency_ms or 0.0


def _iso(ts: Any) -> str | None:
    return ts.isoformat() if ts is not None else None


async def history(session: AsyncSession, project_id: str) -> dict[str, Any]:
    jobs = (await session.execute(select(Job).where(Job.project_id == project_id))).scalars().all()
    job_ids = [j.id for j in jobs]
    calls_q = select(ModelCall).where(or_(ModelCall.project_id == project_id,
                                          ModelCall.job_id.in_(job_ids) if job_ids else false()))
    calls = (await session.execute(calls_q)).scalars().all()
    decisions = (await session.execute(select(AuditLog).where(AuditLog.project_id == project_id,
                                                              AuditLog.actor_kind == "user"))).scalars().all()
    procs = {p.id: p for p in (await session.execute(select(Process).where(Process.project_id == project_id)))
             .scalars()}
    gates = (await session.execute(select(Gate).where(Gate.process_id.in_(list(procs)) if procs else false())))\
        .scalars().all()
    job_phase = {j.id: PHASE_OF_JOB.get(j.kind, "demo") for j in jobs}

    def call_phase(c: ModelCall) -> str:
        if c.job_id and c.job_id in job_phase:
            return job_phase[c.job_id]
        return "discovery" if c.stage == "ask" else "demo"

    def name(pid: str | None) -> str | None:
        return procs[pid].name if pid and pid in procs else None

    timeline: list[dict[str, Any]] = []
    for j in jobs:
        dur = (j.finished_at - j.started_at).total_seconds() * 1000 if j.finished_at and j.started_at else None
        timeline.append({"ts": _iso(j.created_at), "kind": "job", "id": j.id, "job_kind": j.kind,
                         "status": j.status, "stage": j.stage, "phase": job_phase[j.id], "process_id": j.process_id,
                         "process_name": name(j.process_id), "duration_ms": dur, "error": j.error})
    for c in calls:
        timeline.append({"ts": _iso(c.created_at), "kind": "model_call", "id": c.id, "job_id": c.job_id,
                         "phase": call_phase(c), "stage": c.stage, "agent": c.stage, "provider": c.provider,
                         "model": c.model, "tier": c.tier, "tokens_in": c.tokens_in, "tokens_out": c.tokens_out,
                         "tokens_cached": c.tokens_cached, "cost_usd": c.cost_usd, "latency_ms": c.latency_ms,
                         "ok": c.ok, "error": c.error, "fallback_from": c.fallback_from,
                         "process_id": c.process_id, "process_name": name(c.process_id)})
    for d in decisions:
        timeline.append({"ts": _iso(d.ts), "kind": "decision", "id": d.id, "actor": d.actor, "action": d.action,
                         "gate": d.gate, "process_id": d.process_id, "process_name": name(d.process_id),
                         "detail": d.detail})
    timeline.sort(key=lambda e: (e["ts"] or "", e["kind"]))

    by_phase = {p: _zero() for p in PHASES}
    total = _zero()
    per_proc: dict[str, dict[str, Any]] = {}
    for j in jobs:
        by_phase[job_phase[j.id]]["jobs"] += 1
        total["jobs"] += 1
        if j.process_id in procs:
            per_proc.setdefault(j.process_id, _zero())["jobs"] += 1
    for c in calls:
        _add_call(by_phase[call_phase(c)], c)
        _add_call(total, c)
        if c.process_id in procs:
            _add_call(per_proc.setdefault(c.process_id, _zero()), c)
    for d in decisions:
        total["decisions"] += 1
        if d.process_id in procs:
            per_proc.setdefault(d.process_id, _zero())["decisions"] += 1

    gate_state: dict[str, dict[str, str]] = {}
    for g in gates:
        gate_state.setdefault(g.process_id, {})[g.gate] = g.state
    workflows = []
    by_band: dict[str, dict[str, Any]] = {}
    for pid, p in sorted(procs.items(), key=lambda kv: kv[1].name):
        t = per_proc.get(pid, _zero())
        band = p.complexity_band or "unknown"
        workflows.append({"process_id": pid, "name": p.name, "complexity_band": band,
                          "complexity_score": p.complexity_score, "gates": gate_state.get(pid, {}), **t})
        agg = by_band.setdefault(band, {**_zero(), "workflows": 0})
        agg["workflows"] += 1
        for k in ("jobs", "model_calls", "failed_calls", "tokens_in", "tokens_out", "tokens_cached", "decisions"):
            agg[k] += t[k]
        agg["cost_usd"] = round(agg["cost_usd"] + t["cost_usd"], 6)
        agg["latency_ms"] += t["latency_ms"]
    cached = total["tokens_cached"]
    return {"project_id": project_id, "timeline": timeline,
            "totals": {"all": total, "by_phase": by_phase, "by_band": by_band,
                       "cache_hit_rate": (cached / total["tokens_in"]) if total["tokens_in"] else None},
            "workflows": workflows, "phases": list(PHASES)}
