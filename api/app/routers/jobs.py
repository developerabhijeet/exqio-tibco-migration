"""Job status and the SSE event stream.

``GET /jobs/{id}/events`` replays stored events after ``Last-Event-ID`` (default 0), then
streams new ones as this process's :class:`~api.app.jobs.listener.JobListener` hears their
NOTIFY (any worker, any API replica), polling the table every 500 ms in case a notification is
lost, and ends after the terminal ``done`` or ``error`` event. When the
job has finished and ``Last-Event-ID`` is at or past its terminal event the reply is 204, so a
browser ``EventSource`` stops reconnecting.

Each event's ``data`` is JSON ``{seq, tag, text, data, ts}``. ``tag`` is ``code`` or ``ai`` on
``log`` events and ``null`` on ``stage``, ``done`` and ``error`` events.

``POST /jobs/{id}/cancel`` fails a queued job at once and flags a running one; its worker cancels
it at the next heartbeat. It needs a signed-in human (``X-User`` in dev, the session in oidc).
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, Request, Response
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sse_starlette.sse import EventSourceResponse, ServerSentEvent

from api.app.deps import SessionDep, UserDep
from api.app.jobs.listener import JobListener
from api.app.jobs.runner import CANCELLED_REASON, EventOut, add_terminal_event
from api.app.models import Job, JobEvent
from api.app.schemas.job import JobOut

router = APIRouter(tags=["jobs"])
POLL_SECONDS = 0.5


@router.get("/jobs/{job_id}", response_model=JobOut)
async def get_job(job_id: str, session: SessionDep) -> Job:
    job = await session.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job


async def _stored(sm: async_sessionmaker[AsyncSession], job_id: str, after: int) -> list[EventOut]:
    async with sm() as s:
        rows = (await s.execute(select(JobEvent).where(JobEvent.job_id == job_id, JobEvent.seq > after)
                                .order_by(JobEvent.seq))).scalars().all()
    return [EventOut.from_row(r) for r in rows]


def _sse(ev: EventOut) -> ServerSentEvent:
    return ServerSentEvent(data=json.dumps(ev.payload()), event=ev.event, id=str(ev.seq))


async def stream_events(request: Request, listener: JobListener | None,
                        sm: async_sessionmaker[AsyncSession], job_id: str,
                        after: int) -> AsyncIterator[ServerSentEvent]:
    # subscribe before the replay, so nothing falls between the two; without a listener (it is
    # still connecting, or this process has none) the poll alone carries the stream
    queue = listener.subscribe(job_id) if listener is not None else asyncio.Queue()
    last = after
    try:
        pending = await _stored(sm, job_id, last)
        while True:
            for ev in pending:
                if ev.seq <= last:
                    continue
                yield _sse(ev)
                last = ev.seq
                if ev.terminal:
                    return
            pending = []
            try:
                seq = await asyncio.wait_for(queue.get(), timeout=POLL_SECONDS)
            except TimeoutError:
                if await request.is_disconnected():
                    return
                pending = await _stored(sm, job_id, last)
                continue
            while not queue.empty():  # one read covers every notification already queued
                seq = max(seq, queue.get_nowait())
            if seq > last:
                pending = await _stored(sm, job_id, last)
    finally:
        if listener is not None:
            listener.unsubscribe(job_id, queue)


@router.get("/jobs/{job_id}/events", response_model=None)
async def job_events(job_id: str, request: Request,
                     session: SessionDep) -> EventSourceResponse | Response:
    job = await session.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    try:
        after = int(request.headers.get("last-event-id") or 0)
    except ValueError:
        after = 0
    if job.status in ("done", "failed"):
        terminal = await session.scalar(select(func.max(JobEvent.seq)).where(
            JobEvent.job_id == job_id, JobEvent.event.in_(("done", "error"))))
        if terminal is not None and after >= terminal:
            return Response(status_code=204)  # nothing left: EventSource stops reconnecting
    listener = getattr(request.app.state, "listener", None)
    return EventSourceResponse(stream_events(request, listener, request.app.state.sessionmaker, job_id,
                                             after), ping=15)


@router.post("/jobs/{job_id}/cancel", status_code=202, response_model=JobOut)
async def cancel_job(job_id: str, session: SessionDep, user: UserDep) -> Job:
    """Queued: failed "cancelled" now. Running: flagged for its worker. Finished: 409."""
    status = await session.scalar(update(Job).where(
        Job.id == job_id, Job.status.in_(("queued", "running")),
    ).values(cancel_requested=True).returning(Job.status))
    if status is None:
        await session.rollback()
        if await session.get(Job, job_id) is None:
            raise HTTPException(status_code=404, detail="job not found")
        raise HTTPException(status_code=409, detail="job already finished")
    if status == "queued":  # no worker has it: give it its outcome here (unless one claims it now)
        failed = await session.scalar(update(Job).where(Job.id == job_id, Job.status == "queued")
                                      .values(status="failed", error=CANCELLED_REASON,
                                              finished_at=func.now()).returning(Job.id))
        if failed is not None:
            await add_terminal_event(session, job_id, CANCELLED_REASON)
    await session.commit()
    return await session.get(Job, job_id, populate_existing=True)
