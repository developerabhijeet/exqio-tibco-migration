"""Iterations of a project (sub-project 6, spec section 1): numbering, the implicit iteration 1, the
process's current iteration, opening one (superseding an open one) and its status.

The counter is per project: ``n = max + 1`` under a transaction-level advisory lock on
``iteration:<project_id>``, so two concurrent opens never take one number (the unique
``(project_id, n)`` backs it). Iteration 1 (``kind = initial``, naming no process, so it holds every
process that was never re-entered) is created the first time anything reads or writes iterations
for a project. Functions here flush; the caller commits (which also releases the lock).
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import cast, func, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from api.app.models import Iteration, Job, Process, Project
from api.app.services import gates as gate_svc
from engine.agents.provider import mask_sensitive

ITERATION_STATUSES: tuple[str, ...] = ("open", "designing", "building", "verifying", "deploying", "closed",
                                       "abandoned")
#: An iteration in one of these is the one a process's phase jobs belong to.
ACTIVE_STATUSES: tuple[str, ...] = ("open", "designing", "building", "verifying", "deploying")
ENDED_STATUSES: tuple[str, ...] = ("closed", "abandoned")
KINDS: tuple[str, ...] = ("initial", "reentry", "edits", "repo")
RESUME_PHASES: tuple[str, ...] = ("design", "build", "infra")
#: Phase jobs an open iteration cannot be superseded under.
BUSY_JOB_KINDS: tuple[str, ...] = ("design", "build")
MAX_REQUIREMENTS = 20_000
INITIAL_ACTOR = "system"
LOCK_SQL = text("SELECT pg_advisory_xact_lock(hashtext('iteration:' || :project_id))")


class IterationBusy(Exception):
    """A design or build of a process the new iteration names is queued or running (HTTP 409)."""


async def _lock(session: AsyncSession, project_id: str) -> None:
    await session.execute(LOCK_SQL, {"project_id": project_id})


def _naming(process_id: str):
    return Iteration.process_ids.op("@>")(cast([process_id], JSONB))


async def _initial(session: AsyncSession, project_id: str) -> Iteration | None:
    return await session.scalar(select(Iteration).where(Iteration.project_id == project_id, Iteration.n == 1))


async def ensure_initial(session: AsyncSession, project_id: str) -> Iteration:
    """The project's iteration 1, created (``opened_at`` = the project's ``created_at``) when absent."""
    row = await _initial(session, project_id)
    if row is not None:
        return row
    await _lock(session, project_id)
    row = await _initial(session, project_id)  # another transaction may have created it meanwhile
    if row is not None:
        return row
    created_at = await session.scalar(select(Project.created_at).where(Project.id == project_id))
    if created_at is None:
        raise LookupError(f"project {project_id} not found")
    row = Iteration(project_id=project_id, n=1, process_ids=[], kind="initial", status="open",
                    resume_phase="design", opened_by=INITIAL_ACTOR, opened_at=created_at)
    session.add(row)
    await session.flush()
    return row


async def current_iteration(session: AsyncSession, process_id: str) -> Iteration:
    """The active iteration naming the process, else the highest closed one naming it, else the
    project's iteration 1. Abandoned iterations never count."""
    process = await session.get(Process, process_id)
    if process is None:
        raise LookupError(f"process {process_id} not found")
    for statuses in (ACTIVE_STATUSES, ("closed",)):
        row = await session.scalar(
            select(Iteration).where(Iteration.project_id == process.project_id, Iteration.status.in_(statuses),
                                    _naming(process_id))
            .order_by(Iteration.n.desc()).limit(1))
        if row is not None:
            return row
    return await ensure_initial(session, process.project_id)


async def job_iteration(session: AsyncSession, payload: dict, process_id: str) -> int:
    """The iteration a phase job was stamped with on submit (``payload["iteration"]``), else the
    process's current one."""
    stamped = payload.get("iteration")
    if isinstance(stamped, int) and not isinstance(stamped, bool):
        return stamped
    return (await current_iteration(session, process_id)).n


