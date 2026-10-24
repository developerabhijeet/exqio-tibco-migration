"""Generated services hosted on workers, reached by the browser only through the API.

Verify starts the build's generated .NET service with ``dotnet run`` on a loopback port. In the
cluster that happens on a worker pod (the ``service_start`` job), and the worker's
:class:`Hosting` keeps the process. The build row records where it runs: ``run_host`` (the
worker's internal listener, ``WORKER_HOST:INTERNAL_PORT``), ``run_owner`` (the worker's name, so
the Verify job can be pinned to it with ``affinity`` and call the service on loopback) and the
service's ``run_port`` and ``run_pid``.

The API reaches a hosting worker through a :class:`HostClient`: :class:`RemoteHostClient` calls the
worker's internal API over HTTP with ``Authorization: Bearer INTERNAL_TOKEN``;
:class:`LocalHostClient` calls the in-process :class:`Hosting` directly, for builds the API's own
worker hosts (role ``all``, which has no internal listener). Both answer the same shapes. A worker
that no longer answers raises :class:`HostGone`; the caller clears the row and says so
(:data:`GONE_REASON`).

The browser's calls (Swagger UI, "Try it out", the dev token) go to
``/builds/{id}/run/proxy/{path}`` on the API, which streams them to the service. The proxy sends
``X-Forwarded-Host``, ``X-Forwarded-Proto`` and ``X-Forwarded-Prefix`` (the proxy root), which the
generated service honours in Development, so its OpenAPI ``servers`` entry points back through the
proxy. On the API-to-worker hop the caller's own ``Authorization`` (the service's bearer, pasted
into Swagger) travels as ``X-Exiqo-Service-Authorization`` because ``Authorization`` carries the
internal token there; the worker puts it back before calling the service.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Iterable
from typing import Any, Protocol
from urllib.parse import quote

import httpx
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.responses import JSONResponse, Response, StreamingResponse

from api.app.config import Settings
from api.app.jobs.locks import scenario_lock
from api.app.models import Build, Process
from api.app.services import verify as verify_svc
from api.app.services.context import engine_app

log = logging.getLogger(__name__)

GONE_REASON = "the worker hosting the service is gone"
NOT_RUNNING = "the generated service of this build is not running: POST /builds/{id}/run first"
SERVICE_AUTH = "x-exiqo-service-authorization"  # the caller's Authorization on the internal hop

#: Swagger "Try it out" calls can be slow (a scenario hits the database and the stubs); the
#: connect is loopback or in-cluster, so a slow connect means the far end is gone.
PROXY_TIMEOUT = httpx.Timeout(60.0, connect=5.0)
#: Status, logs and stop on the worker's internal API: an unreachable pod should be called gone
#: quickly rather than hold the Verify tab's polling.
CONTROL_TIMEOUT = httpx.Timeout(10.0, connect=3.0)

# RFC 9110 section 7.6.1 connection-specific fields, plus what the proxy recomputes itself
HOP_BY_HOP = frozenset({"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te",
                        "trailer", "trailers", "transfer-encoding", "upgrade", "proxy-connection"})
_REQUEST_DROPPED = HOP_BY_HOP | {"host", "content-length", "cookie", "x-forwarded-host",
                                 "x-forwarded-proto", "x-forwarded-prefix", "x-forwarded-for",
                                 SERVICE_AUTH}


PROXY_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]


class HostGone(Exception):
    """The worker recorded as hosting the service does not answer."""


class HostError(Exception):
    """The hosting worker answered, but not as expected (for example a wrong internal token)."""


def http_client() -> httpx.AsyncClient:
    """The proxy's client: no environment proxies (pod IPs must not go to a corporate proxy) and
    no redirects (the browser follows the service's redirects itself)."""
    return httpx.AsyncClient(timeout=PROXY_TIMEOUT, follow_redirects=False, trust_env=False)


# ------------------------------------------------------------------ the worker side
class Hosting:
    """The generated services this worker process runs, by build id.

    ``start`` and ``stop`` of one build run under one lock, and the build row is written under it
    too, so a stop queued behind a start stops the service that start registered and its null
    write lands after the start's.
    """

    def __init__(self, settings: Settings, sessionmaker: async_sessionmaker[AsyncSession], *,
                 owner: str, address: str | None = None) -> None:
        self.settings = settings
        self.sessionmaker = sessionmaker
        self.owner = owner
        self.address = address or f"{settings.worker_host or '127.0.0.1'}:{settings.internal_port}"
        self._runs: dict[str, verify_svc.RunInfo] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, build_id: str) -> asyncio.Lock:
        return self._locks.setdefault(build_id, asyncio.Lock())

    def get(self, build_id: str) -> verify_svc.RunInfo | None:
        return self._runs.get(build_id)

    async def start(self, build_id: str) -> verify_svc.RunInfo:
        """Start the build's service (``verify.start_service``), or return the one running."""
        async with self._lock(build_id):
            current = self._runs.get(build_id)
            if current is not None and current.running:
                return current
            if current is not None:  # exited: release its stub server and port
                self._runs.pop(build_id, None)
                await asyncio.to_thread(current.stop)
            async with self.sessionmaker() as s:
                build = await s.get(Build, build_id)
                if build is None:
                    raise RuntimeError(f"build {build_id} not found")
                proc = await s.get(Process, build.process_id)
                app = await engine_app(s, proc)
                info = await verify_svc.start_service(self.settings, build, proc.project_id, app,
                                                      scenario_lock(self.sessionmaker))
            try:
                await self._record(info)
            except BaseException:
                await asyncio.to_thread(info.stop)
                raise
            return info

    async def register(self, info: verify_svc.RunInfo) -> None:
        """Adopt a running service (a test's stand-in) as if :meth:`start` had started it."""
        async with self._lock(info.build_id):
            self._runs[info.build_id] = info
            await self._record(info)

    async def _record(self, info: verify_svc.RunInfo) -> None:
        self._runs[info.build_id] = info
        async with self.sessionmaker() as s:
            await s.execute(update(Build).where(Build.id == info.build_id).values(
                run_host=self.address, run_owner=self.owner, run_port=info.port, run_pid=info.handle.pid))
            await s.commit()

    async def stop(self, build_id: str) -> bool:
        """Stop the build's service; clears the row when this worker is still its owner."""
        async with self._lock(build_id):
            info = self._runs.pop(build_id, None)
            if info is not None:
                await asyncio.to_thread(info.stop)
            async with self.sessionmaker() as s:
                await s.execute(update(Build).where(Build.id == build_id, Build.run_owner == self.owner)
                                .values(run_host=None, run_owner=None, run_port=None, run_pid=None))
                await s.commit()
            return info is not None

    async def stop_all(self) -> None:
        for build_id in list(self._runs):
            try:
                await self.stop(build_id)
            except Exception:  # best effort on shutdown
                log.exception("could not stop the service of build %s", build_id)

    async def clear_stale(self) -> int:
        """At start: rows this worker's name owned in an earlier life (a restarted container keeps
        ``POD_NAME``) name services nobody manages any more."""
        return await verify_svc.clear_stale_runs(self.sessionmaker, owner=self.owner)

    def status(self, build_id: str) -> dict[str, Any]:
        info = self.get(build_id)
        return {**verify_svc.run_status(info), "log_tail": verify_svc.read_log(info, 4000)}

    def logs(self, build_id: str) -> dict[str, Any]:
        info = self.get(build_id)
        return {"running": bool(info and info.running), "log": verify_svc.read_log(info)}


