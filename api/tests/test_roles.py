"""Process roles: ``api`` only queues, ``worker`` is not an HTTP app, the worker drains on SIGTERM."""
from __future__ import annotations

import asyncio
import os
import signal
import socket
import sys

import httpx
import pytest

from api.app.jobs import registry
from api.app.main import create_app
from api.app.models import Job
from api.tests.conftest import REPO_ROOT
from api.tests.support_handlers import SLOW_KIND


@pytest.fixture
def settings(settings):
    settings.exiqo_role = "api"
    return settings


@pytest.fixture
def noop_kind():
    async def noop(ctx, w):
        return {}

    registry.register("noop-roles-test", noop)
    yield "noop-roles-test"
    registry.unregister("noop-roles-test")


async def get(app, job_id: str) -> Job:
    async with app.state.sessionmaker() as s:
        return await s.get(Job, job_id)


async def wait_for(app, job_id: str, pred, timeout: float) -> Job:
    async with asyncio.timeout(timeout):
        while True:
            job = await get(app, job_id)
            if pred(job):
                return job
            await asyncio.sleep(0.1)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def healthz(port: int, timeout: float = 30) -> dict:
    """The worker's internal listener, retried while uvicorn starts."""
    async with asyncio.timeout(timeout), httpx.AsyncClient(timeout=2) as c:
        while True:
            try:
                r = await c.get(f"http://127.0.0.1:{port}/internal/healthz")
                if r.status_code == 200:
                    return r.json()
            except httpx.TransportError:
                pass
            await asyncio.sleep(0.2)


async def test_api_role_has_no_local_worker(app, noop_kind):
    assert app.state.runner.worker is None
    job_id = await app.state.runner.submit(noop_kind, {})
    await asyncio.sleep(1)
    assert (await get(app, job_id)).status == "queued"


def test_worker_role_rejects_http_app(settings):
    with pytest.raises(RuntimeError, match=r"EXIQO_ROLE=worker runs python -m api\.app\.worker"):
        create_app(settings.model_copy(update={"exiqo_role": "worker"}))


async def test_worker_sigterm_drains(app, settings, tmp_path):
    job_id = await app.state.runner.submit(SLOW_KIND, {})
    port = free_port()
    env = {**os.environ, "EXIQO_ROLE": "worker", "EXIQO_TEST_HANDLERS": "1",
           "POD_NAME": "sigterm-worker", "DATABASE_URL": settings.database_url,
           "DATA_DIR": str(tmp_path / "data"), "PROVIDER": "fake", "INTERNAL_PORT": str(port),
           "INTERNAL_TOKEN": "roles-test-token",
           "JOB_DRAIN_SECONDS": "1", "JOB_HEARTBEAT_SECONDS": "1",
           "PYTHONPATH": str(REPO_ROOT)}
    proc = await asyncio.create_subprocess_exec(sys.executable, "-m", "api.app.worker",
                                                cwd=tmp_path, env=env)
    try:
        job = await wait_for(app, job_id, lambda j: j.status == "running", timeout=60)
        assert (job.owner, job.attempts) == ("sigterm-worker", 1)
        assert await healthz(port) == {"status": "ok"}
        proc.send_signal(signal.SIGTERM)
        async with asyncio.timeout(30):
            code = await proc.wait()
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
    assert code == 0
    job = await get(app, job_id)
    assert (job.status, job.owner, job.attempts) == ("queued", None, 1)
