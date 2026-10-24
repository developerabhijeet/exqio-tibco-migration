"""Settings-screen edits reach every process through the ``settings`` table, not one pod's memory.

The PATCH lands on one API replica; a worker started before it must use the edit on its next job,
and another API replica must show it within ``STORED_TTL_SECONDS``.
"""
from __future__ import annotations

import asyncio
from dataclasses import replace

import httpx
import pytest

from api.app.jobs import registry
from api.app.jobs.runner import JobWorker
from api.app.main import create_app
from api.app.models import Job
from api.app.services import settings as svc

USER = {"X-User": "architect.lee"}
NEW_MODEL = "claude-test-strong-after-patch"


@pytest.fixture
def settings(settings):
    settings.exiqo_role = "api"  # the jobs below run on the separate worker only
    settings.job_heartbeat_seconds = 1
    return settings


@pytest.fixture(autouse=True)
def model_kind():
    seen: list[str] = []

    async def report(ctx, w):
        seen.append(w.settings.model_strong)
        await asyncio.sleep(float(ctx.payload.get("sleep", 0)))
        return {"model": w.settings.model_strong}

    registry.register("model-test", report)
    yield seen
    registry.unregister("model-test")


async def _wait_done(app, job_id: str, timeout: float = 10.0) -> Job:
    async with asyncio.timeout(timeout):
        while True:
            async with app.state.sessionmaker() as s:
                job = await s.get(Job, job_id)
            if job.status in ("done", "failed"):
                return job
            await asyncio.sleep(0.05)


async def test_worker_started_before_the_patch_uses_it(client, app):
    # the worker pod: its own Settings object, loaded before the edit
    services = replace(app.state.worker_services, settings=app.state.settings.model_copy())
    old = services.settings.model_strong
    assert old != NEW_MODEL
    w = JobWorker(app.state.sessionmaker, services, name="w-settings", slots=2, heartbeat_s=1,
                  stale_s=90, max_attempts=2, poll_s=0.1)
    await w.start()
    try:
        running = await app.state.runner.submit("model-test", {"sleep": 1.5})
        await asyncio.sleep(0.5)  # claimed and running before the edit
        r = await client.put("/settings", json={"models": {"strong": NEW_MODEL}}, headers=USER)
        assert r.status_code == 200, r.text
        after = await app.state.runner.submit("model-test", {})
        assert (await _wait_done(app, after)).result == {"model": NEW_MODEL}
        # the job already running kept the values it started with
        assert (await _wait_done(app, running)).result == {"model": old}
        assert services.settings.model_strong == old  # the worker's base settings are not mutated
    finally:
        await w.stop()


async def test_second_api_replica_sees_the_patch_within_the_ttl(client, app, settings, monkeypatch):
    monkeypatch.setattr(svc, "STORED_TTL_SECONDS", 0.5)
    other = create_app(settings.model_copy())
    async with other.router.lifespan_context(other):
        assert other.state.settings is not app.state.settings
        r = await client.put("/settings", json={"models": {"strong": NEW_MODEL}}, headers=USER)
        assert r.status_code == 200, r.text
        transport = httpx.ASGITransport(app=other)
        async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=10) as b:
            async with asyncio.timeout(3):
                while True:
                    view = (await b.get("/settings", headers=USER)).json()
                    if view["models"]["tiers"]["strong"]["claude"] == NEW_MODEL:
                        break
                    await asyncio.sleep(0.1)
        assert other.state.settings.model_strong == NEW_MODEL


async def test_refresh_stale_waits_for_the_ttl(app):
    state = app.state
    async with state.sessionmaker() as s:
        from api.app.models import Setting
        s.add(Setting(key="models", value={"strong": NEW_MODEL}, updated_by="t"))
        await s.commit()
    await svc.refresh_stale(state, ttl=60)  # loaded at start, well inside 60 s
    assert state.settings.model_strong != NEW_MODEL
    await svc.refresh_stale(state, ttl=0)
    assert state.settings.model_strong == NEW_MODEL
