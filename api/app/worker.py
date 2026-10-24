"""The job worker process: ``python -m api.app.worker`` (role ``worker``, ``make worker``).

API pods only queue jobs; worker pods run them. This process builds the same
:class:`~api.app.jobs.registry.WorkerServices` the API builds in role ``all``, runs one
:class:`~api.app.jobs.runner.JobWorker` named ``POD_NAME`` (else ``<hostname>-<pid>``) with
``JOB_WORKERS`` slots, and serves a small internal app on ``0.0.0.0:INTERNAL_PORT`` for the kubelet
(``GET /internal/healthz``) and the API pods: the generated services this worker hosts (Verify),
their status, logs and stop, and a streaming proxy to them (``/internal/services/{build_id}...``,
see :mod:`api.app.services.hosting`). Everything but healthz requires
``Authorization: Bearer INTERNAL_TOKEN``; the worker refuses to start without one. At start it
clears the service runs its name recorded in an earlier life; at shutdown it stops the services.

On SIGTERM (a rollout or scale-down) it stops claiming, gives running jobs ``JOB_DRAIN_SECONDS``
(570, inside the pod's 600 s grace period) to finish, requeues the rest, and exits 0. Uvicorn's own
signal handling is switched off so a signal cannot stop the listener before the drain is done.
"""
from __future__ import annotations

import asyncio
import contextlib
import hmac
import importlib
import logging
import os
import signal
from typing import Annotated, Any

import httpx
import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response

from api.app.config import Settings
from api.app.db import make_engine, make_sessionmaker, psycopg_url
from api.app.jobs import handlers  # noqa: F401  registers the job kinds
from api.app.jobs.registry import WorkerServices
from api.app.jobs.runner import JobWorker, worker_name
from api.app.services import hosting as hosting_svc
from api.app.services.hosting import Hosting
from api.app.services.infra import make_fernet
from api.app.services.knowledge import Knowledge
from api.app.services.settings import apply_stored, load_stored

log = logging.getLogger("api.app.worker")

TEST_HANDLERS_MODULE = "api.tests.support_handlers"  # only with EXIQO_TEST_HANDLERS=1


def internal_app(hosting: Hosting | None = None, token: str = "",
                 http: httpx.AsyncClient | None = None) -> FastAPI:
    """The worker's cluster-internal app. Without ``hosting`` only healthz is served."""
    app = FastAPI(title="ExiQO Migrate worker", docs_url=None, redoc_url=None, openapi_url=None)
    expected = f"Bearer {token}".encode()

    def internal_caller(authorization: Annotated[str | None, Header()] = None) -> None:
        # an empty token never matches: a worker without one serves nothing but healthz
        if not token or not hmac.compare_digest((authorization or "").encode(), expected):
            raise HTTPException(status_code=401, detail="internal token required")

    @app.get("/internal/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    if hosting is None:
        return app
    client = http or hosting_svc.http_client()
    guarded = [Depends(internal_caller)]

    @app.get("/internal/services/{build_id}", dependencies=guarded)
    async def service(build_id: str) -> dict[str, Any]:
        return hosting.status(build_id)

    @app.get("/internal/services/{build_id}/logs", dependencies=guarded)
    async def service_logs(build_id: str) -> dict[str, Any]:
        return hosting.logs(build_id)

    @app.delete("/internal/services/{build_id}", dependencies=guarded)
    async def stop_service(build_id: str) -> dict[str, bool]:
        return {"stopped": await hosting.stop(build_id)}

    @app.api_route("/internal/services/{build_id}/proxy/{path:path}", methods=hosting_svc.PROXY_METHODS,
                   dependencies=guarded)
    async def proxy(build_id: str, path: str, request: Request) -> Response:
        # the API refuses these already; refused again here so no caller can leave the build's root
        if hosting_svc.unsafe_proxy_path(path, request.scope.get("raw_path")):
            raise HTTPException(status_code=400, detail="dot segments are not allowed in a proxy path")
        return await hosting_svc.forward_local(hosting, client, build_id, request.method, path,
                                               request.url.query,
                                               hosting_svc.internal_headers(request.headers.items()),
                                               await request.body())

    return app


class _Server(uvicorn.Server):
    """Uvicorn without its SIGINT/SIGTERM handlers: :func:`run` owns shutdown."""

    @contextlib.contextmanager
    def capture_signals(self):
        yield


async def run(settings: Settings) -> None:
    if not settings.internal_token:
        raise RuntimeError("INTERNAL_TOKEN (or INTERNAL_TOKEN_FILE) is required in role worker: the API "
                           "pods authenticate to this worker's internal listener with it")
    engine = make_engine(settings.database_url)
    sessionmaker = make_sessionmaker(engine)
    fernet, _ = make_fernet(settings)
    name = worker_name()
    hosting = Hosting(settings, sessionmaker, owner=name)
    services = WorkerServices(settings=settings, knowledge=Knowledge(psycopg_url(settings.database_url)),
                              sessionmaker=sessionmaker, hosting=hosting, fernet=fernet)
    async with sessionmaker() as s:  # provider keys and models saved in the Settings screen
        apply_stored(settings, await load_stored(s))
    cleared = await hosting.clear_stale()
    if cleared:
        log.warning("worker %s: cleared %d service run(s) recorded by its previous life", name, cleared)
    worker = JobWorker(sessionmaker, services, name=name, slots=settings.job_workers,
                       heartbeat_s=settings.job_heartbeat_seconds, stale_s=settings.job_stale_seconds,
                       max_attempts=settings.job_max_attempts)
    http = hosting_svc.http_client()
    server = _Server(uvicorn.Config(internal_app(hosting, settings.internal_token, http), host="0.0.0.0",
                                    port=settings.internal_port, log_level="info"))
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
        await hosting.stop_all()
        server.should_exit = True
        await serving
        stopped.cancel()
    finally:
        await http.aclose()
        await engine.dispose()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if os.environ.get("EXIQO_TEST_HANDLERS") == "1":
        importlib.import_module(TEST_HANDLERS_MODULE)
    asyncio.run(run(Settings()))


if __name__ == "__main__":
    main()
