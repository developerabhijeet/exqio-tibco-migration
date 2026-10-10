"""Domains, the analysis job, rule and finding decisions, gates G1 to G6, audit, memory, Ask (Task 18).

Real Postgres (schema ``api`` reset per test; schema ``knowledge`` shared, rows scoped by the
project id) and ``PROVIDER=fake`` from the conftest settings unless a test switches it off.
"""
from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path

from sqlalchemy import func, select

from api.app.models import AnalysisRun, AuditLog, Gate, JobEvent, ModelCall, Process, Rule
from api.app.services import analysis as analysis_svc
from engine.agents.provider import ProviderError

# Do not spend 30 s on a blocked embedding-model download; fall back to the hash backend sooner.
os.environ.setdefault("EMBEDDINGS_LOAD_TIMEOUT", "8")

REPO_ROOT = Path(__file__).resolve().parents[2]
SYNTHETIC = REPO_ROOT / "samples" / "synthetic-gmf"
DFR = "dealerfunding.DealerFundingRequest"
CAI = "creditorigination.CreditApplicationIntake"
SME = {"X-User": "sme.vicky"}
STAGES = ["static rules", "analyst", "verifier", "coverage", "risk reviewer", "challenger", "persist"]


# ------------------------------------------------------------------ helpers
async def _wait_job(client, job_id: str, timeout: float = 120.0) -> dict:
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


async def _analyse(client, process_id: str) -> dict:
    r = await client.post(f"/processes/{process_id}/analyse")
    assert r.status_code == 202, r.text
    job = await _wait_job(client, r.json()["job_id"])
    assert job["status"] == "done", job
    r = await client.get(f"/processes/{process_id}/analysis")
    assert r.status_code == 200, r.text
    return r.json()


async def _decide_rules(client, analysis: dict, skip: tuple[str, ...] = ()) -> dict:
    out = analysis
    for rule in analysis["rules"]:
        if rule["rule_key"] in skip:
            continue
        r = await client.patch(f"/rules/{rule['id']}", json={"status": "approved"}, headers=SME)
        assert r.status_code == 200, r.text
        out = r.json()
    return out


async def _decide_findings(client, analysis: dict) -> dict:
    out = analysis
    for f in analysis["findings"]:
        r = await client.patch(f"/findings/{f['id']}", json={"decision": "fix", "note": "fix in .NET"},
                               headers=SME)
        assert r.status_code == 200, r.text
        out = r.json()
    return out


def _gate(view: dict, name: str) -> dict:
    return next(g for g in view["gates"] if g["gate"] == name)


async def _audit_actions(client, process_id: str) -> list[str]:
    r = await client.get(f"/processes/{process_id}/audit")
    assert r.status_code == 200, r.text
    return [a["action"] for a in r.json()]


# ------------------------------------------------------------------ the six named tests
async def test_analyse_without_provider_uses_static_only_and_says_so(client, app, settings):
    settings.provider = "auto"
    settings.claude_code_oauth_token = ""
    settings.azure_openai_endpoint = ""
    settings.azure_openai_api_key = ""
    _, procs = await _scanned(client)
    proc_id = procs[DFR]["id"]
    r = await client.post(f"/processes/{proc_id}/analyse")
    assert r.status_code == 202
    job_id = r.json()["job_id"]
    job = await _wait_job(client, job_id)
    assert job["status"] == "done", job
    assert job["result"]["ai_available"] is False

    async with app.state.sessionmaker() as s:
        events = (await s.execute(select(JobEvent).where(JobEvent.job_id == job_id)
                                  .order_by(JobEvent.seq))).scalars().all()
        assert await s.scalar(select(ModelCall.id).where(ModelCall.process_id == proc_id)) is None
    assert [e.text for e in events if e.event == "stage"] == STAGES
    logs = [e for e in events if e.event == "log"]
    assert {e.tag for e in logs} == {"code", "ai"}
    ai_lines = [e.text for e in logs if e.tag == "ai"]
    assert len(ai_lines) == 3 and all("code-only analysis: no model configured" in t for t in ai_lines)

    view = (await client.get(f"/processes/{proc_id}/analysis")).json()
    assert view["analysed"] is True
    assert view["ai_available"] is False
    assert view["notice"] == "code-only analysis: no model configured"
    assert "no model configured" in view["ai_reason"]
    assert view["provider"] == "none"
    assert view["rules"] == []
    keys = [f["finding_key"] for f in view["findings"]]
    assert "S1-DealerFundingRequest-1" in keys and "S2-DealerFundingRequest-1" in keys
    assert all(f["source"] == "static" and f["business_impact"] is None for f in view["findings"])
    assert view["verification"] == {"verified": 0, "total": 0, "failures": [], "passed": True}
    cov = view["coverage"]
    assert cov["total"] > 0 and cov["covered"] == 0 and cov["complete"] is False
    assert view["summary"]  # a code-generated summary, never empty
    g1 = _gate(view, "G1")
    assert g1["enabled"] is False and "coverage" in g1["reason"]


