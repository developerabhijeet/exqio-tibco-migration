"""Java and Go end to end (target stacks, Tasks 9 and 13): Verify starts the service through the stack
runner, with the runtime contract's environment, and parity runs against it.

The start/ownership tests stand a fake runner in for ``MavenRunner``, so they run without a JDK;
``test_seven_steps_parity_clean[java|go|python]`` is the real thing (scan, analyse, design, build with
Maven, go or uv, ``java -jar``, the Go binary or uvicorn, Verify) and skips without the stack's tools.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from api.app.models import Build
from api.app.services import verify as verify_svc
from api.tests.test_build_verify import ARCHITECT, FSL, SME, _scanned, _wait_job
from engine.stacks import get_stack
from engine.stacks.go.runner import find_tool as find_go
from engine.stacks.java import stack as java_stack_mod
from engine.stacks.java.runner import find_tool

CONTRACT = {"connections": ["GMFFundingDB"], "clients": ["DealerApi"],
            "secrets": [{"name": "ConnectionStrings--GMFFundingDB", "key": "ConnectionStrings__GMFFundingDB"}],
            "plain_keys": ["Clients__DealerApi__BaseAddress"], "port_env": None, "health": "/health"}


class _Stubs:
    def __init__(self) -> None:
        self.url = "http://127.0.0.1:1/"
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


class _Handle:
    url, pid, log_path = "http://127.0.0.1:2", 1, "/dev/null"

    def poll(self):
        return None

    def stop(self) -> None:
        pass


def _java_build(monkeypatch, tmp_path: Path, contract: dict | None = CONTRACT) -> Build:
    root = tmp_path / "GmfCredit"
    (root / ".exiqo").mkdir(parents=True)
    if contract is not None:
        (root / ".exiqo/service-contract.json").write_text(json.dumps(contract), encoding="utf-8")
    monkeypatch.setattr(verify_svc, "StubServer", _Stubs)
    monkeypatch.setattr(verify_svc, "open_db", lambda *a: type("DB", (), {"close": lambda self: None})())
    monkeypatch.setattr(verify_svc, "reset_db", lambda *a, **k: None)
    monkeypatch.setattr(verify_svc, "missing_service_tools", lambda stack: [])

    def no_dotnet():
        raise AssertionError("a Java build never looks for dotnet")
    monkeypatch.setattr(verify_svc, "find_dotnet", no_dotnet)
    return Build(id="b1", process_id="p1", output_dir=str(root), stack="java")


async def test_start_service_runs_the_java_service_through_its_stack_runner(monkeypatch, tmp_path, settings):
    build = _java_build(monkeypatch, tmp_path)
    calls = []

    class FakeRunner:
        def __init__(self, root) -> None:
            self.root = root

        def run_service(self, port, env=None, timeout_s=180.0):
            calls.append((self.root, port, env, timeout_s))
            return _Handle()

    monkeypatch.setattr(java_stack_mod.JavaStack, "runner", lambda self, root, **kw: FakeRunner(root))
    info = await verify_svc.start_service(settings, build, "proj", None, asyncio.Lock, timeout_s=33)
    try:
        assert len(calls) == 1
        root, port, env, timeout_s = calls[0]
        assert root == Path(build.output_dir) and port == info.port and timeout_s == 33
        # the runtime contract: development profile, in-memory bus, no Key Vault, the contract's
        # connections and clients (the clients pointed at the stub server)
        assert env["ASPNETCORE_ENVIRONMENT"] == "Development" and env["Auth__Mode"] == "Development"
        assert env["Messaging__Mode"] == "InMemory" and env["KeyVault__Uri"] == ""
        assert env["ConnectionStrings__GMFFundingDB"].startswith("Host=")
        assert env["Clients__DealerApi__BaseAddress"] == "http://127.0.0.1:1/"
        assert info.clients == ["DealerApi"]
        status = verify_svc.run_status(info)
        assert status["running"] is True and status["swagger_url"] is None  # the Java service has no Swagger UI
    finally:
        info.stop()


async def test_start_service_names_the_missing_java_tools(monkeypatch, tmp_path, settings):
    """Carry-over of X14a: the worker checks a non-.NET stack's tools when Verify starts it."""
    build = _java_build(monkeypatch, tmp_path)
    monkeypatch.setattr(verify_svc, "missing_service_tools", lambda stack: ["java", "mvn"])
    with pytest.raises(RuntimeError, match="the java toolchain is missing on this worker: java, mvn"):
        await verify_svc.start_service(settings, build, "proj", None, asyncio.Lock)


async def test_start_service_without_the_contract_says_so(monkeypatch, tmp_path, settings):
    build = _java_build(monkeypatch, tmp_path, contract=None)
    with pytest.raises(RuntimeError, match="service-contract.json"):
        await verify_svc.start_service(settings, build, "proj", None, asyncio.Lock)


def test_missing_service_tools_reads_the_stack_tools(monkeypatch):
    monkeypatch.setattr(verify_svc.settings_svc, "detect_toolchains",
                        lambda: {"java": {"present": True}, "mvn": {"present": False}})
    assert verify_svc.missing_service_tools("java") == ["mvn"]


def test_connection_names_from_the_contract(tmp_path):
    (tmp_path / ".exiqo").mkdir()
    (tmp_path / ".exiqo/service-contract.json").write_text(
        json.dumps({**CONTRACT, "connections": ["ServiceBus", "B", "A"]}), encoding="utf-8")
    assert verify_svc.connection_names(tmp_path, "GmfCredit", "java") == ["A", "B"]
    assert verify_svc.connection_names(tmp_path, "GmfCredit") == []  # .NET reads appsettings


