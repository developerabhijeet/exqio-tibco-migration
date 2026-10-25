"""The ``memory.ingest`` job (Task 8): queued after phase jobs, gate decisions and project
creation; one ingest per project at a time; supersession; Neo4j outages retried with backoff.

Needs the test Neo4j (``memory_app`` / ``memory_client`` skip without ``NEO4J_TEST_URI``), except
the last test, which runs the plain app with ``NEO4J_URI`` empty.
"""
from __future__ import annotations

import asyncio
import os
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select, update

from api.app.db import utcnow
from api.app.jobs import registry
from api.app.jobs.runner import JobWorker, enqueue
from api.app.models import (
    AnalysisRun,
    Build,
    BuildFile,
    DesignRecord,
    FieldDiff,
    Job,
    JobEvent,
    MemoryEvent,
    Plan,
    Process,
    Rule,
    Scenario,
    ScenarioRun,
    TestResult,
)
from api.app.services import memory_ingest as mi

os.environ.setdefault("EMBEDDINGS_LOAD_TIMEOUT", "8")

DFR = "dealerfunding.DealerFundingRequest"
SME = {"X-User": "sme.vicky"}
KIND = "memory.ingest"


# ------------------------------------------------------------------ helpers
async def wait_job(client, job_id: str, timeout: float = 120.0) -> dict:
    async with asyncio.timeout(timeout):
        while True:
            job = (await client.get(f"/jobs/{job_id}")).json()
            if job["status"] in ("done", "failed"):
                return job
            await asyncio.sleep(0.2)


async def scan_sample(client, sample_upload) -> str:
    pid = (await client.post("/projects", json={"name": "memory"})).json()["id"]
    r = await client.post(f"/projects/{pid}/upload", json={"path": str(sample_upload)})
    assert r.status_code == 201, r.text
    r = await client.post(f"/projects/{pid}/scan", json={})
    assert (await wait_job(client, r.json()["job_id"]))["status"] == "done"
    return pid


async def jobs_of_kind(app, pid: str, kind: str) -> list[Job]:
    async with app.state.sessionmaker() as s:
        return list((await s.execute(select(Job).where(Job.project_id == pid, Job.kind == kind)
                                     .order_by(Job.created_at, Job.id))).scalars())


async def latest_kind(app, pid: str, kind: str) -> Job:
    return (await jobs_of_kind(app, pid, kind))[-1]


async def wait_kind(app, pid: str, kind: str, *, status: str, phase: str | None = None,
                    timeout: float = 120.0) -> Job:
    """The first ``kind`` job of the project (of ``phase``) once it has ``status``."""
    async with asyncio.timeout(timeout):
        while True:
            jobs = [j for j in await jobs_of_kind(app, pid, kind)
                    if phase is None or j.payload.get("phase") == phase]
            if jobs and jobs[0].status == status:
                return jobs[0]
            assert not jobs or jobs[0].status != "failed", jobs[0].error
            await asyncio.sleep(0.1)


async def get_job(app, job_id: str) -> Job:
    async with app.state.sessionmaker() as s:
        return await s.get(Job, job_id)


async def wait_terminal(app, job_id: str, timeout: float = 120.0) -> Job:
    async with asyncio.timeout(timeout):
        while True:
            job = await get_job(app, job_id)
            if job.status in ("done", "failed"):
                return job
            await asyncio.sleep(0.1)


async def idle(app, pid: str) -> None:
    """Every memory ingest of the project has finished."""
    for job in await jobs_of_kind(app, pid, KIND):
        assert (await wait_terminal(app, job.id)).status == "done", job.error


async def rerun_job(app, job_id: str) -> Job:
    """Requeue a finished job row as it is (same payload, same ingest key)."""
    async with app.state.sessionmaker() as s:
        await s.execute(update(Job).where(Job.id == job_id).values(
            status="queued", owner=None, heartbeat_at=None, result=None, error=None, finished_at=None))
        await s.commit()
    app.state.runner.worker.wake()
    return await wait_terminal(app, job_id)


