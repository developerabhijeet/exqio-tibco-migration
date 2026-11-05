"""Iterations (sub-project 6, Task 1): numbering per project, the implicit iteration 1, superseding
an open iteration, stamping on design records and builds, and the role dependency's messages."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import Depends, FastAPI
from sqlalchemy import select

from api.app.deps import require_roles
from api.app.models import Application, AuditLog, Iteration, Job, Process, Project, Upload
from api.app.services import build as build_svc
from api.app.services.iterations import (
    IterationBusy,
    abandon,
    close,
    current_iteration,
    ensure_initial,
    job_iteration,
    open_iteration,
    set_status,
)
from api.tests import test_build_verify as build_tests
from api.tests import test_connectors_framework as connector_tests
from api.tests.test_auth import as_roles
from api.tests.test_build_verify import ARCHITECT, _designed, _wait_job

# fixtures shared with those modules: the scenario database (autouse), an oidc client, a viewer
scenario_db = build_tests.scenario_db
client_oidc = connector_tests.client_oidc
viewer_token = connector_tests.viewer_token


# ------------------------------------------------------------------ fixtures
@pytest.fixture
async def session(app_no_worker) -> AsyncIterator:
    """A session on the app's database; no in-process worker, so a queued job stays queued."""
    async with app_no_worker.state.sessionmaker() as s:
        yield s


async def _project(s, name: str) -> Project:
    project = Project(name=name)
    s.add(project)
    await s.flush()
    return project


@pytest.fixture
async def project(session) -> Project:
    project = await _project(session, "iterations")
    await session.commit()
    return project


@pytest.fixture
async def other_project(session) -> Project:
    project = await _project(session, "other")
    await session.commit()
    return project


@pytest.fixture
async def proc(session, project) -> Process:
    upload = Upload(project_id=project.id, source_kind="path", source_name="x", root_dir="/tmp")
    session.add(upload)
    await session.flush()
    application = Application(project_id=project.id, upload_id=upload.id, app_key="a", name="a",
                              dialect="bw5", ir={})
    session.add(application)
    await session.flush()
    row = Process(project_id=project.id, application_id=application.id, upload_id=upload.id,
                  ir_id="dealerfunding.DealerFundingRequest", name="DealerFundingRequest", dialect="bw5",
                  source_path="p.process", ir={}, spec_hash="h")
    session.add(row)
    await session.commit()
    return row


@pytest.fixture
async def proc_b(session, proc) -> Process:
    row = Process(project_id=proc.project_id, application_id=proc.application_id, upload_id=proc.upload_id,
                  ir_id="dealerfunding.FundingStatusLookup", name="FundingStatusLookup", dialect="bw5",
                  source_path="b.process", ir={}, spec_hash="h")
    session.add(row)
    await session.commit()
    return row


@pytest.fixture
async def queued_build_job(session, proc) -> Job:
    job = Job(kind="build", status="queued", project_id=proc.project_id, process_id=proc.id,
              payload={"process_id": proc.id})
    session.add(job)
    await session.commit()
    return job


async def finish_job(session, job: Job) -> None:
    job.status = "done"
    await session.flush()


@pytest.fixture
async def approved_g3(client, monkeypatch) -> str:
    """A process of the synthetic estate with a design record (iteration 1) and G3 approved."""
    monkeypatch.setattr(build_svc, "find_dotnet", lambda: None)
    _, proc_id, design = await _designed(client)
    assert design["iteration"] == 1
    return proc_id


@pytest.fixture
async def open_iteration_2(app, approved_g3) -> Iteration:
    async with app.state.sessionmaker() as s:
        proc_row = await s.get(Process, approved_g3)
        it = await open_iteration(s, proc_row.project_id, [proc_row.id], "reentry", actor="architect.lee")
        await s.commit()
    return it


@pytest.fixture
def wait_build(client):
    async def run(proc_id: str) -> dict:
        r = await client.post(f"/processes/{proc_id}/build", json={"runtime": "container_app"})
        assert r.status_code == 202, r.text
        job = await _wait_job(client, r.json()["job_id"], timeout=1800)
        assert job["status"] == "done", job
        return (await client.get(f"/builds/{r.json()['build_id']}")).json()

    return run


@pytest.fixture
def sme_token() -> dict[str, str]:
    return as_roles("sme")