@pytest.mark.parametrize("stack, cmdline, owned", [
    ("java", "/usr/bin/java -jar {root}/target/gmfcredit.jar --server.port=1", True),
    ("java", "dotnet run --project {root}/src/X.Api", False),
    ("dotnet", "dotnet run --project {root}/src/X.Api", True),
    ("dotnet", "/usr/bin/java -jar {root}/target/gmfcredit.jar", False),
    ("java", "/usr/bin/java -jar /elsewhere/target/gmfcredit.jar", False),
    ("go", "{root}/.exiqo-bin/server", True),
    ("go", "/elsewhere/.exiqo-bin/server", False),
    ("go", "/usr/bin/java -jar {root}/target/gmfcredit.jar", False),
])
def test_owned_service_follows_the_stack_marker(monkeypatch, tmp_path, stack, cmdline, owned):
    root = str(tmp_path / "build")
    monkeypatch.setattr(verify_svc, "process_cmdline", lambda pid: cmdline.format(root=root))
    assert verify_svc._owned_service(42, Build(id="b", process_id="p", output_dir=root, stack=stack)) is owned


def test_java_stack_marker_is_what_the_runner_starts():
    assert get_stack("java").service_process_marker == "java"


# ------------------------------------------------------------------ the real thing
async def run_verify_and_wait(client, build_id: str) -> dict:
    """Start the build's service, run Verify, stop the service; ``{"summary": job result, "parity": ...}``."""
    r = await client.post(f"/builds/{build_id}/run")
    assert r.status_code == 200, r.text
    try:
        run = r.json()
        assert run["running"] is True and run["swagger_url"] is None
        token = await client.post(f"{run['url']}auth/dev-token", json={"subject": "exiqo-verifier"})
        assert token.status_code == 200 and token.json()["access_token"]  # through the API's proxy
        r = await client.post(f"/builds/{build_id}/verify")
        assert r.status_code == 202, r.text
        job = await _wait_job(client, r.json()["job_id"], timeout=600)
        assert job["status"] == "done", job
        return {"summary": job["result"], "parity": (await client.get(f"/builds/{build_id}/parity")).json()}
    finally:
        await client.delete(f"/builds/{build_id}/run")


def _stack_tools(stack: str) -> bool:
    """Whether this machine has the stack's build tools (``scripts/toolchain.py``)."""
    if stack == "python":
        from engine.stacks.python.runner import find_tool as find_python_tool
        return bool(find_python_tool("uv") and find_python_tool("python3"))
    if stack == "go":
        return bool(find_go("go"))
    return bool(find_tool("mvn") and find_tool("java"))


@pytest.fixture(params=["java", "go", "python"])
async def project_through_g4(request, client, settings):
    """FundingStatusLookup of a project on the stack: scanned, analysed, decided, designed (G3), built
    with the stack's tools (Maven, go, uv); skipped when the machine lacks them."""
    stack = request.param
    if not _stack_tools(stack):
        pytest.skip(f"the {stack} toolchain is not found (scripts/toolchain.py)")
    settings.test_database_url = settings.scenario_database_url
    pid, procs = await _scanned(client)
    r = await client.patch(f"/projects/{pid}", json={"target_stack": stack}, headers=ARCHITECT)
    assert r.status_code == 200, r.text
    proc_id = procs[FSL]["id"]
    r = await client.post(f"/processes/{proc_id}/analyse")
    assert (await _wait_job(client, r.json()["job_id"]))["status"] == "done"
    view = (await client.get(f"/processes/{proc_id}/analysis")).json()
    for rule in view["rules"]:
        state = "rejected" if rule["status"] == "unverified" else "approved"
        assert (await client.patch(f"/rules/{rule['id']}", json={"status": state}, headers=SME)).status_code == 200
    for f in view["findings"]:
        r = await client.patch(f"/findings/{f['id']}", json={"decision": "keep", "note": "decided"}, headers=SME)
        assert r.status_code == 200
    for gate in ("G1", "G2"):
        assert (await client.post(f"/gates/{proc_id}/{gate}", json={"state": "approved", "note": ""},
                                  headers=SME)).status_code == 200
    r = await client.post(f"/processes/{proc_id}/design")
    assert (await _wait_job(client, r.json()["job_id"]))["status"] == "done"
    r = await client.post(f"/gates/{proc_id}/G3", json={"state": "approved", "note": "ok"}, headers=ARCHITECT)
    assert r.status_code == 200, r.text
    r = await client.post(f"/processes/{proc_id}/build", json={"runtime": "container_app"})
    assert r.status_code == 202, r.text
    build_id = r.json()["build_id"]
    job = await _wait_job(client, r.json()["job_id"], timeout=1800)
    assert job["status"] == "done", job
    build = (await client.get(f"/builds/{build_id}")).json()
    steps = {s["name"]: s for s in build["steps"]}
    assert steps["restore"]["ok"] and steps["build"]["ok"], steps
    return stack, build_id


async def test_seven_steps_parity_clean(client, project_through_g4):  # spec criterion 1
    stack, build_id = project_through_g4
    report = await run_verify_and_wait(client, build_id)
    print(f"{stack} parity summary:", json.dumps(report["summary"], sort_keys=True))
    assert report["summary"]["unexplained"] == 0 and report["summary"]["scenarios"] > 0
    assert report["summary"]["errors"] == 0
    assert (await client.get(f"/builds/{build_id}")).json()["stack"] == stack