async def test_agent_header_forbidden_on_gate(client, app):
    _, procs = await _scanned(client)
    proc_id = procs[DFR]["id"]
    for gate in ("G1", "G3"):
        r = await client.post(f"/gates/{proc_id}/{gate}", json={"state": "approved", "note": "x"},
                              headers={"X-Agent": "analyst", "X-User": "sme.vicky"})
        assert r.status_code == 403, r.text
    r = await client.post(f"/gates/{proc_id}/G1", json={"state": "approved", "note": "x"})
    assert r.status_code == 401
    r = await client.patch("/rules/nope", json={"status": "approved"}, headers={"X-Agent": "a"})
    assert r.status_code == 403
    async with app.state.sessionmaker() as s:
        assert (await s.execute(select(Gate))).scalars().all() == []
        assert await s.scalar(select(AuditLog.id).where(AuditLog.gate.is_not(None))) is None


async def test_g1_requires_all_rules_decided_and_checks(client, app):
    _, procs = await _scanned(client)
    proc_id = procs[DFR]["id"]
    view = await _analyse(client, proc_id)
    assert view["ai_available"] is True and view["notice"] is None
    assert view["summary"] and view["rules"]
    assert view["verification"]["failures"] == [] and view["verification"]["passed"] is True
    assert view["verification"]["verified"] == view["verification"]["total"] > 0
    assert view["coverage"]["complete"] is True
    assert all(r["challenge"] and r["challenge"]["verdict"] in ("agree", "disagree") for r in view["rules"])
    assert [r["rule_key"] for r in view["rules"]] == [f"R{i}" for i in range(1, len(view["rules"]) + 1)]
    assert any(f["source"] == "ai" and f["finding_key"] == "F1" for f in view["findings"])
    assert all(f["business_impact"] for f in view["findings"] if f["source"] == "static")
    async with app.state.sessionmaker() as s:
        calls = (await s.execute(select(ModelCall).where(ModelCall.process_id == proc_id))).scalars().all()
    assert {c.stage for c in calls} == {"analyst", "risk", "challenger"}
    assert all(c.job_id and c.ok and c.provider == "fake" for c in calls)

    # nothing decided
    r = await client.post(f"/gates/{proc_id}/G1", json={"state": "approved", "note": ""}, headers=SME)
    assert r.status_code == 409 and "undecided" in r.json()["detail"]
    assert _gate(view, "G1")["enabled"] is False

    # every rule but R1 decided
    view = await _decide_rules(client, view, skip=("R1",))
    r = await client.post(f"/gates/{proc_id}/G1", json={"state": "approved", "note": ""}, headers=SME)
    assert r.status_code == 409 and "R1" in r.json()["detail"]

    # R1's citation stops matching the IR: an edit re-verifies and the rule turns unverified
    r1 = next(r for r in view["rules"] if r["rule_key"] == "R1")
    good = r1["citations"]
    async with app.state.sessionmaker() as s:
        row = await s.get(Rule, r1["id"])
        row.citations = [{**good[0], "text": "this text is nowhere in the process"}]
        await s.commit()
    r = await client.patch(f"/rules/{r1['id']}", json={"text": r1["text"] + " (checked)"}, headers=SME)
    assert r.status_code == 200, r.text
    view = r.json()
    r1 = next(r for r in view["rules"] if r["rule_key"] == "R1")
    assert r1["status"] == "unverified" and r1["citations"][0]["verified"] is False
    assert view["verification"]["passed"] is False
    r = await client.post(f"/gates/{proc_id}/G1", json={"state": "approved", "note": ""}, headers=SME)
    assert r.status_code == 409 and "citations verified" in r.json()["detail"]
    # an unverified rule cannot be approved
    r = await client.patch(f"/rules/{r1['id']}", json={"status": "approved"}, headers=SME)
    assert r.status_code == 409

    # rejecting it clears the citation check, but what it covered is now uncovered
    r = await client.patch(f"/rules/{r1['id']}", json={"status": "rejected"}, headers=SME)
    assert r.status_code == 200
    r = await client.post(f"/gates/{proc_id}/G1", json={"state": "approved", "note": ""}, headers=SME)
    assert r.status_code == 409 and "coverage" in r.json()["detail"]
    assert "citations" not in r.json()["detail"]

    # the citation is restored and the SME edits the rule again: verified, decided, covered
    async with app.state.sessionmaker() as s:
        row = await s.get(Rule, r1["id"])
        row.citations = good and [{k: c[k] for k in ("activity_id", "kind", "text")} for c in good]
        await s.commit()
    r = await client.patch(f"/rules/{r1['id']}", json={"text": r1["text"] + " Confirmed."}, headers=SME)
    view = r.json()
    assert next(r for r in view["rules"] if r["rule_key"] == "R1")["status"] == "edited"
    assert _gate(view, "G1")["enabled"] is True and _gate(view, "G1")["reason"] is None
    r = await client.post(f"/gates/{proc_id}/G1", json={"state": "approved", "note": "rules agreed"},
                          headers=SME)
    assert r.status_code == 200, r.text
    g1 = r.json()
    assert g1["state"] == "approved" and g1["decided_by"] == "sme.vicky" and g1["note"] == "rules agreed"
    assert g1["spec_hash"] == (await client.get(f"/processes/{proc_id}/analysis")).json()["spec_hash"]
    assert "gate approved" in await _audit_actions(client, proc_id)


