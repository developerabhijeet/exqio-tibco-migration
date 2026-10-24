"""Generated services hosted on workers and reached through the API (Task 5).

A stand-in HTTP service plays the generated .NET service. ``fake_hosting_worker`` serves a real
worker internal app (uvicorn on a free port) whose :class:`Hosting` owns that service, so the API
under test reaches it the way an API pod reaches a worker pod: HTTP to ``run_host`` with the
internal bearer. The API's own in-process worker (role ``all``) is the local path.
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote

import httpx
import pytest
import uvicorn
from sqlalchemy import select, update

from api.app import worker as worker_mod
from api.app.jobs import registry
from api.app.jobs.locks import advisory_lock
from api.app.jobs.runner import JobWorker
from api.app.models import Application, Build, Job, Process, Project, Upload
from api.app.services import hosting as hosting_mod
from api.app.services import verify as verify_svc
from api.app.services.hosting import GONE_REASON, Hosting

TOKEN = "test-internal-token"


@pytest.fixture
def settings(settings, request):
    settings.internal_token = TOKEN
    if "app_no_worker" in request.fixturenames:
        settings.exiqo_role = "api"
    return settings


class Service:
    """The generated service's stand-in: ``/health``, and ``/echo`` answering what it received."""

    def __init__(self, log_path: Path) -> None:
        self.log_path = str(log_path)
        Path(log_path).write_text("stand-in service\n", encoding="utf-8")

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _answer(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length).decode("utf-8") if length else ""
                if self.path == "/health":
                    out = {"status": "Healthy"}
                else:
                    out = {"method": self.command, "path": self.path, "body": body,
                           "headers": {k.lower(): v for k, v in self.headers.items()}}
                data = json.dumps(out).encode("utf-8")
                self.send_response(200 if self.path == "/health" or self.path.startswith("/echo") else 404)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("X-Service", "stand-in")
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = do_PUT = do_DELETE = _answer

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self.pid = os.getpid()
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self._stopped = False

    def poll(self):
        return 0 if self._stopped else None

    def stop(self, timeout_s: float = 5.0) -> None:
        if not self._stopped:
            self.server.shutdown()
            self.server.server_close()
            self._stopped = True


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def make_build(app, **cols) -> str:
    """A build row with the minimum around it (project, upload, application, process)."""
    async with app.state.sessionmaker() as s:
        project = Project(name="hosting")
        s.add(project)
        await s.flush()
        upload = Upload(project_id=project.id, source_kind="path", source_name="x", root_dir="/tmp")
        s.add(upload)
        await s.flush()
        application = Application(project_id=project.id, upload_id=upload.id, app_key="a", name="a",
                                  dialect="bw5", ir={})
        s.add(application)
        await s.flush()
        proc = Process(project_id=project.id, application_id=application.id, upload_id=upload.id,
                       ir_id="p", name="p", dialect="bw5", source_path="p.process", ir={},
                       spec_hash="h")
        s.add(proc)
        await s.flush()
        build = Build(process_id=proc.id, status="passed", **cols)
        s.add(build)
        await s.commit()
        return build.id


async def build_row(app, build_id: str) -> Build:
    async with app.state.sessionmaker() as s:
        return await s.get(Build, build_id)


@pytest.fixture
async def services():
    made: list[Service] = []
    yield made
    for svc in made:
        svc.stop()


@pytest.fixture
async def fake_hosting_worker(app, settings, tmp_path, services):
    """A worker named ``w-a`` with its internal listener on a free port, hosting one service."""
    port = free_port()
    hosting = Hosting(settings, app.state.sessionmaker, owner="w-a", address=f"127.0.0.1:{port}")
    async with httpx.AsyncClient(trust_env=False) as http:
        server = worker_mod._Server(uvicorn.Config(worker_mod.internal_app(hosting, TOKEN, http),
                                                   host="127.0.0.1", port=port, log_level="warning"))
        serving = asyncio.create_task(server.serve())
        while not server.started:
            await asyncio.sleep(0.05)
        svc = Service(tmp_path / "service.log")
        services.append(svc)
        build_id = await make_build(app)
        await hosting.register(verify_svc.RunInfo(build_id=build_id, handle=svc, port=svc.port))
        try:
            yield SimpleNamespace(build_id=build_id, hosting=hosting, service=svc, port=port)
        finally:
            server.should_exit = True
            await serving


