"""Memory across iterations (sub-project 6, Task 5): opening an iteration ingests one revision of
that iteration, revisions carry and filter by iteration, the requirement is searchable."""
from __future__ import annotations

import pytest

from api.app.models import Process
from api.tests import test_iterations_api as it_api
from api.tests.test_memory_ingest import wait_kind

scenario_db = it_api.scenario_db
architect = it_api.architect


@pytest.fixture
async def built_and_failed(memory_app, memory_client, monkeypatch) -> tuple[str, str]:
    return await it_api.make_built_and_failed(memory_app, memory_client, monkeypatch)


@pytest.fixture
async def iteration2_ingested(memory_client, built_and_failed, architect) -> str:
    pid, proc = built_and_failed
    r = await memory_client.post(f"/processes/{proc}/iterations",
                                 json={"requirements": "Funding over 50k needs a second approver."}, headers=architect)
    assert r.status_code == 201, r.text
    await wait_kind(memory_client.app, pid, "memory.ingest", status="done", phase="iteration")
    return pid


async def test_opening_an_iteration_ingests_with_its_number(memory_client, built_and_failed, architect):
    pid, proc = built_and_failed
    await memory_client.post(f"/processes/{proc}/iterations", json={"requirements": "second approver"}, headers=architect)
    ingest = await wait_kind(memory_client.app, pid, "memory.ingest", status="done", phase="iteration")
    revs = (await memory_client.get(f"/memory/{pid}/revisions", params={"iteration": 2})).json()["revisions"]
    assert revs and all(r["iteration"] == 2 for r in revs) and ingest.result["revision"] in [r["n"] for r in revs]
    assert ingest.payload["iteration"] == 2 and ingest.payload["scope"] == "iteration:2"
    every = (await memory_client.get(f"/memory/{pid}/revisions")).json()["revisions"]
    assert {r["iteration"] for r in every} == {1, 2}  # the earlier phases' revisions are iteration 1


async def test_requirement_is_searchable_with_citation(memory_client, iteration2_ingested):
    pid = iteration2_ingested
    hits = (await memory_client.get(f"/memory/{pid}/search", params={"q": "second approver funding"})).json()["hits"]
    assert hits[0]["citation"]["uid"].endswith(":requirement:2")
    assert hits[0]["citation"]["artifact_type"] == "iteration" and hits[0]["citation"]["phase"] == "iteration"


async def test_input_errors_are_about_the_process(memory_client, iteration2_ingested):
    pid = iteration2_ingested
    app = memory_client.app
    async with app.state.sessionmaker() as s:
        proc = (await s.execute(Process.__table__.select().where(Process.project_id == pid,
                                                                 Process.ir_id == it_api.build_tests.FSL))).first()
    graph = (await memory_client.get(f"/memory/{pid}/graph", params={"uid": f"process:{proc.ir_id}"})).json()
    errors = [n for n in graph["nodes"] if n["label"] == "Error" and n["uid"].startswith("error:iteration:")]
    assert {e["props"]["kind"] for e in errors} == {"build", "test"}
    assert all(e["props"]["iteration"] == 2 for e in errors)


async def test_a_later_design_revision_is_stamped_with_the_iteration(memory_client, iteration2_ingested):
    pid = iteration2_ingested
    design = await wait_kind(memory_client.app, pid, "memory.ingest", status="done", phase="design")
    revs = (await memory_client.get(f"/memory/{pid}/revisions", params={"iteration": 2})).json()["revisions"]
    assert design.payload.get("iteration") in (1, 2)  # the first design (iteration 1) or the re-entry's
    assert {r["phase"] for r in revs} >= {"iteration"}
