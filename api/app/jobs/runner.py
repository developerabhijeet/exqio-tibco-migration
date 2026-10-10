"""In-process job runner: a bounded asyncio worker pool over persisted ``jobs`` and ``job_events``.

``submit`` stores a queued job and hands its coroutine to the pool (``JOB_WORKERS`` workers).
A running job writes events through :class:`JobContext` (``log``, ``stage``); every event is
committed to ``job_events`` with a per-job ``seq`` and then published to in-process
subscribers, which the SSE endpoint reads (with a database poll as the fallback for events
written by another worker). The runner emits the terminal ``done`` or ``error`` event.

On startup :meth:`JobRunner.recover` marks jobs left ``queued`` or ``running`` by a previous
process as failed with the reason "api restarted" (their coroutines died with that process).
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.app.db import utcnow
from api.app.models import Job, JobEvent

log = logging.getLogger(__name__)

EventKind = Literal["log", "stage", "done", "error"]
Tag = Literal["code", "ai"]
RESTART_REASON = "api restarted"


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


class JobContext:
    """Handed to a running job: write log lines and stage changes."""

    def __init__(self, runner: JobRunner, job_id: str, payload: dict[str, Any]) -> None:
        self.runner = runner
        self.job_id = job_id
        self.payload = payload
        self._seq = 0
        self._lock = asyncio.Lock()

    @property
    def sessionmaker(self) -> async_sessionmaker[AsyncSession]:
        return self.runner.sessionmaker

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
            self._seq += 1
            row = JobEvent(job_id=self.job_id, seq=self._seq, event=event, tag=tag, text=text,
                           data=data or {})
            async with self.sessionmaker() as s:
                s.add(row)
                await s.commit()
                await s.refresh(row)
            out = EventOut.from_row(row)
        self.runner.publish(self.job_id, out)
        return out


RunFn = Callable[[JobContext], Awaitable[dict[str, Any]]]


class JobRunner:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession], workers: int = 2) -> None:
        self.sessionmaker = sessionmaker
        self.size = max(1, workers)
        self._queue: asyncio.Queue[tuple[str, RunFn]] = asyncio.Queue()
        self._workers: list[asyncio.Task[None]] = []
        self._subscribers: dict[str, set[asyncio.Queue[EventOut]]] = {}

    # ------------------------------------------------------------- lifecycle
    async def start(self) -> None:
        await self.recover()
        self._workers = [asyncio.create_task(self._work(i), name=f"job-worker-{i}")
                         for i in range(self.size)]

    async def stop(self) -> None:
        for t in self._workers:
            t.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers = []

    async def recover(self) -> int:
        """Mark jobs a previous process left queued or running as failed; return how many."""
        async with self.sessionmaker() as s:
            stale = (await s.execute(select(Job).where(Job.status.in_(("queued", "running"))))).scalars().all()
            for job in stale:
                last = await s.scalar(select(func.max(JobEvent.seq)).where(JobEvent.job_id == job.id))
                job.status = "failed"
                job.error = RESTART_REASON
                job.finished_at = utcnow()
                s.add(JobEvent(job_id=job.id, seq=(last or 0) + 1, event="error", tag=None,
                               text=RESTART_REASON, data={}))
            await s.commit()
        if stale:
            log.warning("marked %d interrupted job(s) failed: %s", len(stale), RESTART_REASON)
        return len(stale)

    # ------------------------------------------------------------- submit
    async def submit(self, kind: str, payload: dict[str, Any], run: RunFn, *,
                     project_id: str | None = None, process_id: str | None = None) -> str:
        async with self.sessionmaker() as s:
            job = Job(kind=kind, status="queued", payload=payload, project_id=project_id,
                      process_id=process_id)
            s.add(job)
            await s.commit()
            job_id = job.id
        await self._queue.put((job_id, run))
        return job_id

    # ------------------------------------------------------------- pub/sub
    def subscribe(self, job_id: str) -> asyncio.Queue[EventOut]:
        q: asyncio.Queue[EventOut] = asyncio.Queue()
        self._subscribers.setdefault(job_id, set()).add(q)
        return q

    def unsubscribe(self, job_id: str, q: asyncio.Queue[EventOut]) -> None:
        subs = self._subscribers.get(job_id)
        if subs is not None:
            subs.discard(q)
            if not subs:
                self._subscribers.pop(job_id, None)

    def publish(self, job_id: str, event: EventOut) -> None:
        for q in list(self._subscribers.get(job_id, ())):
            q.put_nowait(event)

    # ------------------------------------------------------------- workers
    async def _work(self, index: int) -> None:
        while True:
            job_id, run = await self._queue.get()
            try:
                await self._execute(job_id, run)
            except Exception:
                log.exception("job %s: runner bookkeeping failed", job_id)
            finally:
                self._queue.task_done()

    async def _execute(self, job_id: str, run: RunFn) -> None:
        async with self.sessionmaker() as s:
            job = await s.get(Job, job_id)
            if job is None or job.status != "queued":
                return
            job.status = "running"
            job.started_at = utcnow()
            payload = dict(job.payload or {})
            await s.commit()
        ctx = JobContext(self, job_id, payload)
        try:
            result = await run(ctx)
        except asyncio.CancelledError:
            await self._finish(job_id, "failed", None, "cancelled")
            await ctx.emit("error", "cancelled")
            raise
        except Exception as exc:
            log.exception("job %s failed", job_id)
            message = f"{type(exc).__name__}: {exc}"
            await self._finish(job_id, "failed", None, message)
            await ctx.emit("error", message)
            return
        result = result or {}
        await self._finish(job_id, "done", result, None)
        await ctx.emit("done", "done", data=result)

    async def _finish(self, job_id: str, status: str, result: dict[str, Any] | None,
                      error: str | None) -> None:
        async with self.sessionmaker() as s:
            await s.execute(update(Job).where(Job.id == job_id).values(
                status=status, result=result, error=error, finished_at=utcnow()))
            await s.commit()