async def test_rule_edit_resets_gates_and_hash(client, app):
    pid, procs = await _scanned(client)
    proc_id = procs[DFR]["id"]
    view = await _analyse(client, proc_id)
    hash_analysed = view["spec_hash"]
    view = await _decide_rules(client, view)
    assert view["spec_hash"] != hash_analysed  # approved rules are part of the spec hash
    assert (await client.post(f"/gates/{proc_id}/G1", json={"state": "approved", "note": ""},
                              headers=SME)).status_code == 200
    view = await _decide_findings(client, (await client.get(f"/processes/{proc_id}/analysis")).json())
    g1 = _gate(view, "G1")
    assert g1["state"] == "approved" and g1["spec_hash"] == view["spec_hash"]  # rebound, G1 is rules only
    assert (await client.post(f"/gates/{proc_id}/G2", json={"state": "approved", "note": ""},
                              headers=SME)).status_code == 200
    before = (await client.get(f"/processes/{proc_id}/analysis")).json()
    assert _gate(before, "G2")["state"] == "approved"

    rule = before["rules"][0]
    r = await client.patch(f"/rules/{rule['id']}", json={"text": rule["text"] + " Applies to every dealer."},
                           headers=SME)
    assert r.status_code == 200, r.text
    after = r.json()
    edited = next(x for x in after["rules"] if x["id"] == rule["id"])
    assert edited["status"] == "edited" and edited["original_text"] == rule["original_text"]
    assert edited["updated_by"] == "sme.vicky"
    assert after["spec_hash"] != before["spec_hash"]
    for name in ("G1", "G2"):
        g = _gate(after, name)
        assert g["state"] == "pending" and g["reset_reason"] == "rule edited"
    listed = {p["id"]: p for p in (await client.get(f"/projects/{pid}/processes")).json()}
    assert listed[proc_id]["spec_hash"] == after["spec_hash"]

    async with app.state.sessionmaker() as s:
        audit = (await s.execute(select(AuditLog).where(AuditLog.process_id == proc_id)
                                 .order_by(AuditLog.id))).scalars().all()
    actions = [a.action for a in audit]
    assert "rule edited" in actions
    resets = [a for a in audit if a.action == "gate reset"]
    assert {a.gate for a in resets[-2:]} == {"G1", "G2"}
    assert all(a.detail["reason"] == "rule edited" and a.detail["old_spec_hash"] == before["spec_hash"]
               and a.detail["new_spec_hash"] == after["spec_hash"] for a in resets[-2:])
    assert next(a for a in audit if a.action == "rule edited").actor == "sme.vicky"


async def test_unknown_activity_type_blocks_g1(client, app):
    _, procs = await _scanned(client)
    proc_id = procs[DFR]["id"]
    async with app.state.sessionmaker() as s:
        row = await s.get(Process, proc_id)
        ir = dict(row.ir)
        acts = [dict(a) for a in ir["activities"]]
        for a in acts:
            if a["id"] == "LogDuplicateFunding":
                a["type"], a["native_type"] = "unknown", "bw.custom.Mystery"
        ir["activities"] = acts
        row.ir = ir
        await s.commit()
    view = await _analyse(client, proc_id)
    view = await _decide_rules(client, view)
    assert view["blockers"] == ["unknown activity types: bw.custom.Mystery"]
    g1 = _gate(view, "G1")
    assert g1["enabled"] is False and "unknown activity types: bw.custom.Mystery" in g1["reason"]
    r = await client.post(f"/gates/{proc_id}/G1", json={"state": "approved", "note": ""}, headers=SME)
    assert r.status_code == 409
    assert "unknown activity types: bw.custom.Mystery" in r.json()["detail"]