async def fast_forward(app, job_id: str) -> None:
    async with app.state.sessionmaker() as s:
        await s.execute(update(Job).where(Job.id == job_id, Job.status == "queued")
                        .values(not_before=None))
        await s.commit()
    app.state.runner.worker.wake()


async def revision_count(app, pid: str) -> int:
    return len(await app.state.memory.revisions(pid, limit=200))


async def processes(client, pid: str) -> list[dict[str, Any]]:
    return (await client.get(f"/projects/{pid}/processes")).json()


async def enqueue_ingest_for(app, pid: str, process_id: str, n: int) -> str:
    job_id = await mi.enqueue_ingest(app.state.sessionmaker, app.state.settings, project_id=pid,
                                     phase="analysis", scope=f"analysis:{process_id}",
                                     source={"process_id": process_id}, actor="test",
                                     ingest_key=f"analysis:test-{n}")
    assert job_id is not None
    return job_id


async def log_lines(app, job_id: str) -> list[str]:
    async with app.state.sessionmaker() as s:
        return list((await s.execute(select(JobEvent.text).where(JobEvent.job_id == job_id,
                                                                 JobEvent.event == "log"))).scalars())


@pytest.fixture
async def two_workers(memory_app):
    """Two more workers on the app's database, so ingests of one project run on three workers."""
    made = [JobWorker(memory_app.state.sessionmaker, memory_app.state.worker_services,
                      name=f"memory-test-{i}", slots=2, heartbeat_s=1, stale_s=90, max_attempts=2,
                      poll_s=0.05) for i in range(2)]
    for w in made:
        await w.start()
    yield made
    for w in made:
        await w.stop()


@pytest.fixture
async def approved_g1(memory_client, sample_upload):
    pid = await scan_sample(memory_client, sample_upload)
    proc = next(p for p in await processes(memory_client, pid) if p["ir_id"] == DFR)
    r = await memory_client.post(f"/processes/{proc['id']}/analyse")
    assert (await wait_job(memory_client, r.json()["job_id"]))["status"] == "done"
    view = (await memory_client.get(f"/processes/{proc['id']}/analysis")).json()
    for rule in view["rules"]:
        r = await memory_client.patch(f"/rules/{rule['id']}", json={"status": "approved"}, headers=SME)
        assert r.status_code == 200, r.text
    r = await memory_client.post(f"/gates/{proc['id']}/G1", json={"state": "approved", "note": "ok"},
                                 headers=SME)
    assert r.status_code == 200, r.text
    return pid, SimpleNamespace(id=proc["id"], ir_id=proc["ir_id"])


@pytest.fixture
async def two_builds(memory_client, memory_app, sample_upload):
    pid = await scan_sample(memory_client, sample_upload)
    proc = next(p for p in await processes(memory_client, pid) if p["ir_id"] == DFR)
    async with memory_app.state.sessionmaker() as s:
        old = Build(process_id=proc["id"], status="passed", created_at=utcnow() - timedelta(hours=1))
        done = Job(kind="build", status="done", project_id=pid, process_id=proc["id"])
        s.add_all([old, done])
        await s.flush()
        s.add(Build(process_id=proc["id"], status="failed", job_id=done.id))  # a failed build, job done
        await s.commit()
        old_id = old.id
    payload = mi.followup_payload("build", "job-old-build", pid, proc["id"],
                                  {"process_id": proc["id"], "build_id": old_id})
    return pid, proc, payload


async def run_ingest_directly(app, payload: dict[str, Any]) -> dict[str, Any]:
    job_id = await enqueue(app.state.sessionmaker, KIND, payload, project_id=payload["project_id"])
    job = await wait_terminal(app, job_id)
    assert job.status == "done", job.error
    return job.result


# ------------------------------------------------------------------ tests
async def test_scan_queues_and_applies_an_estate_revision(memory_client, memory_app, sample_upload):
    app = memory_app
    pid = await scan_sample(memory_client, sample_upload)
    ingest = await wait_kind(app, pid, KIND, status="done", phase="discover")
    assert ingest.result["phase"] == "discover" and ingest.result["revision"] == 2  # 1 = seed
    assert ingest.result["scope"] == "estate" and ingest.payload["ingest_key"].startswith("discover:")
    assert ingest.payload["dedupe_key"] == f"{pid}|estate"
    seed = await wait_kind(app, pid, KIND, status="done", phase="seed")
    assert seed.result["revision"] == 1
    assert (await app.state.memory.counts(pid))["Process"] == len(await processes(memory_client, pid))