# ------------------------------------------------------------------ forwarding
def request_headers(headers: Iterable[tuple[str, str]], *, host: str, proto: str,
                    prefix: str) -> list[tuple[str, str]]:
    """The caller's headers minus hop-by-hop fields and the platform's session cookie, plus the
    X-Forwarded-* the generated service builds its own URLs from."""
    out = [(k, v) for k, v in headers if k.lower() not in _REQUEST_DROPPED]
    return out + [("x-forwarded-host", host), ("x-forwarded-proto", proto), ("x-forwarded-prefix", prefix)]


_PATH_SAFE = "/:@!$&'()*+,;=-._~"  # RFC 3986 pchar: kept as is, everything else percent-encoded


def _target(base: str, path: str, query: str) -> str:
    url = f"{base}/{quote(path.lstrip('/'), safe=_PATH_SAFE)}"
    return f"{url}?{query}" if query else url


async def forward(http: httpx.AsyncClient, method: str, url: str, headers: list[tuple[str, str]],
                  body: bytes) -> Response:
    """Send the call and stream the answer back: status, headers (minus hop-by-hop) and the raw
    body (still content-encoded, so ``Content-Length`` and ``Content-Encoding`` stay true).
    Raises ``httpx.TransportError`` when the far end does not answer."""
    upstream = await http.send(http.build_request(method, url, headers=headers, content=body), stream=True)

    async def body_of() -> AsyncIterator[bytes]:
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:  # also when the browser goes away mid-answer: the connection returns to the pool
            await upstream.aclose()

    response = StreamingResponse(body_of(), status_code=upstream.status_code)
    response.raw_headers = [(k.encode("latin-1"), v.encode("latin-1"))
                            for k, v in upstream.headers.multi_items() if k.lower() not in HOP_BY_HOP]
    return response


def _not_running() -> Response:
    return JSONResponse({"detail": NOT_RUNNING}, status_code=404)


async def forward_local(hosting: Hosting, http: httpx.AsyncClient, build_id: str, method: str, path: str,
                        query: str, headers: list[tuple[str, str]], body: bytes) -> Response:
    """Forward to the service this process hosts, on loopback."""
    info = hosting.get(build_id)
    if info is None or not info.running:
        return _not_running()
    try:
        return await forward(http, method, _target(info.url, path, query), headers, body)
    except httpx.TransportError as exc:
        return JSONResponse({"detail": f"the generated service did not answer: {exc}"}, status_code=502)