# ------------------------------------------------------------------ numbering
async def test_first_read_creates_initial_iteration(session, project):
    it = await ensure_initial(session, project.id)
    assert (it.n, it.kind, it.status) == (1, "initial", "open")
    assert (await ensure_initial(session, project.id)).id == it.id
    created_at = await session.scalar(select(Project.created_at).where(Project.id == project.id))
    assert it.opened_by == "system" and it.opened_at == created_at and it.process_ids == []


async def test_numbers_are_per_project_and_consecutive(session, project, other_project, proc):
    a = await open_iteration(session, project.id, [proc.id], "reentry", actor="architect.lee")
    b = await open_iteration(session, other_project.id, [], "reentry", actor="architect.lee")
    assert (a.n, b.n) == (2, 2)
    c = await open_iteration(session, project.id, [], "edits", actor="eng.raj", resume_phase="build")
    assert c.n == 3
    await session.commit()
    rows = (await session.execute(select(Iteration.project_id, Iteration.n).order_by(Iteration.n))).all()
    assert sorted(rows) == sorted([(project.id, 1), (project.id, 2), (project.id, 3),
                                   (other_project.id, 1), (other_project.id, 2)])


async def test_second_open_while_build_queued_is_busy_then_supersedes(session, project, proc, queued_build_job):
    first = await open_iteration(session, project.id, [proc.id], "reentry", actor="architect.lee")
    with pytest.raises(IterationBusy, match="queued or running"):
        await open_iteration(session, project.id, [proc.id], "reentry", actor="architect.lee")
    await finish_job(session, queued_build_job)
    second = await open_iteration(session, project.id, [proc.id], "reentry", actor="architect.lee")
    await session.commit()
    await session.refresh(first)
    assert first.status == "abandoned" and first.note == f"superseded by iteration {second.n}"
    assert first.closed_at is not None
    actions = (await session.execute(select(AuditLog.action).where(AuditLog.project_id == project.id)
                                     .order_by(AuditLog.id))).scalars().all()
    assert actions.count("iteration opened") == 2 and actions.count("iteration abandoned") == 1


async def test_busy_message_names_the_process(session, project, proc, queued_build_job):
    await open_iteration(session, project.id, [proc.id], "reentry", actor="architect.lee")
    with pytest.raises(IterationBusy) as err:
        await open_iteration(session, project.id, [proc.id], "reentry", actor="architect.lee")
    assert str(err.value) == "a design or build of DealerFundingRequest is queued or running"


async def test_current_iteration_follows_open_then_closed(session, project, proc):
    assert (await current_iteration(session, proc.id)).n == 1
    it = await open_iteration(session, project.id, [proc.id], "reentry", actor="architect.lee")
    assert (await current_iteration(session, proc.id)).id == it.id
    await set_status(session, it.id, "building")
    assert (await current_iteration(session, proc.id)).id == it.id
    await close(session, it.id, "architect.lee", "G6 approved")
    assert (await current_iteration(session, proc.id)).id == it.id  # closed: still the latest
    third = await open_iteration(session, project.id, [proc.id], "reentry", actor="architect.lee")
    await abandon(session, third.id, "architect.lee", "not needed")
    assert (await current_iteration(session, proc.id)).id == it.id  # abandoned ones never count
    with pytest.raises(ValueError):
        await set_status(session, it.id, "bogus")
    with pytest.raises(ValueError, match="already closed"):
        await set_status(session, it.id, "open")  # an ended iteration is never reopened
    assert (await session.get(Iteration, it.id)).status == "closed"


async def test_set_status_to_ended_writes_the_audit_line(session, project, proc):
    it = await open_iteration(session, project.id, [proc.id], "reentry", actor="architect.lee")
    await set_status(session, it.id, "closed")
    assert it.closed_at is not None
    rows = (await session.execute(select(AuditLog).where(AuditLog.action == "iteration closed"))).scalars().all()
    assert [(r.actor, r.detail["iteration_id"]) for r in rows] == [("system", it.id)]


