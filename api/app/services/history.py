"""History and tokenomics (design spec 2.9): every job, every model call and every human decision of
a project on one timeline, with totals by phase and per workflow (and per complexity band).

Tokenomics also rolls the same calls up two other ways, for the question "what does each agent
cost?": ``by_agent`` is one row per agent identity (the ``stage`` on the call: analyst, risk,
challenger, designer, builder, ...), and ``sessions`` is one row per job with its own per-agent
split, so a single analyse or build run can be priced on its own. Calls made outside a job (Ask)
are collected into one session with no job id.

Phases, by job kind: ``discovery`` (scan, discover, analyse, and Ask calls made outside a job),
``design`` (design), ``build`` (build, generate, test, infra), ``verify`` (verify, plan) and ``platform``
(provider evaluations and anything else not on the migration path).
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import false, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.app.models import AuditLog, Gate, Job, ModelCall, Process

# "platform" is the bucket for work outside the four wizard stages (evaluation runs, and any
# model call not tied to a stage job).
PHASES = ("platform", "discovery", "design", "build", "verify")
PHASE_OF_JOB = {"scan": "discovery", "discover": "discovery", "analyse": "discovery", "design": "design",
                "build": "build", "generate": "build", "test": "build", "infra": "build",
                "verify": "verify", "plan": "verify", "eval": "platform"}


def _zero() -> dict[str, Any]:
    return {"jobs": 0, "model_calls": 0, "failed_calls": 0, "tokens_in": 0, "tokens_out": 0, "tokens_cached": 0,
            "cost_usd": 0.0, "latency_ms": 0.0, "decisions": 0}


def _zero_agent(agent: str) -> dict[str, Any]:
    return {"agent": agent, "model_calls": 0, "failed_calls": 0, "fallbacks": 0, "tokens_in": 0,
            "tokens_out": 0, "tokens_cached": 0, "cost_usd": 0.0, "latency_ms": 0.0, "models": [],
            "providers": [], "tiers": []}


def _add_agent_call(t: dict[str, Any], c: ModelCall) -> None:
    """One call into an agent row; the model, provider and tier lists stay in first-seen order so a
    row shows every model that answered for that agent, not just the last one."""
    t["model_calls"] += 1
    t["failed_calls"] += 0 if c.ok else 1
    t["fallbacks"] += 1 if c.fallback_from else 0
    t["tokens_in"] += c.tokens_in or 0
    t["tokens_out"] += c.tokens_out or 0
    t["tokens_cached"] += c.tokens_cached or 0
    t["cost_usd"] = round(t["cost_usd"] + (c.cost_usd or 0.0), 6)
    t["latency_ms"] += c.latency_ms or 0.0
    for key, value in (("models", c.model), ("providers", c.provider), ("tiers", c.tier)):
        if value and value not in t[key]:
            t[key].append(value)


def _agent_rows(calls: list[ModelCall]) -> list[dict[str, Any]]:
    """Agent rows, dearest first: cost is the question the table is asked to answer."""
    rows: dict[str, dict[str, Any]] = {}
    for c in calls:
        _add_agent_call(rows.setdefault(c.stage or "unknown", _zero_agent(c.stage or "unknown")), c)
    for r in rows.values():
        r["latency_ms_avg"] = round(r["latency_ms"] / r["model_calls"], 1) if r["model_calls"] else 0.0
    return sorted(rows.values(), key=lambda r: (-r["cost_usd"], -r["model_calls"], r["agent"]))


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
    job_phase = {j.id: PHASE_OF_JOB.get(j.kind, "platform") for j in jobs}

    def call_phase(c: ModelCall) -> str:
        if c.job_id and c.job_id in job_phase:
            return job_phase[c.job_id]
        return "discovery" if c.stage == "ask" else "platform"

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
    # Tokenomics: the same calls by agent identity, and one session per job with its own split.
    by_job: dict[str | None, list[ModelCall]] = {}
    for c in calls:
        by_job.setdefault(c.job_id if c.job_id in job_phase else None, []).append(c)
    job_by_id = {j.id: j for j in jobs}
    sessions: list[dict[str, Any]] = []
    for jid, jcalls in by_job.items():
        t = _zero()
        for c in jcalls:
            _add_call(t, c)
        j = job_by_id.get(jid) if jid else None
        dur = (j.finished_at - j.started_at).total_seconds() * 1000 if j and j.finished_at and j.started_at else None
        sessions.append({
            "job_id": jid,
            # Ask answers outside a job are a session of their own, so every call is in exactly one
            "job_kind": j.kind if j else "ask",
            "phase": job_phase.get(jid, "discovery") if jid else "discovery",
            "status": j.status if j else None,
            "process_id": j.process_id if j else None,
            "process_name": name(j.process_id) if j else None,
            "started_at": _iso(j.started_at or j.created_at) if j else _iso(min(c.created_at for c in jcalls)),
            "finished_at": _iso(j.finished_at) if j else _iso(max(c.created_at for c in jcalls)),
            "duration_ms": dur,
            "totals": t,
            "by_agent": _agent_rows(jcalls),
        })
    sessions.sort(key=lambda e: (e["started_at"] or "", e["job_kind"]), reverse=True)

    cached = total["tokens_cached"]
    return {"project_id": project_id, "timeline": timeline,
            "totals": {"all": total, "by_phase": by_phase, "by_band": by_band,
                       "cache_hit_rate": (cached / total["tokens_in"]) if total["tokens_in"] else None},
            "by_agent": _agent_rows(calls), "sessions": sessions,
            "workflows": workflows, "phases": list(PHASES)}


async def reset_tokenomics(session: AsyncSession, project_id: str, user: str) -> dict[str, Any]:
    """Drop this project's model calls, so the Tokenomics screen counts from zero again.

    The calls are a running meter, not evidence: a gate record names the model that answered and
    the design and build hashes it produced, and none of that lives here. What does live here is a
    number that only ever grows, so a project re-run a dozen times while the pipeline was being
    fixed reads as the cost of migrating it. Clearing the meter is how that number becomes true
    again.

    The reset is itself audited, with the totals it discarded, because ``audit_log`` has no foreign
    keys and survives the rows it describes. Nothing else is touched: the jobs, the gates and the
    builds stay exactly as they were.
    """
    jobs = (await session.execute(select(Job.id).where(Job.project_id == project_id))).scalars().all()
    where = or_(ModelCall.project_id == project_id,
                ModelCall.job_id.in_(jobs) if jobs else false())
    calls = (await session.execute(select(ModelCall).where(where))).scalars().all()
    discarded = {"model_calls": len(calls),
                 "tokens_in": sum(c.tokens_in for c in calls),
                 "tokens_out": sum(c.tokens_out for c in calls),
                 "cost_usd": round(sum(c.cost_usd or 0.0 for c in calls), 6)}
    for call in calls:
        await session.delete(call)
    session.add(AuditLog(project_id=project_id, actor=user, actor_kind="user",
                         action="tokenomics reset", detail=discarded))
    await session.commit()
    return discarded