async def test_rerun_of_the_same_ingest_job_creates_no_duplicates(memory_client, memory_app,
                                                                  sample_upload):
    app = memory_app
    pid = await scan_sample(memory_client, sample_upload)
    done = await wait_kind(app, pid, KIND, status="done", phase="discover")
    await idle(app, pid)
    counts = await app.state.memory.counts(pid)
    again = await rerun_job(app, done.id)
    assert again.result["skipped"] is True and again.result["revision"] == done.result["revision"]
    assert await revision_count(app, pid) == 2
    assert await app.state.memory.counts(pid) == counts


async def test_phase_succeeds_and_ingest_retries_while_neo4j_is_down(memory_client, memory_app,
                                                                     sample_upload, neo4j_down):
    app = memory_app
    pid = await scan_sample(memory_client, sample_upload)
    assert (await latest_kind(app, pid, "scan")).status == "done"
    ingest = await latest_kind(app, pid, KIND)
    assert ingest.payload["phase"] == "discover"
    async with asyncio.timeout(60):  # its first attempt failed and it went back to the queue
        while "retry in 15 s: memory unavailable: ServiceUnavailable" not in await log_lines(app, ingest.id):
            await asyncio.sleep(0.1)
    ingest = await get_job(app, ingest.id)
    assert ingest.status == "queued" and ingest.not_before > utcnow() and ingest.attempts == 1
    neo4j_down.restore()
    seed = (await jobs_of_kind(app, pid, KIND))[0]  # the seed ingest failed too: it goes first
    assert seed.payload["phase"] == "seed"
    await fast_forward(app, seed.id)
    assert (await wait_terminal(app, seed.id)).result["revision"] == 1
    await fast_forward(app, ingest.id)
    assert (await wait_terminal(app, ingest.id)).result["revision"] == 2


async def test_ingest_gives_up_with_the_memory_unavailable_text(memory_client, memory_app, neo4j_down):
    """Ruling R23: the final error names the attempts and the exception class."""
    app = memory_app
    app.state.worker_services.settings.memory_ingest_max_attempts = 2
    project_id = (await memory_client.post("/projects", json={"name": "down"})).json()["id"]
    seed = (await jobs_of_kind(app, project_id, KIND))[0]
    async with asyncio.timeout(60):
        while (await get_job(app, seed.id)).attempts < 1 or (await get_job(app, seed.id)).status != "queued":
            await asyncio.sleep(0.1)
    await fast_forward(app, seed.id)
    job = await wait_terminal(app, seed.id)
    assert (job.status, job.error) == ("failed", "memory unavailable after 2 attempts: ServiceUnavailable")


async def test_two_concurrent_ingests_of_one_project_serialise(memory_client, memory_app, two_workers,
                                                              sample_upload):
    """Ruling R8: six analysis ingests of one project on three workers give six consecutive
    revisions, and the project's revisions are exactly 1..N."""
    pid = await scan_sample(memory_client, sample_upload)
    await idle(memory_app, pid)
    procs = (await processes(memory_client, pid))[:6]
    assert len(procs) == 6
    async with memory_app.state.sessionmaker() as s:  # real analysis rows, no analysis job
        for i, p in enumerate(procs):
            run = AnalysisRun(process_id=p["id"], summary={"text": f"{p['name']} summary {i}"})
            s.add(run)
            await s.flush()
            s.add(Rule(process_id=p["id"], analysis_id=run.id, rule_key="R1", status="approved",
                       text=f"{p['name']} rule {i}"))
        await s.commit()
    before = await revision_count(memory_app, pid)
    ids = await asyncio.gather(*(enqueue_ingest_for(memory_app, pid, p["id"], i)
                                 for i, p in enumerate(procs)))
    jobs = [await wait_terminal(memory_app, i) for i in ids]
    assert all(j.status == "done" for j in jobs), [j.error for j in jobs]
    results = [j.result["revision"] for j in jobs]
    assert None not in results
    assert sorted(results) == list(range(before + 1, before + 7))
    ns = sorted(r.n for r in await memory_app.state.memory.revisions(pid, limit=200))
    assert ns == list(range(1, len(ns) + 1)) and len(ns) >= 6


