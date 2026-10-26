"""The build-side agents read the iteration (sub-project 6, Task 4) and the iteration's status follows
its jobs: designing, building, verifying, deploying, closed when G6 is approved for every process."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from api.app.models import AuditLog, Gate, Iteration, Process
from api.app.services import fake_responses
from api.app.services import gates as gate_svc
from api.app.services.iterations import follow_job, open_iteration, status_for_job
from api.tests import test_iterations_api as it_api
from api.tests.test_build_verify import _wait_job

# fixtures shared with the Task 3 tests: the scenario database, the architect persona
scenario_db = it_api.scenario_db
architect = it_api.architect
#: a process with agent-written bodies, so the Code Builder is called (FundingStatusLookup has none)
DFR = "dealerfunding.DealerFundingRequest"
wait_design = it_api.wait_design

OPS = {"X-User": "ops.sam"}


@pytest.fixture
def ops() -> dict[str, str]:
    return OPS


@pytest.fixture
async def built_and_failed(app, client, monkeypatch) -> tuple[str, str]:
    return await it_api.make_built_and_failed(app, client, monkeypatch, DFR)


@pytest.fixture
def recording_provider(monkeypatch):
    """The fake provider, recording every prompt per agent (builder, test_engineer, repair, designer)."""
    names = {"build": "builder", "test": "test_engineer", "repair": "repair", "design": "designer"}

    class Recorder:
        def __init__(self) -> None:
            self.prompts: dict[str, list[str]] = {}

        def prompt_for(self, agent: str) -> str:
            return "\n".join(self.prompts.get(agent, []))

    rec = Recorder()
    original = fake_responses.reply_for

    def reply_for(req, process):
        user = "".join(str(m.get("content", "")) for m in req.messages if m.get("role") == "user")
        rec.prompts.setdefault(names.get(str(req.tags.get("stage")), str(req.tags.get("stage"))), []).append(user)
        return original(req, process)

    monkeypatch.setattr(fake_responses, "reply_for", reply_for)
    return rec


@pytest.fixture
async def iteration2_designed_and_approved(client, built_and_failed, architect) -> str:
    """Iteration 2 opened on a failed build (its compiler error among the inputs), designed, G3
    approved again."""
    _, proc = built_and_failed
    r = await client.post(f"/processes/{proc}/iterations",
                          json={"requirements": "Funding over 50k needs a second approver."}, headers=architect)
    assert r.status_code == 201, r.text
    await wait_design(client, proc)
    r = await client.post(f"/gates/{proc}/G3", json={"state": "approved", "note": "ok"}, headers=architect)
    assert r.status_code == 200, r.text
    return proc


@pytest.fixture
def wait_build(client):
    async def run(proc_id: str) -> dict:
        r = await client.post(f"/processes/{proc_id}/build", json={"runtime": "container_app"})
        assert r.status_code == 202, r.text
        job = await _wait_job(client, r.json()["job_id"], timeout=1800)
        assert job["status"] == "done", job
        return job

    return run


async def iteration_of(client, proc: str) -> dict:
    app = client._transport.app
    async with app.state.sessionmaker() as s:
        pid = (await s.get(Process, proc)).project_id
    rows = [i for i in (await client.get(f"/projects/{pid}/iterations")).json() if proc in i["process_ids"]]
    return max(rows, key=lambda i: i["n"])


async def approve(client, proc: str, gate: str, headers: dict[str, str]) -> dict:
    r = await client.post(f"/gates/{proc}/{gate}", json={"state": "approved", "note": "ok"}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture
async def iteration2_through_g5(app, iteration2_designed_and_approved, monkeypatch) -> str:
    """Iteration 2 with G4 and G5 approved; G6 enabled (its evidence, a plan, is out of scope here)."""
    proc = iteration2_designed_and_approved
    async with app.state.sessionmaker() as s:
        process = await s.get(Process, proc)
        rows = await gate_svc.gate_rows(s, proc)
        for name in ("G4", "G5"):
            row = rows.get(name) or Gate(process_id=proc, gate=name)
            row.state, row.spec_hash, row.decided_by = "approved", process.spec_hash, "test"
            s.add(row)
        await s.commit()
    original = gate_svc.gate_reasons

    async def reasons(session, process):
        return {**await original(session, process), "G6": []}

    monkeypatch.setattr(gate_svc, "gate_reasons", reasons)
    return proc


# ------------------------------------------------------------------ the brief's tests
async def test_builder_prompt_carries_previous_compiler_errors(client, iteration2_designed_and_approved, recording_provider, wait_build):
    await wait_build(iteration2_designed_and_approved)
    assert "error CS0103" in recording_provider.prompt_for("builder")         # from iteration 2's inputs


async def test_status_follows_jobs(client, iteration2_designed_and_approved, wait_build):
    it = await iteration_of(client, iteration2_designed_and_approved)
    assert it["status"] == "designing"
    await wait_build(iteration2_designed_and_approved)
    assert (await iteration_of(client, iteration2_designed_and_approved))["status"] == "building"


async def test_g6_closes_the_iteration(client, iteration2_through_g5, ops):
    await approve(client, iteration2_through_g5, "G6", ops)
    assert (await iteration_of(client, iteration2_through_g5))["status"] == "closed"


# ------------------------------------------------------------------ more
async def test_build_agents_read_only_build_inputs_inside_source(client, iteration2_designed_and_approved,
                                                                 recording_provider, wait_build):
    await wait_build(iteration2_designed_and_approved)
    for agent in ("builder", "test_engineer"):
        prompt = recording_provider.prompt_for(agent)
        start = prompt.index("<source>\nIteration 2")
        block = prompt[start:prompt.index("</source>", start)]
        assert "error CS0103" in block and "A.B.Fails" in block and "second approver" in block, agent


async def test_g6_closes_only_when_every_process_is_approved(client, app, iteration2_through_g5, ops):
    proc = iteration2_through_g5
    async with app.state.sessionmaker() as s:
        process = await s.get(Process, proc)
        other = await s.scalar(select(Process.id).where(Process.project_id == process.project_id,
                                                        Process.id != proc).limit(1))
        it = await open_iteration(s, process.project_id, [proc, other], "reentry", actor="architect.lee")
        await s.commit()
    await approve(client, proc, "G6", ops)
    async with app.state.sessionmaker() as s:
        assert (await s.get(Iteration, it.id)).status == "open"
        s.add(Gate(process_id=other, gate="G6", state="approved", decided_by="test"))
        await s.commit()
    await approve(client, proc, "G6", ops)  # approving again, now that the other one is approved
    async with app.state.sessionmaker() as s:
        row = await s.get(Iteration, it.id)
        assert row.status == "closed" and row.note == "G6 approved for every process"
        lines = (await s.scalars(select(AuditLog).where(AuditLog.action == "iteration closed"))).all()
        assert any(a.actor == "system" and a.actor_kind == "system" and a.detail["iteration_id"] == it.id
                   for a in lines)


def test_status_for_job():
    assert [status_for_job(k) for k in ("design", "build", "verify", "plan", "scan")] == \
        ["designing", "building", "verifying", "deploying", None]


async def test_follow_job_leaves_iteration_1_and_ended_ones(app, iteration2_designed_and_approved):
    proc = iteration2_designed_and_approved
    async with app.state.sessionmaker() as s:
        assert (await follow_job(s, "verify", proc, {"iteration": 1})) is None  # iteration 1 stays open
        row = await follow_job(s, "verify", proc, {})
        assert row is not None and row.status == "verifying"
        row.status = "abandoned"
        await s.flush()
        assert (await follow_job(s, "plan", proc, {"iteration": row.n})) is None
        await s.rollback()


async def test_verify_of_an_older_build_leaves_the_new_iteration_alone(client, app, iteration2_designed_and_approved,
                                                                       wait_build):  # T4 review I1
    proc = iteration2_designed_and_approved
    build = (await wait_build(proc))["result"]
    build_id = build.get("build_id") or build.get("id")
    async with app.state.sessionmaker() as s:
        process = await s.get(Process, proc)
        assert (await s.get(it_api.Build, build_id)).iteration == 2
        it3 = await open_iteration(s, process.project_id, [proc], "reentry", actor="architect.lee")
        await s.commit()
    job_id = await app.state.runner.submit("verify", {"build_id": build_id}, project_id=process.project_id,
                                           process_id=proc)
    await _wait_job(client, job_id)  # it fails (no running service): it did start
    async with app.state.sessionmaker() as s:
        job = await s.get(it_api.Job, job_id)
        assert job.payload["iteration"] == 2 and job.started_at is not None
        assert (await s.get(Iteration, it3.id)).status == "open"
        assert (await follow_job(s, "plan", proc, {"build_id": build_id})) is None  # unstamped: the build's
