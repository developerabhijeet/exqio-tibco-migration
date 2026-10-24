"""Jobs are stored data (kind + payload) resolved to registered handlers."""
from __future__ import annotations

import asyncio

import pytest

from api.app.jobs import registry
from api.app.models import Job


@pytest.fixture
def noop_kind():
    async def noop(ctx, w):
        return {"seen": ctx.payload}

    registry.register("noop-test", noop)
    yield "noop-test"
    registry.unregister("noop-test")


async def wait_terminal(app, job_id: str, timeout: float = 10.0) -> Job:
    async with asyncio.timeout(timeout):
        while True:
            async with app.state.sessionmaker() as s:
                job = await s.get(Job, job_id)
            if job.status in ("done", "failed"):
                return job
            await asyncio.sleep(0.05)


async def test_submit_stores_payload_only(app, noop_kind):
    job_id = await app.state.runner.submit(noop_kind, {"x": 1})
    async with app.state.sessionmaker() as s:
        job = await s.get(Job, job_id)
    assert job.payload == {"x": 1} and job.status in ("queued", "running", "done")
    done = await wait_terminal(app, job_id)
    assert done.status == "done" and done.result == {"seen": {"x": 1}}


async def test_unknown_kind_fails_job_with_reason(app):
    job_id = await app.state.runner.submit("does-not-exist", {})
    job = await wait_terminal(app, job_id)
    assert job.status == "failed" and "no handler for job kind 'does-not-exist'" in job.error


def test_handler_for_unknown_kind_raises_keyerror():
    with pytest.raises(KeyError, match="no handler for job kind 'nope'"):
        registry.handler_for("nope")


def test_every_job_kind_is_registered():
    from api.app.jobs import handlers  # noqa: F401

    for kind in ("scan", "discover", "analyse", "design", "build", "verify", "plan", "eval"):
        assert registry.handler_for(kind)