@pytest.fixture
async def local_service(app, tmp_path, services):
    """A service hosted by the API's own in-process worker (role ``all``)."""
    svc = Service(tmp_path / "local.log")
    services.append(svc)
    build_id = await make_build(app)
    await app.state.hosting.register(verify_svc.RunInfo(build_id=build_id, handle=svc, port=svc.port))
    return SimpleNamespace(build_id=build_id, service=svc)


@pytest.fixture
async def internal_client(app, settings):
    hosting = Hosting(settings, app.state.sessionmaker, owner="w-i")
    transport = httpx.ASGITransport(app=worker_mod.internal_app(hosting, TOKEN))
    async with httpx.AsyncClient(transport=transport, base_url="http://worker") as c:
        yield c


# ------------------------------------------------------------------ the proxy
async def test_proxy_forwards_to_hosting_worker(client, fake_hosting_worker):
    r = await client.get(f"/builds/{fake_hosting_worker.build_id}/run/proxy/health")
    assert r.status_code == 200 and r.json() == {"status": "Healthy"}
    assert r.headers["x-service"] == "stand-in"


async def test_proxy_to_a_worker_passes_the_call_and_the_forwarded_headers(client, fake_hosting_worker):
    await _passes_the_call(client, fake_hosting_worker.build_id)


async def test_proxy_in_process_passes_the_call_and_the_forwarded_headers(client, local_service):
    await _passes_the_call(client, local_service.build_id)


async def _passes_the_call(client, bid: str) -> None:
    r = await client.post(f"/builds/{bid}/run/proxy/echo/x?dealId=DL-1&a=b", content=b'{"k": 1}',
                          headers={"Authorization": "Bearer svc-token", "Cookie": "exiqo_session=secret",
                                   "Content-Type": "application/json", "Connection": "keep-alive"})
    assert r.status_code == 200, r.text
    seen = r.json()
    assert seen["method"] == "POST" and seen["path"] == "/echo/x?dealId=DL-1&a=b"
    assert seen["body"] == '{"k": 1}'
    h = seen["headers"]
    # the caller's bearer is for the generated service; the internal one never reaches it
    assert h["authorization"] == "Bearer svc-token"
    assert TOKEN not in json.dumps(h)
    assert "cookie" not in h  # the platform's session cookie stays with the platform
    assert h["x-forwarded-prefix"] == f"/builds/{bid}/run/proxy"
    assert h["x-forwarded-host"] == "test" and h["x-forwarded-proto"] == "http"


async def test_proxy_unknown_build_or_no_service(client, app):
    assert (await client.get("/builds/nope/run/proxy/health")).status_code == 404
    bid = await make_build(app)
    r = await client.get(f"/builds/{bid}/run/proxy/health")
    assert r.status_code == 404 and "not running" in r.json()["detail"]


async def test_proxy_refuses_agents(client, local_service):
    r = await client.get(f"/builds/{local_service.build_id}/run/proxy/health", headers={"X-Agent": "a"})
    assert r.status_code == 403


# ------------------------------------------------------------------ status, logs, stop
async def test_run_status_points_at_the_proxy(client, fake_hosting_worker):
    bid = fake_hosting_worker.build_id
    body = (await client.get(f"/builds/{bid}/run")).json()
    root = f"http://test/builds/{bid}/run/proxy/"
    assert body["running"] is True and body["url"] == root
    assert body["swagger_url"] == root + "swagger/index.html"
    assert "stand-in" in body["log_tail"] and body["reason"] is None
    logs = (await client.get(f"/builds/{bid}/run/logs")).json()
    assert logs["running"] is True and "stand-in" in logs["log"]


