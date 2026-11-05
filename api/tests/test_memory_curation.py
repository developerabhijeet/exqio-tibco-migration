"""Curation and admin re-ingest of the memory (Task 13). Needs the test Neo4j (``memory_client``)."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from api.app.models import AuditLog, Job, MemoryEvent
from engine.memory.model import MemNode, MemRel, ScopedDelta
from engine.tests.memory_helpers import apply

SME = {"X-User": "sme.vicky"}


async def wait_terminal(app, job_id: str, timeout: float = 120.0) -> Job:
    async with asyncio.timeout(timeout):
        while True:
            async with app.state.sessionmaker() as s:
                job = await s.get(Job, job_id)
            if job is not None and job.status in ("done", "failed"):
                return job
            await asyncio.sleep(0.2)


@pytest.fixture
async def built(memory_client, memory_app):
    """A project whose memory holds a process, an old activity mapped to a method, a second
    method and a memory item."""
    pid = (await memory_client.post("/projects", json={"name": "curation"})).json()["id"]
    n = SimpleNamespace(pid=pid, process_uid="process:p1", activity_uid="activity:p1/a1",
                        method_uid="method:Calc/Run", other_method_uid="method:Calc/Other",
                        memory_uid="memory:seed-1")
    async with asyncio.timeout(60):  # project creation queues a seed ingest: let it finish first
        while True:
            async with memory_app.state.sessionmaker() as s:
                open_jobs = (await s.execute(select(func.count()).select_from(Job).where(
                    Job.project_id == pid, Job.status.in_(("queued", "running"))))).scalar_one()
            if not open_jobs:
                break
            await asyncio.sleep(0.2)
    delta = ScopedDelta(scope="estate", nodes=[
        MemNode(label="Process", uid=n.process_uid, name="P1", text="Pricing process"),
        MemNode(label="Activity", uid=n.activity_uid, name="Round", text="Round the price",
                props={"process_id": "p1"}),
        MemNode(label="Method", uid=n.method_uid, name="Run", text="Run the calculation"),
        MemNode(label="Method", uid=n.other_method_uid, name="Other", text="Other calculation"),
        MemNode(label="Memory", uid=n.memory_uid, name="seed", text="a seeded memory item",
                props={"kind": "note", "source": "seed"}),
    ], rels=[MemRel(type="MIGRATED_TO", src=n.activity_uid, dst=n.method_uid,
                    props={"status": "proposed"})])
    await apply(memory_app.state.memory, "cur-1", delta, project=pid)
    return n


async def curate(client, built, action, *, target, reason="because", text=None, replacement=None,
                 user="sme.vicky"):
    body = {"action": action, "target_uid": target, "reason": reason}
    if text is not None:
        body["text"] = text
    if replacement is not None:
        body["replacement_uid"] = replacement
    r = await client.post(f"/memory/{built.pid}/curations", headers={"X-User": user}, json=body)
    if r.status_code == 202:
        job = await wait_terminal(client.app, r.json()["job_id"])
        assert job.status == "done", job.error
    return r


async def memory_event_text(app, pid: str) -> str:
    async with app.state.sessionmaker() as s:
        rows = (await s.execute(select(MemoryEvent).where(MemoryEvent.project_id == pid))).scalars()
        return " ".join(f"{r.text} {r.context}" for r in rows)


def mapping_statuses(detail) -> list[str]:
    return sorted(str(x["edge"]["status"]) for x in detail["lineage"]
                  if x["edge"]["type"] == "MIGRATED_TO")


async def test_viewer_cannot_curate(memory_client, built):
    r = await memory_client.post(f"/memory/{built.pid}/curations", headers={"X-User": "viewer.val"},
                                 json={"action": "note", "target_uid": built.activity_uid,
                                       "reason": "r", "text": "t"})
    assert r.status_code == 403 and r.json()["detail"].startswith("read-only")


async def test_viewer_can_read(memory_client, built):
    r = await memory_client.get(f"/memory/{built.pid}/status", headers={"X-User": "viewer.val"})
    assert r.status_code == 200


async def test_note_lands_as_curation_revision_and_agent_memory(memory_client, built):
    r = await memory_client.post(f"/memory/{built.pid}/curations", headers=SME, json={
        "action": "note", "target_uid": built.activity_uid, "reason": "context",
        "text": "Rounds half-even"})
    assert r.status_code == 202
    job = await wait_terminal(memory_client.app, r.json()["job_id"])
    assert job.status == "done" and job.result["phase"] == "curation"
    items = await memory_client.app.state.knowledge.retrieve_memory(built.pid, "rounds half even")
    assert any(i.kind == "note" for i in items)
    async with memory_client.app.state.sessionmaker() as s:
        audit = (await s.execute(select(AuditLog).where(
            AuditLog.project_id == built.pid, AuditLog.action == "memory curated"))).scalar_one()
    assert audit.detail == {"event_id": r.json()["event_id"], "action": "note",
                            "target_uid": built.activity_uid}


async def test_reject_then_lineage_reads_rejected(memory_client, built):
    r = await curate(memory_client, built, "reject_mapping", target=built.activity_uid,
                     reason="wrong pattern")
    assert r.status_code == 202
    d = (await memory_client.get(f"/memory/{built.pid}/node",
                                 params={"uid": built.activity_uid})).json()
    assert mapping_statuses(d) == ["rejected"]
    async with memory_client.app.state.sessionmaker() as s:  # the stored target is the rel uid
        row = await s.get(MemoryEvent, r.json()["event_id"])
    assert row.target_uid == f"MIGRATED_TO:{built.activity_uid}->{built.method_uid}"
    assert row.context["reason"] == "wrong pattern"


async def test_rel_uid_target_is_accepted(memory_client, built):
    rel = f"MIGRATED_TO:{built.activity_uid}->{built.method_uid}"
    assert (await curate(memory_client, built, "reject_mapping", target=rel)).status_code == 202


async def test_correct_mapping_adds_corrected_edge(memory_client, built):
    r = await curate(memory_client, built, "correct_mapping", target=built.activity_uid,
                     replacement=built.other_method_uid, reason="r")
    assert r.status_code == 202
    d = (await memory_client.get(f"/memory/{built.pid}/node",
                                 params={"uid": built.activity_uid})).json()
    assert mapping_statuses(d) == ["corrected", "rejected"]


async def test_obsolete_on_a_process_is_409(memory_client, built):
    r = await curate(memory_client, built, "obsolete", target=built.process_uid, reason="r")
    assert r.status_code == 409 and r.json()["detail"] == "obsolete applies to Memory items only"


async def test_obsolete_memory_item_is_accepted(memory_client, built):
    assert (await curate(memory_client, built, "obsolete", target=built.memory_uid)).status_code == 202


@pytest.mark.parametrize("body,status,detail", [
    ({"action": "note"}, 422, "note needs text"),
    ({"action": "correct_mapping"}, 422, "correct_mapping needs replacement_uid"),
    ({"action": "note", "text": "t", "target_uid": "nope:x"}, 404, "target not found"),
    ({"action": "reject_mapping", "target_uid": "method:Calc/Run"}, 409,
     "target has no mapping to curate"),
    ({"action": "correct_mapping", "replacement_uid": "process:p1"}, 409,
     "replacement must be a Method or Class"),
])
async def test_curation_errors(memory_client, built, body, status, detail):
    body = {"target_uid": built.activity_uid, "reason": "r", **body}
    r = await memory_client.post(f"/memory/{built.pid}/curations", headers=SME, json=body)
    assert r.status_code == status and r.json()["detail"] == detail


@pytest.mark.parametrize("reason", ["", "x" * 2001])
async def test_reason_is_1_to_2000_chars(memory_client, built, reason):
    r = await memory_client.post(f"/memory/{built.pid}/curations", headers=SME, json={
        "action": "obsolete", "target_uid": built.memory_uid, "reason": reason})
    assert r.status_code == 422


async def test_curation_text_is_masked_in_postgres(memory_client, built):
    await curate(memory_client, built, "note", target=built.activity_uid, reason="SSN 123-45-6789",
                 text="x")
    assert "123-45-6789" not in await memory_event_text(memory_client.app, built.pid)


async def test_unknown_project_is_404(memory_client):
    r = await memory_client.post("/memory/nope/curations", headers=SME, json={
        "action": "note", "target_uid": "a", "reason": "r", "text": "t"})
    assert r.status_code == 404


async def test_reingest_is_admin_only(memory_client, built):
    assert (await memory_client.post(f"/memory/{built.pid}/reingest",
                                     headers=SME)).status_code == 403
    r = await memory_client.post(f"/memory/{built.pid}/reingest", headers={"X-User": "ops.sam"})
    assert r.status_code == 202 and isinstance(r.json()["job_ids"], list)


READ_ROUTES = ["status", "search?q=x", "graph", "node?uid=a", "revisions", "revisions/1/diff"]


@pytest.mark.parametrize("route", READ_ROUTES)
async def test_viewer_reads_every_route(memory_client, built, route):
    r = await memory_client.get(f"/memory/{built.pid}/{route}", headers={"X-User": "viewer.val"})
    assert r.status_code in (200, 404), r.text  # never 401/403


async def test_viewer_is_refused_on_both_write_routes(memory_client, built):
    h = {"X-User": "viewer.val"}
    assert (await memory_client.post(f"/memory/{built.pid}/reingest", headers=h)).status_code == 403
    assert (await memory_client.post(f"/memory/{built.pid}/curations", headers=h, json={
        "action": "obsolete", "target_uid": built.memory_uid, "reason": "r"})).status_code == 403
