"""Jobs as Postgres rows claimed by workers: ``submit`` queues, any :class:`JobWorker` runs.

``JobRunner.submit`` stores a queued job (``kind`` + ``payload``, optionally an ``affinity``
naming the one worker allowed to run it). Workers claim queued jobs with one
``UPDATE ... FOR UPDATE SKIP LOCKED`` statement, so two workers never run the same job, and run
``handler_for(kind)`` in one of their ``slots``. In role ``all`` the runner owns an in-process
worker (named by :func:`worker_name`); in role ``api`` it only queues, and worker processes
(``python -m api.app.worker``) run the jobs.

A worker proves it is alive by refreshing ``heartbeat_at`` on its running jobs every
``heartbeat_s``; the same statement reads ``cancel_requested`` and cancels the job's task when it is
set (the services' ``CancelledError`` cleanup runs; the outcome is failed, "cancelled"). If a
row is no longer ours (another worker's reaper failed it) the task is cancelled with no outcome.
The heartbeat runs on its own daemon thread with a synchronous psycopg connection, not on the event
loop: some handlers do synchronous work on the loop (``services/analysis.py``), and a stalled loop
must not let a healthy job look lost. The thread hands cancellations to the loop with
``call_soon_threadsafe``.
``reap`` (at start and every 30 s) fails running jobs whose heartbeat is older than ``stale_s``,
and queued jobs pinned to a worker that has shown no running heartbeat for ``stale_s``, with
"worker lost". ``drain`` stops claiming, lets running jobs finish within its timeout, and requeues
the rest (``attempts < max_attempts``) or fails them with "worker stopped".

A running job writes events through :class:`JobContext` (``log``, ``stage``). Each event is
inserted into ``job_events`` with a per-job ``seq`` and announced with
``pg_notify('job_events', '<job_id>:<seq>')`` in the same transaction; API processes hear it on
their :class:`~api.app.jobs.listener.JobListener`. The terminal ``done`` or ``error`` event is
written in the transaction that sets the job's final status, and only while the job is still
``running`` and owned by this worker, so a reaped job never gets a second outcome.

Event tags: ``log`` events carry ``tag`` ``code`` or ``ai``; ``stage``, ``done`` and ``error``
events carry ``tag`` ``None`` (``null`` in the stream).
"""
from __future__ import annotations

import asyncio
import logging
import os
import socket
import threading
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Literal

import psycopg
from sqlalchemy import String, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from api.app.db import psycopg_url
from api.app.jobs.registry import WorkerServices, handler_for
from api.app.models import Job, JobEvent

log = logging.getLogger(__name__)

EventKind = Literal["log", "stage", "done", "error"]
Tag = Literal["code", "ai"]
LOST_REASON = "worker lost"
STOPPED_REASON = "worker stopped"
CANCELLED_REASON = "cancelled"
REAP_SECONDS = 30.0
POLL_SECONDS = 0.5  # how often an idle worker looks for jobs queued by another process

_JOBS = Job.__table__.fullname
CLAIM_SQL = text(f"""
UPDATE {_JOBS} SET status='running', owner=:me, heartbeat_at=now(), started_at=coalesce(started_at, now()),
       attempts=attempts+1
WHERE id = (SELECT id FROM {_JOBS} WHERE status='queued' AND (affinity IS NULL OR affinity=:me)
            ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1)
RETURNING id, kind, payload
""").columns(id=String, kind=String, payload=JSONB)
NOTIFY_SQL = text("SELECT pg_notify('job_events', :job_id || ':' || :seq)")
HEARTBEAT_SQL = f"""
UPDATE {_JOBS} SET heartbeat_at=now()
WHERE id = ANY(%(ids)s) AND owner=%(me)s AND status='running'
RETURNING id, cancel_requested
"""


def worker_name() -> str:
    """``POD_NAME`` in Kubernetes, else ``<hostname>-<pid>``: unique across containers sharing a
    database, which a bare pid is not."""
    return os.environ.get("POD_NAME") or f"{socket.gethostname()}-{os.getpid()}"


@dataclass(frozen=True)
class EventOut:
    """One job event as streamed: ``seq`` is the SSE id."""

    seq: int
    event: str
    tag: str | None
    text: str
    data: dict[str, Any] = field(default_factory=dict)
    ts: str | None = None

    @property
    def terminal(self) -> bool:
        return self.event in ("done", "error")

    def payload(self) -> dict[str, Any]:
        return {"seq": self.seq, "tag": self.tag, "text": self.text, "data": self.data, "ts": self.ts}

    @classmethod
    def from_row(cls, row: JobEvent) -> EventOut:
        return cls(seq=row.seq, event=row.event, tag=row.tag, text=row.text, data=row.data or {},
                   ts=row.ts.isoformat() if row.ts else None)