async def test_gate_decision_creates_a_gate_revision(memory_app, approved_g1):
    app = memory_app
    pid, proc = approved_g1
    ingest = await wait_kind(app, pid, KIND, status="done", phase="gate")
    assert ingest.payload["scope"] == f"gate:{proc.ir_id}:G1"
    assert ingest.payload["ingest_key"].startswith("gate:")
    d = await app.state.memory.node_detail(pid, f"gate:{proc.ir_id}:G1")
    assert d.node.props["state"] == "approved" and ingest.result["revision"] >= 2


async def test_older_build_ingest_is_superseded(memory_app, two_builds):
    _pid, _proc, old_build_ingest_payload = two_builds
    assert old_build_ingest_payload["dedupe_key"].endswith(old_build_ingest_payload["source"]["build_id"])
    out = await run_ingest_directly(memory_app, old_build_ingest_payload)
    assert out == {"superseded": True}


async def test_a_newer_build_whose_job_crashed_supersedes_nothing(memory_app, two_builds):
    pid, proc, payload = two_builds
    async with memory_app.state.sessionmaker() as s:
        crashed = Job(kind="build", status="failed", project_id=pid, process_id=proc["id"])
        s.add(crashed)
        await s.flush()
        s.add(Build(process_id=proc["id"], status="failed", job_id=crashed.id,
                    created_at=utcnow() + timedelta(minutes=1)))
        await s.commit()
    newest_done = (await run_ingest_directly(memory_app, {**payload, "ingest_key": "build:x"}))
    assert newest_done == {"superseded": True}  # the done one still supersedes the old build
    async with memory_app.state.sessionmaker() as s:  # without it, the crashed build does not
        await s.execute(update(Job).where(Job.kind == "build", Job.status == "done",
                                          Job.project_id == pid).values(status="failed"))
        await s.commit()
    out = await run_ingest_directly(memory_app, {**payload, "ingest_key": "build:y"})
    assert out.get("superseded") is None and out["phase"] == "build"


async def test_lock_busy_requeues_without_counting_the_attempt(memory_client, memory_app):
    app = memory_app
    project_id = (await memory_client.post("/projects", json={"name": "busy"})).json()["id"]
    seed = (await jobs_of_kind(app, project_id, KIND))[0]
    await wait_terminal(app, seed.id)
    from api.app.jobs.locks import advisory_lock

    async with advisory_lock(app.state.sessionmaker, f"memory:{project_id}"):
        job_id = await enqueue_ingest_for(app, project_id, "nonexistent", 0)
        async with asyncio.timeout(30):
            while "retry in 2 s: memory of this project is being written" not in await log_lines(app, job_id):
                await asyncio.sleep(0.05)
        job = await get_job(app, job_id)
        assert job.status == "queued" and job.attempts == 0


def test_followup_payloads_and_dedupe_keys():
    assert mi.followup_payload("eval", "j", "p", None, {}) is None
    scan = mi.followup_payload("scan", "j1", "p", None, {"project_id": "p", "upload_id": "u"})
    assert scan == {"project_id": "p", "phase": "discover", "scope": "estate", "source_kind": "scan",
                    "source_job_id": "j1", "source": {}, "actor": "system",
                    "ingest_key": "discover:j1", "dedupe_key": "p|estate"}
    an = mi.followup_payload("analyse", "j2", "p", "q", {"process_id": "q", "provider": "fake"})
    assert (an["scope"], an["source"], an["dedupe_key"]) == ("analysis:q", {"process_id": "q"}, "p|analysis:q")
    plan = mi.followup_payload("plan", "j3", "p", "q", {"build_id": "b", "plan_id": "pl"})
    assert plan["phase"] == "deploy" and plan["dedupe_key"] == "p|deploy:q|pl"
    assert mi.followup_payload("build", "j4", None, "q", {"build_id": "b"})["dedupe_key"] == "q|build:q|b"
    assert mi.dedupe_key("p", "estate") == "p|estate"
    assert [mi._backoff(a) for a in (1, 2, 3, 6, 10)] == [15, 30, 60, 480, 600]
    assert registry.handler_for(KIND) is not None and KIND in registry.kinds_with_own_max_attempts()


