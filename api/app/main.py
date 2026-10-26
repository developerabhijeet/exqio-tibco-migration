"""FastAPI app factory: settings, database, job runner, CORS and routers.

Run with ``uvicorn api.app.main:app`` (``make api``). The schema is created by Alembic
(``make db``); if it is missing the API still starts and logs how to create it.

``EXIQO_ROLE`` decides what runs here: roles ``api`` and ``all`` hold the job-event listener
(SSE); role ``all`` also runs an in-process job worker (``POD_NAME`` or ``<hostname>-<pid>``) and
hosts the generated services that worker starts, reached in-process (no internal listener).
Role ``worker`` is not this app: it runs ``python -m api.app.worker``.

``AUTH_MODE=oidc`` adds the sign-in routes (``/auth/*``) and puts every router behind a signed-in
user (:func:`~api.app.deps.require_signed_in`), except ``/health``, ``/system/brand`` and the
probes; it refuses to start without ``SESSION_SECRET``, ``OIDC_TENANT_ID`` and ``OIDC_CLIENT_ID``.
"""
from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.app.auth import router as auth_router
from api.app.auth.oidc import Oidc
from api.app.config import Settings
from api.app.connectors.config import install_problems, load_install, secret_names
from api.app.db import make_engine, make_sessionmaker, psycopg_url
from api.app.deps import require_admin_writes, require_signed_in
from api.app.jobs import handlers  # noqa: F401  registers the job kinds
from api.app.jobs.listener import JobListener
from api.app.jobs.registry import WorkerServices
from api.app.jobs.runner import JobRunner, JobWorker, enqueue, worker_name
from api.app.routers import (
    analysis,
    ask,
    build,
    connectors,
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
    system,
    verify,
)
from api.app.routers import memory as memory_router
from api.app.routers import settings as settings_router
from api.app.services.citations import configure_masking
from api.app.services.hosting import Hosting, http_client
from api.app.services.infra import make_fernet
from api.app.services.knowledge import Knowledge
from api.app.services.memory_ingest import open_memory
from api.app.services.memory_migrate import MIGRATE_KIND, legacy_pending
from api.app.services.settings import apply_stored, load_stored
from api.app.services.verify import clear_stale_runs
from engine.parity import xslt

log = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    """The API app. With ``api_base_path`` set (``/api`` in the cluster, where the ingress routes
    that prefix without rewriting it) an outer app mounts the API there, so every route and every
    ``url_for`` link carries the prefix. The outer app keeps ``/healthz`` and ``/readyz`` at the
    root for probes, shares the API's state and runs its lifespan (Starlette does not run a
    mounted app's)."""
    settings = settings or Settings()
    inner = _create_api(settings)
    base = settings.api_base_path.rstrip("/")
    if not base:
        return inner
    outer = FastAPI(title=inner.title, version=inner.version, lifespan=_delegate(inner), docs_url=None, redoc_url=None, openapi_url=None)
    outer.state = inner.state
    outer.add_api_route("/healthz", system.healthz, methods=["GET"], tags=["system"])
    outer.add_api_route("/readyz", system.readyz, methods=["GET"], tags=["system"])
    outer.mount(base, inner)
    return outer


def _delegate(inner: FastAPI):
    @asynccontextmanager
    async def lifespan(_outer: FastAPI):
        async with inner.router.lifespan_context(inner):
            yield

    return lifespan


def check_auth_settings(settings: Settings) -> None:
    if settings.auth_mode != "oidc":
        return
    missing = [name.upper() for name in ("session_secret", "oidc_tenant_id", "oidc_client_id")
               if not getattr(settings, name).strip()]
    if missing:
        raise RuntimeError(f"AUTH_MODE=oidc needs {', '.join(missing)} (or the *_FILE variant)")


