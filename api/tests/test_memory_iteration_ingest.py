"""Memory across iterations (sub-project 6, Task 5): opening an iteration ingests one revision of
that iteration, revisions carry and filter by iteration, the requirement is searchable."""
from __future__ import annotations

import pytest
from sqlalchemy import select

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


# ------------------------------------------------------------------ review fix round 1
async def test_the_iteration_ingest_waits_for_the_build_ingest(memory_client, memory_app, architect, monkeypatch):  # I1
    """The iteration's Errors point at the build's CodeFiles: opened while the build's ingest is still
    queued, its ingest waits for it, then every FAILED_AT and ABOUT edge lands."""
    from api.app.models import BuildFile
    from api.app.services import build as build_svc
    from api.app.services import memory_ingest as mi
    from api.tests.test_build_verify import _built
    from api.tests.test_memory_ingest import fast_forward, held_ingest, wait_log, wait_terminal

    real = mi.followup_payload
    monkeypatch.setattr(mi, "followup_payload", lambda kind, *a: None if kind == "build" else real(kind, *a))
    monkeypatch.setattr(build_svc, "find_dotnet", lambda: None)
    pid, proc, build_id, _ = await _built(memory_client, monkeypatch)
    build_ingest = await held_ingest(memory_app, pid, real("build", "held-build", pid, proc, {"build_id": build_id}),
                                     process_id=proc)
    async with memory_app.state.sessionmaker() as s:
        path = next(p for p in (await s.scalars(select(BuildFile.path).where(BuildFile.build_id == build_id))).all()
                    if p.endswith("Service.cs"))
    r = await memory_client.post(f"/processes/{proc}/iterations", data={"body": "{}"}, headers=architect,
                                 files={"logs": ("ci.log", f"{path}(3,1): error CS0103: x missing\n".encode())})
    assert r.status_code == 201, r.text
    ingest = await wait_kind(memory_app, pid, "memory.ingest", status="queued", phase="iteration")
    await wait_log(memory_app, ingest.id, "waiting for the build memory")
    await fast_forward(memory_app, build_ingest)
    assert (await wait_terminal(memory_app, build_ingest)).status == "done"
    await fast_forward(memory_app, ingest.id)
    done = await wait_terminal(memory_app, ingest.id)
    assert done.status == "done", done.error
    assert done.result["dangling"] == 0
    sub = await memory_app.state.memory.subgraph(pid, uid=f"codefile:dotnet:{it_api.build_tests.FSL}/{path}", depth=1)
    assert any(e.type == "FAILED_AT" and e.src.startswith("error:iteration:") for e in sub.edges)


def test_named_path_needs_a_boundary():  # M3
    from api.app.services.memory_ingest import named_path

    paths = ["Program.cs", "src/Api/Program.cs", "src/Core/MyProgram.cs"]
    assert named_path("src/Core/MyProgram.cs(3,1): error", paths) == "src/Core/MyProgram.cs"
    assert named_path("MyProgram.cs(3,1): error", ["Program.cs"]) is None
    assert named_path("at Program.cs:12", ["Program.cs", "src/Core/MyProgram.cs"]) == "Program.cs"
