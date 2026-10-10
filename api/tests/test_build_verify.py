"""Design, build, verify, infra and plan, settings and evals, history, readiness (Task 19).

Real Postgres (schema ``api`` reset per test), ``PROVIDER=fake``, and the scenario database pointed
at ``TEST_DATABASE_URL`` (its ``gmf`` schema is dropped and reseeded; the role here cannot create
databases, so the per-project ``exiqo_scn_<project>_test`` default is only exercised by
:func:`test_scenario_database_name_per_project`).

Where ``dotnet`` or NuGet are missing the build must say so honestly: a failed ``restore`` step
with the reason, G4 disabled, and still the solution files, the tests (as files), the infra assets
and their validation. The verify tests drive a stand-in HTTP service registered as the build's
running service, so the verify job, the comparison and the parity report are exercised without a
restored .NET solution; ``test_build_scaffold_passes_with_dotnet`` runs the real thing and is
skipped when NuGet is unreachable.
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import re
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

import pytest
from sqlalchemy import select

from api.app.models import AzureConnection, Build, FieldDiff, Gate, JobEvent, ModelCall
from api.app.models import Process as ProcessRow
from api.app.models import Scenario as ScenarioRow
from api.app.services import build as build_svc
from api.app.services import evidence as evidence_svc
from api.app.services import readiness as readiness_svc
from api.app.services import verify as verify_svc
from api.tests.conftest import TEST_DSN

os.environ.setdefault("EMBEDDINGS_LOAD_TIMEOUT", "8")

REPO_ROOT = Path(__file__).resolve().parents[2]
SYNTHETIC = REPO_ROOT / "samples" / "synthetic-gmf"
FSL = "dealerfunding.FundingStatusLookup"
ACC = "loanservicing.AccountLookup"
SME = {"X-User": "sme.vicky"}
ARCHITECT = {"X-User": "architect.lee"}
OPS = {"X-User": "ops.maria"}
BUILD_STEPS = ["restore", "build", "format", "test"]


# ------------------------------------------------------------------ helpers
@pytest.fixture(autouse=True)
def scenario_db(settings):
    settings.scenario_database_url = TEST_DSN


async def _wait_job(client, job_id: str, timeout: float = 240.0) -> dict:
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        r = await client.get(f"/jobs/{job_id}")
        assert r.status_code == 200, r.text
        job = r.json()
        if job["status"] in ("done", "failed"):
            return job
        assert asyncio.get_running_loop().time() < deadline, f"job still {job['status']}"
        await asyncio.sleep(0.2)


async def _scanned(client) -> tuple[str, dict[str, dict]]:
    r = await client.post("/projects", json={"name": "gmf"})
    pid = r.json()["id"]
    r = await client.post(f"/projects/{pid}/upload", json={"path": str(SYNTHETIC)})
    assert r.status_code == 201, r.text
    r = await client.post(f"/projects/{pid}/scan", json={})
    job = await _wait_job(client, r.json()["job_id"])
    assert job["status"] == "done", job
    procs = (await client.get(f"/projects/{pid}/processes")).json()
    return pid, {p["ir_id"]: p for p in procs}


async def _validated(client, ir_id: str = FSL, decision: str = "fix") -> tuple[str, str]:
    """Scan, analyse, decide every rule and finding (``decision``), approve G1 and G2."""
    pid, procs = await _scanned(client)
    proc_id = procs[ir_id]["id"]
    r = await client.post(f"/processes/{proc_id}/analyse")
    assert r.status_code == 202, r.text
    assert (await _wait_job(client, r.json()["job_id"]))["status"] == "done"
    view = (await client.get(f"/processes/{proc_id}/analysis")).json()
    for rule in view["rules"]:
        state = "rejected" if rule["status"] == "unverified" else "approved"
        r = await client.patch(f"/rules/{rule['id']}", json={"status": state}, headers=SME)
        assert r.status_code == 200, r.text
    for f in view["findings"]:
        r = await client.patch(f"/findings/{f['id']}", json={"decision": decision, "note": "decided"},
                               headers=SME)
        assert r.status_code == 200, r.text
    for gate in ("G1", "G2"):
        r = await client.post(f"/gates/{proc_id}/{gate}", json={"state": "approved", "note": ""}, headers=SME)
        assert r.status_code == 200, (gate, r.text)
    return pid, proc_id


async def _designed(client, ir_id: str = FSL, decision: str = "fix") -> tuple[str, str, dict]:
    pid, proc_id = await _validated(client, ir_id, decision)
    r = await client.post(f"/processes/{proc_id}/design")
    assert r.status_code == 202, r.text
    job = await _wait_job(client, r.json()["job_id"])
    assert job["status"] == "done", job
    r = await client.post(f"/gates/{proc_id}/G3", json={"state": "approved", "note": "ok"}, headers=ARCHITECT)
    assert r.status_code == 200, r.text
    return pid, proc_id, (await client.get(f"/processes/{proc_id}/design")).json()


async def _built(client, monkeypatch, ir_id: str = FSL, *, dotnet: bool = False,
                 decision: str = "fix") -> tuple[str, str, str, dict]:
    if not dotnet:
        monkeypatch.setattr(build_svc, "find_dotnet", lambda: None)
    pid, proc_id, _ = await _designed(client, ir_id, decision)
    r = await client.post(f"/processes/{proc_id}/build", json={"runtime": "container_app"})
    assert r.status_code == 202, r.text
    build_id = r.json()["build_id"]
    job = await _wait_job(client, r.json()["job_id"], timeout=1800)
    assert job["status"] == "done", job
    return pid, proc_id, build_id, (await client.get(f"/builds/{build_id}")).json()


async def _gates(client, proc_id: str) -> dict[str, dict]:
    return {g["gate"]: g for g in (await client.get(f"/processes/{proc_id}/gates")).json()}


async def _stages(app, job_id: str) -> list[str]:
    async with app.state.sessionmaker() as s:
        events = (await s.execute(select(JobEvent).where(JobEvent.job_id == job_id)
                                  .order_by(JobEvent.seq))).scalars().all()
    return [e.text for e in events if e.event == "stage"]


class StandIn:
    """A stand-in for the generated FundingStatusLookup service: dev token plus ``GET /funding/{dealId}``
    answering from a table, so the verify job runs without a restored .NET solution."""

    def __init__(self, answers: dict[str, dict], log_path: Path) -> None:
        self.answers = answers
        self.log_path = str(log_path)
        Path(log_path).write_text("stand-in service\n", encoding="utf-8")
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # quiet
                pass

            def _send(self, status: int, body: object) -> None:
                data = json.dumps(body).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(length)
                if self.path == "/auth/dev-token":
                    self._send(200, {"access_token": "dev", "token_type": "Bearer", "expires_at": "2030-01-01"})
                else:
                    self._send(404, {"title": "not found"})

            def do_GET(self):
                m = re.fullmatch(r"/funding/([^/?]+)", self.path)
                if self.headers.get("Authorization") != "Bearer dev":
                    self._send(401, {})
                elif m and m.group(1) in outer.answers:
                    out = outer.answers[m.group(1)]
                    self._send(out["status"], out.get("body"))
                elif self.path.startswith("/_bus/messages"):
                    self._send(200, [])
                else:
                    self._send(404, {"title": "not found"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.pid = os.getpid()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self._stopped = False

    def poll(self):
        return 0 if self._stopped else None

    def stop(self, timeout_s: float = 5.0) -> None:
        if not self._stopped:
            self.server.shutdown()
            self.server.server_close()
            self._stopped = True


# ------------------------------------------------------------------ the named tests
async def test_design_then_g3(client, app):
    _, proc_id = await _validated(client)
    gates = await _gates(client, proc_id)
    assert gates["G3"]["enabled"] is False
    assert "design record has not been generated" in gates["G3"]["reason"]
    assert (await client.get(f"/processes/{proc_id}/design")).status_code == 404
    r = await client.post(f"/processes/{proc_id}/build", json={})
    assert r.status_code == 409 and "G3 not approved" in r.json()["detail"]

    r = await client.post(f"/processes/{proc_id}/design")
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]
    job = await _wait_job(client, job_id)
    assert job["status"] == "done", job
    assert job["result"]["ai_available"] is True and job["result"]["provider"] == "fake"
    assert await _stages(app, job_id) == ["skeleton", "memory", "designer", "persist"]

    design = (await client.get(f"/processes/{proc_id}/design")).json()
    assert design["version"] == 1 and design["stale"] is False
    assert design["spec_hash"] == design["current_spec_hash"]
    assert len(design["design_hash"]) == 64
    assert {r["file"] for r in design["mapping"]} >= {"Repositories/FundingStatusLookupRepository.cs"}
    assert all({"activity_id", "class_name", "method", "pattern", "origin"} <= set(r) for r in design["mapping"])
    assert design["narrative"] and design["decisions"] and design["open_questions"]
    assert design["sequence"] and "from" in design["sequence"][0]

    gates = await _gates(client, proc_id)
    assert gates["G3"]["enabled"] is True, gates["G3"]
    r = await client.post(f"/gates/{proc_id}/G3", json={"state": "approved", "note": "ok"}, headers=ARCHITECT)
    assert r.status_code == 200, r.text
    assert r.json()["design_hash"] == design["design_hash"]
    assert r.json()["spec_hash"] == design["spec_hash"]
    g4 = (await _gates(client, proc_id))["G4"]
    assert g4["enabled"] is False and "no build has finished" in g4["reason"]

    # a second design with the same code-decided content keeps the hash and the approval
    r = await client.post(f"/processes/{proc_id}/design")
    assert (await _wait_job(client, r.json()["job_id"]))["result"]["gates_reset"] == []
    again = (await client.get(f"/processes/{proc_id}/design")).json()
    assert again["version"] == 2 and again["design_hash"] == design["design_hash"]
    assert (await _gates(client, proc_id))["G3"]["state"] == "approved"

    # editing a rule moves the spec hash: G3 resets and the record is stale
    rule = (await client.get(f"/processes/{proc_id}/analysis")).json()["rules"][0]
    r = await client.patch(f"/rules/{rule['id']}", json={"text": rule["text"] + " (edited)"}, headers=SME)
    assert r.status_code == 200
    stale = (await client.get(f"/processes/{proc_id}/design")).json()
    assert stale["stale"] is True
    gates = await _gates(client, proc_id)
    assert gates["G3"]["state"] == "pending"
    assert "stale" in gates["G3"]["reason"]


async def test_build_fails_without_dotnet_and_g4_disabled(client, app, monkeypatch):
    _, proc_id, build_id, build = await _built(client, monkeypatch)
    assert build["status"] == "failed"
    assert [s["name"] for s in build["steps"]] == BUILD_STEPS
    restore = build["steps"][0]
    assert restore["ok"] is False and "dotnet not found" in restore["log"]
    assert all(s["ok"] is False for s in build["steps"])
    assert len(build["build_hash"]) == 64
    assert build["metrics"]["compile_ok"] is False
    assert set(build["metrics"]["formulas"]) >= {"rule_carry_forward", "test_pass_rate", "branch_coverage"}
    stages = await _stages(app, build["job_id"])
    assert stages == build_svc.STAGES

    # the solution, the tests and the infrastructure are there even though nothing compiled
    files = (await client.get(f"/builds/{build_id}/files")).json()
    paths = {f["path"] for f in files["files"]}
    assert "GmfCredit.sln" in paths and "Dockerfile" in paths
    assert files["counts"]["code"] > 0 and files["counts"]["infra"] > 0
    assert any(p.startswith("infra/live/dev/") for p in paths)
    assert files["tree"]["type"] == "dir" and files["tree"]["children"]
    infra_only = (await client.get(f"/builds/{build_id}/files", params={"origin": "infra"})).json()
    assert {f["origin"] for f in infra_only["files"]} == {"infra"}
    repo = next(f for f in files["files"] if f["path"].endswith("Repositories/FundingStatusLookupRepository.cs"))
    assert repo["process_id"] == FSL and repo["tibco_ref"]["activity_ids"]
    r = await client.get(f"/builds/{build_id}/files/{repo['path']}")
    assert r.status_code == 200 and r.json()["language"] == "csharp"
    assert "QueryFundingStatus" in r.json()["content"] and r.json()["tibco_ref"]["rows"]
    assert (await client.get(f"/builds/{build_id}/files/../../etc/passwd")).status_code == 404

    tests = (await client.get(f"/builds/{build_id}/tests")).json()
    assert tests["summary"]["total"] > 0 and tests["summary"]["not_run"] == tests["summary"]["total"]
    kinds = {c["kind"] for c in tests["cases"]}
    assert {"rule", "branch", "scenario"} <= kinds
    assert all(c["file"].startswith("tests/") for c in tests["cases"])
    # the (fake) Test Engineer wrote every rule test body
    assert all(c["has_body"] for c in tests["cases"] if c["kind"] == "rule")
    assert build["builder"]["test_engineer"]["filled"]
    rules_file = next(c["file"] for c in tests["cases"] if c["kind"] == "rule")
    content = (await client.get(f"/builds/{build_id}/files/{rules_file}")).json()
    assert content["origin"] == "ai" and "GetRequiredService<FundingStatusLookupService>" in content["content"]
    assert isinstance(build["builder"]["test_engineer"]["warnings"], list)
    # the builder filled no production file, but the Test Engineer wrote tests: the test reviewer ran
    assert build["builder"]["reviewers"] == ["test"]
    assert build["review_findings"] == {"test": []}  # the fake reviewer finds nothing
    async with app.state.sessionmaker() as s:
        review_calls = (await s.execute(select(ModelCall).where(ModelCall.job_id == build["job_id"],
                                                                ModelCall.stage == "reviewer"))).scalars().all()
    assert len(review_calls) == 1  # the test reviewer only: no production reviewer call
    assert (await client.get(f"/builds/{build_id}/reviews")).json() == []

    infra = (await client.get(f"/builds/{build_id}/infra")).json()
    assert infra["files"] and infra["validation"] is not None
    assert {f["kind"] for f in infra["files"]} >= {"terraform", "terragrunt", "dockerfile", "pipeline"}

    g4 = (await _gates(client, proc_id))["G4"]
    assert g4["enabled"] is False
    assert "compile did not pass" in g4["reason"]
    assert "NuGet" not in g4["reason"]  # the remedy is named only when the restore log carries the NuGet note
    assert "without a test body" not in g4["reason"]
    r = await client.post(f"/gates/{proc_id}/G4", json={"state": "approved", "note": ""}, headers=SME)
    assert r.status_code == 409


async def test_g4_disabled_when_restore_fails(client, app, monkeypatch):
    if build_svc.find_dotnet() is None:
        pytest.skip("dotnet is not installed here")
    if build_svc.nuget_available():  # a machine with NuGet: simulate the unreachable source
        from engine.dotnet_gen.runner import StepResult

        monkeypatch.setattr(build_svc, "run_restore", lambda root, dotnet: StepResult(
            ok=False, log="error NU1301: Unable to load the service index (simulated)"))
        monkeypatch.setattr(build_svc, "nuget_probe", lambda: (False, "simulated: source unreachable"))
    _, proc_id, build_id, build = await _built(client, monkeypatch, dotnet=True)
    steps = {s["name"]: s for s in build["steps"]}
    assert steps["restore"]["ok"] is False and "NuGet unreachable" in steps["restore"]["log"]
    assert "NU1301" in steps["restore"]["log"] and "probe:" in steps["restore"]["log"]  # restore really ran
    assert "reduced check" in build["metrics"]["details"]["standards_adherence"]
    assert steps["build"]["skipped"] is True and "restore failed" in steps["build"]["log"]
    assert steps["format"]["ok"] is True and "whitespace" in steps["format"]["log"]  # runs without NuGet
    assert steps["test"]["skipped"] is True
    assert build["status"] == "failed"
    g4 = (await _gates(client, proc_id))["G4"]
    assert g4["enabled"] is False and "compile did not pass (restore failed)" in g4["reason"]
    assert "NuGet unreachable: run `make nuget-cache` on a connected machine" in g4["reason"]  # the remedy
    tests = (await client.get(f"/builds/{build_id}/tests")).json()
    assert tests["summary"]["total"] > 0  # the tests exist as files


async def test_test_engineer_fills_rule_bodies_and_clears_g4_reason(client, app, monkeypatch):
    async def no_bodies(*args, **kwargs):
        return []

    monkeypatch.setattr(build_svc, "fill_test_bodies", no_bodies)
    _, proc_id, build_id, _ = await _built(client, monkeypatch)
    tests = (await client.get(f"/builds/{build_id}/tests")).json()
    unfilled = [c for c in tests["cases"] if c["kind"] == "rule"]
    assert unfilled and all(c["has_body"] is False for c in unfilled)
    assert "without a test body" in (await _gates(client, proc_id))["G4"]["reason"]

    monkeypatch.undo()  # the real Test Engineer (fake provider) again; dotnet stays absent
    monkeypatch.setattr(build_svc, "find_dotnet", lambda: None)
    r = await client.post(f"/processes/{proc_id}/build", json={})
    job = await _wait_job(client, r.json()["job_id"])
    assert job["status"] == "done", job
    assert "test engineer" in await _stages(app, job["id"])
    tests = (await client.get(f"/builds/{r.json()['build_id']}/tests")).json()
    filled = [c for c in tests["cases"] if c["kind"] == "rule"]
    assert {c["name"] for c in filled} == {c["name"] for c in unfilled}
    assert all(c["has_body"] for c in filled)
    g4 = (await _gates(client, proc_id))["G4"]
    assert "without a test body" not in g4["reason"] and "compile did not pass" in g4["reason"]
    async with app.state.sessionmaker() as s:
        calls = (await s.execute(select(ModelCall).where(ModelCall.stage == "test_engineer"))).scalars().all()
    assert calls


DOCKER_DOWN = ("DotNet.Testcontainers.Builders.DockerUnavailableException: Docker is either not running or "
               "misconfigured.\nFailed! - Failed: 17, Passed: 0")
TESTDB_HINT = "TEST_DATABASE_URL not set: generated tests will start a Testcontainers Postgres (needs Docker)"


class RecordingRunner:
    """A ``DotnetRunner`` whose ``dotnet`` commands are recorded with their child environment:
    restore, build and format succeed, the tests fail as they do when Testcontainers finds no Docker."""
    calls: ClassVar[list[tuple[str, dict[str, str]]]] = []

    @classmethod
    def make(cls):
        from engine.dotnet_gen.runner import DotnetRunner

        class Recording(DotnetRunner):
            def _run(self, args, timeout_s=None, env=None):
                cls.calls.append((args[0], self.env(env)))
                if args[0] == "build":
                    return 0, "Build succeeded.\n    0 Warning(s)\n    0 Error(s)\n"
                if args[0] == "test":
                    return 1, DOCKER_DOWN
                return 0, ""

        return Recording


async def test_generated_tests_get_the_test_database(client, app, monkeypatch, settings):
    recording = RecordingRunner.make()
    RecordingRunner.calls.clear()
    monkeypatch.setattr(build_svc, "DotnetRunner", recording)
    monkeypatch.setattr(build_svc, "find_dotnet", lambda: "dotnet")
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql://inherited/not_passed_test")  # not inherited
    settings.test_database_url = "postgresql://exiqo:exiqo@db.example:5432/generated_test"
    _, proc_id, build_id, _ = await _built(client, monkeypatch, dotnet=True)
    tests = [env for name, env in RecordingRunner.calls if name == "test"]
    assert len(tests) >= 2, RecordingRunner.calls  # the test step and the repair loop's re-run
    assert all(env.get("TEST_DATABASE_URL") == settings.test_database_url for env in tests)
    assert TESTDB_HINT not in (await _gates(client, proc_id))["G4"]["reason"]
    async with app.state.sessionmaker() as s:
        job_id = (await s.get(Build, build_id)).job_id
        logs = [e.text for e in (await s.execute(select(JobEvent).where(JobEvent.job_id == job_id))).scalars()]
    assert TESTDB_HINT not in logs

    # unset: one log line says so, and the G4 reason names Docker when Testcontainers could not start
    RecordingRunner.calls.clear()
    settings.test_database_url = ""
    r = await client.post(f"/processes/{proc_id}/build", json={})
    job = await _wait_job(client, r.json()["job_id"], timeout=1800)
    assert job["status"] == "done", job
    tests = [env for name, env in RecordingRunner.calls if name == "test"]
    assert tests and all("TEST_DATABASE_URL" not in env for env in tests)
    async with app.state.sessionmaker() as s:
        logs = [e.text for e in (await s.execute(select(JobEvent).where(JobEvent.job_id == job["id"]))).scalars()]
    assert logs.count(TESTDB_HINT) == 1
    g4 = (await _gates(client, proc_id))["G4"]
    assert g4["enabled"] is False and TESTDB_HINT in g4["reason"], g4


async def test_build_scaffold_passes_with_dotnet(client, app, monkeypatch):
    if build_svc.find_dotnet() is None:
        pytest.skip("dotnet is not installed here")
    if not build_svc.nuget_available():
        pytest.skip("NuGet unreachable: run `make nuget-cache` on a machine with NuGet access")
    _, _proc_id, build_id, build = await _built(client, monkeypatch, dotnet=True)
    steps = {s["name"]: s for s in build["steps"]}
    assert steps["restore"]["ok"] and steps["build"]["ok"], steps
    r = await client.post(f"/builds/{build_id}/run")
    assert r.status_code == 200, r.text
    try:
        r = await client.post(f"/builds/{build_id}/verify")
        assert r.status_code == 202, r.text
        job = await _wait_job(client, r.json()["job_id"], timeout=600)
        assert job["status"] == "done", job
        parity = (await client.get(f"/builds/{build_id}/parity")).json()
        assert parity["source"] == "model" and parity["reports"]
    finally:
        await client.delete(f"/builds/{build_id}/run")


async def test_verify_parity_report_shape(client, app, monkeypatch, tmp_path):
    # findings decided "keep": no difference is explained by a fix, so a wrong field is a defect
    _, proc_id, build_id, _ = await _built(client, monkeypatch, decision="keep")
    r = await client.post(f"/builds/{build_id}/verify")
    assert r.status_code == 409 and "not running" in r.json()["detail"]
    assert (await client.get(f"/builds/{build_id}/parity")).status_code == 404

    scenarios = (await client.get(f"/processes/{proc_id}/scenarios")).json()["scenarios"]
    answers = {s["input"]["parameters"]["dealId"]: s["expected"]["output"] for s in scenarios}
    wrong = json.loads(json.dumps(answers))
    wrong["DL-50001"]["body"]["status"] = "PENDING"  # one defect per scenario on DL-50001
    stand_in = StandIn(wrong, tmp_path / "service.log")
    app.state.services[build_id] = verify_svc.RunInfo(build_id=build_id, handle=stand_in,
                                                      port=int(stand_in.url.rsplit(":", 1)[1]))
    try:
        state = (await client.get(f"/builds/{build_id}/run")).json()
        assert state["running"] is True and state["url"] == stand_in.url
        assert "stand-in" in (await client.get(f"/builds/{build_id}/run/logs")).json()["log"]

        r = await client.post(f"/builds/{build_id}/verify")
        assert r.status_code == 202, r.text
        job = await _wait_job(client, r.json()["job_id"])
        assert job["status"] == "done", job
        parity = (await client.get(f"/builds/{build_id}/parity")).json()
        assert set(parity) >= {"reports", "parity", "unexplained", "source"}
        assert parity["source"] == "model"
        assert len(parity["reports"]) == len(scenarios)
        for rep in parity["reports"]:
            assert set(rep) >= {"scenario_id", "fields", "side_effects", "parity", "unexplained", "status"}
        defects = [f for rep in parity["reports"] for f in rep["fields"] if f["status"] == "defect"]
        assert {f["path"] for f in defects} == {"output.body.status"}
        assert parity["unexplained"] == len(defects) >= 1
        assert 0 < parity["parity"] < 1
        noise = [f for rep in parity["reports"] for f in rep["fields"] if f["status"] == "noise"]
        assert any(f["path"] == "output.body.fundedAt" for f in noise)
        async with app.state.sessionmaker() as s:
            diffs = (await s.execute(select(FieldDiff))).scalars().all()
        assert any(d.classification == "defect" and d.field == "output.body.status" for d in diffs)
        g5 = (await _gates(client, proc_id))["G5"]
        assert g5["enabled"] is False and "unexplained difference" in g5["reason"]

        # the service fixed: no unexplained difference, only the G4 approval is missing
        stand_in.answers.clear()
        stand_in.answers.update(answers)
        job = await _wait_job(client, (await client.post(f"/builds/{build_id}/verify")).json()["job_id"])
        assert job["status"] == "done" and job["result"]["unexplained"] == 0
        parity = (await client.get(f"/builds/{build_id}/parity")).json()
        assert parity["unexplained"] == 0 and parity["parity"] == 1.0
        g5 = (await _gates(client, proc_id))["G5"]
        assert g5["reason"] == "G4 not approved"
    finally:
        r = await client.delete(f"/builds/{build_id}/run")
        assert r.json()["stopped"] is True
    assert (await client.get(f"/builds/{build_id}/run")).json()["running"] is False


async def test_plan_connect_later(client, app, monkeypatch):
    pid, proc_id, build_id, _ = await _built(client, monkeypatch)
    assert (await client.get(f"/builds/{build_id}/plan")).status_code == 404
    r = await client.post(f"/builds/{build_id}/plan")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["connected"] is False and "not connected" in out["reason"]
    assert out["last_validation"] is not None and "modules" in out["last_validation"]
    plan = (await client.get(f"/builds/{build_id}/plan")).json()
    assert plan["status"] == "connect_later" and plan["apply"]["enabled"] is False
    g6 = (await _gates(client, proc_id))["G6"]
    assert g6["enabled"] is False and "no plan" in g6["reason"]

    # connect Azure: the secret is stored encrypted and never returned
    body = {"tenant": "t-1", "subscription": "s-1", "resource_group": "rg-gmf", "runtime": "container_app",
            "client_id": "app-1", "client_secret": "super-secret-value"}
    assert (await client.post(f"/projects/{pid}/azure", json=body)).status_code == 401
    r = await client.post(f"/projects/{pid}/azure", json=body, headers=OPS)
    assert r.status_code == 200, r.text
    assert r.json()["connected"] is True and r.json()["secret_stored"] is True
    assert "super-secret-value" not in r.text
    assert "super-secret-value" not in (await client.get(f"/projects/{pid}/azure")).text
    async with app.state.sessionmaker() as s:
        conn = await s.scalar(select(AzureConnection).where(AzureConnection.project_id == pid))
    assert conn.secret_enc and "super-secret-value" not in conn.secret_enc
    assert app.state.fernet.decrypt(conn.secret_enc.encode()).decode() == "super-secret-value"

    # connected but a tool is missing here (az): still connect-later, with the reason
    tools = build_svc.toolchain()
    if not all((tools.get(t) or {}).get("present") for t in ("terragrunt", "terraform", "az")):
        out = (await client.post(f"/builds/{build_id}/plan")).json()
        assert out["connected"] is False and "missing tool" in out["reason"]

    # operations acknowledges connect-later with a note: G6 evidence is there
    r = await client.post(f"/builds/{build_id}/plan/acknowledge", json={"note": "deploy next sprint"},
                          headers={"X-Agent": "bot"})
    assert r.status_code == 403
    r = await client.post(f"/builds/{build_id}/plan/acknowledge", json={"note": "deploy next sprint"}, headers=OPS)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "acknowledged" and r.json()["decided_by"] == "ops.maria"
    g6 = (await _gates(client, proc_id))["G6"]
    assert g6["reason"] == "G5 not approved"
    # a later plan request while still not ready keeps the acknowledgement
    out = (await client.post(f"/builds/{build_id}/plan")).json()
    assert out["acknowledged"] is True and out["plan"]["status"] == "acknowledged"
    assert (await client.get(f"/builds/{build_id}/plan")).json()["status"] == "acknowledged"
    assert (await _gates(client, proc_id))["G6"]["reason"] == "G5 not approved"

    # the solution downloads as a zip without build output
    r = await client.get(f"/builds/{build_id}/download")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert "GmfCredit.sln" in names and "Dockerfile" in names and any(n.startswith("infra/") for n in names)
    assert not any(part in ("bin", "obj", "TestResults", ".exiqo") for n in names for part in n.split("/"))


async def test_readiness_rows(client, app, monkeypatch):
    pid, _ = await _scanned(client)
    r = await client.get(f"/projects/{pid}/readiness")
    assert r.status_code == 200, r.text
    rows = {row["key"]: row for row in r.json()["rows"]}
    assert list(rows) == [k for k, _ in readiness_svc.EXPECTATIONS]
    assert len(rows) == 13
    for row in rows.values():
        assert row["status"] in ("met", "partial", "missing")
        assert set(row) >= {"expectation", "status", "evidence", "gap"}
        assert all(set(e) == {"kind", "ref", "label"} for e in row["evidence"])
        assert (row["gap"] is None) == (row["status"] == "met")
    assert rows["synthetic_estate"]["status"] == "met"
    assert rows["domain_entry_point"]["status"] == "met"
    assert rows["process_canvas"]["status"] == "met"
    assert rows["compilable_dotnet"]["status"] == "missing"
    assert rows["provider_choice"]["status"] == "partial"  # PROVIDER=fake
    assert (await client.get("/projects/nope/readiness")).status_code == 404

    _, _, build_id, _ = await _built(client, monkeypatch)
    rows = {row["key"]: row for row in (await client.get(
        f"/projects/{(await client.get('/projects')).json()[0]['id']}/readiness")).json()["rows"]}
    assert rows["compilable_dotnet"]["status"] == "partial" and "did not compile" in rows["compilable_dotnet"]["gap"]
    assert rows["azure_assets"]["status"] in ("met", "partial")
    assert rows["functional_tests"]["status"] == "partial"
    assert rows["test_data"]["status"] == "met"
    assert rows["tokenomics"]["status"] == "met"
    assert rows["routing"]["status"] == "met"
    assert rows["metrics_on_screen"]["evidence"][0]["ref"] == f"/builds/{build_id}"


async def test_scenarios_generated_and_cached(client, app):
    _pid, procs = await _scanned(client)
    proc_id = procs[FSL]["id"]
    empty = (await client.get(f"/processes/{proc_id}/scenarios")).json()  # GET never generates
    assert empty["scenarios"] == [] and "scenarios/generate" in empty["hint"]
    async with app.state.sessionmaker() as s:
        assert (await s.execute(select(ScenarioRow))).scalars().all() == []
    r = await client.post(f"/processes/{proc_id}/scenarios/generate", json={})
    assert r.status_code == 200, r.text
    first = r.json()
    assert first["generated_now"] is True and first["source"] == "model"
    assert "derived from the TIBCO model" in first["label"]
    keys = [s["key"] for s in first["scenarios"]]
    assert "FundingStatusLookup:happy" in keys and any(":branch:" in k for k in keys)
    assert all(s["expected"] and s["expected"]["output"] for s in first["scenarios"])
    second = (await client.get(f"/processes/{proc_id}/scenarios")).json()
    assert second["generated_now"] is False and second["hint"] is None
    assert [s["id"] for s in second["scenarios"]] == [s["id"] for s in first["scenarios"]]
    again = (await client.post(f"/processes/{proc_id}/scenarios/generate")).json()
    assert again["generated_now"] is False  # the cache is kept without refresh
    refreshed = (await client.post(f"/processes/{proc_id}/scenarios/generate", json={"refresh": True})).json()
    assert refreshed["generated_now"] is True
    assert [s["id"] for s in refreshed["scenarios"]] == [s["id"] for s in first["scenarios"]]  # upserted

    sme = {"name": "Unknown deal", "input": {"parameters": {"dealId": "DL-99999"}}}
    assert (await client.post(f"/processes/{proc_id}/scenarios", json=sme,
                              headers={"X-Agent": "bot"})).status_code == 403
    r = await client.post(f"/processes/{proc_id}/scenarios", json=sme, headers=SME)
    assert r.status_code == 201, r.text
    added = r.json()
    assert added["origin"] == "sme" and added["created_by"] == "sme.vicky"
    assert added["expected"]["output"]["status"] == 404
    assert (await client.post(f"/processes/{proc_id}/scenarios", json=sme, headers=SME)).status_code == 409
    third = (await client.get(f"/processes/{proc_id}/scenarios")).json()
    assert len(third["scenarios"]) == len(keys) + 1

    acc = procs[ACC]["id"]
    before = (await client.get(f"/processes/{acc}/test-data")).json()
    assert before["inputs"] == [] and before["notes"]  # read only: the hint, no generation
    assert (await client.post(f"/processes/{acc}/scenarios/generate")).status_code == 200
    data = (await client.get(f"/processes/{acc}/test-data")).json()
    assert "separate workstream" in data["note"]
    assert data["inputs"] and data["tables"]
    text = json.dumps(data)
    assert not re.search(r"\b\d{3}-\d{2}-\d{4}\b", text)  # no SSN in clear
    assert data["masked_fields"]


async def test_scenario_database_name_per_project(settings):
    settings.scenario_database_url = ""
    dsn = verify_svc.scenario_dsn(settings, "abc123")
    assert "dbname=exiqo_scn_abc123_test" in dsn
    assert verify_svc.scenario_db_name(settings, "abc123") == "exiqo_scn_abc123_test"
    assert "Database=exiqo_scn_abc123_test" in verify_svc.npgsql_connection_string(dsn)
    with pytest.raises(verify_svc.ScenarioDBUnavailable):
        verify_svc.ensure_database("postgresql://x@localhost/not_a_test_db", dsn)


async def test_history_totals_by_phase(client, app):
    pid, proc_id, _ = await _designed(client)
    r = await client.get("/history", params={"project_id": pid})
    assert r.status_code == 200, r.text
    h = r.json()
    phases = h["totals"]["by_phase"]
    assert list(phases) == ["demo", "discovery", "design", "build", "verify"]
    assert phases["discovery"]["jobs"] >= 2  # scan, analyse
    assert phases["discovery"]["model_calls"] >= 3  # analyst, risk reviewer, challenger
    assert phases["design"]["jobs"] == 1 and phases["design"]["model_calls"] >= 1
    assert phases["build"]["jobs"] == 0
    total = h["totals"]["all"]
    assert total["model_calls"] == sum(p["model_calls"] for p in phases.values())
    assert total["tokens_in"] == sum(p["tokens_in"] for p in phases.values())
    kinds = {e["kind"] for e in h["timeline"]}
    assert kinds == {"job", "model_call", "decision"}
    assert any(e["kind"] == "decision" and e["action"] == "gate approved" and e["gate"] == "G3"
               for e in h["timeline"])
    assert [e["ts"] for e in h["timeline"]] == sorted(e["ts"] for e in h["timeline"])
    wf = next(w for w in h["workflows"] if w["process_id"] == proc_id)
    assert wf["complexity_band"] and wf["model_calls"] >= 4 and wf["gates"]["G3"] == "approved"
    assert sum(b["workflows"] for b in h["totals"]["by_band"].values()) == len(h["workflows"])
    async with app.state.sessionmaker() as s:
        designer = (await s.execute(select(ModelCall).where(ModelCall.stage == "designer"))).scalars().all()
    assert designer and designer[0].tier


async def test_settings_and_provider_eval(client, app, settings):
    r = await client.get("/settings")
    assert r.status_code == 200, r.text
    view = r.json()
    assert view["provider"]["active"] == "fake"
    assert {p["name"] for p in view["providers"]} == {"claude", "azure", "fake"}
    assert set(view["toolchains"]) >= {"dotnet", "terraform", "terragrunt", "docker", "az"}
    assert view["routing"]["bands"] == {"strong": [8, 10], "standard": [4, 7], "efficient": [0, 3]}
    assert "langfuse" in view
    bad = {"routing_bands": {"strong": [7, 10], "standard": [4, 7], "efficient": [0, 3]}}
    assert (await client.put("/settings", json=bad, headers=SME)).status_code == 422
    good = {"routing_bands": {"strong": [9, 10], "standard": [4, 8], "efficient": [0, 3]},
            "models": {"strong": "claude-opus-test"}}
    assert (await client.put("/settings", json=good)).status_code == 401
    r = await client.put("/settings", json=good, headers=SME)
    assert r.status_code == 200, r.text
    assert r.json()["routing"]["bands"]["strong"] == [9, 10]
    assert r.json()["models"]["tiers"]["strong"]["claude"] == "claude-opus-test"
    assert settings.routing_bands == {"strong": (9, 10), "standard": (4, 8), "efficient": (0, 3)}

    _, procs = await _scanned(client)
    r = await client.post("/providers/eval", json={"process_ids": [procs[FSL]["id"]]})
    assert r.status_code == 202, r.text
    job = await _wait_job(client, r.json()["job_id"])
    assert job["status"] == "done", job
    ev = (await client.get(f"/providers/eval/{r.json()['eval_id']}")).json()
    assert ev["status"] == "done" and len(ev["rows"]) == 1
    row = ev["rows"][0]
    assert row["provider"] == "fake" and row["error"] is None and row["rules"] > 0
    assert row["citations_verified"] == row["citations_total"]
    summary = ev["summary"][0]
    assert summary["provider"] == "fake" and summary["citation_verified_rate"] == 1.0
    async with app.state.sessionmaker() as s:
        calls = (await s.execute(select(ModelCall).where(ModelCall.stage == "eval"))).scalars().all()
        gates = (await s.execute(select(Gate))).scalars().all()
    assert calls and all(c.job_id == job["id"] for c in calls)
    assert gates == []  # an evaluation never touches a gate


def test_plan_output_parsed_per_unit():
    from api.app.services.infra import parse_plan

    output = (
        "14:02:11.120 INFO   The runner at . will be processed in the following order:\n"
        "14:02:40.001 STDOUT [gmf-credit/resource_group] tofu: Plan: 1 to add, 0 to change, 0 to destroy.\n"
        "14:02:41.002 STDOUT [gmf-credit/service_bus] tofu: \x1b[1mPlan:\x1b[0m 4 to add, 1 to change, 0 to destroy.\n"
        "14:02:42.003 STDOUT [gmf-credit/key_vault] tofu: No changes. Your infrastructure matches the configuration.\n"
    )
    summary = parse_plan(output)
    assert summary["units"] == [
        {"unit": "gmf-credit/key_vault", "add": 0, "change": 0, "destroy": 0},
        {"unit": "gmf-credit/resource_group", "add": 1, "change": 0, "destroy": 0},
        {"unit": "gmf-credit/service_bus", "add": 4, "change": 1, "destroy": 0},
    ]
    assert (summary["add"], summary["change"], summary["destroy"]) == (5, 1, 0)


def test_fernet_key_from_app_secret():
    from cryptography.fernet import Fernet

    from api.app.services.infra import fernet_key

    key = Fernet.generate_key()
    assert fernet_key(key.decode()) == key  # a Fernet key is used as is
    derived = fernet_key("a passphrase")
    assert derived != b"a passphrase" and Fernet(derived).decrypt(Fernet(derived).encrypt(b"x")) == b"x"


def test_bus_messages_become_side_effects():
    from engine.parity.models import ExpectedResult, SideEffect

    expected = ExpectedResult(side_effects=[SideEffect(kind="message_published", target="gmf.funding.events",
                                                       payload={"dealId": "DL-1", "status": "FUNDED"})])
    messages = [{"entity": "gmf.audit", "body": json.dumps({"x": 1}), "properties": {}},
                {"entity": "gmf.funding.events", "body": json.dumps({"dealId": "DL-1", "status": "FUNDED"})}]
    effects = verify_svc.actual_messages(expected, messages)
    assert [(e.target, e.payload) for e in effects] == [
        ("gmf.funding.events", {"dealId": "DL-1", "status": "FUNDED"}),
        ("gmf.audit", {"x": 1}),  # published but not expected: compared, so it shows as a difference
    ]
    assert dict(verify_svc._SELECTOR.findall("MsgType = 'FUNDING_REQUEST' AND Region='US'")) == {
        "MsgType": "FUNDING_REQUEST", "Region": "US"}


UNCONFIRMED = "not confirmed: no TIBCO sandbox run; derived from the TIBCO model"


CONTROL_ONLY = ("touched only through a branch decision by a Fix finding; not accepted as intended "
                "without a TIBCO sandbox run")


async def test_intended_is_unconfirmed_and_not_unexplained(client, app, monkeypatch, tmp_path):
    # every finding decided Fix: F1 cites QueryFundingStatus, the lookup that decides the branch. The
    # found reply's body fields read its record (data lineage); the not-found reply's fields depend
    # on it only through the otherwise link (control lineage).
    pid, proc_id, build_id, _ = await _built(client, monkeypatch, decision="fix")
    fixed = {f["finding_key"] for f in (await client.get(f"/processes/{proc_id}/analysis")).json()["findings"]
             if f["decision"] == "fix"}
    scenarios = (await client.get(f"/processes/{proc_id}/scenarios")).json()["scenarios"]
    answers = {s["input"]["parameters"]["dealId"]: s["expected"]["output"] for s in scenarios}
    found = [s for s in scenarios if s["input"]["parameters"]["dealId"] == "DL-50001"]
    not_found = [s for s in scenarios if s["input"]["parameters"]["dealId"] == "DL-50003"]
    assert len(found) == 2 and len(not_found) == 1  # two scenarios read DL-50001, one reads DL-50003
    changed = json.loads(json.dumps(answers))
    changed["DL-50001"]["body"]["status"] = "FUNDED (reworded by the fix)"  # produced from the lookup
    stand_in = StandIn(changed, tmp_path / "service.log")
    app.state.services[build_id] = verify_svc.RunInfo(build_id=build_id, handle=stand_in,
                                                      port=int(stand_in.url.rsplit(":", 1)[1]))
    # G4 needs a compiling build (no NuGet here): record its approval so G5 depends on parity only
    async with app.state.sessionmaker() as s:
        proc = await s.get(ProcessRow, proc_id)
        s.add(Gate(process_id=proc_id, gate="G4", state="approved", spec_hash=proc.spec_hash,
                   decided_by="sme.vicky"))
        await s.commit()
    try:
        job = await _wait_job(client, (await client.post(f"/builds/{build_id}/verify")).json()["job_id"])
        assert job["status"] == "done", job
        assert job["result"]["unexplained"] == 0
        parity = (await client.get(f"/builds/{build_id}/parity")).json()
        intended = [f for rep in parity["reports"] for f in rep["fields"] + rep["side_effects"]
                    if f["status"] == "intended"]
        assert [(f["path"], f["confirmed"]) for f in intended] == [("output.body.status", False)] * 2
        assert {f["finding_id"] for f in intended} <= fixed
        assert all("confirmed" not in f and "via" not in f for rep in parity["reports"] for f in rep["fields"]
                   if f["status"] != "intended")
        assert {rep["status"] for rep in parity["reports"]} == {"match"}
        assert parity["unexplained"] == 0 and parity["intended"] == 2
        assert parity["unconfirmed_intended"] == 2 and parity["counterfactual"] is False
        assert parity["control_only_defects"] == 0
        assert parity["unconfirmed_note"] == UNCONFIRMED
        note = f"2 intended difference(s) {UNCONFIRMED}"
        g5 = (await _gates(client, proc_id))["G5"]
        assert g5["enabled"] is True and g5["notes"] == [note], g5
        assert (await _gates(client, proc_id))["G4"]["notes"] == []
        rows = {row["key"]: row for row in (await client.get(f"/projects/{pid}/readiness")).json()["rows"]}
        assert rows["swagger_parity"]["status"] == "met"
        assert note in rows["swagger_parity"]["evidence"][0]["label"]

        r = await client.post(f"/gates/{proc_id}/G5", json={"state": "approved", "note": "ok"}, headers=SME)
        assert r.status_code == 200, r.text
        assert r.json()["notes"] == [note]
        audit = (await client.get(f"/processes/{proc_id}/audit")).json()
        approved = next(a for a in audit if a["action"] == "gate approved" and a["gate"] == "G5")
        assert approved["detail"]["unconfirmed_intended"] == 2

        # a difference F1 touches only through the branch decision (the not-found message reads the
        # request, not the lookup) stays a defect: it keeps the finding id, carries via "control",
        # counts as unexplained and blocks G5 with its own reason
        changed["DL-50003"]["body"]["message"] += " (reworded by the fix)"
        job = await _wait_job(client, (await client.post(f"/builds/{build_id}/verify")).json()["job_id"])
        assert job["status"] == "done" and job["result"]["unexplained"] == 1
        parity = (await client.get(f"/builds/{build_id}/parity")).json()
        defects = [f for rep in parity["reports"] for f in rep["fields"] if f["status"] == "defect"]
        assert [(f["path"], f["via"]) for f in defects] == [("output.body.message", "control")]
        assert defects[0]["finding_id"] in fixed and "confirmed" not in defects[0]
        assert parity["unexplained"] == 1 and parity["control_only_defects"] == 1
        assert parity["unconfirmed_intended"] == 2 and parity["intended"] == 2
        async with app.state.sessionmaker() as s:
            run_ids = [r.id for r in await evidence_svc.latest_verify_runs(s, build_id)]
            diffs = (await s.execute(select(FieldDiff).where(FieldDiff.run_id.in_(run_ids),
                                                             FieldDiff.field == "output.body.message"))
                     ).scalars().all()
        assert [(d.classification, d.finding_key) for d in diffs] == [("defect", defects[0]["finding_id"])]
        g5 = (await _gates(client, proc_id))["G5"]
        assert g5["state"] == "pending" and g5["enabled"] is False
        assert f"1 unexplained difference(s) in the parity report, of which 1 are {CONTROL_ONLY}" in g5["reason"]
        assert g5["notes"] == [note]

        # a difference no fix touches (a field the model never produces has no lineage) is a defect
        # without a finding id
        changed["DL-50003"]["body"]["unexpected"] = "only in .NET"
        job = await _wait_job(client, (await client.post(f"/builds/{build_id}/verify")).json()["job_id"])
        assert job["status"] == "done" and job["result"]["unexplained"] == 2
        parity = (await client.get(f"/builds/{build_id}/parity")).json()
        defects = {f["path"]: f for rep in parity["reports"] for f in rep["fields"] if f["status"] == "defect"}
        assert defects["output.body.unexpected"]["finding_id"] is None
        assert "via" not in defects["output.body.unexpected"]
        assert defects["output.body.message"]["via"] == "control"
        assert parity["unexplained"] == 2 and parity["control_only_defects"] == 1
        g5 = (await _gates(client, proc_id))["G5"]
        assert f"2 unexplained difference(s) in the parity report, of which 1 are {CONTROL_ONLY}" in g5["reason"]
    finally:
        await client.delete(f"/builds/{build_id}/run")


def test_bus_publish_outcome_and_window():
    assert verify_svc.publish_outcome({"disposition": "completed", "reason": None, "messages_published": 1}) is None
    fault = verify_svc.publish_outcome({"disposition": "dead_lettered", "reason": "DealerInactiveFault",
                                        "description": "dealer D1 inactive", "messages_published": 0})
    assert fault is not None and fault.name == "DealerInactiveFault"
    for outcome in ({"disposition": "dead_lettered", "reason": "SelectorMismatch"},
                    {"disposition": "abandoned", "reason": "NpgsqlException", "description": "x"},
                    {"disposition": "weird"}):
        with pytest.raises(RuntimeError):
            verify_svc.publish_outcome(outcome)
    # completed but nothing handled it (the consumer subscribes to another entity): the scenario did not run
    with pytest.raises(RuntimeError, match="no consumer subscribed to gmf.dealer.funding.request"):
        verify_svc.publish_outcome({"disposition": "completed", "messages_published": 0,
                                    "reason": "no consumer subscribed to gmf.dealer.funding.request"})
    with pytest.raises(RuntimeError, match="messages_published"):
        verify_svc.publish_outcome({"disposition": "completed", "reason": None})
    before = [{"entity": "old", "body": "{}"}]
    after = before + [{"entity": "gmf.dealer.funding.request", "body": "{}"},
                      {"entity": "gmf.funding.events", "body": "{\"status\": \"FUNDED\"}"}]
    assert verify_svc.published_window(before, after, "gmf.dealer.funding.request") == [after[2]]


async def test_outbound_stub_server_records_service_calls():
    import httpx

    from api.app.services.stub_server import StubServer
    from engine.parity.models import StubResponse

    stubs = StubServer()
    try:
        stubs.load({"Res.Equifax/consumer-credit/v2/scores": StubResponse(status=200, body={"score": 781}),
                    "Res.TransUnion/credit-report/v1/score": StubResponse(status=200, body={}, delay_ms=1500)},
                   {"Res.Equifax": "EquifaxClient", "Res.TransUnion": "TransUnionClient"},
                   {"Res.Equifax": 1000, "Res.TransUnion": 1000})
        async with httpx.AsyncClient(base_url=stubs.url, timeout=5) as c:
            r = await c.post("consumer-credit/v2/scores", json={"ssn": "1"})
            assert r.json() == {"score": 781}
            await c.post("consumer-credit/v2/scores", json={"ssn": "1"})  # a retry: counted once
            with pytest.raises(httpx.ReadTimeout):
                await c.post("credit-report/v1/score", json={"ssn": "1"}, timeout=0.2)
            assert (await c.get("unknown")).status_code == 404
        effects = stubs.side_effects()
        assert [(e.target, e.payload) for e in effects] == [
            ("EquifaxClient POST /consumer-credit/v2/scores", {"request": {"ssn": "1"}, "status": 200}),
            ("TransUnionClient POST /credit-report/v1/score", {"request": {"ssn": "1"}, "outcome": "timeout"}),
            ("(unstubbed) GET /unknown", {"request": None, "status": 404}),
        ]
    finally:
        stubs.stop()


def test_stub_timeouts_default_like_the_evaluator():
    from api.app.services.stub_server import StubServer
    from engine.ir.models import Application as IRApp
    from engine.parity.models import StubResponse
    from engine.parity.stubs import DEFAULT_TIMEOUT_MS

    app = IRApp.model_validate({"id": "a", "name": "a", "dialect": "bw5", "resources": [
        {"id": "Res.A", "kind": "http", "name": "A", "settings": {"timeout_ms": "5000"}},
        {"id": "Res.B", "kind": "http", "name": "B", "settings": {}}]})
    assert DEFAULT_TIMEOUT_MS == 30_000
    assert verify_svc.resource_timeouts(app) == {"Res.A": 5000, "Res.B": DEFAULT_TIMEOUT_MS}

    stubs = StubServer()
    try:
        # the longest resource timeout plus 1 s, bounded to 60 s
        stubs.load({}, {}, {"Res.A": 5000, "Res.B": 30_000})
        assert stubs.max_delay_s == 31.0
        stubs.load({}, {}, {"Res.C": 90_000})
        assert stubs.max_delay_s == 60.0
        # a delay the server cannot honor makes the scenario an error instead of a silent cut
        with pytest.raises(ValueError, match="cannot be honored"):
            stubs.load({"Res.C/slow": StubResponse(status=200, delay_ms=90_001)}, {}, {"Res.C": 90_000})
        stubs.load({"Res.B/slow": StubResponse(status=200, delay_ms=30_001)}, {"Res.B": "BClient"},
                   {"Res.B": 30_000})  # the generated timeout scenario against the default
        assert stubs.match("/slow") is not None
    finally:
        stubs.stop()


async def test_stub_server_records_the_query_like_the_evaluator():
    import httpx

    from api.app.services.stub_server import StubServer
    from engine.parity.models import StubResponse

    stubs = StubServer()
    try:
        stubs.load({"Res.Dealer/dealers": StubResponse(status=200, body={"ok": True})}, {"Res.Dealer": "DealerClient"},
                   {"Res.Dealer": 1000})
        async with httpx.AsyncClient(base_url=stubs.url, timeout=5) as c:
            r = await c.get("dealers?id=5")  # matched by path, the query ignored
            assert r.status_code == 200 and r.json() == {"ok": True}
            await c.get("dealers")
        effects = stubs.side_effects()
        # the evaluator stores the raw QueryString text under "query" when one is mapped
        assert [(e.target, e.payload) for e in effects] == [
            ("DealerClient GET /dealers", {"request": None, "query": "id=5", "status": 200}),
            ("DealerClient GET /dealers", {"request": None, "status": 200}),
        ]
    finally:
        stubs.stop()


class _TrackedStubs:
    instances: ClassVar[list[_TrackedStubs]] = []

    def __init__(self) -> None:
        self.url = "http://127.0.0.1:1/"
        self.stopped = False
        _TrackedStubs.instances.append(self)

    def stop(self) -> None:
        self.stopped = True


def _startable(monkeypatch, tmp_path):
    """A build folder with a solution and the seeding and dotnet lookup stubbed out."""
    (tmp_path / "GmfCredit.sln").write_text("", encoding="utf-8")
    _TrackedStubs.instances = []
    monkeypatch.setattr(verify_svc, "StubServer", _TrackedStubs)
    monkeypatch.setattr(verify_svc, "find_dotnet", lambda: "dotnet")
    monkeypatch.setattr(verify_svc, "open_db", lambda *a: type("DB", (), {"close": lambda self: None})())
    monkeypatch.setattr(verify_svc, "reset_db", lambda *a, **k: None)
    return Build(id="b1", process_id="p1", output_dir=str(tmp_path))


@pytest.mark.parametrize("fail", ["reserve_port", "runner"])
async def test_start_service_cleans_up_when_it_cannot_start(monkeypatch, tmp_path, settings, fail):
    build = _startable(monkeypatch, tmp_path)
    reserved = set(verify_svc._RESERVED_PORTS)
    if fail == "reserve_port":
        def no_port():
            raise RuntimeError("no free port")
        monkeypatch.setattr(verify_svc, "reserve_port", no_port)
    else:
        def broken(*args, **kwargs):
            raise RuntimeError("runner cannot be created")
        monkeypatch.setattr(verify_svc, "DotnetRunner", broken)
    with pytest.raises(RuntimeError):
        await verify_svc.start_service(settings, build, "proj", None, asyncio.Lock())
    assert [s.stopped for s in _TrackedStubs.instances] == [True]
    assert verify_svc._RESERVED_PORTS == reserved  # no port stays reserved


async def test_start_service_canceled_stops_what_the_launch_started(monkeypatch, tmp_path, settings):
    build = _startable(monkeypatch, tmp_path)
    reserved = set(verify_svc._RESERVED_PORTS)
    handles = []

    class Handle:
        url, pid, log_path = "http://127.0.0.1:2", 1, str(tmp_path / "log")

        def __init__(self) -> None:
            self.stopped = False
            handles.append(self)

        def poll(self):
            return 0 if self.stopped else None

        def stop(self) -> None:
            self.stopped = True

    class SlowRunner:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def run_service(self, *args, **kwargs):
            import time
            time.sleep(0.6)  # dotnet run still starting when the request is canceled
            return Handle()

    monkeypatch.setattr(verify_svc, "DotnetRunner", SlowRunner)
    task = asyncio.create_task(verify_svc.start_service(settings, build, "proj", None, asyncio.Lock()))
    await asyncio.sleep(0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(handles) == 1 and handles[0].stopped  # waited for the launch and stopped its service
    assert [s.stopped for s in _TrackedStubs.instances] == [True]
    assert verify_svc._RESERVED_PORTS == reserved

    # a second cancellation while the handler waits for the launch: the stubs and the port are
    # released at once and the service the launch returns is still stopped
    task = asyncio.create_task(verify_svc.start_service(settings, build, "proj", None, asyncio.Lock()))
    await asyncio.sleep(0.2)
    task.cancel()
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert [s.stopped for s in _TrackedStubs.instances] == [True, True]
    assert verify_svc._RESERVED_PORTS == reserved
    for _ in range(50):  # the launch finishes after the handler returned
        if len(handles) == 2 and handles[1].stopped:
            break
        await asyncio.sleep(0.1)
    assert len(handles) == 2 and handles[1].stopped


async def test_stop_waits_for_a_start_in_progress(client, app, monkeypatch, tmp_path):
    _, procs = await _scanned(client)
    async with app.state.sessionmaker() as s:
        b = Build(process_id=procs[FSL]["id"], status="passed", output_dir=str(tmp_path))
        s.add(b)
        await s.commit()
        build_id = b.id
    handle = None

    async def slow_start(settings, build, project_id, app_ir, lock, timeout_s=240.0):
        nonlocal handle
        await asyncio.sleep(0.5)
        handle = StandIn({}, tmp_path / "service.log")
        return verify_svc.RunInfo(build_id=build.id, handle=handle, port=int(handle.url.rsplit(":", 1)[1]))

    monkeypatch.setattr(verify_svc, "start_service", slow_start)

    async def stop_soon():
        await asyncio.sleep(0.1)
        return await client.delete(f"/builds/{build_id}/run")

    started, stopped = await asyncio.gather(client.post(f"/builds/{build_id}/run"), stop_soon())
    assert started.status_code == 200, started.text
    # the stop waited for the start and stopped the service it registered
    assert stopped.json()["stopped"] is True
    assert build_id not in app.state.services and handle.poll() is not None
    assert (await client.get(f"/builds/{build_id}/run")).json()["running"] is False
    # the build row is written under the same lock: the stop's null write lands after the start's
    async with app.state.sessionmaker() as s:
        row = await s.get(Build, build_id)
        assert (row.run_pid, row.run_port) == (None, None)


def test_unreadable_secret_is_reported():
    from cryptography.fernet import Fernet

    from api.app.models import AzureConnection as Conn
    from api.app.services import infra as infra_svc

    conn = Conn(project_id="p", tenant="t", subscription="s",
                secret_enc=Fernet(Fernet.generate_key()).encrypt(b"x").decode())
    with pytest.raises(infra_svc.SecretUnreadable):
        infra_svc.decrypt_secret(Fernet(Fernet.generate_key()), conn)
    assert infra_svc.decrypt_secret(Fernet(Fernet.generate_key()), Conn(project_id="p", tenant="t",
                                                                        subscription="s")) is None


async def test_stale_service_runs_cleared_at_startup(client, app, monkeypatch):
    _, _, build_id, _ = await _built(client, monkeypatch)
    async with app.state.sessionmaker() as s:
        b = await s.get(Build, build_id)
        b.run_pid, b.run_port = os.getpid(), 5999  # a live pid that is not this build's dotnet
        await s.commit()
    assert await verify_svc.clear_stale_runs(app.state.sessionmaker) == 1
    async with app.state.sessionmaker() as s:
        b = await s.get(Build, build_id)
        assert b.run_pid is None and b.run_port is None
    assert os.getpid()  # still alive: never killed a pid that is not the build's service
