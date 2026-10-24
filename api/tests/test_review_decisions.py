"""Review findings an engineer keeps or rejects, and what the next build does with them.

The review panel writes its findings once when the build runs. The engineer then decides each one:
``keep`` is handed to the next build, which asks the model for a diff that applies it; ``reject``
is remembered so the same point is not put to them twice. Nothing in this path edits code — the
build is the only thing that writes.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from api.app.models import Build
from api.app.services import build as svc

os.environ.setdefault("EMBEDDINGS_LOAD_TIMEOUT", "8")
SYNTHETIC = Path(__file__).resolve().parents[2] / "samples" / "synthetic-gmf"
ENGINEER = {"X-User": "engineer.ravi"}

FINDINGS = [
    {"reviewer": "equivalence", "file": "src/App.Core/Mappings/M.cs", "line": 12,
     "text": "the rounding differs from the TIBCO expression", "triage": "patch"},
    {"reviewer": "security", "file": "src/App.Core/Mappings/M.cs", "line": 40,
     "text": "the SSN is written to the log", "triage": "patch"},
    {"reviewer": "standards", "file": "src/App.Core/Mappings/M.cs", "line": 3,
     "text": "the file has no header comment", "triage": "defer"},
]


async def _wait_job(client, job_id: str, timeout: float = 120.0) -> dict:
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        job = (await client.get(f"/jobs/{job_id}")).json()
        if job["status"] in ("done", "failed"):
            return job
        assert asyncio.get_running_loop().time() < deadline, f"job still {job['status']}"
        await asyncio.sleep(0.2)


async def _build_with_reviews(client, app) -> str:
    """A real process (the build row's process is a foreign key) carrying a finished build whose
    review panel found three things."""
    pid = (await client.post("/projects", json={"name": "reviews"})).json()["id"]
    assert (await client.post(f"/projects/{pid}/upload", json={"path": str(SYNTHETIC)})).status_code == 201
    job = await _wait_job(client, (await client.post(f"/projects/{pid}/scan", json={})).json()["job_id"])
    assert job["status"] == "done", job
    process_id = (await client.get(f"/projects/{pid}/processes")).json()[0]["id"]
    async with app.state.sessionmaker() as s:
        build = Build(process_id=process_id, status="passed", reviews=[dict(f) for f in FINDINGS])
        s.add(build)
        await s.commit()
        return build.id


async def test_keep_and_reject_are_recorded_against_the_finding(client, app):
    build_id = await _build_with_reviews(client, app)

    r = await client.patch(f"/builds/{build_id}/reviews/0", json={"decision": "keep"},
                           headers=ENGINEER)
    assert r.status_code == 200
    rows = r.json()
    assert rows[0]["decision"] == "keep" and rows[0]["decided_by"] == "engineer.ravi"
    assert rows[0]["decided_at"]
    assert "decision" not in rows[1]  # its neighbours are untouched

    r = await client.patch(f"/builds/{build_id}/reviews/2",
                           json={"decision": "reject", "note": "house style does not require it"},
                           headers=ENGINEER)
    rows = r.json()
    assert rows[2]["decision"] == "reject" and rows[2]["note"] == "house style does not require it"

    # the verdicts survive a reload
    got = (await client.get(f"/builds/{build_id}/reviews")).json()
    assert [x.get("decision") for x in got] == ["keep", None, "reject"]


async def test_a_verdict_can_be_taken_back(client, app):
    build_id = await _build_with_reviews(client, app)
    await client.patch(f"/builds/{build_id}/reviews/1", json={"decision": "keep"},
                       headers=ENGINEER)
    rows = (await client.patch(f"/builds/{build_id}/reviews/1", json={"decision": None},
                               headers=ENGINEER)).json()
    assert "decision" not in rows[1] and "decided_by" not in rows[1]


async def test_an_index_outside_the_list_is_404(client, app):
    build_id = await _build_with_reviews(client, app)
    r = await client.patch(f"/builds/{build_id}/reviews/9", json={"decision": "keep"},
                           headers=ENGINEER)
    assert r.status_code == 404


@pytest.mark.parametrize("decision", ["maybe", "", "KEEP"])
async def test_only_keep_or_reject_are_accepted(client, app, decision):
    build_id = await _build_with_reviews(client, app)
    r = await client.patch(f"/builds/{build_id}/reviews/0", json={"decision": decision},
                           headers=ENGINEER)
    assert r.status_code == 422


async def test_only_kept_findings_are_carried_to_the_next_build(client, app):
    """What the next build acts on: the kept ones, never the rejected or the undecided."""
    build_id = await _build_with_reviews(client, app)
    await client.patch(f"/builds/{build_id}/reviews/0", json={"decision": "keep"},
                       headers=ENGINEER)
    await client.patch(f"/builds/{build_id}/reviews/1", json={"decision": "reject"},
                       headers=ENGINEER)

    async with app.state.sessionmaker() as s:
        build = await s.get(Build, build_id)
        carried = svc.kept_findings(build)

    assert [f["text"] for f in carried] == ["the rounding differs from the TIBCO expression"]
    assert svc.kept_findings(None) == []


# ----------------------------------------------- open questions on the design record
async def _designed(client, app) -> tuple[str, dict]:
    """A process with a design record; the record always carries open questions for the synthetic
    estate (findings with no decision at G2 alone produce them)."""
    pid = (await client.post("/projects", json={"name": "questions"})).json()["id"]
    assert (await client.post(f"/projects/{pid}/upload", json={"path": str(SYNTHETIC)})).status_code == 201
    job = await _wait_job(client, (await client.post(f"/projects/{pid}/scan", json={})).json()["job_id"])
    assert job["status"] == "done", job
    procs = (await client.get(f"/projects/{pid}/processes")).json()
    process_id = next(p["id"] for p in procs if p["name"] == "DealerFundingRequest")
    job = await _wait_job(client, (await client.post(f"/processes/{process_id}/analyse")).json()["job_id"])
    assert job["status"] == "done", job
    job = await _wait_job(client, (await client.post(f"/processes/{process_id}/design")).json()["job_id"])
    assert job["status"] == "done", job
    design = (await client.get(f"/processes/{process_id}/design")).json()
    assert design["open_questions"], "the synthetic estate should raise open questions"
    return process_id, design


async def test_an_open_question_can_be_answered_and_reopened(client, app):
    process_id, design = await _designed(client, app)
    before_hash = design["design_hash"]

    r = await client.patch(f"/processes/{process_id}/design/questions/0",
                           json={"decision": "answered", "answer": "Finance confirmed the 30 day window."},
                           headers={"X-User": "architect.lee"})
    assert r.status_code == 200, r.text
    view = r.json()
    q = view["open_questions"][0]
    assert q["decision"] == "answered"
    assert q["answer"] == "Finance confirmed the 30 day window."
    assert q["decided_by"] == "architect.lee" and q["decided_at"]
    # the whole point: settling a question is a note on the design, not a change to it
    assert view["design_hash"] == before_hash

    r = await client.patch(f"/processes/{process_id}/design/questions/0", json={"decision": None},
                           headers={"X-User": "architect.lee"})
    assert r.json()["open_questions"][0].get("decision") is None


async def test_a_question_can_be_rejected_without_an_answer(client, app):
    process_id, _ = await _designed(client, app)
    r = await client.patch(f"/processes/{process_id}/design/questions/0", json={"decision": "rejected"},
                           headers={"X-User": "architect.lee"})
    assert r.status_code == 200
    assert r.json()["open_questions"][0]["decision"] == "rejected"


async def test_answering_needs_an_answer(client, app):
    process_id, _ = await _designed(client, app)
    r = await client.patch(f"/processes/{process_id}/design/questions/0",
                           json={"decision": "answered", "answer": "   "},
                           headers={"X-User": "architect.lee"})
    assert r.status_code == 422


async def test_an_unknown_question_index_is_404(client, app):
    process_id, _ = await _designed(client, app)
    r = await client.patch(f"/processes/{process_id}/design/questions/999", json={"decision": "rejected"},
                           headers={"X-User": "architect.lee"})
    assert r.status_code == 404


# ------------------------------ a test blocked by an un-migrated sub-process is not a failure
def test_blocking_subprocess_names_the_missing_process():
    """A caller whose sub-process was never migrated throws before reaching anything the build
    produced, so its tests never ran. The name comes from the one exception the generator emits,
    never from prose a model wrote."""
    from api.app.services.build import blocking_subprocess

    blocked = ("GmfCredit.Core.Exceptions.SubprocessNotMigratedException : subprocess "
               "creditorigination.DecisionEngine was not migrated: it has no approved design record")
    assert blocking_subprocess(blocked) == "DecisionEngine"
    # a real defect stays a real defect
    assert blocking_subprocess("Assert.Equal() Failure: Expected: 200 Actual: 503") is None
    assert blocking_subprocess(None) is None


def test_the_generated_stub_throws_the_named_exception_and_is_not_an_agent_fill():
    """Asking a model to write a decision engine it has no source for means asking it to invent an
    APR and an approved amount. The stub is generated code that throws instead."""
    from engine.dotnet_gen.generator import gen_subprocess_not_migrated, gen_subprocess_stub

    stub = gen_subprocess_stub("GmfCredit", "DecisionEngine", "creditorigination.DecisionEngine")
    assert "SubprocessNotMigratedException(\"creditorigination.DecisionEngine\")" in stub
    assert "agent fills" not in stub  # never handed to the Code Builder
    assert "NotImplementedException" not in stub

    exc = gen_subprocess_not_migrated("GmfCredit")
    assert "class SubprocessNotMigratedException(string target)" in exc
    assert "no approved design record" in exc


async def test_keep_all_decides_only_the_undecided(client, app):
    """A panel can carry dozens of findings; this is what makes it workable. It must not overwrite
    a verdict somebody already gave by hand."""
    build_id = await _build_with_reviews(client, app)
    await client.patch(f"/builds/{build_id}/reviews/1", json={"decision": "reject"}, headers=ENGINEER)

    rows = (await client.patch(f"/builds/{build_id}/reviews",
                               json={"decision": "keep", "only_undecided": True},
                               headers=ENGINEER)).json()

    assert [r["decision"] for r in rows] == ["keep", "reject", "keep"]
    assert rows[1]["decision"] == "reject"  # the hand-made verdict stands
    assert all(r["decided_by"] == "engineer.ravi" for r in rows)


async def test_reject_all_can_overwrite_when_asked(client, app):
    build_id = await _build_with_reviews(client, app)
    await client.patch(f"/builds/{build_id}/reviews/0", json={"decision": "keep"}, headers=ENGINEER)
    rows = (await client.patch(f"/builds/{build_id}/reviews",
                               json={"decision": "reject", "only_undecided": False},
                               headers=ENGINEER)).json()
    assert [r["decision"] for r in rows] == ["reject", "reject", "reject"]
