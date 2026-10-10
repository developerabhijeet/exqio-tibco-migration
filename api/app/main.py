"""FastAPI app factory: settings, database, job runner, CORS and routers.

Run with ``uvicorn api.app.main:app`` (``make api``). The schema is created by Alembic
(``make db``); if it is missing the API still starts and logs how to create it.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.app.config import Settings
from api.app.db import make_engine, make_sessionmaker, psycopg_url
from api.app.jobs.runner import JobRunner
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
    verify,
)
from api.app.routers import settings as settings_router
from api.app.services.infra import make_fernet
from api.app.services.knowledge import Knowledge
from api.app.services.settings import apply_stored, load_stored

log = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = make_engine(settings.database_url)
        sessionmaker = make_sessionmaker(engine)
        runner = JobRunner(sessionmaker, workers=settings.job_workers)
        app.state.settings = settings
        app.state.engine = engine
        app.state.sessionmaker = sessionmaker
        app.state.runner = runner
        app.state.scan_lock = asyncio.Lock()
        app.state.analyse_lock = asyncio.Lock()
        app.state.knowledge = Knowledge(psycopg_url(settings.database_url))
        app.state.scenario_lock = asyncio.Lock()  # one scenario-database user at a time (reseeds)
        app.state.services = {}  # build id -> running generated service (verify.RunInfo)
        app.state.fernet, app.state.fernet_generated = make_fernet(settings)
        settings.uploads_dir.mkdir(parents=True, exist_ok=True)
        try:
            await runner.start()
            async with sessionmaker() as s:
                apply_stored(settings, await load_stored(s))
        except Exception as exc:  # noqa: BLE001  start without a database; requests will report it
            log.error("job runner not started (database unavailable or schema missing; run `make db`): %s",
                      exc)
        try:
            yield
        finally:
            for info in list(app.state.services.values()):
                try:
                    await asyncio.to_thread(info.handle.stop)
                except Exception:  # best effort on shutdown
                    log.exception("could not stop the service of build %s", info.build_id)
            app.state.services.clear()
            await runner.stop()
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
