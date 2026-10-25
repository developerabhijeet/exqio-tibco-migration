"""Claiming workers: no double claim, heartbeat recovery, cancel, drain requeue, NOTIFY fan-out."""
from __future__ import annotations

import asyncio
import json
import threading
import time
from datetime import timedelta
from typing import Self

import httpx
import pytest
from sqlalchemy import func, select

from api.app.db import make_engine, make_sessionmaker, utcnow
from api.app.jobs import registry
from api.app.jobs.runner import JobWorker
from api.app.main import create_app
from api.app.models import Job, JobEvent

SLOW_SECONDS = 60
USER = {"X-User": "engineer.ravi"}
BLOCK_STALE_SECONDS = 2
BLOCK_SECONDS = BLOCK_STALE_SECONDS + 2  # the loop is stalled longer than a job may go silent


@pytest.fixture
def settings(settings):
    """A 1 s heartbeat so cancel and stale tests stay quick."""
    settings.job_heartbeat_seconds = 1
    return settings


@pytest.fixture(autouse=True)
def test_kinds():
    async def noop(ctx, w):
        await ctx.log("working")
        return {"seen": ctx.payload}

    async def slow(ctx, w):
        await ctx.stage("sleeping")
        await asyncio.sleep(SLOW_SECONDS)
        return {}

    def blocking(ctx, w):  # synchronous work on the loop, like services/analysis.py
        time.sleep(BLOCK_SECONDS)
        return {"blocked": True}

    async def block(ctx, w):
        return blocking(ctx, w)

    async def short(ctx, w):
        await asyncio.sleep(0.5)
        return {"slept": True}

    registry.register("noop-test", noop)
    registry.register("slow-test", slow)
    registry.register("short-test", short)
    registry.register("block-test", block)
    yield
    for kind in ("noop-test", "slow-test", "short-test", "block-test"):
        registry.unregister(kind)


@pytest.fixture
def app_with_slow_handler(app):
    return app


@pytest.fixture
async def workers():
    made: list[JobWorker] = []
    yield made
    for w in made:
        await w.stop()


@pytest.fixture
def make_worker(workers):
    def make(app, name: str, *, stale_s: int = 90, slots: int = 2, max_attempts: int = 2,
             heartbeat_s: float = 1) -> JobWorker:
        w = JobWorker(app.state.sessionmaker, app.state.worker_services, name=name, slots=slots,
                      heartbeat_s=heartbeat_s, stale_s=stale_s, max_attempts=max_attempts, reap_s=0.5, poll_s=0.1)
        workers.append(w)
        return w
    return make


@pytest.fixture
async def app_pair(app, settings):
    """A (role ``all``, the only worker) and B (role ``api``) on one database."""
    other = create_app(settings.model_copy(update={"exiqo_role": "api"}))
    async with other.router.lifespan_context(other):
        yield app, other


# ----------------------------------------------------------------- helpers
async def get(app, job_id: str) -> Job:
    async with app.state.sessionmaker() as s:
        return await s.get(Job, job_id)


async def wait_status(app, job_id: str, statuses: tuple[str, ...], timeout: float = 10.0) -> Job:
    async with asyncio.timeout(timeout):
        while True:
            job = await get(app, job_id)
            if job.status in statuses:
                return job
            await asyncio.sleep(0.05)


async def wait_all_terminal(app, ids: list[str], timeout: float = 20.0) -> None:
    for job_id in ids:
        await wait_status(app, job_id, ("done", "failed"), timeout)


async def owners_of(app, ids: list[str]) -> dict[str, str | None]:
    async with app.state.sessionmaker() as s:
        rows = (await s.execute(select(Job.id, Job.owner).where(Job.id.in_(ids)))).all()
    return {r.id: r.owner for r in rows}


async def count_events(app, ids: list[str], event: str) -> int:
    async with app.state.sessionmaker() as s:
        return await s.scalar(select(func.count()).select_from(JobEvent).where(
            JobEvent.job_id.in_(ids), JobEvent.event == event))


async def insert_job(app, *, status: str, owner: str | None, heartbeat_ago_s: float,
                     kind: str = "noop-test", affinity: str | None = None) -> str:
    async with app.state.sessionmaker() as s:
        job = Job(kind=kind, status=status, payload={}, owner=owner, affinity=affinity,
                  heartbeat_at=utcnow() - timedelta(seconds=heartbeat_ago_s), attempts=1)
        s.add(job)
        await s.commit()
        return job.id


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
            data = json.loads(ev["data"])
            events.append({"event": ev["event"], "id": int(ev["id"]), **data})
    return events


async def collect_sse(client: httpx.AsyncClient, job_id: str, timeout: float) -> list[dict]:
    async with asyncio.timeout(timeout):
        async with client.stream("GET", f"/jobs/{job_id}/events") as r:
            assert r.status_code == 200
            return _parse_sse("".join([c async for c in r.aiter_text()]))