def _failed(error: str) -> dict[str, Any]:
    return {"status": "failed", "error": error, "finished_at": func.now()}


async def add_event(s: AsyncSession, job_id: str, seq: int, event: str, text_: str,
                    tag: str | None = None, data: dict[str, Any] | None = None) -> JobEvent:
    """Insert one event and NOTIFY it; delivered when the caller's transaction commits."""
    row = JobEvent(job_id=job_id, seq=seq, event=event, tag=tag, text=text_, data=data or {})
    s.add(row)
    await s.flush()
    await s.execute(NOTIFY_SQL, {"job_id": job_id, "seq": str(seq)})
    return row


async def add_terminal_event(s: AsyncSession, job_id: str, text_: str) -> None:
    """Append an ``error`` event after the job's last one (for jobs no context is writing)."""
    last = await s.scalar(select(func.max(JobEvent.seq)).where(JobEvent.job_id == job_id))
    await add_event(s, job_id, (last or 0) + 1, "error", text_)


class JobContext:
    """Handed to a running job: write log lines and stage changes."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession], job_id: str,
                 payload: dict[str, Any], *, owner: str | None = None, seq: int = 0) -> None:
        self.sessionmaker = sessionmaker
        self.job_id = job_id
        self.payload = payload
        self.owner = owner
        self._seq = seq  # the job's last stored event: a requeued job continues its sequence
        self._lock = asyncio.Lock()

    async def log(self, text: str, tag: Tag = "code", data: dict[str, Any] | None = None) -> None:
        await self.emit("log", text, tag=tag, data=data)

    async def stage(self, name: str, data: dict[str, Any] | None = None) -> None:
        async with self.sessionmaker() as s:
            await s.execute(update(Job).where(Job.id == self.job_id).values(stage=name))
            await s.commit()
        await self.emit("stage", name, tag=None, data=data)

    async def emit(self, event: EventKind, text: str, tag: str | None = None,
                   data: dict[str, Any] | None = None) -> EventOut:
        async with self._lock:
            async with self.sessionmaker() as s:
                row = await add_event(s, self.job_id, self._seq + 1, event, text, tag, data)
                await s.commit()
                await s.refresh(row)
            self._seq += 1
            return EventOut.from_row(row)

    async def finish(self, values: dict[str, Any], event: EventKind | None = None, text: str = "",
                    data: dict[str, Any] | None = None) -> bool:
        """Set the job's final ``values`` (and write ``event``) if it is still ``running`` and ours.
        Returns whether it was."""
        async with self._lock:
            async with self.sessionmaker() as s:
                ours = await s.scalar(update(Job).where(
                    Job.id == self.job_id, Job.status == "running", Job.owner == self.owner,
                ).values(**values).returning(Job.id))
                if ours is None:
                    await s.rollback()
                    return False
                if event is not None:
                    await add_event(s, self.job_id, self._seq + 1, event, text, None, data)
                await s.commit()
            if event is not None:
                self._seq += 1
            return True


class JobWorker:
    """Claims queued jobs and runs up to ``slots`` of them; see the module docstring."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession], services: WorkerServices | None,
                 *, name: str, slots: int, heartbeat_s: int, stale_s: int, max_attempts: int,
                 reap_s: float = REAP_SECONDS, poll_s: float = POLL_SECONDS,
                 dsn: str | None = None) -> None:
        self.sessionmaker = sessionmaker
        if dsn is None:  # the sessionmaker's database, for the heartbeat thread's psycopg connection
            dsn = psycopg_url(sessionmaker.kw["bind"].url.render_as_string(hide_password=False))
        self.dsn = dsn
        self.services = services
        self.name = name
        self.slots = max(1, slots)
        self.heartbeat_s = heartbeat_s
        self.stale_s = stale_s
        self.max_attempts = max_attempts
        self.reap_s = reap_s
        self.poll_s = poll_s
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._reasons: dict[str, str] = {}  # job id -> why its task was cancelled
        self._finishing: set[str] = set()  # writing their outcome: never cancelled
        self._claimer: asyncio.Task[None] | None = None
        self._loops: list[asyncio.Task[None]] = []
        self._wake = asyncio.Event()
        self._draining = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._running: set[str] = set()  # job ids the heartbeat thread refreshes
        self._running_lock = threading.Lock()
        self._hb_stop = threading.Event()
        self._hb_thread: threading.Thread | None = None

    # ------------------------------------------------------------- lifecycle
    async def start(self) -> None:
        await self.reap()
        self._loop = asyncio.get_running_loop()
        self._hb_stop.clear()
        self._hb_thread = threading.Thread(target=self._heartbeat_thread, daemon=True,
                                           name=f"{self.name}-heartbeat")
        self._hb_thread.start()
        self._claimer = asyncio.create_task(self._claim_loop(), name=f"{self.name}-claim")
        self._loops = [asyncio.create_task(self._reap_loop(), name=f"{self.name}-reap")]

    def wake(self) -> None:
        """A job was queued in this process: claim now instead of at the next poll."""
        self._wake.set()

    async def drain(self, timeout_s: float) -> None:
        """Stop claiming, let running jobs finish for ``timeout_s``, then requeue the rest."""
        self._draining = True
        self._wake.set()
        if self._claimer is not None:
            await asyncio.gather(self._claimer, return_exceptions=True)  # exits at the next check
            self._claimer = None
        tasks = dict(self._tasks)
        if tasks:
            await asyncio.wait(tasks.values(), timeout=timeout_s)
        for job_id, task in tasks.items():  # heartbeats continue meanwhile
            if not task.done() and job_id not in self._finishing:
                self._reasons.setdefault(job_id, "drain")
                task.cancel()
        await asyncio.gather(*tasks.values(), return_exceptions=True)
        for t in self._loops:
            t.cancel()
        await asyncio.gather(*self._loops, return_exceptions=True)
        self._loops = []
        if self._hb_thread is not None:
            self._hb_stop.set()
            await asyncio.to_thread(self._hb_thread.join, self.heartbeat_s + 5)
            self._hb_thread = None

    async def stop(self) -> None:
        """Drain without waiting: running jobs are requeued (or fail once out of attempts)."""
        await self.drain(0)

    # ------------------------------------------------------------- claiming
    async def claim(self) -> tuple[str, str, dict[str, Any]] | None:
        async with self.sessionmaker() as s:
            row = (await s.execute(CLAIM_SQL, {"me": self.name})).first()
            await s.commit()
        return (row.id, row.kind, dict(row.payload or {})) if row else None

    async def _claim_loop(self) -> None:
        while not self._draining:
            self._wake.clear()
            claimed = None
            if len(self._tasks) < self.slots:
                try:
                    claimed = await self.claim()
                except Exception:
                    log.exception("worker %s: claim failed", self.name)
            if claimed is not None:  # also when drain began meanwhile: drain waits for it
                job_id, kind, payload = claimed
                with self._running_lock:
                    self._running.add(job_id)
                self._tasks[job_id] = asyncio.create_task(self._run(job_id, kind, payload),
                                                          name=f"job-{job_id}")
                continue
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.poll_s)
            except TimeoutError:
                pass

    # ------------------------------------------------------------- running
    async def _run(self, job_id: str, kind: str, payload: dict[str, Any]) -> None:
        try:
            ctx = JobContext(self.sessionmaker, job_id, payload, owner=self.name)
            try:
                async with self.sessionmaker() as s:  # a requeued job continues its sequence
                    ctx._seq = await s.scalar(select(func.max(JobEvent.seq))
                                              .where(JobEvent.job_id == job_id)) or 0
                result = await handler_for(kind)(ctx, self.services)
            except asyncio.CancelledError:
                await self._after_cancel(ctx, self._reasons.pop(job_id, "drain"))
                return
            except Exception as exc:
                log.exception("job %s failed", job_id)
                message = f"{type(exc).__name__}: {exc}"
                self._finishing.add(job_id)
                await ctx.finish(_failed(message),
                                "error", message)
                return
            result = result or {}
            self._finishing.add(job_id)
            await ctx.finish({"status": "done", "result": result, "finished_at": func.now()},
                            "done", "done", data=result)
        except Exception:
            log.exception("job %s: worker bookkeeping failed", job_id)
        finally:
            with self._running_lock:
                self._running.discard(job_id)
            self._tasks.pop(job_id, None)
            self._reasons.pop(job_id, None)
            self._finishing.discard(job_id)
            self._wake.set()  # a slot is free

    async def _after_cancel(self, ctx: JobContext, reason: str) -> None:
        if reason == "lost":  # another worker already gave the job its outcome
            log.warning("job %s: no longer owned by %s; abandoned", ctx.job_id, self.name)
            return
        if reason == "cancelled":
            await ctx.finish(_failed(CANCELLED_REASON),
                            "error", CANCELLED_REASON)
            return
        async with self.sessionmaker() as s:  # drain: requeue unless out of attempts
            attempts = await s.scalar(select(Job.attempts).where(Job.id == ctx.job_id))
        if (attempts or 0) < self.max_attempts:
            if await ctx.finish({"status": "queued", "owner": None, "heartbeat_at": None}):
                log.warning("job %s: requeued by %s (attempt %d of %d)", ctx.job_id, self.name,
                            attempts, self.max_attempts)
        else:
            await ctx.finish(_failed(STOPPED_REASON),
                            "error", STOPPED_REASON)

    # ------------------------------------------------------------- heartbeat
    def _heartbeat_thread(self) -> None:
        """Every ``heartbeat_s`` until drained: refresh our running jobs from this thread."""
        conn: psycopg.Connection | None = None
        try:
            while not self._hb_stop.wait(self.heartbeat_s):
                try:
                    if conn is None or conn.closed:
                        conn = psycopg.connect(self.dsn, autocommit=True, connect_timeout=10)
                    self._beat(conn)
                except Exception:
                    log.exception("worker %s: heartbeat failed", self.name)
                    if conn is not None:
                        conn.close()
                    conn = None  # reconnect at the next beat
        finally:
            if conn is not None:
                conn.close()

    def _beat(self, conn: psycopg.Connection) -> None:
        with self._running_lock:
            ids = list(self._running)
        if not ids:
            return
        rows = conn.execute(HEARTBEAT_SQL, {"ids": ids, "me": self.name}).fetchall()
        alive = {job_id: bool(cancel) for job_id, cancel in rows}
        try:
            self._loop.call_soon_threadsafe(self._apply_heartbeat, ids, alive)
        except RuntimeError:  # the loop is closed: nothing left to cancel
            pass

    def _apply_heartbeat(self, ids: list[str], alive: dict[str, bool]) -> None:
        """On the loop: cancel the jobs flagged ``cancel_requested`` or no longer ours."""
        for job_id in ids:
            task = self._tasks.get(job_id)
            if task is None or task.done() or job_id in self._finishing or job_id in self._reasons:
                continue
            if job_id not in alive:
                reason = "lost"
            elif alive[job_id]:
                reason = "cancelled"
            else:
                continue
            self._reasons[job_id] = reason
            task.cancel()

    # ------------------------------------------------------------- recovery
    async def _reap_loop(self) -> None:
        while True:
            await asyncio.sleep(self.reap_s)
            try:
                await self.reap()
            except Exception:
                log.exception("worker %s: recovery failed", self.name)

    async def reap(self) -> int:
        """Fail jobs whose worker is gone (see the module docstring); return how many."""
        stale = func.now() - timedelta(seconds=self.stale_s)
        lost = _failed(LOST_REASON)
        live = aliased(Job)
        async with self.sessionmaker() as s:
            running = (await s.execute(update(Job).where(
                Job.status == "running", or_(Job.heartbeat_at.is_(None), Job.heartbeat_at < stale),
            ).values(**lost).returning(Job.id))).scalars().all()
            orphaned = (await s.execute(update(Job).where(
                Job.status == "queued", Job.affinity.is_not(None), Job.affinity != self.name,
                Job.created_at < stale,
                ~select(live.id).where(live.status == "running", live.owner == Job.affinity,
                                       live.heartbeat_at >= stale).exists(),
            ).values(**lost).returning(Job.id))).scalars().all()
            for job_id in (*running, *orphaned):
                await add_terminal_event(s, job_id, LOST_REASON)
            await s.commit()
        n = len(running) + len(orphaned)
        if n:
            log.warning("worker %s: failed %d job(s) of lost workers", self.name, n)
        return n


class JobRunner:
    """Queues jobs; in role ``all`` also owns the in-process :class:`JobWorker`."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession],
                 worker: JobWorker | None = None) -> None:
        self.sessionmaker = sessionmaker
        self.worker = worker

    async def start(self) -> None:
        if self.worker is not None:
            await self.worker.start()

    async def stop(self) -> None:
        if self.worker is not None:
            await self.worker.stop()

    async def submit(self, kind: str, payload: dict[str, Any], *, project_id: str | None = None,
                     process_id: str | None = None, affinity: str | None = None) -> str:
        """Store a queued job; the handler registered for ``kind`` runs it from ``payload`` on the
        first worker to claim it (only the worker named ``affinity``, when given)."""
        async with self.sessionmaker() as s:
            job = Job(kind=kind, status="queued", payload=payload, project_id=project_id,
                      process_id=process_id, affinity=affinity)
            s.add(job)
            await s.commit()
            job_id = job.id
        if self.worker is not None:
            self.worker.wake()
        return job_id