async def test_decisions_recorded_in_memory(client, app):
    pid, procs = await _scanned(client)
    proc_id = procs[DFR]["id"]
    view = await _analyse(client, proc_id)
    rule = view["rules"][0]
    new_text = rule["text"] + " Inactive dealers are told why."
    r = await client.patch(f"/rules/{rule['id']}", json={"text": new_text}, headers=SME)
    assert r.status_code == 200
    finding = next(f for f in view["findings"] if f["finding_key"] == "S2-DealerFundingRequest-1")
    r = await client.patch(f"/findings/{finding['id']}",
                           json={"decision": "keep", "note": "dealer ids are always exact"},
                           headers={"X-User": "arch.sam"})
    assert r.status_code == 200
    decided = next(f for f in r.json()["findings"] if f["id"] == finding["id"])
    assert decided["decision"] == "keep" and decided["decided_by"] == "arch.sam"
    domain = next(d for d in (await client.get(f"/projects/{pid}/domains")).json()
                  if proc_id in d["process_ids"])
    r = await client.patch(f"/domains/{domain['id']}", json={"name": "Dealer Funding Desk"}, headers=SME)
    assert r.status_code == 200 and r.json()["renamed_by"] == "sme.vicky"

    rows = await asyncio.to_thread(app.state.knowledge.query,
                                   "SELECT kind, text, context, created_by, source FROM "
                                   "knowledge.memory_items WHERE project_id = %s AND source <> "
                                   "'rsi-seed' ORDER BY created_at",
                                   [pid])
    assert [r["kind"] for r in rows] == ["decision"] * 3
    edit, keep, rename = rows
    assert edit["context"]["process_id"] == proc_id and edit["context"]["user"] == "sme.vicky"
    assert edit["context"]["old_text"] == rule["text"] and edit["context"]["new_text"] == new_text
    assert edit["context"]["rule_key"] == rule["rule_key"] and edit["created_by"] == "sme.vicky"
    assert keep["context"]["decision"] == "keep" and keep["context"]["note"] == "dealer ids are always exact"
    assert keep["context"]["finding_key"] == "S2-DealerFundingRequest-1" and keep["created_by"] == "arch.sam"
    assert rename["context"]["new_name"] == "Dealer Funding Desk"
    assert rename["context"]["old_name"] == domain["name"]
    assert all(r["source"] == "sme" for r in rows)


# ------------------------------------------------------------------ further tests
async def test_rules_ids_stable_across_reruns(client, app):
    _, procs = await _scanned(client)
    proc_id = procs[CAI]["id"]
    first = await _analyse(client, proc_id)
    assert first["rules"]
    r1 = first["rules"][0]
    r = await client.patch(f"/rules/{r1['id']}", json={"status": "approved"}, headers=SME)
    assert r.status_code == 200
    second = await _analyse(client, proc_id)
    assert [(x["id"], x["rule_key"], x["text"]) for x in second["rules"]] == \
           [(x["id"], x["rule_key"], x["text"]) for x in first["rules"]]
    assert second["rules"][0]["status"] == "approved"  # the SME decision survives a re-run
    assert [f["finding_key"] for f in second["findings"]] == [f["finding_key"] for f in first["findings"]]


async def test_g2_requires_g1(client):
    _, procs = await _scanned(client)
    proc_id = procs[DFR]["id"]
    view = await _analyse(client, proc_id)
    view = await _decide_findings(client, view)
    r = await client.post(f"/gates/{proc_id}/G2", json={"state": "approved", "note": ""}, headers=SME)
    assert r.status_code == 409 and "G1 not approved" in r.json()["detail"]
    view = await _decide_rules(client, view)
    assert (await client.post(f"/gates/{proc_id}/G1", json={"state": "approved", "note": ""},
                              headers=SME)).status_code == 200
    r = await client.post(f"/gates/{proc_id}/G2", json={"state": "approved", "note": "ok"}, headers=SME)
    assert r.status_code == 200 and r.json()["state"] == "approved"


async def test_g2_requires_every_finding_decided(client):
    _, procs = await _scanned(client)
    proc_id = procs[DFR]["id"]
    await _decide_rules(client, await _analyse(client, proc_id))
    assert (await client.post(f"/gates/{proc_id}/G1", json={"state": "approved", "note": ""},
                              headers=SME)).status_code == 200
    r = await client.post(f"/gates/{proc_id}/G2", json={"state": "approved", "note": ""}, headers=SME)
    assert r.status_code == 409 and "undecided" in r.json()["detail"]


