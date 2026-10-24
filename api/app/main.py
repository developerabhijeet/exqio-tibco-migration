"""FastAPI app factory: settings, database, job runner, CORS and routers.

Run with ``uvicorn api.app.main:app`` (``make api``). The schema is created by Alembic
(``make db``); if it is missing the API still starts and logs how to create it.

``EXIQO_ROLE`` decides what runs here: roles ``api`` and ``all`` hold the job-event listener
(SSE); role ``all`` also runs an in-process job worker (``POD_NAME`` or ``<hostname>-<pid>``) and
hosts the generated services that worker starts, reached in-process (no internal listener).
Role ``worker`` is not this app: it runs ``python -m api.app.worker``.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.app.config import Settings
from api.app.db import make_engine, make_sessionmaker, psycopg_url
from api.app.jobs import handlers  # noqa: F401  registers the job kinds
from api.app.jobs.listener import JobListener
from api.app.jobs.registry import WorkerServices
from api.app.jobs.runner import JobRunner, JobWorker, worker_name
from api.app.routers import (
    analysis,
    ask,
    build,
    design,
    domains,
    gates,
    history,
    infra,
    jobs,
    processes,
    projects,
    readiness,
    run_proxy,
    verify,
)
from api.app.routers import settings as settings_router
from api.app.services.hosting import Hosting, http_client
from api.app.services.infra import make_fernet
from api.app.services.knowledge import Knowledge
from api.app.services.settings import apply_stored, load_stored
from api.app.services.verify import clear_stale_runs

log = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    if settings.exiqo_role == "worker":
        raise RuntimeError("EXIQO_ROLE=worker runs python -m api.app.worker, not the HTTP app")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = make_engine(settings.database_url)
        sessionmaker = make_sessionmaker(engine)
        knowledge = Knowledge(psycopg_url(settings.database_url))
        fernet, fernet_generated = make_fernet(settings)
        name = worker_name()
        # only role all's worker starts services here; in role api nothing ever registers one
        hosting = Hosting(settings, sessionmaker, owner=name)
        worker_services = WorkerServices(settings=settings, knowledge=knowledge, sessionmaker=sessionmaker,
                                         hosting=hosting, fernet=fernet)
        worker = None
        if settings.exiqo_role == "all":
            worker = JobWorker(sessionmaker, worker_services, name=name,
                               slots=settings.job_workers, heartbeat_s=settings.job_heartbeat_seconds,
                               stale_s=settings.job_stale_seconds, max_attempts=settings.job_max_attempts)
        runner = JobRunner(sessionmaker, worker=worker)
        listener = None
        if settings.exiqo_role in ("api", "all"):
            listener = JobListener(psycopg_url(settings.database_url))
            await listener.start()
        app.state.settings = settings
        app.state.engine = engine
        app.state.sessionmaker = sessionmaker
        app.state.runner = runner
        app.state.worker_services = worker_services
        app.state.listener = listener
        app.state.knowledge = knowledge
        app.state.background = set()  # fire-and-forget tasks (project seeding), awaited on shutdown
        app.state.hosting = hosting
        app.state.http = http_client()  # the run proxy's and the worker host clients' connections
        app.state.fernet, app.state.fernet_generated = fernet, fernet_generated
        settings.uploads_dir.mkdir(parents=True, exist_ok=True)
        try:
            await runner.start()
            async with sessionmaker() as s:
                apply_stored(settings, await load_stored(s))
            if settings.exiqo_role == "all":  # role api never hosts: the workers clear their own
                cleared = await clear_stale_runs(sessionmaker)
                if cleared:
                    log.warning("cleared %d service run(s) recorded by a previous API process", cleared)
        except Exception as exc:  # noqa: BLE001  start without a database; requests will report it
            log.error("job runner not started (database unavailable or schema missing; run `make db`): %s",
                      exc)
        try:
            yield
        finally:
            if app.state.background:
                await asyncio.gather(*app.state.background, return_exceptions=True)
            try:
                await hosting.stop_all()
            except Exception:  # best effort on shutdown (the database may be gone)
                log.exception("could not stop the generated services")
            await app.state.http.aclose()
            await runner.stop()
            if listener is not None:
                await listener.stop()
            await engine.dispose()

    app = FastAPI(title="ExiQO Migrate API", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origin_list, allow_credentials=True,
                       allow_methods=["*"], allow_headers=["*"])
    app.include_router(projects.router)
    app.include_router(processes.router)
    app.include_router(jobs.router)
    app.include_router(domains.router)
    app.include_router(analysis.router)
    app.include_router(gates.router)
    app.include_router(ask.router)
    app.include_router(design.router)
    app.include_router(build.router)
    app.include_router(run_proxy.router)
    app.include_router(verify.router)
    app.include_router(infra.router)
    app.include_router(settings_router.router)
    app.include_router(history.router)
    app.include_router(readiness.router)

    @app.get("/health", tags=["health"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "provider": settings.resolved_provider()}

    return app


app = create_app()