def check_connector_settings(settings: Settings) -> None:
    """A malformed ``CONNECTORS`` or ``CONNECTOR_SECRET_NAMES`` stops the start, not a request. An
    enabled connector whose install URL is off its own ``allowedHosts`` is logged as an error at
    start-up (and is not ready: ``BaseConnector.ready`` refuses it), so it does not first show up
    as a refused request."""
    try:
        install = load_install(settings)
        secret_names(settings)
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc
    for kind, problem in install_problems(
            install, allow_insecure_loopback=settings.connectors_allow_insecure_loopback).items():
        log.error("connector %s is not ready: %s", kind, problem)


def _create_api(settings: Settings) -> FastAPI:
    if settings.exiqo_role == "worker":
        raise RuntimeError("EXIQO_ROLE=worker runs python -m api.app.worker, not the HTTP app")
    check_auth_settings(settings)
    check_connector_settings(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        xslt.warm_up()  # SaxonC's GraalVM isolate must be created on the main thread
        engine = make_engine(settings.database_url)
        sessionmaker = make_sessionmaker(engine)
        fernet, fernet_generated = make_fernet(settings)
        name = worker_name()
        # only role all's worker starts services here; in role api nothing ever registers one
        hosting = Hosting(settings, sessionmaker, owner=name)
        memory = await open_memory(settings)
        knowledge = Knowledge(memory, sessionmaker, settings)
        worker_services = WorkerServices(settings=settings, knowledge=knowledge, sessionmaker=sessionmaker,
                                         hosting=hosting, fernet=fernet, memory=memory)
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
        app.state.memory = memory  # None when NEO4J_URI is empty
        app.state.background = set()  # fire-and-forget tasks (project seeding), awaited on shutdown
        app.state.hosting = hosting
        app.state.http = http_client()  # the run proxy's and the worker host clients' connections
        app.state.fernet, app.state.fernet_generated = fernet, fernet_generated
        settings.uploads_dir.mkdir(parents=True, exist_ok=True)
        try:
            await runner.start()
            async with sessionmaker() as s:
                apply_stored(settings, await load_stored(s))
                migrate = bool(settings.neo4j_uri) and await legacy_pending(s)
            if migrate:  # the Postgres knowledge schema of an earlier release: copy it once
                await enqueue(sessionmaker, MIGRATE_KIND, {}, dedupe_key=MIGRATE_KIND)
            app.state.settings_loaded_at = time.monotonic()
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
            if app.state.oidc is not None:
                await app.state.oidc.aclose()
            await runner.stop()
            if listener is not None:
                await listener.stop()
            await engine.dispose()
            if memory is not None:
                await memory.close()

    # The interactive docs and the OpenAPI schema are a development aid; with sign-in on (oidc, the
    # installed product) they are not served, so nobody unauthenticated can map the API.
    docs = {} if settings.auth_mode == "dev" else {"docs_url": None, "redoc_url": None, "openapi_url": None}
    app = FastAPI(title="ExiQO Migrate API", version="0.1.0", lifespan=lifespan, **docs)
    app.state.settings = settings
    configure_masking(settings.sensitive_fields)
    app.state.oidc = Oidc(settings) if settings.auth_mode == "oidc" else None
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origin_list, allow_credentials=True,
                       allow_methods=["*"], allow_headers=["*"])
    # oidc: a signed-in user with a role for everything but the public routes (dev: a no-op)
    guarded = [Depends(require_signed_in)]
    for module in (projects, processes, jobs, domains, analysis, gates, ask, design, build, memory_router):
        app.include_router(module.router, dependencies=guarded)
    app.include_router(run_proxy.router)  # its own check: the session cookie, never the bearer
    for module in (verify, infra, readiness, connectors):  # connectors: AdminDep on its writes
        app.include_router(module.router, dependencies=guarded)
    for module in (settings_router, history):  # oidc: writing these needs admin
        app.include_router(module.router, dependencies=[*guarded, Depends(require_admin_writes)])
    app.include_router(system.router)  # probes and brand public, /system/status admin only
    if settings.auth_mode == "oidc":
        app.include_router(auth_router.router)

    @app.get("/health", tags=["health"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "provider": settings.resolved_provider()}

    return app


app = create_app()