async def test_gate_endpoint_accepts_g3_with_g2_approved(client):
    _, procs = await _scanned(client)
    proc_id = procs[DFR]["id"]
    await _decide_findings(client, await _decide_rules(client, await _analyse(client, proc_id)))
    r = await client.post(f"/gates/{proc_id}/G3", json={"state": "approved", "note": ""}, headers=SME)
    assert r.status_code == 409 and "G2 not approved" in r.json()["detail"]
    for gate in ("G1", "G2"):
        r = await client.post(f"/gates/{proc_id}/{gate}", json={"state": "approved", "note": ""},
                              headers=SME)
        assert r.status_code == 200, (gate, r.text)
    # G3 also needs its evidence (Task 19): a design record bound to the current spec hash
    r = await client.post(f"/gates/{proc_id}/G3", json={"state": "approved", "note": ""}, headers=SME)
    assert r.status_code == 409 and "design record has not been generated" in r.json()["detail"]
    job = await _wait_job(client, (await client.post(f"/processes/{proc_id}/design")).json()["job_id"])
    assert job["status"] == "done", job
    r = await client.post(f"/gates/{proc_id}/G3", json={"state": "approved", "note": ""}, headers=SME)
    assert r.status_code == 200, r.text
    assert r.json()["gate"] == "G3" and r.json()["state"] == "approved"
    gates = (await client.get(f"/processes/{proc_id}/gates")).json()
    assert [g["gate"] for g in gates] == ["G1", "G2", "G3", "G4", "G5", "G6"]
    g4 = _gate({"gates": gates}, "G4")
    assert g4["enabled"] is False and g4["reason"] == "no build has finished"
    assert (await client.post(f"/gates/{proc_id}/G7", json={"state": "approved", "note": ""},
                              headers=SME)).status_code == 404
    # rejecting G1 now reopens everything downstream
    r = await client.post(f"/gates/{proc_id}/G1", json={"state": "rejected", "note": "rethink"},
                          headers=SME)
    assert r.status_code == 200
    gates = {g["gate"]: g for g in (await client.get(f"/processes/{proc_id}/gates")).json()}
    assert gates["G1"]["state"] == "rejected"
    assert gates["G2"]["state"] == gates["G3"]["state"] == "pending"
    assert gates["G2"]["reset_reason"] == "G1 rejected"


async def test_domains_rename_merge_graph_and_naming(client, app):
    pid, procs = await _scanned(client)
    domains = (await client.get(f"/projects/{pid}/domains")).json()
    assert len(domains) == 4
    assert set(domains[0]) >= {"id", "name", "suggested_name", "process_ids", "evidence", "renamed_by"}
    by_name = {d["name"]: d for d in domains}
    dealer, servicing = by_name["Dealer Funding"], by_name["Loan Servicing"]
    assert procs[DFR]["id"] in dealer["process_ids"]

    r = await client.patch(f"/domains/{dealer['id']}", json={"name": "Dealer Funding Desk"},
                           headers={"X-Agent": "namer"})
    assert r.status_code == 403
    r = await client.post(f"/domains/{servicing['id']}/merge/{dealer['id']}", headers=SME)
    assert r.status_code == 200, r.text
    merged = r.json()
    assert merged["id"] == dealer["id"]
    assert set(servicing["process_ids"]) <= set(merged["process_ids"])
    domains = (await client.get(f"/projects/{pid}/domains")).json()
    assert len(domains) == 3 and servicing["id"] not in {d["id"] for d in domains}
    assert (await client.post(f"/domains/{dealer['id']}/merge/{dealer['id']}", headers=SME)).status_code == 409
    listed = {p["id"]: p for p in (await client.get(f"/projects/{pid}/processes")).json()}
    assert all(listed[x]["domain_id"] == dealer["id"] for x in servicing["process_ids"])

    graph = (await client.get(f"/projects/{pid}/graph")).json()
    assert graph["nodes"] and graph["edges"]
    node = next(n for n in graph["nodes"] if n["id"] == f"process:{DFR}")
    assert node["attrs"]["process_id"] == procs[DFR]["id"]
    assert node["attrs"]["complexity_band"] and node["attrs"]["migration_status"] == "not_started"

    r = await client.post(f"/projects/{pid}/domains/name")
    assert r.status_code == 202
    job = await _wait_job(client, r.json()["job_id"])
    assert job["status"] == "done" and job["result"]["ai_available"] is True
    named = {d["id"]: d for d in (await client.get(f"/projects/{pid}/domains")).json()}
    credit = next(d for d in named.values() if "Credit" in d["suggested_name"])
    assert credit["suggested_name"] == credit["name"] != "Credit Origination"
    async with app.state.sessionmaker() as s:
        assert await s.scalar(select(ModelCall.stage).where(ModelCall.project_id == pid)) == "namer"
    actions = [a["action"] for a in (await client.get(f"/projects/{pid}/audit")).json()]
    assert "domain merged" in actions