async def test_reentering_one_process_moves_only_that_one(session, project, proc, proc_b):
    second = await open_iteration(session, project.id, [proc.id, proc_b.id], "reentry", actor="architect.lee")
    third = await open_iteration(session, project.id, [proc.id], "reentry", actor="architect.lee")
    await session.commit()
    await session.refresh(second)
    assert (second.status, second.process_ids) == ("open", [proc_b.id])
    assert (await current_iteration(session, proc_b.id)).n == second.n == 2
    assert (await current_iteration(session, proc.id)).n == third.n == 3
    assert await job_iteration(session, {}, proc_b.id) == 2  # B keeps being stamped 2
    moved = (await session.execute(select(AuditLog).where(AuditLog.action == "iteration processes moved")))
    assert [r.detail["moved"] for r in moved.scalars()] == [[proc.id]]
    # the last process leaving iteration 2 abandons it
    fourth = await open_iteration(session, project.id, [proc_b.id], "reentry", actor="architect.lee")
    await session.refresh(second)
    assert (second.status, second.note) == ("abandoned", f"superseded by iteration {fourth.n}")


async def test_busy_check_covers_every_moved_process(session, project, proc, proc_b):
    await open_iteration(session, project.id, [proc.id, proc_b.id], "reentry", actor="architect.lee")
    session.add(Job(kind="design", status="running", project_id=project.id, process_id=proc.id, payload={}))
    await session.flush()
    with pytest.raises(IterationBusy, match="DealerFundingRequest"):
        await open_iteration(session, project.id, [proc_b.id, proc.id], "reentry", actor="architect.lee")


async def test_concurrent_opens_take_distinct_numbers(app_no_worker, project, proc):
    async def open_one() -> int:
        async with app_no_worker.state.sessionmaker() as s:
            it = await open_iteration(s, project.id, [], "reentry", actor="architect.lee")
            await s.commit()
            return it.n

    assert sorted(await asyncio.gather(open_one(), open_one(), open_one())) == [2, 3, 4]


async def test_a_retried_job_keeps_its_stamped_iteration(session, project, proc):
    it = await open_iteration(session, project.id, [proc.id], "reentry", actor="architect.lee")
    payload = {"process_id": proc.id, "iteration": it.n}  # stamped on submit; a retry reuses the row
    await close(session, it.id, "architect.lee")
    newer = await open_iteration(session, project.id, [proc.id], "reentry", actor="architect.lee")
    assert newer.n == 3 and await job_iteration(session, payload, proc.id) == 2
    assert await job_iteration(session, {"process_id": proc.id}, proc.id) == 3


# ------------------------------------------------------------------ stamping
async def test_design_and_build_rows_carry_the_iteration(client, approved_g3, open_iteration_2, wait_build):
    r = await client.post(f"/processes/{approved_g3}/design")
    assert r.status_code == 202, r.text
    job = await _wait_job(client, r.json()["job_id"])
    assert job["status"] == "done" and job["payload"]["iteration"] == 2
    design = (await client.get(f"/processes/{approved_g3}/design")).json()
    assert (design["version"], design["iteration"]) == (2, 2)
    gates = {g["gate"]: g for g in (await client.get(f"/processes/{approved_g3}/gates")).json()}
    if gates["G3"]["state"] != "approved":
        r = await client.post(f"/gates/{approved_g3}/G3", json={"state": "approved", "note": "ok"},
                              headers=ARCHITECT)
        assert r.status_code == 200, r.text
    build = await wait_build(approved_g3)
    assert build["iteration"] == open_iteration_2.n == 2


# ------------------------------------------------------------------ roles
async def test_require_roles_messages(client_oidc, viewer_token, sme_token):
    probe = FastAPI()

    @probe.post("/_probe/architect-only", dependencies=[Depends(require_roles("architect", "admin"))])
    async def architect_only() -> dict:
        return {}

    probe.state = client_oidc.app.state
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=probe), base_url="http://test") as c:
        assert (await c.post("/_probe/architect-only", headers=viewer_token)).json()["detail"] == "viewer is read only"
        assert (await c.post("/_probe/architect-only", headers=sme_token)).json()["detail"] == \
            "this needs one of: architect, admin"
        assert (await c.post("/_probe/architect-only", headers=as_roles("architect"))).status_code == 200

    @probe.post("/_probe/engineer-only", dependencies=[Depends(require_roles("engineer"))])
    async def engineer_only() -> dict:
        return {}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=probe), base_url="http://test") as c:
        assert (await c.post("/_probe/engineer-only", headers=as_roles("admin"))).status_code == 200  # always
        assert (await c.post("/_probe/engineer-only", headers=as_roles("architect"))).json()["detail"] == \
            "this needs one of: engineer"
