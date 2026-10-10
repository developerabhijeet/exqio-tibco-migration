"""Job runner, SSE event stream, restart recovery and the user principal dependency (Task 17)."""
from __future__ import annotations

import asyncio
import json

from fastapi import Depends
from sqlalchemy import select

from api.app.deps import require_user
from api.app.jobs.runner import JobContext
from api.app.models import Job, JobEvent


def _parse_sse(text: str) -> list[dict]:
    events: list[dict] = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        ev: dict = {}
        for line in block.split("\n"):
            if not line or line.startswith(":"):
                continue
            key, _, value = line.partition(":")
            ev[key] = value.removeprefix(" ")
        if "event" in ev:
            ev["data"] = json.loads(ev["data"])
            events.append(ev)
    return events


async def _demo(ctx: JobContext) -> dict:
    await ctx.stage("parse")
    await ctx.log("reading 3 files", tag="code")
    await asyncio.sleep(0.3)  # the client is subscribed by now: these arrive live
    await ctx.log("naming domains", tag="ai", data={"model": "fake"})
    await ctx.log("done reading", tag="code")
    return {"count": 3}


async def test_sse_streams_log_events(client, app):
    runner = app.state.runner
    job_id = await runner.submit("demo", {"n": 3}, _demo)
    async with client.stream("GET", f"/jobs/{job_id}/events") as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        text = "".join([chunk async for chunk in r.aiter_text()])
    events = _parse_sse(text)
    kinds = [e["event"] for e in events]
    assert kinds == ["stage", "log", "log", "log", "done"], kinds
    logs = [e["data"] for e in events if e["event"] == "log"]
    assert [x["tag"] for x in logs] == ["code", "ai", "code"]
    assert logs[0]["text"] == "reading 3 files"
    assert logs[1]["data"] == {"model": "fake"}
    assert events[0]["data"]["text"] == "parse"
    assert events[-1]["data"]["data"] == {"count": 3}
    assert [int(e["id"]) for e in events] == [1, 2, 3, 4, 5]

    job = (await client.get(f"/jobs/{job_id}")).json()
    assert job["status"] == "done" and job["stage"] == "parse" and job["result"] == {"count": 3}
    assert job["started_at"] and job["finished_at"]

    # replay after completion yields the same stored events, and Last-Event-ID resumes
    async with client.stream("GET", f"/jobs/{job_id}/events", headers={"Last-Event-ID": "3"}) as r:
        replay = _parse_sse("".join([c async for c in r.aiter_text()]))
    assert [int(e["id"]) for e in replay] == [4, 5]


async def test_failed_job_streams_error(client, app):
    async def boom(ctx: JobContext) -> dict:
        await ctx.log("about to fail")
        raise RuntimeError("parser exploded")

    job_id = await app.state.runner.submit("demo", {}, boom)
    async with client.stream("GET", f"/jobs/{job_id}/events") as r:
        events = _parse_sse("".join([c async for c in r.aiter_text()]))
    assert [e["event"] for e in events] == ["log", "error"]
    assert "parser exploded" in events[-1]["data"]["text"]
    job = (await client.get(f"/jobs/{job_id}")).json()
    assert job["status"] == "failed" and "parser exploded" in job["error"]


async def test_events_for_other_worker_are_polled(client, app):
    """An event written straight to the table (another worker) still reaches the stream."""
    sm = app.state.sessionmaker
    async with sm() as s:
        job = Job(kind="external", status="running", payload={})
        s.add(job)
        await s.commit()
        job_id = job.id

    async def writer():
        await asyncio.sleep(0.3)
        async with sm() as s:
            s.add(JobEvent(job_id=job_id, seq=1, event="log", tag="code", text="from elsewhere", data={}))
            s.add(JobEvent(job_id=job_id, seq=2, event="done", tag=None, text="done", data={}))
            await s.commit()

    task = asyncio.create_task(writer())
    async with client.stream("GET", f"/jobs/{job_id}/events") as r:
        events = _parse_sse("".join([c async for c in r.aiter_text()]))
    await task
    assert [e["data"]["text"] for e in events] == ["from elsewhere", "done"]


async def test_restart_marks_running_jobs_failed(app):
    sm = app.state.sessionmaker
    async with sm() as s:
        job = Job(kind="scan", status="running", payload={})
        s.add(job)
        await s.commit()
        job_id = job.id
    await app.state.runner.recover()
    async with sm() as s:
        job = await s.get(Job, job_id)
        assert job.status == "failed" and job.error == "api restarted" and job.finished_at
        evs = (await s.execute(select(JobEvent).where(JobEvent.job_id == job_id))).scalars().all()
        assert [(e.event, e.text) for e in evs] == [("error", "api restarted")]


async def test_agent_header_forbidden(client, app):
    async def whoami(user: str = Depends(require_user)) -> dict:
        return {"user": user}

    app.add_api_route("/_test/whoami", whoami, methods=["POST"])
    r = await client.post("/_test/whoami", headers={"X-User": "sme.vicky"})
    assert r.status_code == 200 and r.json() == {"user": "sme.vicky"}
    r = await client.post("/_test/whoami", headers={"X-User": "sme.vicky", "X-Agent": "analyst"})
    assert r.status_code == 403
    r = await client.post("/_test/whoami")
    assert r.status_code == 401