# ------------------------------------------------------------------ the API side
class HostClient(Protocol):
    """How the API reaches the worker that hosts a build's service."""

    async def status(self, build_id: str) -> dict[str, Any]: ...

    async def logs(self, build_id: str) -> dict[str, Any]: ...

    async def stop(self, build_id: str) -> bool: ...

    async def proxy(self, build_id: str, method: str, path: str, query: str,
                    headers: list[tuple[str, str]], body: bytes) -> Response: ...


class LocalHostClient:
    """Role ``all``: the build's service runs in this process's :class:`Hosting`."""

    def __init__(self, hosting: Hosting, http: httpx.AsyncClient) -> None:
        self.hosting = hosting
        self.http = http

    async def status(self, build_id: str) -> dict[str, Any]:
        return self.hosting.status(build_id)

    async def logs(self, build_id: str) -> dict[str, Any]:
        return self.hosting.logs(build_id)

    async def stop(self, build_id: str) -> bool:
        return await self.hosting.stop(build_id)

    async def proxy(self, build_id: str, method: str, path: str, query: str,
                    headers: list[tuple[str, str]], body: bytes) -> Response:
        return await forward_local(self.hosting, self.http, build_id, method, path, query, headers, body)


class RemoteHostClient:
    """Role ``api``: the worker's internal API at ``run_host``, with the internal bearer."""

    def __init__(self, run_host: str, token: str, http: httpx.AsyncClient) -> None:
        self.run_host = run_host
        self.auth = f"Bearer {token}"
        self.http = http

    def _base(self, build_id: str) -> str:
        return f"http://{self.run_host}/internal/services/{quote(build_id, safe='')}"

    async def _call(self, method: str, url: str) -> Any:
        try:
            r = await self.http.request(method, url, headers={"Authorization": self.auth},
                                        timeout=CONTROL_TIMEOUT)
        except httpx.TransportError as exc:
            raise HostGone(str(exc)) from exc
        if r.status_code != 200:
            raise HostError(f"the worker hosting the service answered {r.status_code}")
        return r.json()

    async def status(self, build_id: str) -> dict[str, Any]:
        return await self._call("GET", self._base(build_id))

    async def logs(self, build_id: str) -> dict[str, Any]:
        return await self._call("GET", f"{self._base(build_id)}/logs")

    async def stop(self, build_id: str) -> bool:
        return bool((await self._call("DELETE", self._base(build_id)))["stopped"])

    async def proxy(self, build_id: str, method: str, path: str, query: str,
                    headers: list[tuple[str, str]], body: bytes) -> Response:
        hop = [(SERVICE_AUTH, v) if k.lower() == "authorization" else (k, v) for k, v in headers]
        hop.append(("authorization", self.auth))
        try:
            return await forward(self.http, method, _target(f"{self._base(build_id)}/proxy", path, query),
                                 hop, body)
        except httpx.TransportError as exc:
            raise HostGone(str(exc)) from exc


def internal_headers(headers: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    """On the worker: the API's headers for the service. The internal bearer is dropped and the
    caller's own ``Authorization`` restored; the X-Forwarded-* the API set pass through."""
    out: list[tuple[str, str]] = []
    for k, v in headers:
        key = k.lower()
        if key == SERVICE_AUTH:
            out.append(("authorization", v))
        elif key not in HOP_BY_HOP | {"host", "content-length", "authorization", "cookie"}:
            out.append((k, v))
    return out


def host_client(state: Any, build: Build) -> HostClient:
    """The client for the worker that hosts ``build``'s service (``state`` is ``app.state``)."""
    worker = state.runner.worker
    if worker is not None and build.run_owner == worker.name:
        return LocalHostClient(state.hosting, state.http)
    if not build.run_host:
        raise HostGone("no worker address recorded")
    return RemoteHostClient(build.run_host, state.settings.internal_token, state.http)


async def clear_run(session: AsyncSession, build: Build) -> None:
    build.run_host = build.run_owner = None
    build.run_port = build.run_pid = None
    await session.commit()


async def service_state(state: Any, session: AsyncSession, build: Build) -> dict[str, Any]:
    """``run_status`` plus ``log_tail`` and ``reason`` of the build's service, from the worker
    that hosts it. Urls are still the worker's loopback ones (see ``public_urls``)."""
    if not build.run_owner:
        return {**verify_svc.run_status(None), "log_tail": "", "reason": None}
    try:
        state_ = await host_client(state, build).status(build.id)
    except HostGone as exc:
        log.warning("build %s: the worker %s at %s is gone (%s); clearing the run", build.id,
                    build.run_owner, build.run_host, exc)
        await clear_run(session, build)
        return {**verify_svc.run_status(None), "log_tail": "", "reason": GONE_REASON}
    return {**state_, "reason": None}