# ----------------------------------------------------------------- tests
async def test_two_workers_never_claim_the_same_job(app_no_worker, make_worker):
    ids = [await app_no_worker.state.runner.submit("noop-test", {"i": i}) for i in range(20)]
    w1, w2 = make_worker(app_no_worker, "w1"), make_worker(app_no_worker, "w2")
    await asyncio.gather(w1.start(), w2.start())
    await wait_all_terminal(app_no_worker, ids)
    owners = await owners_of(app_no_worker, ids)
    assert len(owners) == 20 and all(o in ("w1", "w2") for o in owners.values())
    assert await count_events(app_no_worker, ids, "done") == 20  # each ran exactly once
    async with app_no_worker.state.sessionmaker() as s:
        attempts = (await s.execute(select(Job.attempts).where(Job.id.in_(ids)))).scalars().all()
    assert set(attempts) == {1}


async def test_stale_running_job_fails_live_one_kept(app_no_worker, make_worker):
    stale = await insert_job(app_no_worker, status="running", owner="dead", heartbeat_ago_s=200)
    live = await insert_job(app_no_worker, status="running", owner="w1", heartbeat_ago_s=1)
    await make_worker(app_no_worker, "w2", stale_s=90).reap()
    job = await get(app_no_worker, stale)
    assert job.status == "failed" and job.error == "worker lost" and job.finished_at
    assert (await get(app_no_worker, live)).status == "running"
    async with app_no_worker.state.sessionmaker() as s:
        evs = (await s.execute(select(JobEvent).where(JobEvent.job_id == stale))).scalars().all()
    assert [(e.event, e.text) for e in evs] == [("error", "worker lost")]


async def test_queued_job_for_lost_worker_fails(app_no_worker, make_worker):
    sm = app_no_worker.state.sessionmaker
    orphan = await app_no_worker.state.runner.submit("noop-test", {}, affinity="gone")
    kept = await app_no_worker.state.runner.submit("noop-test", {}, affinity="alive")
    await insert_job(app_no_worker, status="running", owner="alive", heartbeat_ago_s=1)
    async with sm() as s:  # both were queued longer than stale_s ago
        for job_id in (orphan, kept):
            (await s.get(Job, job_id)).created_at = utcnow() - timedelta(seconds=200)
        await s.commit()
    await make_worker(app_no_worker, "w2", stale_s=90).reap()
    assert (await get(app_no_worker, orphan)).error == "worker lost"
    job = await get(app_no_worker, kept)
    assert job.status == "queued" and job.affinity == "alive"


async def test_stream_ends_when_worker_dies(client, app_no_worker, make_worker):
    job = await insert_job(app_no_worker, status="running", owner="dead", heartbeat_ago_s=0)
    reaper = make_worker(app_no_worker, "w2", stale_s=1)
    await reaper.start()
    events = await collect_sse(client, job, timeout=10)
    assert events[-1]["event"] == "error" and events[-1]["text"] == "worker lost"


class ThreadReaper:
    """A reaper on its own thread, event loop and engine, standing in for another worker pod: it
    keeps reaping while the test's loop is blocked."""

    def __init__(self, url: str, stale_s: int) -> None:
        self.url, self.stale_s = url, stale_s
        self.stop = threading.Event()
        self.thread = threading.Thread(target=lambda: asyncio.run(self._run()), daemon=True)

    async def _run(self) -> None:
        engine = make_engine(self.url)
        try:
            w = JobWorker(make_sessionmaker(engine), None, name="reaper", slots=1, heartbeat_s=1,
                          stale_s=self.stale_s, max_attempts=2)
            while not self.stop.is_set():
                await w.reap()
                await asyncio.sleep(0.2)
        finally:
            await engine.dispose()

    def __enter__(self) -> Self:
        self.thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop.set()
        self.thread.join(10)


async def test_heartbeat_survives_blocked_event_loop(app_no_worker, make_worker):
    """The heartbeat runs on the worker's own thread, so a handler that blocks the loop for longer
    than ``stale_s`` is not reaped as lost."""
    app = app_no_worker
    with ThreadReaper(app.state.settings.database_url, BLOCK_STALE_SECONDS):
        job_id = await app.state.runner.submit("block-test", {})
        w1 = make_worker(app, "w1", stale_s=BLOCK_STALE_SECONDS, heartbeat_s=0.5)
        await w1.start()
        job = await wait_status(app, job_id, ("done", "failed"), timeout=BLOCK_SECONDS + 10)
    assert (job.status, job.error, job.result) == ("done", None, {"blocked": True})


async def test_cancel_flag_stops_running_job(client, app_with_slow_handler):
    app = app_with_slow_handler
    job_id = await app.state.runner.submit("slow-test", {})
    await wait_status(app, job_id, ("running",))
    r = await client.post(f"/jobs/{job_id}/cancel", headers=USER)
    assert r.status_code == 202
    started = time.monotonic()
    job = await wait_status(app, job_id, ("done", "failed"),
                            timeout=app.state.settings.job_heartbeat_seconds + 2)
    assert job.status == "failed" and job.error == "cancelled"
    assert time.monotonic() - started < app.state.settings.job_heartbeat_seconds + 2
    events = await collect_sse(client, job_id, timeout=5)
    assert events[-1]["event"] == "error" and events[-1]["text"] == "cancelled"