async def test_no_neo4j_uri_queues_no_ingest(client, app, sample_upload):
    pid = await scan_sample(client, sample_upload)
    assert app.state.memory is None
    assert await jobs_of_kind(app, pid, KIND) == []


# ------------------------------------------------------------------ loaders on real rows
def _design(ir_id: str, starter: str) -> dict[str, Any]:
    from engine.dotnet_gen.models import (
        Decision,
        MappingRow,
        ProjectLayout,
        SolutionLayout,
    )
    from engine.dotnet_gen.models import DesignRecord as EngineDesign

    return EngineDesign(
        process_id=ir_id, spec_hash="s",
        solution=SolutionLayout(name="DealerFunding", projects=[
            ProjectLayout(name="Api", kind="api", files=["Flow.cs"])]),
        mapping=[MappingRow(activity_id=starter, activity_name="Start", canonical_type="rest_receive",
                            pattern="controller", project="Api", file="Flow.cs", class_name="Flow",
                            method="Run", rule_ids=["R1"])],
        decisions=[Decision(title="Data access", choice="Dapper", reason="SQL kept verbatim")],
        narrative="DealerFundingRequest becomes one ASP.NET Core service.",
    ).model_dump(mode="json")


@pytest.fixture
async def phase_rows(memory_client, memory_app, sample_upload):
    """A scanned project (its seed and estate ingested) with a design, a finished build (job
    done), a verify run with one defect, a failed plan and a curation note, as real rows."""
    pid = await scan_sample(memory_client, sample_upload)
    await idle(memory_app, pid)
    proc = next(p for p in await processes(memory_client, pid) if p["ir_id"] == DFR)
    async with memory_app.state.sessionmaker() as s:
        row = await s.get(Process, proc["id"])
        starter = row.ir["starter"]["activity_id"]
        design = DesignRecord(process_id=proc["id"], version=1, spec_hash="s", design_hash="d",
                              record=_design(row.ir_id, starter))
        job = Job(kind="build", status="done", project_id=pid, process_id=proc["id"])
        s.add_all([design, job])
        await s.flush()
        build = Build(process_id=proc["id"], design_id=design.id, job_id=job.id, status="passed",
                      build_hash="h", steps=[{"name": "build", "ok": True, "log": ""}])
        s.add(build)
        await s.flush()
        s.add_all([BuildFile(build_id=build.id, path="src/Api/Flow.cs", origin="code", sha256="x", size=10),
                   BuildFile(build_id=build.id, path="infra/main.tf", origin="infra", sha256="z", size=5),
                   TestResult(build_id=build.id, name="T.Flow.R1", kind="rule", rule_key="R1",
                              outcome="passed", duration_ms=3)])
        scenario = Scenario(process_id=proc["id"], key="happy", name="happy", data={})
        s.add(scenario)
        await s.flush()
        run = ScenarioRun(build_id=build.id, scenario_id=scenario.id, job_id="verify-1",
                          status="differs", parity=50.0)
        plan = Plan(build_id=build.id, status="failed", connected=True,
                    output="infra/main.tf line 3: bad")
        event = MemoryEvent(project_id=pid, kind="curation", action="note", text="checked with ops",
                            context={"reason": "checked"}, target_uid=f"process:{row.ir_id}",
                            actor="sme.vicky")
        s.add_all([run, plan, event])
        await s.flush()
        s.add(FieldDiff(run_id=run.id, field="amount", expected=1, actual=2, classification="defect",
                        finding_key="F1"))
        await s.commit()
        ids = SimpleNamespace(pid=pid, proc=proc["id"], ir=row.ir_id, name=row.name,
                              build=build.id, plan=plan.id, event=event.id)
    return ids