async def test_ask_answers_from_the_knowledge_base(client):
    pid, procs = await _scanned(client)
    await _analyse(client, procs[DFR]["id"])
    r = await client.post("/ask", json={"project_id": pid, "question": "When is a dealer allowed funding?"})
    assert r.status_code == 200, r.text
    answer = r.json()
    assert answer["used_model"] is True and answer["text"] and answer["citations"]
    assert all(c["process_id"] == procs[DFR]["id"] for c in answer["citations"] if c["process_id"])
    r = await client.post("/ask", json={"project_id": "nope", "question": "x"})
    assert r.status_code == 404


# ------------------------------------------------------------------ review round 1
def _provider_off(settings) -> None:
    settings.provider = "auto"
    settings.claude_code_oauth_token = ""
    settings.azure_openai_endpoint = ""
    settings.azure_openai_api_key = ""


async def _audit_count(app) -> int:
    async with app.state.sessionmaker() as s:
        return await s.scalar(select(func.count()).select_from(AuditLog))


async def test_code_only_rerun_keeps_decisions_and_gates(client, app, settings):
    _, procs = await _scanned(client)
    proc_id = procs[DFR]["id"]
    view = await _decide_findings(client, await _decide_rules(client, await _analyse(client, proc_id)))
    assert (await client.post(f"/gates/{proc_id}/G1", json={"state": "approved", "note": ""},
                              headers=SME)).status_code == 200
    before = (await client.get(f"/processes/{proc_id}/analysis")).json()
    assert any(f["finding_key"] == "F1" for f in before["findings"])

    _provider_off(settings)
    after = await _analyse(client, proc_id)
    assert after["ai_available"] is False and after["notice"] == "code-only analysis: no model configured"
    assert [(r["id"], r["rule_key"], r["status"], r["text"]) for r in after["rules"]] == \
           [(r["id"], r["rule_key"], r["status"], r["text"]) for r in before["rules"]]
    assert all(r["challenge"] for r in after["rules"])  # earlier verdicts carried
    assert [(f["finding_key"], f["decision"], f["business_impact"]) for f in after["findings"]] == \
           [(f["finding_key"], f["decision"], f["business_impact"]) for f in before["findings"]]
    assert after["summary"] == before["summary"]
    assert after["coverage"]["complete"] is True and after["verification"]["passed"] is True
    assert after["spec_hash"] == before["spec_hash"]
    g1 = _gate(after, "G1")
    assert g1["state"] == "approved" and g1["stale"] is False
    assert "gate reset" not in await _audit_actions(client, proc_id)
    assert view  # (decisions made above)


async def test_failed_analyst_carries_forward_or_fails_without_persisting(client, app, monkeypatch):
    _, procs = await _scanned(client)

    async def broken(*args, **kwargs):
        raise ProviderError("model unavailable")

    # nothing to carry forward: the job fails and nothing is stored
    fresh = procs[CAI]["id"]
    monkeypatch.setattr(analysis_svc, "analyse", broken)
    r = await client.post(f"/processes/{fresh}/analyse")
    job = await _wait_job(client, r.json()["job_id"])
    assert job["status"] == "failed" and "nothing was stored" in job["error"]
    async with app.state.sessionmaker() as s:
        assert await s.scalar(select(AnalysisRun.id).where(AnalysisRun.process_id == fresh)) is None
        assert await s.scalar(select(Rule.id).where(Rule.process_id == fresh)) is None

    # with an earlier analysis: the analyst failure keeps the earlier rules and decisions
    monkeypatch.undo()
    proc_id = procs[DFR]["id"]
    before = await _decide_rules(client, await _analyse(client, proc_id))
    monkeypatch.setattr(analysis_svc, "analyse", broken)
    after = await _analyse(client, proc_id)
    assert after["notice"] == "partial analysis: a model stage failed, see the job log"
    assert "analyst" in after["ai_reason"]
    assert [(r["rule_key"], r["status"]) for r in after["rules"]] == \
           [(r["rule_key"], r["status"]) for r in before["rules"]]
    assert {f["finding_key"] for f in after["findings"]} >= {"F1"}  # the risk stage still ran


async def test_rule_keys_are_never_reused(client, app):
    _, procs = await _scanned(client)
    proc_id = procs[DFR]["id"]
    first = await _analyse(client, proc_id)
    last = first["rules"][-1]
    assert last["rule_key"] == f"R{len(first['rules'])}" and last["citations"][0]["kind"] == "sql"
    activity = last["citations"][0]["activity_id"]
    async with app.state.sessionmaker() as s:
        row = await s.get(Process, proc_id)
        original_ir = row.ir
        ir = dict(original_ir)
        ir["activities"] = [{**a, "config": {k: v for k, v in a["config"].items()
                                             if k not in ("sql", "settings")}}
                            if a["id"] == activity else a for a in ir["activities"]]
        row.ir = ir
        await s.commit()
    second = await _analyse(client, proc_id)
    assert last["rule_key"] not in {r["rule_key"] for r in second["rules"]}
    async with app.state.sessionmaker() as s:
        row = await s.get(Process, proc_id)
        row.ir = original_ir
        await s.commit()
    third = await _analyse(client, proc_id)
    back = next(r for r in third["rules"] if r["text"] == last["text"])
    assert back["rule_key"] == f"R{len(first['rules']) + 1}"  # a new key, the old one is retired