async def open_iteration(session: AsyncSession, project_id: str, process_ids: list[str], kind: str, *,
                         actor: str, requirements: str | None = None, resume_phase: str = "design",
                         baseline_build_id: str | None = None, source: dict | None = None,
                         note: str | None = None) -> Iteration:
    """Open iteration ``max + 1`` naming ``process_ids``. An active iteration naming one of them is
    abandoned ("superseded by iteration <n>"), unless a design or build of that process is queued
    or running: then :class:`IterationBusy`, and nothing changes."""
    if kind not in KINDS or kind == "initial":
        raise ValueError(f"kind must be one of: {', '.join(k for k in KINDS if k != 'initial')}")
    if resume_phase not in RESUME_PHASES:
        raise ValueError(f"resume_phase must be one of: {', '.join(RESUME_PHASES)}")
    if requirements is not None and len(requirements) > MAX_REQUIREMENTS:
        raise ValueError(f"requirements are longer than {MAX_REQUIREMENTS} characters")
    process_ids = list(dict.fromkeys(process_ids))
    await _lock(session, project_id)
    await ensure_initial(session, project_id)

    superseded: list[Iteration] = []
    for pid in process_ids:
        rows = (await session.execute(
            select(Iteration).where(Iteration.project_id == project_id, Iteration.status.in_(ACTIVE_STATUSES),
                                    Iteration.n > 1, _naming(pid)))).scalars().all()
        if not rows:
            continue
        busy = await session.scalar(select(Job.id).where(Job.process_id == pid, Job.kind.in_(BUSY_JOB_KINDS),
                                                         Job.status.in_(("queued", "running"))).limit(1))
        if busy is not None:
            process = await session.get(Process, pid)
            raise IterationBusy(f"a design or build of {process.name if process else pid} is queued or running")
        superseded.extend(r for r in rows if r not in superseded)

    n = (await session.scalar(select(func.max(Iteration.n)).where(Iteration.project_id == project_id)) or 0) + 1
    row = Iteration(project_id=project_id, n=n, process_ids=process_ids, kind=kind, status="open",
                    resume_phase=resume_phase,
                    requirements=mask_sensitive(requirements) if requirements else requirements,
                    baseline_build_id=baseline_build_id, source=source, opened_by=actor, note=note)
    session.add(row)
    await session.flush()
    for old in superseded:
        _end(old, "abandoned", f"superseded by iteration {n}")
        _audit(session, old, actor, "iteration abandoned", {"superseded_by": row.id})
    _audit(session, row, actor, "iteration opened",
           {"kind": kind, "resume_phase": resume_phase, "baseline_build_id": baseline_build_id})
    await session.flush()
    return row


def _end(row: Iteration, status: str, note: str | None) -> None:
    row.status = status
    row.closed_at = datetime.now(UTC)
    if note is not None:
        row.note = note


def _audit(session: AsyncSession, row: Iteration, actor: str, action: str, detail: dict | None = None) -> None:
    gate_svc.audit(session, None, actor, action, project_id=row.project_id,
                   actor_kind=gate_svc.SYSTEM if actor == INITIAL_ACTOR else "user",
                   detail={"iteration_id": row.id, "n": row.n, "process_ids": row.process_ids, **(detail or {})})


async def _get(session: AsyncSession, iteration_id: str) -> Iteration:
    row = await session.get(Iteration, iteration_id)
    if row is None:
        raise LookupError(f"iteration {iteration_id} not found")
    return row


async def set_status(session: AsyncSession, iteration_id: str, status: str) -> Iteration:
    """Move an iteration to ``status`` (``closed``/``abandoned`` also stamp ``closed_at``)."""
    if status not in ITERATION_STATUSES:
        raise ValueError(f"status must be one of: {', '.join(ITERATION_STATUSES)}")
    row = await _get(session, iteration_id)
    if status in ENDED_STATUSES:
        _end(row, status, None)
    else:
        row.status = status
    await session.flush()
    return row


async def _finish(session: AsyncSession, iteration_id: str, status: str, actor: str, note: str | None) -> Iteration:
    row = await _get(session, iteration_id)
    if row.status in ENDED_STATUSES:
        raise ValueError(f"iteration {row.n} is already {row.status}")
    _end(row, status, note)
    _audit(session, row, actor, f"iteration {status}", {"note": note} if note else None)
    await session.flush()
    return row


async def close(session: AsyncSession, iteration_id: str, actor: str, note: str | None = None) -> Iteration:
    return await _finish(session, iteration_id, "closed", actor, note)


async def abandon(session: AsyncSession, iteration_id: str, actor: str, note: str | None = None) -> Iteration:
    return await _finish(session, iteration_id, "abandoned", actor, note)