async def test_cancel_queued_job_fails_it_without_running(client, app_no_worker):
    job_id = await app_no_worker.state.runner.submit("noop-test", {})
    r = await client.post(f"/jobs/{job_id}/cancel", headers=USER)
    assert r.status_code == 202 and r.json()["status"] == "failed"
    job = await get(app_no_worker, job_id)
    assert job.error == "cancelled" and job.started_at is None
    r = await client.post(f"/jobs/{job_id}/cancel", headers=USER)  # already finished
    assert r.status_code == 409
    assert (await client.post("/jobs/nope/cancel", headers=USER)).status_code == 404
    assert (await client.post("/jobs/nope/cancel")).status_code == 401  # a signed-in human only


async def test_drain_requeues_until_max_attempts(app_no_worker, make_worker):
    job_id = await app_no_worker.state.runner.submit("slow-test", {})
    w1 = make_worker(app_no_worker, "w1", max_attempts=2)
    await w1.start()
    await wait_status(app_no_worker, job_id, ("running",))
    await w1.drain(timeout_s=0.2)
    job = await get(app_no_worker, job_id)
    assert (job.status, job.owner, job.attempts) == ("queued", None, 1)

    w2 = make_worker(app_no_worker, "w2", max_attempts=2)
    await w2.start()
    job = await wait_status(app_no_worker, job_id, ("running",))
    assert (job.owner, job.attempts) == ("w2", 2)
    await w2.drain(timeout_s=0.2)
    job = await get(app_no_worker, job_id)
    assert (job.status, job.error, job.attempts) == ("failed", "worker stopped", 2)
    async with app_no_worker.state.sessionmaker() as s:
        evs = (await s.execute(select(JobEvent).where(JobEvent.job_id == job_id)
                               .order_by(JobEvent.seq))).scalars().all()
    # the second attempt continues the event sequence of the first
    assert [e.seq for e in evs] == list(range(1, len(evs) + 1))
    assert (evs[-1].event, evs[-1].text) == ("error", "worker stopped")


async def test_drain_waits_for_jobs_that_finish_in_time(app_no_worker, make_worker):
    job_id = await app_no_worker.state.runner.submit("short-test", {})
    w1 = make_worker(app_no_worker, "w1")
    await w1.start()
    await wait_status(app_no_worker, job_id, ("running",))
    await w1.drain(timeout_s=5)
    job = await get(app_no_worker, job_id)
    assert (job.status, job.result) == ("done", {"slept": True})
    later = await app_no_worker.state.runner.submit("noop-test", {})
    await asyncio.sleep(0.3)
    assert (await get(app_no_worker, later)).status == "queued"  # a drained worker claims nothing


async def test_notify_reaches_other_api_instance(app_pair):
    a, b = app_pair
    job_id = await a.state.runner.submit("noop-test", {"x": 1})
    transport = httpx.ASGITransport(app=b)
    started = time.monotonic()
    async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=10) as client:
        events = await collect_sse(client, job_id, timeout=5)
    assert time.monotonic() - started < 2
    assert [e["event"] for e in events] == ["log", "done"]
    assert [e["id"] for e in events] == [1, 2]


async def test_listener_delivers_notifications(app_no_worker):
    listener = app_no_worker.state.listener
    q = listener.subscribe("job-x")
    try:
        async with app_no_worker.state.sessionmaker() as s:
            from sqlalchemy import text
            await s.execute(text("SELECT pg_notify('job_events', 'job-x:7')"))
            await s.commit()
        async with asyncio.timeout(5):
            assert await q.get() == 7
    finally:
        listener.unsubscribe("job-x", q)


async def test_heartbeat_with_an_asyncpg_ssl_url(app_no_worker):
    """The deployed ``DATABASE_URL`` carries asyncpg's ``?ssl=``: the heartbeat thread's psycopg
    connection must still beat, or every job is reaped "worker lost" (local Postgres has no TLS)."""
    app = app_no_worker
    url = app.state.settings.database_url
    engine = make_engine(url + ("&" if "?" in url else "?") + "ssl=disable")
    w = JobWorker(make_sessionmaker(engine), None, name="w-ssl", slots=1, heartbeat_s=0.5,
                  stale_s=BLOCK_STALE_SECONDS, max_attempts=2, reap_s=0.5, poll_s=0.1)
    assert "sslmode=disable" in w.dsn and "ssl=" not in w.dsn.replace("sslmode=", "")
    try:
        with ThreadReaper(url, BLOCK_STALE_SECONDS):
            job_id = await app.state.runner.submit("block-test", {})
            await w.start()
            job = await wait_status(app, job_id, ("done", "failed"), timeout=BLOCK_SECONDS + 10)
        assert (job.status, job.error) == ("done", None)
    finally:
        await w.stop()
        await engine.dispose()
