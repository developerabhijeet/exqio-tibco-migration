"""FastAPI app factory: settings, database, job runner, CORS and routers.

Run with ``uvicorn api.app.main:app`` (``make api``). The schema is created by Alembic
(``make db``); if it is missing the API still starts and logs how to create it.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.app.config import Settings
from api.app.db import make_engine, make_sessionmaker
from api.app.jobs.runner import JobRunner
from api.app.routers import jobs, processes, projects

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
        settings.uploads_dir.mkdir(parents=True, exist_ok=True)
        try:
            await runner.start()
        except Exception as exc:  # noqa: BLE001  start without a database; requests will report it
            log.error("job runner not started (database unavailable or schema missing; run `make db`): %s",
                      exc)
        try:
            yield
        finally:
            await runner.stop()
            await engine.dispose()

    app = FastAPI(title="ExiQO Migrate API", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origin_list, allow_credentials=True,
                       allow_methods=["*"], allow_headers=["*"])
    app.include_router(projects.router)
    app.include_router(processes.router)
    app.include_router(jobs.router)

    @app.get("/health", tags=["health"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "provider": settings.resolved_provider()}

    return app


app = create_app()