async def test_status_when_hosting_worker_gone(client, app):
    bid = await make_build(app, run_host="10.0.0.9:9100", run_owner="w-gone", run_port=5001, run_pid=1)
    body = (await client.get(f"/builds/{bid}/run")).json()
    assert body["running"] is False and body["reason"] == "the worker hosting the service is gone"
    assert body["reason"] == GONE_REASON
    row = await build_row(app, bid)
    assert (row.run_host, row.run_owner, row.run_port) == (None, None, None)


async def test_stop_on_the_hosting_worker(client, app, fake_hosting_worker):
    bid = fake_hosting_worker.build_id
    r = await client.delete(f"/builds/{bid}/run")
    assert r.json() == {"stopped": True, "running": False}
    assert fake_hosting_worker.service.poll() is not None
    row = await build_row(app, bid)
    assert (row.run_host, row.run_owner, row.run_port, row.run_pid) == (None, None, None, None)
    assert (await client.get(f"/builds/{bid}/run")).json()["running"] is False


async def test_start_runs_on_a_worker_and_records_where(client, app, monkeypatch, tmp_path, services):
    async def fake_start(settings, build, project_id, app_ir, lock, timeout_s=240.0):
        svc = Service(tmp_path / "started.log")
        services.append(svc)
        return verify_svc.RunInfo(build_id=build.id, handle=svc, port=svc.port)

    async def no_app(session, proc):
        return None

    monkeypatch.setattr(verify_svc, "start_service", fake_start)
    monkeypatch.setattr(hosting_mod, "engine_app", no_app)
    bid = await make_build(app)
    r = await client.post(f"/builds/{bid}/run")
    assert r.status_code == 200, r.text
    assert r.json()["running"] is True and r.json()["url"] == f"http://test/builds/{bid}/run/proxy/"
    row = await build_row(app, bid)
    assert row.run_owner == app.state.runner.worker.name
    assert row.run_host == "127.0.0.1:9100" and row.run_port and row.run_pid
    # started again while running: the same service
    assert (await client.post(f"/builds/{bid}/run")).json()["port"] == row.run_port
    assert (await client.get(f"/builds/{bid}/run/proxy/health")).json() == {"status": "Healthy"}
    assert (await client.delete(f"/builds/{bid}/run")).json()["stopped"] is True


async def test_start_failure_is_409_with_the_reason(client, app, monkeypatch):
    async def failing(settings, build, project_id, app_ir, lock, timeout_s=240.0):
        raise RuntimeError("service exited with code 1:\nUnhandled exception. boom")

    async def no_app(session, proc):
        return None

    monkeypatch.setattr(verify_svc, "start_service", failing)
    monkeypatch.setattr(hosting_mod, "engine_app", no_app)
    bid = await make_build(app)
    r = await client.post(f"/builds/{bid}/run")
    assert r.status_code == 409
    assert r.json()["detail"] == "the service did not start: service exited with code 1:\nUnhandled exception. boom"


# ------------------------------------------------------------------ verify affinity
async def test_verify_job_has_affinity_to_service_owner(client, app, fake_hosting_worker):  # Review Focus 5
    r = await client.post(f"/builds/{fake_hosting_worker.build_id}/verify")
    assert r.status_code == 202, r.text
    async with app.state.sessionmaker() as s:
        job = await s.get(Job, r.json()["job_id"])
    assert job.affinity == "w-a"


async def test_verify_refused_when_nothing_runs(client, app):
    bid = await make_build(app)
    r = await client.post(f"/builds/{bid}/verify")
    assert r.status_code == 409 and "not running" in r.json()["detail"]


@pytest.fixture
def noop_kind():
    async def noop(ctx, w):
        return {}

    registry.register("noop-hosting-test", noop)
    yield "noop-hosting-test"
    registry.unregister("noop-hosting-test")


