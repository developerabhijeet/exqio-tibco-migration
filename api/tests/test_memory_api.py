"""The ``/memory`` read API (Task 12). Needs the test Neo4j (``memory_client``), except the
not-configured tests, which run the plain app with ``NEO4J_URI`` empty."""
from __future__ import annotations

import asyncio

import pytest

from engine.memory.model import MemNode, ScopedDelta
from engine.tests.memory_helpers import apply

DFR = "DealerFundingRequest"
OTHER_UID = "process:isolation-only-in-b"
OTHER_TEXT = "Zanzibar quokka reconciliation flow exclusive to the second project"


async def wait_ingests(app, pid: str, timeout: float = 120.0) -> None:
    from sqlalchemy import func, select

    from api.app.models import Job

    async with asyncio.timeout(timeout):
        while True:
            async with app.state.sessionmaker() as s:
                open_jobs = (await s.execute(select(func.count()).select_from(Job).where(
                    Job.project_id == pid, Job.kind == "memory.ingest",
                    Job.status.in_(("queued", "running"))))).scalar_one()
            if not open_jobs and (await app.state.memory.latest_revision(pid)) is not None:
                return
            await asyncio.sleep(0.2)


@pytest.fixture
async def scanned(memory_client, memory_app, sample_upload) -> str:
    pid = (await memory_client.post("/projects", json={"name": "memory-a"})).json()["id"]
    r = await memory_client.post(f"/projects/{pid}/upload", json={"path": str(sample_upload)})
    assert r.status_code == 201, r.text
    r = await memory_client.post(f"/projects/{pid}/scan", json={})
    job_id = r.json()["job_id"]
    async with asyncio.timeout(120):
        while (await memory_client.get(f"/jobs/{job_id}")).json()["status"] not in ("done", "failed"):
            await asyncio.sleep(0.2)
    await wait_ingests(memory_app, pid)
    return pid


@pytest.fixture
async def second_scanned(memory_client, memory_app) -> str:
    """A second project holding a node (and text) no other project has."""
    pid = (await memory_client.post("/projects", json={"name": "memory-b"})).json()["id"]
    delta = ScopedDelta(scope="estate", nodes=[MemNode(
        label="Process", uid=OTHER_UID, name="Isolation B", text=OTHER_TEXT,
        props={"module": "b"})])
    # POST /projects queues the seed ingest (revision 1); applying beside it, outside the ingest's
    # per-project lock, would race it for revision 1 (Revision.rid is unique)
    await wait_ingests(memory_app, pid)
    await apply(memory_app.state.memory, "b-1", delta, project=pid)
    return pid


async def test_status_reports_revision_and_counts(memory_client, scanned):
    s = (await memory_client.get(f"/memory/{scanned}/status")).json()
    assert s["configured"] and s["available"] and s["revision"] >= 2 and s["counts"]["Process"] > 0
    assert s["pending_ingests"] == 0 and s["embedding_backend"] and s["phase"] and s["at"]


async def test_search_returns_hits_with_citations(memory_client, scanned):
    hits = (await memory_client.get(f"/memory/{scanned}/search",
                                    params={"q": DFR, "labels": ["Process"]})).json()["hits"]
    assert hits and hits[0]["label"] == "Process"
    assert hits[0]["citation"]["artifact_type"] == "process"


async def test_search_without_labels_searches_every_label(memory_client, scanned):
    r = await memory_client.get(f"/memory/{scanned}/search", params={"q": DFR})
    assert r.status_code == 200 and r.json()["hits"]


async def test_graph_without_uid_returns_top_level_nodes(memory_client, scanned):
    g = (await memory_client.get(f"/memory/{scanned}/graph")).json()
    assert {n["label"] for n in g["nodes"]} <= {"Domain", "Application", "Solution"} and g["nodes"]
    assert g["truncated"] is False and g["total"] == len(g["nodes"])
    assert all(n["valid_to"] is None for n in g["nodes"])


async def test_graph_neighbourhood_lineage_and_node_detail(memory_client, scanned):
    hit = (await memory_client.get(f"/memory/{scanned}/search",
                                   params={"q": DFR, "labels": ["Process"]})).json()["hits"][0]
    uid = hit["uid"]
    g = (await memory_client.get(f"/memory/{scanned}/graph", params={"uid": uid, "depth": 2})).json()
    assert uid in {n["uid"] for n in g["nodes"]} and g["edges"]
    lineage = await memory_client.get(f"/memory/{scanned}/graph",
                                      params={"uid": uid, "mode": "lineage"})
    assert lineage.status_code == 200 and uid in {n["uid"] for n in lineage.json()["nodes"]}
    d = (await memory_client.get(f"/memory/{scanned}/node", params={"uid": uid})).json()
    assert d["node"]["uid"] == uid and d["node"]["valid_to"] is None and d["versions"]
    assert set(d) == {"node", "versions", "about", "lineage", "curations"}
    first = d["versions"][-1]["valid_from"]  # newest first: the last is the first version
    at = await memory_client.get(f"/memory/{scanned}/node", params={"uid": uid, "as_of": first})
    assert at.status_code == 200
    if first > 1:  # as_of >= 1 is the API's floor: a node born in revision 1 has no "before"
        before = await memory_client.get(f"/memory/{scanned}/node",
                                         params={"uid": uid, "as_of": first - 1})
        assert before.status_code == 404


