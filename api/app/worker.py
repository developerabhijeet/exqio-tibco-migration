"""The job worker process: ``python -m api.app.worker`` (role ``worker``, ``make worker``).

API pods only queue jobs; worker pods run them. This process builds the same
:class:`~api.app.jobs.registry.WorkerServices` the API builds in role ``all``, runs one
:class:`~api.app.jobs.runner.JobWorker` named ``POD_NAME`` (else ``<hostname>-<pid>``) with
``JOB_WORKERS`` slots, and serves a small internal app on ``0.0.0.0:INTERNAL_PORT`` for the kubelet
and the API pods (only ``GET /internal/healthz`` for now).

On SIGTERM (a rollout or scale-down) it stops claiming, gives running jobs ``JOB_DRAIN_SECONDS``
(570, inside the pod's 600 s grace period) to finish, requeues the rest, and exits 0. Uvicorn's own
signal handling is switched off so a signal cannot stop the listener before the drain is done.
"""
from __future__ import annotations

import asyncio
import contextlib
import importlib
import logging
import os
import signal

import uvicorn
from fastapi import FastAPI

from api.app.config import Settings
from api.app.db import make_engine, make_sessionmaker, psycopg_url
from api.app.jobs import handlers  # noqa: F401  registers the job kinds
from api.app.jobs.registry import WorkerServices
from api.app.jobs.runner import JobWorker, worker_name
from api.app.services.infra import make_fernet
from api.app.services.knowledge import Knowledge
from api.app.services.settings import apply_stored, load_stored

log = logging.getLogger("api.app.worker")

TEST_HANDLERS_MODULE = "api.tests.support_handlers"  # only with EXIQO_TEST_HANDLERS=1


def internal_app() -> FastAPI:
    app = FastAPI(title="ExiQO Migrate worker", docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/internal/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


class _Server(uvicorn.Server):
    """Uvicorn without its SIGINT/SIGTERM handlers: :func:`run` owns shutdown."""

    @contextlib.contextmanager
    def capture_signals(self):
        yield


async def run(settings: Settings) -> None:
    engine = make_engine(settings.database_url)
    sessionmaker = make_sessionmaker(engine)
    fernet, _ = make_fernet(settings)
    services = WorkerServices(settings=settings, knowledge=Knowledge(psycopg_url(settings.database_url)),
                              sessionmaker=sessionmaker, services={}, fernet=fernet)
    async with sessionmaker() as s:  # provider keys and models saved in the Settings screen
        apply_stored(settings, await load_stored(s))
    worker = JobWorker(sessionmaker, services, name=worker_name(), slots=settings.job_workers,
                       heartbeat_s=settings.job_heartbeat_seconds, stale_s=settings.job_stale_seconds,
                       max_attempts=settings.job_max_attempts)
    server = _Server(uvicorn.Config(internal_app(), host="0.0.0.0", port=settings.internal_port,
                                    log_level="info"))
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    try:
        await worker.start()
        log.info("worker %s: %d slot(s), internal listener on port %d", worker.name, worker.slots,
                 settings.internal_port)
        serving = asyncio.create_task(server.serve(), name="internal-listener")
        stopped = asyncio.create_task(stop.wait())
        await asyncio.wait({serving, stopped}, return_when=asyncio.FIRST_COMPLETED)
        log.info("worker %s: draining (up to %d s)", worker.name, settings.job_drain_seconds)
        await worker.drain(timeout_s=settings.job_drain_seconds)
        server.should_exit = True
        await serving
        stopped.cancel()
    finally:
        await engine.dispose()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if os.environ.get("EXIQO_TEST_HANDLERS") == "1":
        importlib.import_module(TEST_HANDLERS_MODULE)
    asyncio.run(run(Settings()))


if __name__ == "__main__":
    main()
