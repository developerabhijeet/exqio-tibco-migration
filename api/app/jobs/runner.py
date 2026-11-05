"""Jobs as Postgres rows claimed by workers: ``enqueue`` queues, any :class:`JobWorker` runs.

:func:`enqueue` (and ``JobRunner.submit``, which calls it) stores a queued job (``kind`` +
``payload``, optionally an ``affinity`` naming the one worker allowed to run it). With a
``dedupe_key`` (kept in the payload) nothing is stored while a queued job of the same kind has the
same key. Workers claim queued jobs whose ``not_before`` has passed with one
``UPDATE ... FOR UPDATE SKIP LOCKED`` statement, so two workers never run the same job, and run
``handler_for(kind)`` in one of their ``slots``. In role ``all`` the runner owns an in-process
worker (named by :func:`worker_name`); in role ``api`` it only queues, and worker processes
(``python -m api.app.worker``) run the jobs.

A handler that raises :class:`~api.app.jobs.registry.RetryLater` goes back to the queue with
``not_before = now() + delay`` and a ``log`` event ``retry in <delay> s: <reason>`` while it has
attempts left (``max_attempts_for(kind)``; an uncounted retry hands its attempt back), else fails
with ``<reason> after <attempts> attempts``.

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
"worker lost"; a lost running job of a kind that registered its own ``max_attempts`` is requeued
instead while ``attempts < max_attempts_for(kind)``. ``drain`` stops claiming, lets running jobs
finish within its timeout, and requeues the rest (``attempts < max_attempts_for(kind)``) or fails
them with "worker stopped". Kinds without their own ``max_attempts`` use the worker's
``max_attempts`` (``JOB_MAX_ATTEMPTS``).

With ``NEO4J_URI`` set, the transaction that marks a phase job ``done`` also queues its
``memory.ingest`` follow-up (``services.memory_ingest.followup_payload``), in a savepoint so a
failure there never loses the phase's outcome. A handler sees its attempt number as
``JobContext.attempt``.

Each job gets its own copy of the worker's ``Settings`` with the Settings-screen edits stored when
it starts (``services.settings.with_stored``): the PATCH lands on one API pod, the table reaches all.

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
import weakref
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Literal

import psycopg
from sqlalchemy import Integer, String, and_, bindparam, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from api.app.db import new_id, psycopg_url
from api.app.jobs.registry import (
    RetryLater,
    WorkerServices,
    handler_for,
    kinds_with_own_max_attempts,
    max_attempts_for,
)
from api.app.models import Job, JobEvent

if TYPE_CHECKING:
    from api.app.config import Settings

log = logging.getLogger(__name__)

EventKind = Literal["log", "stage", "done", "error"]
Tag = Literal["code", "ai"]
LOST_REASON = "worker lost"
STOPPED_REASON = "worker stopped"
CANCELLED_REASON = "cancelled"
REAP_SECONDS = 30.0
#: Job kinds stamped with the process's current iteration on submit (sub-project 6).
ITERATION_STAMPED = frozenset({"design", "build"})
#: Job kinds stamped with their build's iteration on submit (``payload["build_id"]``): a verify or a
#: plan belongs to the iteration of the build it runs on, not to the process's current one.
BUILD_STAMPED = frozenset({"verify", "plan"})
POLL_SECONDS = 0.5  # how often an idle worker looks for jobs queued by another process

_JOBS = Job.__table__.fullname
CLAIM_SQL = text(f"""
UPDATE {_JOBS} SET status='running', owner=:me, heartbeat_at=now(), started_at=coalesce(started_at, now()),
       attempts=attempts+1
WHERE id = (SELECT id FROM {_JOBS} WHERE status='queued' AND (affinity IS NULL OR affinity=:me)
              AND (not_before IS NULL OR not_before <= now())
            ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1)
