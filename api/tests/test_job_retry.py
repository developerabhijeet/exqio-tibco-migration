"""The job queue's retry rules: ``RetryLater`` with ``not_before``, per-kind attempts, dedupe, and
internal kinds kept off the History screen."""
from __future__ import annotations

import asyncio
import time
from datetime import timedelta

import pytest
from sqlalchemy import select

from api.app.db import utcnow
from api.app.jobs import registry
from api.app.jobs.registry import RetryLater, max_attempts_for, register
from api.app.jobs.runner import JobWorker, enqueue
from api.app.models import Job, JobEvent

TEST_KINDS = ("retry-test", "retry-fail", "retry-lock", "noop-test", "slow-test", "retry-setting")


@pytest.fixture(autouse=True)
def unregister_test_kinds():
    async def noop(ctx, w):
        return {}

    async def slow(ctx, w):
        await asyncio.sleep(60)
        return {}

    register("noop-test", noop)
    register("slow-test", slow, max_attempts=5)
    yield
    for kind in TEST_KINDS:
        registry.unregister(kind)


@pytest.fixture
async def workers():
    made: list[JobWorker] = []
    yield made
    for w in made:
        await w.stop()


def make_worker(app, made: list[JobWorker], name: str, *, stale_s: int = 90) -> JobWorker:
    w = JobWorker(app.state.sessionmaker, app.state.worker_services, name=name, slots=2,
                  heartbeat_s=1, stale_s=stale_s, max_attempts=2, reap_s=0.5, poll_s=0.1)
    made.append(w)
    return w


async def get(app, job_id: str) -> Job:
    async with app.state.sessionmaker() as s:
        return await s.get(Job, job_id)


async def wait_terminal(app, job_id: str, timeout: float = 10.0) -> Job:
    async with asyncio.timeout(timeout):
        while True:
            job = await get(app, job_id)
            if job.status in ("done", "failed"):
                return job
            await asyncio.sleep(0.05)


async def events(app, job_id: str) -> list[JobEvent]:
    async with app.state.sessionmaker() as s:
        return list((await s.execute(select(JobEvent).where(JobEvent.job_id == job_id)
                                     .order_by(JobEvent.seq))).scalars())


async def always_retry(ctx, w):
    raise RetryLater(0.05, "neo4j down")


def busy_then_ok(times: int):
    calls: list[int] = []

    async def handler(ctx, w):
        calls.append(1)
        if len(calls) <= times:
            raise RetryLater(0.05, "lock busy", count_attempt=False)
        return {"calls": len(calls)}
    return handler


# ----------------------------------------------------------------- RetryLater
async def test_retry_later_requeues_with_not_before(app):
    calls: list[float] = []

    async def flaky(ctx, w):
        calls.append(time.monotonic())
        if len(calls) < 3:
            raise RetryLater(0.2, "neo4j down")
        return {"ok": True}

    register("retry-test", flaky, max_attempts=5)
    job_id = await app.state.runner.submit("retry-test", {})
    job = await wait_terminal(app, job_id)
    assert job.status == "done" and job.attempts == 3 and calls[2] - calls[1] >= 0.2
    logs = [e.text for e in await events(app, job_id) if e.event == "log"]
    assert logs == ["retry in 0.2 s: neo4j down"] * 2
    assert [e.tag for e in await events(app, job_id) if e.event == "log"] == ["code", "code"]


async def test_retry_gives_up_after_max_attempts(app):
    register("retry-fail", always_retry, max_attempts=2)
    job_id = await app.state.runner.submit("retry-fail", {})
    job = await wait_terminal(app, job_id)
    assert job.status == "failed" and job.error == "neo4j down after 2 attempts"
    last = (await events(app, job_id))[-1]
    assert (last.event, last.text) == ("error", "neo4j down after 2 attempts")


async def test_retry_give_up_text_is_formatted(app):
    async def custom(ctx, w):
        raise RetryLater(0.05, "neo4j down", give_up="memory unavailable after {attempts} attempts: "
                                                     "ServiceUnavailable")

    register("retry-fail", custom, max_attempts=2)
    job = await wait_terminal(app, await app.state.runner.submit("retry-fail", {}))
    assert job.error == "memory unavailable after 2 attempts: ServiceUnavailable"
    assert RetryLater(1, "x").give_up_text(4) == "x after 4 attempts"
    assert RetryLater(1, "x", give_up="{reason}/{attempts}").give_up_text(4) == "x/4"


