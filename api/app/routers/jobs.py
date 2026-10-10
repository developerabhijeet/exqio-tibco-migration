"""Job status and the SSE event stream.

``GET /jobs/{id}/events`` replays stored events after ``Last-Event-ID`` (default 0), then
streams live ones from the in-process runner, polling the table every 500 ms for events
written by another worker, and ends after the terminal ``done`` or ``error`` event.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sse_starlette.sse import EventSourceResponse, ServerSentEvent

from api.app.deps import RunnerDep, SessionDep
from api.app.jobs.runner import EventOut, JobRunner
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


async def stream_events(request: Request, runner: JobRunner, job_id: str,
                        after: int) -> AsyncIterator[ServerSentEvent]:
    queue = runner.subscribe(job_id)  # before the replay, so nothing falls between the two
    last = after
    try:
        pending = await _stored(runner.sessionmaker, job_id, last)
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
                ev = await asyncio.wait_for(queue.get(), timeout=POLL_SECONDS)
            except TimeoutError:
                if await request.is_disconnected():
                    return
                pending = await _stored(runner.sessionmaker, job_id, last)
                continue
            if ev.seq == last + 1:
                pending = [ev]
            elif ev.seq > last + 1:  # a gap: read it back in order from the table
                pending = await _stored(runner.sessionmaker, job_id, last)
    finally:
        runner.unsubscribe(job_id, queue)


@router.get("/jobs/{job_id}/events")
async def job_events(job_id: str, request: Request, session: SessionDep,
                     runner: RunnerDep) -> EventSourceResponse:
    if await session.get(Job, job_id) is None:
        raise HTTPException(status_code=404, detail="job not found")
    try:
        after = int(request.headers.get("last-event-id") or 0)
    except ValueError:
        after = 0
    return EventSourceResponse(stream_events(request, runner, job_id, after), ping=15)