RETURNING id, kind, payload, attempts
""").columns(id=String, kind=String, payload=JSONB, attempts=Integer)
# One statement, so a dedupe check and its insert see the same snapshot; a NULL key never matches.
ENQUEUE_SQL = text(f"""
INSERT INTO {_JOBS} (id, kind, status, payload, project_id, process_id, affinity)
SELECT :id, :kind, 'queued', :payload, :project_id, :process_id, :affinity
WHERE NOT EXISTS (SELECT 1 FROM {_JOBS} WHERE status='queued' AND kind=:kind
                  AND payload->>'dedupe_key' = :dedupe_key)
RETURNING id
""").bindparams(bindparam("payload", type_=JSONB),
               *(bindparam(name, type_=String) for name in ("id", "kind", "project_id", "process_id",
                                                             "affinity", "dedupe_key")))
# Held until the enqueue commits, so the NOT EXISTS of a second enqueue of the key sees the first.
DEDUPE_LOCK_SQL = text("SELECT pg_advisory_xact_lock(hashtext(:kind || '|' || :dedupe_key))")
NOTIFY_SQL = text("SELECT pg_notify('job_events', :job_id || ':' || :seq)")
HEARTBEAT_SQL = f"""
UPDATE {_JOBS} SET heartbeat_at=now()
WHERE id = ANY(%(ids)s) AND owner=%(me)s AND status='running'
RETURNING id, cancel_requested
"""
# The heartbeat connection must notice a dead peer (a failed-over server, a dropped NAT entry) well
# inside JOB_STALE_SECONDS (default 90), or a healthy job is reaped while a beat hangs on a socket
# nobody answers: TCP keepalives after 30 s idle, 3 probes 10 s apart, unacknowledged writes
# abandoned after 30 s, and a 5 s cap on the UPDATE itself.
HEARTBEAT_CONNECT = {
    "autocommit": True, "connect_timeout": 10,
    "keepalives": 1, "keepalives_idle": 30, "keepalives_interval": 10, "keepalives_count": 3,
    "tcp_user_timeout": 30000, "options": "-c statement_timeout=5000",
}


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


async def add_next_event(s: AsyncSession, job_id: str, event: str, text_: str,
                         tag: str | None = None) -> None:
    """Append an event after the job's last one (for jobs no context is writing)."""
    last = await s.scalar(select(func.max(JobEvent.seq)).where(JobEvent.job_id == job_id))
    await add_event(s, job_id, (last or 0) + 1, event, text_, tag)


async def add_terminal_event(s: AsyncSession, job_id: str, text_: str) -> None:
    """Append an ``error`` event after the job's last one (for jobs no context is writing)."""
    await add_next_event(s, job_id, "error", text_)


FinishHook = Callable[[AsyncSession, Any], Awaitable[None]]


def _memory_followup(kind: str, job_id: str, payload: dict[str, Any],
                     services: WorkerServices | None) -> FinishHook | None:
    """The hook that queues a phase job's memory ingest in its ``done`` transaction (spec section
    2), or ``None`` when memory is not configured or ``kind`` changes no memory."""
    if services is None or not services.settings.neo4j_uri:
        return None
    from api.app.services.memory_ingest import INGEST_AFTER, INGEST_KIND, followup_payload

    if kind not in INGEST_AFTER:
        return None

    async def queue(s: AsyncSession, row: Any) -> None:
        try:  # a savepoint: the phase's own outcome commits even if this insert fails
            async with s.begin_nested():
                ingest = followup_payload(kind, job_id, row.project_id, row.process_id, payload)
                if ingest is not None:
                    await insert_job(s, INGEST_KIND, ingest, project_id=ingest["project_id"],
                                     process_id=row.process_id)
        except Exception:
            log.exception("job %s: memory ingest not queued", job_id)
    return queue


# The workers of this process, so ``enqueue`` can wake the one sharing its database.
_LOCAL_WORKERS: weakref.WeakSet[JobWorker] = weakref.WeakSet()