async def test_get_never_writes_and_shows_stale_gates_after_rescan(client, app, tmp_path):
    estate = tmp_path / "estate"
    shutil.copytree(SYNTHETIC, estate)
    r = await client.post("/projects", json={"name": "gmf"})
    pid = r.json()["id"]
    assert (await client.post(f"/projects/{pid}/upload", json={"path": str(estate)})).status_code == 201
    job = await _wait_job(client, (await client.post(f"/projects/{pid}/scan", json={})).json()["job_id"])
    assert job["status"] == "done"
    procs = {p["ir_id"]: p for p in (await client.get(f"/projects/{pid}/processes")).json()}
    proc_id = procs[DFR]["id"]
    await _decide_findings(client, await _decide_rules(client, await _analyse(client, proc_id)))
    assert (await client.post(f"/gates/{proc_id}/G1", json={"state": "approved", "note": ""},
                              headers=SME)).status_code == 200

    bwp = estate / "DealerFunding/Processes/dealerfunding/DealerFundingRequest.bwp"
    head, _, rest = bwp.read_text().partition("\n")
    bwp.write_text(head + "\n\n" + rest)
    assert (await client.post(f"/projects/{pid}/upload", json={"path": str(estate)})).status_code == 201
    job = await _wait_job(client, (await client.post(f"/projects/{pid}/scan", json={})).json()["job_id"])
    assert job["status"] == "done" and job["result"]["changed"] == 1

    count = await _audit_count(app)
    view = (await client.get(f"/processes/{proc_id}/analysis")).json()
    gates = (await client.get(f"/processes/{proc_id}/gates")).json()
    await client.get(f"/processes/{proc_id}/analysis")
    assert await _audit_count(app) == count  # reads never write
    g1, g2 = _gate({"gates": gates}, "G1"), _gate({"gates": gates}, "G2")
    assert g1["state"] == "approved" and g1["stale"] is True
    assert "IR changed since analysis: re-run analysis" in g1["reason"]
    assert g2["enabled"] is False and "G1 approval is stale" in g2["reason"]
    assert view["ir_changed"] is True and _gate(view, "G1")["stale"] is True

    # the next action reconciles: the stale G1 is reset, and approving needs a re-analysis
    r = await client.post(f"/gates/{proc_id}/G1", json={"state": "approved", "note": ""}, headers=SME)
    assert r.status_code == 409 and "IR changed since analysis" in r.json()["detail"]
    g1 = _gate((await client.get(f"/processes/{proc_id}/analysis")).json(), "G1")
    assert g1["state"] == "pending" and g1["reset_reason"] == "spec hash changed since the gate was decided"
    view = await _analyse(client, proc_id)
    assert view["ir_changed"] is False
    assert "IR changed" not in (_gate(view, "G1")["reason"] or "")


async def test_owner_may_reject_a_gate_that_is_not_enabled(client):
    _, procs = await _scanned(client)
    proc_id = procs[DFR]["id"]
    r = await client.post(f"/gates/{proc_id}/G1", json={"state": "approved", "note": ""}, headers=SME)
    assert r.status_code == 409
    r = await client.post(f"/gates/{proc_id}/G1", json={"state": "rejected", "note": "scope unclear"},
                          headers=SME)
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "rejected" and r.json()["note"] == "scope unclear"
    audit = (await client.get(f"/processes/{proc_id}/audit")).json()
    rejected = next(a for a in audit if a["action"] == "gate rejected")
    assert rejected["detail"]["reason"] == "scope unclear"
    r = await client.post(f"/gates/{proc_id}/G4", json={"state": "rejected", "note": "no"},
                          headers={"X-Agent": "builder"})
    assert r.status_code == 403


async def test_project_creation_seeds_rsi_patterns(client, app):
    r = await client.post("/projects", json={"name": "seeded"})
    pid = r.json()["id"]
    sql = "SELECT count(*) AS n FROM knowledge.memory_items WHERE project_id = %s AND source = 'rsi-seed'"
    for _ in range(150):
        rows = await asyncio.to_thread(app.state.knowledge.query, sql, [pid])
        if rows[0]["n"]:
            break
        await asyncio.sleep(0.2)
    assert rows[0]["n"] > 0