@pytest.fixture
def app_no_worker(app):
    assert app.state.runner.worker is None
    return app


async def test_affinity_job_not_claimed_by_other_worker(app_no_worker, noop_kind):
    app = app_no_worker
    jid = await app.state.runner.submit(noop_kind, {}, affinity="w-a")
    other = JobWorker(app.state.sessionmaker, app.state.worker_services, name="w-b", slots=1,
                      heartbeat_s=1, stale_s=90, max_attempts=2, poll_s=0.1)
    await other.start()
    try:
        await asyncio.sleep(1)
        async with app.state.sessionmaker() as s:
            assert (await s.get(Job, jid)).status == "queued"
    finally:
        await other.stop()


# ------------------------------------------------------------------ the worker's internal API
async def test_internal_api_rejects_without_token(internal_client):
    assert (await internal_client.get("/internal/services/x")).status_code == 401
    bad = {"Authorization": "Bearer wrong"}
    assert (await internal_client.get("/internal/services/x", headers=bad)).status_code == 401
    assert (await internal_client.get("/internal/services/x/proxy/health", headers=bad)).status_code == 401
    assert (await internal_client.delete("/internal/services/x", headers=bad)).status_code == 401
    ok = {"Authorization": f"Bearer {TOKEN}"}
    r = await internal_client.get("/internal/services/x", headers=ok)
    assert r.status_code == 200 and r.json()["running"] is False
    assert (await internal_client.get("/internal/healthz")).status_code == 200  # the kubelet's probe


async def test_worker_refuses_to_start_without_internal_token(settings):
    with pytest.raises(RuntimeError, match="INTERNAL_TOKEN"):
        await worker_mod.run(settings.model_copy(update={"internal_token": ""}))


async def test_worker_clears_its_own_stale_runs(app, settings):
    mine = await make_build(app, run_host="127.0.0.1:9100", run_owner="w-me", run_port=5002, run_pid=1)
    theirs = await make_build(app, run_host="10.0.0.2:9100", run_owner="w-other", run_port=5003, run_pid=2)
    hosting = Hosting(settings, app.state.sessionmaker, owner="w-me")
    assert await hosting.clear_stale() == 1
    assert (await build_row(app, mine)).run_owner is None
    assert (await build_row(app, theirs)).run_owner == "w-other"


# ------------------------------------------------------------------ review fixes
async def asgi_status(app, method: str, raw_path: str, headers: list[tuple[bytes, bytes]] = ()) -> int:
    """Call an ASGI app with this exact raw path, the way uvicorn hands it over (``path`` is the
    percent-decoded form, nothing normalized). An HTTP client would resolve ``..`` before sending."""
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method,
             "scheme": "http", "path": unquote(raw_path), "raw_path": raw_path.encode(), "query_string": b"",
             "root_path": "", "headers": [(b"host", b"test"), *headers], "client": ("127.0.0.1", 1),
             "server": ("test", 80)}
    sent: list[dict] = []
    requested = False

    async def receive():
        nonlocal requested
        if not requested:
            requested = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await asyncio.Event().wait()  # no disconnect: a streaming answer runs to its end

    async def send(message):
        sent.append(message)

    await app(scope, receive, send)
    return next(m["status"] for m in sent if m["type"] == "http.response.start")


DOT_PATHS = ["../../B", "%2e%2e/%2e%2e/B", "%2E%2e%2f%2e%2E%2fB", "./health", "a/%2e/b", "%252e%252e/B",
             "..%5c..%5cB"]


@pytest.fixture
def forwarded(monkeypatch):
    """Every call the proxy would forward (none, for a refused path)."""
    calls: list[str] = []
    real = hosting_mod.forward

    async def recording(http, method, url, headers, body):
        calls.append(url)
        return await real(http, method, url, headers, body)

    monkeypatch.setattr(hosting_mod, "forward", recording)
    return calls