async def test_lineage_mode_needs_a_uid_and_unknown_uid_is_404(memory_client, scanned):
    assert (await memory_client.get(f"/memory/{scanned}/graph",
                                    params={"mode": "lineage"})).status_code == 422
    r = await memory_client.get(f"/memory/{scanned}/graph", params={"uid": "process:nope"})
    assert r.status_code == 404 and r.json()["detail"] == "not found"


@pytest.mark.parametrize("path, params", [
    ("graph", {"labels": ["Nope"]}), ("graph", {"depth": 3}), ("graph", {"depth": 0}),
    ("graph", {"limit": 401}), ("graph", {"side": "sideways"}), ("graph", {"mode": "x"}),
    ("graph", {"uid": "u" * 513}), ("graph", {"as_of": 0}),
    ("search", {"q": ""}), ("search", {"q": "x" * 501}), ("search", {"q": "ab cd", "k": 51}),
    ("search", {"q": "ab cd", "k": 0}), ("search", {"q": "ab cd", "labels": ["Nope"]}),
    ("search", {"q": "ab cd", "phase": "bogus"}), ("search", {}),
    ("node", {}), ("node", {"uid": ""}), ("node", {"uid": "u" * 513}),
    ("revisions", {"limit": 201}), ("revisions", {"limit": 0}), ("revisions", {"before": 0}),
    ("revisions/0/diff", {}), ("revisions/x/diff", {}),
])
async def test_request_limits_are_422(memory_client, scanned, path, params):
    assert (await memory_client.get(f"/memory/{scanned}/{path}", params=params)).status_code == 422


async def test_unknown_project_is_404_on_every_route(memory_client):
    for path, params in [("status", {}), ("search", {"q": "ab cd"}), ("graph", {}),
                         ("node", {"uid": "x"}), ("revisions", {}), ("revisions/1/diff", {})]:
        r = await memory_client.get(f"/memory/nope/{path}", params=params)
        assert r.status_code == 404 and r.json()["detail"] == "project not found", path


async def test_projects_never_see_each_others_nodes(memory_client, scanned, second_scanned):
    own = await memory_client.get(f"/memory/{second_scanned}/node", params={"uid": OTHER_UID})
    assert own.status_code == 200 and own.json()["node"]["uid"] == OTHER_UID
    other = await memory_client.get(f"/memory/{scanned}/node", params={"uid": OTHER_UID})
    assert other.status_code == 404
    q = {"q": "Zanzibar quokka reconciliation"}
    mine = (await memory_client.get(f"/memory/{second_scanned}/search", params=q)).json()["hits"]
    assert [h["uid"] for h in mine] == [OTHER_UID]
    theirs = (await memory_client.get(f"/memory/{scanned}/search", params=q)).json()["hits"]
    assert OTHER_UID not in {h["uid"] for h in theirs}
    revs = (await memory_client.get(f"/memory/{scanned}/revisions")).text
    assert OTHER_UID not in revs
    assert (await memory_client.get(f"/memory/{scanned}/graph",
                                    params={"uid": OTHER_UID})).status_code == 404


async def test_revisions_list_and_diff_of_latest_revision(memory_client, scanned):
    n = (await memory_client.get(f"/memory/{scanned}/status")).json()["revision"]
    revs = (await memory_client.get(f"/memory/{scanned}/revisions")).json()["revisions"]
    assert revs[0]["n"] == n
    older = (await memory_client.get(f"/memory/{scanned}/revisions", params={"before": n})).json()
    assert [r["n"] for r in older["revisions"]] == [r["n"] for r in revs if r["n"] < n]
    d = (await memory_client.get(f"/memory/{scanned}/revisions/{n}/diff")).json()
    assert d["revision"]["n"] == n
    assert d["nodes"]["added"] or d["nodes"]["changed"] or d["nodes"]["removed"]
    assert set(d["relationships"]) == {"added", "changed", "removed"}
    missing = await memory_client.get(f"/memory/{scanned}/revisions/{n + 100}/diff")
    assert missing.status_code == 404 and missing.json()["detail"] == "not found"


async def test_status_when_neo4j_is_down(memory_client, scanned, neo4j_down):
    s = (await memory_client.get(f"/memory/{scanned}/status")).json()
    assert s["configured"] is True and s["available"] is False
    for path, params in [("search", {"q": "x y"}), ("graph", {}), ("node", {"uid": "x"}),
                         ("revisions", {}), ("revisions/1/diff", {})]:
        r = await memory_client.get(f"/memory/{scanned}/{path}", params=params)
        assert r.status_code == 503 and r.json()["detail"].startswith("memory unavailable"), path


async def test_not_configured_status_is_200_and_reads_are_503(client, project_id):
    s = (await client.get(f"/memory/{project_id}/status")).json()
    assert s == {"configured": False, "available": False, "revision": None, "at": None,
                 "phase": None, "counts": {}, "pending_ingests": 0, "embedding_backend": None}
    r = await client.get(f"/memory/{project_id}/search", params={"q": "ab cd"})
    assert r.status_code == 503 and r.json()["detail"] == "memory not configured"


async def test_graph_404_only_for_a_missing_node(memory_client, scanned):
    uid = (await memory_client.get(f"/memory/{scanned}/search", params={
        "q": DFR, "labels": ["Process"]})).json()["hits"][0]["uid"]
    r = await memory_client.get(f"/memory/{scanned}/graph", params={
        "uid": uid, "labels": ["Test"], "side": "new", "depth": 1})  # filtered: still 200
    assert r.status_code == 200
    r = await memory_client.get(f"/memory/{scanned}/graph", params={"uid": "process:nope"})
    assert r.status_code == 404