async def _ingest(app, r, phase: str) -> dict[str, Any]:
    scope, source = {
        "design": (f"design:{r.proc}", {"process_id": r.proc}),
        "build": (f"build:{r.proc}", {"process_id": r.proc, "build_id": r.build}),
        "verify": (f"verify:{r.proc}", {"process_id": r.proc, "build_id": r.build}),
        "deploy": (f"deploy:{r.proc}", {"process_id": r.proc, "plan_id": r.plan}),
        "curation": (f"curation:{r.event}", {"event_id": r.event}),
    }[phase]
    job_id = await mi.enqueue_ingest(app.state.sessionmaker, app.state.settings, project_id=r.pid,
                                     phase=phase, scope=scope, source=source, actor="test",
                                     ingest_key=f"{phase}:loader-test")
    job = await wait_terminal(app, job_id)
    assert job.status == "done", job.error
    return job.result


async def _edges_from(app, pid: str, uid: str) -> set[tuple[str, str]]:
    sub = await app.state.memory.subgraph(pid, uid=uid, depth=1)
    return {(e.type, e.dst) for e in sub.edges if e.src == uid}


@pytest.mark.parametrize("phase", ["design", "build", "verify", "deploy", "curation"])
async def test_each_loader_ingests_real_rows(memory_app, phase_rows, phase):
    app, r = memory_app, phase_rows
    method = f"method:dotnet:{r.ir}/Api/Flow.Run"
    if phase in ("verify", "deploy"):  # their errors fail at nodes the build ingest writes
        await _ingest(app, r, "build")
    out = await _ingest(app, r, phase)
    assert out["revision"] is not None and out["added"] > 0, out
    memory = app.state.memory
    if phase == "design":
        assert await memory.node_detail(r.pid, f"chunk:{r.ir}:design:narrative") is not None
    elif phase == "build":
        assert (await memory.node_detail(r.pid, method)).node.label == "Method"
    elif phase == "verify":
        err = f"error:parity:{r.ir}:happy/amount"
        assert await memory.node_detail(r.pid, err) is not None
        assert ("FAILED_AT", method) in await _edges_from(app, r.pid, err)
    elif phase == "deploy":
        err = f"error:deploy:{r.ir}:{r.plan}"
        assert ("FAILED_AT", f"codefile:dotnet:{r.ir}/infra/main.tf") in await _edges_from(app, r.pid, err)
    else:
        node = (await memory.node_detail(r.pid, f"curation:{r.event}")).node
        assert node.name == f"note Process {r.name}"
        assert ("ABOUT", f"process:{r.ir}") in await _edges_from(app, r.pid, node.uid)


async def test_schema_is_ensured_by_the_first_ingest_after_a_failed_start(memory_client, memory_app):
    """Spec section 2, Driver: Neo4j down at start leaves the schema to the first operation."""
    import socket

    with socket.socket() as sock:  # a port nothing listens on
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    down = await mi.open_memory(memory_app.state.settings.model_copy(
        update={"neo4j_uri": f"bolt://127.0.0.1:{port}"}))
    try:
        assert down.schema_ok is False  # logged, not fatal
    finally:
        await down.close()
    memory = memory_app.state.memory  # now as if it had started while Neo4j was down
    async with memory._driver.session(database=memory.database) as session:
        await (await session.run("DROP CONSTRAINT rev_ikey IF EXISTS")).consume()
    memory.schema_ok = False
    pid = (await memory_client.post("/projects", json={"name": "late schema"})).json()["id"]
    seed = await wait_kind(memory_app, pid, KIND, status="done", phase="seed")
    assert seed.result["revision"] == 1 and memory.schema_ok is True
    async with memory._driver.session(database=memory.database) as session:
        names = [r["name"] async for r in await session.run("SHOW CONSTRAINTS YIELD name RETURN name")]
    assert "rev_ikey" in names