async def test_dot_segments_refused_on_the_api_proxy(app, fake_hosting_worker, forwarded):
    bid = fake_hosting_worker.build_id
    for tail in DOT_PATHS:
        for method in ("GET", "DELETE"):
            status = await asgi_status(app, method, f"/builds/{bid}/run/proxy/{tail}")
            assert status == 400, (method, tail, status)
    assert forwarded == []  # no internal request was made
    assert fake_hosting_worker.service.poll() is None and (await build_row(app, bid)).run_owner == "w-a"


async def test_dot_segments_refused_on_the_internal_proxy(app, settings, local_service, forwarded):
    internal = worker_mod.internal_app(app.state.hosting, TOKEN)
    auth = [(b"authorization", f"Bearer {TOKEN}".encode())]
    bid = local_service.build_id
    for tail in DOT_PATHS:
        status = await asgi_status(internal, "DELETE", f"/internal/services/{bid}/proxy/{tail}", auth)
        assert status == 400, (tail, status)
    assert forwarded == []
    assert local_service.service.poll() is None
    # a plain path still goes through
    assert await asgi_status(internal, "GET", f"/internal/services/{bid}/proxy/health", auth) == 200


def test_unsafe_proxy_path_cases():
    for tail in DOT_PATHS:
        assert hosting_mod.unsafe_proxy_path(unquote(tail), tail), tail
    for tail in ("health", "swagger/index.html", "swagger/v1/swagger.json", "funding/DL-1.2", "a..b/c.",
                 "%2e%2ex"):
        assert not hosting_mod.unsafe_proxy_path(unquote(tail), tail), tail


async def test_second_replica_start_returns_the_committed_run(client, app, local_service):
    """Replica B loaded the build before replica A's start committed, and got the lock after it:
    B must see A's run, not queue a second service_start that another worker could claim."""
    bid = local_service.build_id
    async with app.state.sessionmaker() as s:  # the run is not recorded yet when B loads the row
        await s.execute(update(Build).where(Build.id == bid).values(run_owner=None, run_host=None,
                                                                     run_port=None, run_pid=None))
        await s.commit()
    async with advisory_lock(app.state.sessionmaker, f"service:{bid}"):  # A holds the lock
        b = asyncio.create_task(client.post(f"/builds/{bid}/run"))
        await asyncio.sleep(0.5)  # B has loaded the build and waits for the lock
        assert not b.done()
        await app.state.hosting.register(app.state.hosting.get(bid))  # A's start commits
    r = await b
    assert r.status_code == 200 and r.json()["running"] is True
    assert r.json()["port"] == local_service.service.port
    async with app.state.sessionmaker() as s:
        assert (await s.execute(select(Job).where(Job.kind == "service_start"))).scalars().all() == []


@pytest.fixture
async def stalled_worker():
    """A worker that accepts the connection and never answers."""
    writers: list[asyncio.StreamWriter] = []

    async def stall(reader, writer):
        writers.append(writer)
        await reader.read()  # until the client gives up and closes

    server = await asyncio.start_server(stall, "127.0.0.1", 0)
    yield f"127.0.0.1:{server.sockets[0].getsockname()[1]}"
    for w in writers:
        w.close()
    server.close()


async def test_slow_worker_is_not_gone(client, app, monkeypatch, stalled_worker):
    app.state.http.timeout = httpx.Timeout(1.0, connect=1.0)
    monkeypatch.setattr(hosting_mod, "CONTROL_TIMEOUT", httpx.Timeout(1.0, connect=1.0))
    bid = await make_build(app, run_host=stalled_worker, run_owner="w-slow", run_port=5004, run_pid=1)
    r = await client.get(f"/builds/{bid}/run/proxy/funding/DL-1")
    assert r.status_code == 504, r.text
    r = await client.get(f"/builds/{bid}/run")
    assert r.status_code == 502 and "in time" in r.json()["detail"]
    row = await build_row(app, bid)
    assert (row.run_host, row.run_owner) == (stalled_worker, "w-slow")  # the run is kept