# ------------------------------------------------------------------ follow-up (partial first run, ir_hash)
PARTIAL_RISK = "analysis is partial (risk reviewer failed); re-run analysis before approving G1"


async def test_partial_first_run_keeps_the_analyst_output(client, app, monkeypatch):
    _, procs = await _scanned(client)
    proc_id = procs[DFR]["id"]

    async def broken(*args, **kwargs):
        raise ProviderError("risk model unavailable")

    # a first run whose Risk Reviewer fails stores the analyst's rules and summary as partial
    monkeypatch.setattr(analysis_svc, "review_risk", broken)
    view = await _analyse(client, proc_id)
    assert view["rules"] and all(r["citations"] for r in view["rules"])
    assert view["summary"] and not view["summary"].startswith("Code-only analysis")
    assert view["notice"] == analysis_svc.PARTIAL_NOTICE
    assert "risk reviewer" in view["ai_reason"]
    assert {f["source"] for f in view["findings"]} == {"static"}  # no AI finding without the reviewer
    async with app.state.sessionmaker() as s:
        run = await s.scalar(select(AnalysisRun).where(AnalysisRun.process_id == proc_id))
    assert run.summary["failed_stages"] == ["risk reviewer"]
    assert run.summary["summary_source"] == "model"

    gates = {g["gate"]: g for g in (await client.get(f"/processes/{proc_id}/gates")).json()}
    assert gates["G1"]["enabled"] is False and PARTIAL_RISK in gates["G1"]["reason"]
    decided = await _decide_rules(client, view)
    assert _gate(decided, "G1")["reason"] == PARTIAL_RISK  # the only reason left
    r = await client.post(f"/gates/{proc_id}/G1", json={"state": "approved", "note": ""}, headers=SME)
    assert r.status_code == 409 and PARTIAL_RISK in r.json()["detail"]
    r = await client.post(f"/gates/{proc_id}/G1", json={"state": "rejected", "note": "partial"}, headers=SME)
    assert r.status_code == 200 and r.json()["state"] == "rejected"  # rejection stays allowed

    # a complete run clears the reason
    monkeypatch.undo()
    view = await _analyse(client, proc_id)
    assert view["notice"] is None
    assert any(f["source"] == "ai" for f in view["findings"])
    assert "analysis is partial" not in (_gate(view, "G1")["reason"] or "")
    async with app.state.sessionmaker() as s:
        run = await s.scalar(select(AnalysisRun).where(AnalysisRun.process_id == proc_id)
                             .order_by(AnalysisRun.created_at.desc()).limit(1))
    assert run.summary["failed_stages"] == []


async def test_failed_challenger_on_a_first_run_is_partial(client, app, monkeypatch):
    _, procs = await _scanned(client)
    proc_id = procs[DFR]["id"]

    async def broken(*args, **kwargs):
        raise ProviderError("challenger unavailable")

    monkeypatch.setattr(analysis_svc, "challenge", broken)
    view = await _analyse(client, proc_id)
    assert view["rules"] and all(r["challenge"] is None for r in view["rules"])
    assert view["notice"] == analysis_svc.PARTIAL_NOTICE
    reason = "analysis is partial (challenger failed); re-run analysis before approving G1"
    assert reason in _gate(view, "G1")["reason"]


async def test_analysis_stores_the_hash_of_the_ir_it_analyzed(client, app, monkeypatch):
    from api.app.services import gates as gate_svc

    _, procs = await _scanned(client)
    proc_id = procs[DFR]["id"]
    async with app.state.sessionmaker() as s:
        analyzed = gate_svc.ir_hash((await s.get(Process, proc_id)).ir)
    review_risk = analysis_svc.review_risk

    async def rescan_meanwhile(*args, **kwargs):
        async with app.state.sessionmaker() as s:  # a rescan changes the IR while the job runs
            row = await s.get(Process, proc_id)
            row.ir = {**row.ir, "raw_xml_sha256": "changed-by-a-rescan"}
            await s.commit()
        return await review_risk(*args, **kwargs)

    monkeypatch.setattr(analysis_svc, "review_risk", rescan_meanwhile)
    view = await _analyse(client, proc_id)
    async with app.state.sessionmaker() as s:
        run = await s.scalar(select(AnalysisRun).where(AnalysisRun.process_id == proc_id))
        current = gate_svc.ir_hash((await s.get(Process, proc_id)).ir)
    assert run.summary["ir_hash"] == analyzed != current
    assert view["ir_changed"] is True
    gates = {g["gate"]: g for g in (await client.get(f"/processes/{proc_id}/gates")).json()}
    assert gate_svc.IR_CHANGED in gates["G1"]["reason"]