async def test_uncounted_retry_does_not_use_an_attempt(app):
    register("retry-lock", busy_then_ok(2), max_attempts=1)
    job = await wait_terminal(app, await app.state.runner.submit("retry-lock", {}))
    assert job.status == "done" and job.attempts == 1 and job.result == {"calls": 3}


async def test_not_before_in_the_future_is_not_claimed(app_no_worker, workers):
    sm = app_no_worker.state.sessionmaker
    job_id = await enqueue(sm, "noop-test", {})
    async with sm() as s:
        job = await s.get(Job, job_id)
        job.not_before = utcnow() + timedelta(hours=1)
        await s.commit()
    w = make_worker(app_no_worker, workers, "w1")
    assert await w.claim() is None
    async with sm() as s:
        job = await s.get(Job, job_id)
        job.not_before = utcnow() - timedelta(seconds=1)
        await s.commit()
    claimed = await w.claim()
    assert claimed is not None and claimed[0] == job_id


# ----------------------------------------------------------------- per-kind attempts
async def test_max_attempts_for_kinds(settings):
    register("retry-test", always_retry, max_attempts=5)
    register("retry-setting", always_retry, max_attempts="memory_ingest_max_attempts")
    register("retry-fail", always_retry)
    assert max_attempts_for("retry-test", settings) == 5
    assert max_attempts_for("retry-setting", settings) == settings.memory_ingest_max_attempts
    assert max_attempts_for("retry-fail", settings) == settings.job_max_attempts
    assert max_attempts_for("never-registered", settings) == settings.job_max_attempts
    registry.unregister("retry-test")
    assert max_attempts_for("retry-test", settings) == settings.job_max_attempts


async def test_named_setting_is_read_when_the_job_finishes(app):
    app.state.worker_services.settings.memory_ingest_max_attempts = 3
    register("retry-setting", always_retry, max_attempts="memory_ingest_max_attempts")
    job = await wait_terminal(app, await app.state.runner.submit("retry-setting", {}))
    assert job.error == "neo4j down after 3 attempts"


async def test_drain_requeues_up_to_the_kinds_max_attempts(app_no_worker, workers):
    """``slow-test`` allows 5 attempts while the worker's global max is 2 (ruling R4)."""
    job_id = await enqueue(app_no_worker.state.sessionmaker, "slow-test", {})
    for i in range(3):
        w = make_worker(app_no_worker, workers, f"w{i}")
        await w.start()
        async with asyncio.timeout(10):
            while (await get(app_no_worker, job_id)).status != "running":
                await asyncio.sleep(0.05)
        await w.drain(timeout_s=0.1)
        job = await get(app_no_worker, job_id)
        assert (job.status, job.attempts) == ("queued", i + 1)


async def test_reaper_requeues_a_lost_job_of_a_retrying_kind(app_no_worker, workers):
    sm = app_no_worker.state.sessionmaker
    async with sm() as s:
        retrying = Job(kind="slow-test", status="running", payload={}, owner="dead", attempts=1,
                       heartbeat_at=utcnow() - timedelta(seconds=200))
        exhausted = Job(kind="slow-test", status="running", payload={}, owner="dead", attempts=5,
                        heartbeat_at=utcnow() - timedelta(seconds=200))
        plain = Job(kind="noop-test", status="running", payload={}, owner="dead", attempts=1,
                    heartbeat_at=utcnow() - timedelta(seconds=200))
        s.add_all([retrying, exhausted, plain])
        await s.commit()
        ids = retrying.id, exhausted.id, plain.id
    assert await make_worker(app_no_worker, workers, "w2").reap() == 3
    r, e, p = [await get(app_no_worker, i) for i in ids]
    assert (r.status, r.owner, r.heartbeat_at, r.attempts) == ("queued", None, None, 1)
    assert [(ev.event, ev.text) for ev in await events(app_no_worker, r.id)] == [
        ("log", "worker lost; requeued (attempt 1 of 5)")]
    assert (e.status, e.error) == ("failed", "worker lost")
    assert (p.status, p.error) == ("failed", "worker lost")  # kinds without their own max keep failing