async def enqueue(sessionmaker: async_sessionmaker[AsyncSession], kind: str, payload: dict[str, Any], *,
                  project_id: str | None = None, process_id: str | None = None,
                  dedupe_key: str | None = None, affinity: str | None = None) -> str | None:
    """Store a queued job; the handler registered for ``kind`` runs it from ``payload`` on the
    first worker to claim it (only the worker named ``affinity``, when given).

    ``dedupe_key`` (stored as ``payload["dedupe_key"]``; a key already in ``payload`` counts too)
    makes this a no-op returning ``None`` while a queued job of ``kind`` carries the same key. A
    running job does not count: what it read may already be out of date."""
    if dedupe_key is not None:
        payload = {**payload, "dedupe_key": dedupe_key}
    async with sessionmaker() as s:
        job_id = await insert_job(s, kind, payload, project_id=project_id, process_id=process_id,
                                  affinity=affinity)
        await s.commit()
    if job_id is not None:
        wake_local_workers(sessionmaker)
    return job_id


async def insert_job(s: AsyncSession, kind: str, payload: dict[str, Any], *,
                     project_id: str | None = None, process_id: str | None = None,
                     affinity: str | None = None) -> str | None:
    """:func:`enqueue`'s statement inside the caller's transaction (``payload["dedupe_key"]``
    dedupes); the caller commits and wakes the workers."""
    key = payload.get("dedupe_key")
    if key is not None:  # two concurrent enqueues of one key: the second waits, then sees the first
        await s.execute(DEDUPE_LOCK_SQL, {"kind": kind, "dedupe_key": str(key)})
    return await s.scalar(ENQUEUE_SQL, {
        "id": new_id(), "kind": kind, "payload": payload, "project_id": project_id,
        "process_id": process_id, "affinity": affinity,
        "dedupe_key": None if key is None else str(key)})