async def test_reaper_reads_max_attempts_from_the_stored_settings(app_no_worker, workers, monkeypatch):
    """The reaper uses the settings a job would run with (stored edits applied), not the copy the
    worker started with."""
    from api.app.services import settings as settings_svc

    real = settings_svc.with_stored
    monkeypatch.setattr(settings_svc, "with_stored", lambda base, stored: real(base, stored).model_copy(
        update={"memory_ingest_max_attempts": 1}))
    register("retry-setting", always_retry, max_attempts="memory_ingest_max_attempts")
    assert app_no_worker.state.worker_services.settings.memory_ingest_max_attempts > 1
    async with app_no_worker.state.sessionmaker() as s:
        lost = Job(kind="retry-setting", status="running", payload={}, owner="dead", attempts=1,
                   heartbeat_at=utcnow() - timedelta(seconds=200))
        s.add(lost)
        await s.commit()
    await make_worker(app_no_worker, workers, "w3").reap()
    job = await get(app_no_worker, lost.id)
    assert (job.status, job.error) == ("failed", "worker lost")


# ----------------------------------------------------------------- enqueue
async def test_enqueue_dedupes_on_queued_dedupe_key(app_no_worker):
    sm = app_no_worker.state.sessionmaker
    a = await enqueue(sm, "noop-test", {"dedupe_key": "p|estate"})
    b = await enqueue(sm, "noop-test", {"dedupe_key": "p|estate"})
    assert a is not None and b is None


async def test_concurrent_enqueues_of_one_key_store_one_job(app_no_worker):
    sm = app_no_worker.state.sessionmaker
    ids = await asyncio.gather(*(enqueue(sm, "noop-test", {}, dedupe_key="p|race") for _ in range(8)))
    assert sum(i is not None for i in ids) == 1
    async with sm() as s:
        rows = (await s.execute(select(Job).where(Job.kind == "noop-test"))).scalars().all()
    assert len(rows) == 1


async def test_enqueue_dedupe_key_argument_and_scope(app_no_worker):
    sm = app_no_worker.state.sessionmaker
    a = await enqueue(sm, "noop-test", {"x": 1}, project_id="p1", process_id="q1", dedupe_key="k",
                      affinity="w-a")
    job = await get(app_no_worker, a)
    assert (job.payload, job.project_id, job.process_id, job.affinity, job.status, job.attempts) == (
        {"x": 1, "dedupe_key": "k"}, "p1", "q1", "w-a", "queued", 0)
    assert await enqueue(sm, "noop-test", {}, dedupe_key="k") is None
    assert await enqueue(sm, "slow-test", {}, dedupe_key="k") is not None  # another kind
    assert await enqueue(sm, "noop-test", {}) is not None  # no key: never deduped
    assert await enqueue(sm, "noop-test", {}) is not None
    async with sm() as s:
        job = await s.get(Job, a)
        job.status = "running"
        await s.commit()
    assert await enqueue(sm, "noop-test", {}, dedupe_key="k") is not None  # only queued jobs count


async def test_enqueue_wakes_the_local_worker(app):
    """The in-process worker claims at once instead of at its next poll."""
    app.state.runner.worker.poll_s = 30
    await asyncio.sleep(0.7)  # let the claim loop pick up the longer poll
    job = await wait_terminal(app, await enqueue(app.state.sessionmaker, "noop-test", {}), timeout=5)
    assert job.status == "done"


# ----------------------------------------------------------------- internal kinds
async def test_internal_jobs_hidden_from_history(app_no_worker, client, project_id):
    app = app_no_worker  # no worker claims the queued ingest
    await enqueue(app.state.sessionmaker, "memory.ingest", {"project_id": project_id},
                  project_id=project_id)

    def kinds(body):
        return {e.get("job_kind") for e in body["timeline"] if e["kind"] == "job"}

    hidden = (await client.get("/history", params={"project_id": project_id})).json()
    shown = (await client.get("/history", params={"project_id": project_id, "include_internal": True})).json()
    assert "memory.ingest" not in kinds(hidden)
    assert "memory.ingest" in kinds(shown)
    assert shown["totals"]["all"]["jobs"] == hidden["totals"]["all"]["jobs"] + 1
    assert all(s["job_kind"] != "memory.ingest" for s in hidden["sessions"])


async def test_internal_jobs_not_counted_by_readiness(app_no_worker, client, project_id):
    await enqueue(app_no_worker.state.sessionmaker, "memory.ingest", {"project_id": project_id},
                  project_id=project_id)
    body = (await client.get(f"/projects/{project_id}/readiness")).json()
    row = next(r for r in body["rows"] if r["key"] == "tokenomics")
    assert row["status"] == "missing"  # the one queued job is internal: nothing has run yet