def wake_local_workers(sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    for worker in list(_LOCAL_WORKERS):
        if worker.sessionmaker is sessionmaker:
            worker.wake()


class JobContext:
    """Handed to a running job: write log lines and stage changes. ``attempt`` is the claimed
    row's ``attempts`` (1 on the first run)."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession], job_id: str,
                 payload: dict[str, Any], *, owner: str | None = None, seq: int = 0,
                 attempt: int = 1) -> None:
        self.sessionmaker = sessionmaker
        self.job_id = job_id
        self.payload = payload
        self.owner = owner
        self.attempt = attempt
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
                     data: dict[str, Any] | None = None, tag: Tag | None = None,
                     then: FinishHook | None = None) -> bool:
        """Set the job's final ``values`` (and write ``event``) if it is still ``running`` and ours;
        ``then(session, row)`` runs in the same transaction (``row``: the job's ``id``,
        ``project_id``, ``process_id``). Returns whether it was."""
        async with self._lock:
            async with self.sessionmaker() as s:
                ours = (await s.execute(update(Job).where(
                    Job.id == self.job_id, Job.status == "running", Job.owner == self.owner,
                ).values(**values).returning(Job.id, Job.project_id, Job.process_id))).first()
                if ours is None:
                    await s.rollback()
                    return False
                if event is not None:
                    await add_event(s, self.job_id, self._seq + 1, event, text, tag, data)
                if then is not None:
                    await then(s, ours)
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
        _LOCAL_WORKERS.add(self)
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
        _LOCAL_WORKERS.discard(self)
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
    async def claim(self) -> tuple[str, str, dict[str, Any], int] | None:
        async with self.sessionmaker() as s:
            row = (await s.execute(CLAIM_SQL, {"me": self.name})).first()
            await s.commit()
        return (row.id, row.kind, dict(row.payload or {}), row.attempts) if row else None

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
                job_id, kind, payload, attempt = claimed
                with self._running_lock:
                    self._running.add(job_id)
                self._tasks[job_id] = asyncio.create_task(self._run(job_id, kind, payload, attempt),
                                                          name=f"job-{job_id}")
                continue
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.poll_s)
            except TimeoutError:
                pass

    # ------------------------------------------------------------- running
    async def _run(self, job_id: str, kind: str, payload: dict[str, Any], attempt: int = 1) -> None:
        try:
            ctx = JobContext(self.sessionmaker, job_id, payload, owner=self.name, attempt=attempt)
            services: WorkerServices | None = None
            try:
                async with self.sessionmaker() as s:  # a requeued job continues its sequence
                    ctx._seq = await s.scalar(select(func.max(JobEvent.seq))
                                              .where(JobEvent.job_id == job_id)) or 0
                await self._follow_iteration(job_id, kind, payload)
                services = await self._job_services()
                result = await handler_for(kind)(ctx, services)
            except asyncio.CancelledError:
                await self._after_cancel(ctx, kind, self._reasons.pop(job_id, "drain"),
                                         services.settings if services else None)
                return
            except RetryLater as retry:
                self._finishing.add(job_id)
                await self._retry_later(ctx, kind, retry, services.settings if services else None)
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
            followup = _memory_followup(kind, job_id, payload, services)
            await ctx.finish({"status": "done", "result": result, "finished_at": func.now()},
                            "done", "done", data=result, then=followup)
            if followup is not None:
                wake_local_workers(self.sessionmaker)
        except Exception:
            log.exception("job %s: worker bookkeeping failed", job_id)
        finally:
            with self._running_lock:
                self._running.discard(job_id)
            self._tasks.pop(job_id, None)
            self._reasons.pop(job_id, None)
            self._finishing.discard(job_id)
            self._wake.set()  # a slot is free

    async def _follow_iteration(self, job_id: str, kind: str, payload: dict[str, Any]) -> None:
        """A phase job starts: its process's iteration takes the job's status (design ->
        designing, build -> building, verify -> verifying, plan -> deploying). Never fails the job."""
        from api.app.services import iterations  # services import this module

        if iterations.status_for_job(kind) is None:
            return
        try:
            async with self.sessionmaker() as s:
                process_id = await s.scalar(select(Job.process_id).where(Job.id == job_id))
                await iterations.follow_job(s, kind, process_id, payload)
                await s.commit()
        except Exception:
            log.exception("job %s: the iteration status could not follow it", job_id)

    async def _job_services(self) -> WorkerServices | None:
        """The services for one job: this worker's, with a copy of its settings carrying the
        Settings-screen edits stored now (another pod may have saved them since this one started).
        The copy is the job's own, so a later edit never changes a job mid-run."""
        if self.services is None:
            return None
        from api.app.services.settings import load_stored, with_stored  # imports this module

        try:
            async with self.sessionmaker() as s:
                stored = await load_stored(s)
        except Exception as exc:  # noqa: BLE001  the job still runs, on the values loaded at start
            log.warning("worker %s: could not read the stored settings: %s", self.name, exc)
            return self.services
        return replace(self.services, settings=with_stored(self.services.settings, stored))

    def max_attempts_of(self, kind: str, settings: Settings | None = None) -> int:
        """``max_attempts_for(kind)``, defaulting to this worker's ``max_attempts``."""
        if settings is None and self.services is not None:
            settings = self.services.settings
        return max_attempts_for(kind, settings, default=self.max_attempts)

    async def _attempts(self, job_id: str) -> int:
        async with self.sessionmaker() as s:
            return await s.scalar(select(Job.attempts).where(Job.id == job_id)) or 0

    async def _retry_later(self, ctx: JobContext, kind: str, retry: RetryLater,
                           settings: Settings | None) -> None:
        """Back to the queue for ``retry.delay_s`` while attempts are left, else failed."""
        attempts = await self._attempts(ctx.job_id)
        if retry.count_attempt and attempts >= self.max_attempts_of(kind, settings):
            message = retry.give_up_text(attempts)
            await ctx.finish(_failed(message), "error", message)
            return
        values: dict[str, Any] = {"status": "queued", "owner": None, "heartbeat_at": None,
                                  "not_before": func.now() + timedelta(seconds=retry.delay_s)}
        if not retry.count_attempt:
            values["attempts"] = Job.attempts - 1
        await ctx.finish(values, "log", f"retry in {retry.delay_s:g} s: {retry.reason}", tag="code")

    async def _stored_settings(self) -> Settings | None:
        """This worker's settings with the Settings-screen edits stored now (what a job starting
        now would get); ``None`` without services."""
        services = await self._job_services()
        return services.settings if services else None

    async def _after_cancel(self, ctx: JobContext, kind: str, reason: str,
                            settings: Settings | None = None) -> None:
        if reason == "lost":  # another worker already gave the job its outcome
            log.warning("job %s: no longer owned by %s; abandoned", ctx.job_id, self.name)
            return
        if reason == "cancelled":
            await ctx.finish(_failed(CANCELLED_REASON),
                            "error", CANCELLED_REASON)
            return
        attempts = await self._attempts(ctx.job_id)  # drain: requeue unless out of attempts
        allowed = self.max_attempts_of(kind, settings or await self._stored_settings())
        if attempts < allowed:
            if await ctx.finish({"status": "queued", "owner": None, "heartbeat_at": None}):
                log.warning("job %s: requeued by %s (attempt %d of %d)", ctx.job_id, self.name,
                            attempts, allowed)
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
                        conn = psycopg.connect(self.dsn, **HEARTBEAT_CONNECT)
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
        is_stale = and_(Job.status == "running", or_(Job.heartbeat_at.is_(None), Job.heartbeat_at < stale))
        kinds = kinds_with_own_max_attempts()
        settings = await self._stored_settings() if kinds else None
        allowed = {kind: self.max_attempts_of(kind, settings) for kind in kinds}
        async with self.sessionmaker() as s:
            requeued = []
            if allowed:  # kinds that opted in to retries go back to the queue while attempts are left
                requeued = (await s.execute(update(Job).where(is_stale, or_(*(
                    and_(Job.kind == kind, Job.attempts < n) for kind, n in allowed.items()),
                )).values(status="queued", owner=None, heartbeat_at=None)
                    .returning(Job.id, Job.kind, Job.attempts))).all()
            for row in requeued:
                await add_next_event(s, row.id, "log", f"{LOST_REASON}; requeued (attempt {row.attempts} "
                                     f"of {allowed[row.kind]})", "code")
            running = (await s.execute(update(Job).where(is_stale).values(**lost)
                                       .returning(Job.id))).scalars().all()
            orphaned = (await s.execute(update(Job).where(
                Job.status == "queued", Job.affinity.is_not(None), Job.affinity != self.name,
                Job.created_at < stale,
                ~select(live.id).where(live.status == "running", live.owner == Job.affinity,
                                       live.heartbeat_at >= stale).exists(),
            ).values(**lost).returning(Job.id))).scalars().all()
            for job_id in (*running, *orphaned):
                await add_terminal_event(s, job_id, LOST_REASON)
            await s.commit()
        if requeued:
            log.warning("worker %s: requeued %d job(s) of lost workers", self.name, len(requeued))
        if running or orphaned:
            log.warning("worker %s: failed %d job(s) of lost workers", self.name, len(running) + len(orphaned))
        return len(requeued) + len(running) + len(orphaned)


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
                     process_id: str | None = None, affinity: str | None = None) -> str | None:
        """:func:`enqueue` on this runner's database, then wake its worker (``None`` only when
        ``payload`` carries a ``dedupe_key`` a queued job already has). A design or build job of a
        process is stamped with the process's current iteration (``payload["iteration"]``)."""
        if kind in ITERATION_STAMPED and process_id is not None and "iteration" not in payload:
            payload = {**payload, "iteration": await self._iteration(process_id)}
        elif kind in BUILD_STAMPED and payload.get("build_id") and "iteration" not in payload:
            n = await self._build_iteration(payload["build_id"])
            if n is not None:
                payload = {**payload, "iteration": n}
        job_id = await enqueue(self.sessionmaker, kind, payload, project_id=project_id,
                               process_id=process_id, affinity=affinity)
        if self.worker is not None:
            self.worker.wake()
        return job_id

    async def _build_iteration(self, build_id: str) -> int | None:
        from api.app.models import Build

        async with self.sessionmaker() as s:
            return await s.scalar(select(Build.iteration).where(Build.id == build_id))

    async def _iteration(self, process_id: str) -> int:
        from api.app.services.iterations import current_iteration  # services import this module

        async with self.sessionmaker() as s:
            n = (await current_iteration(s, process_id)).n
            await s.commit()  # iteration 1 may have just been created
        return n
