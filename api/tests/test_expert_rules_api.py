"""Expert rules API: import (dry run, merge, replace), the bundled defaults, list, enable/disable,
clear, the template, role checks, and the rules reaching the analysis prompt and summary."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from api.app.models import AnalysisRun, AuditLog
from api.app.services import fake_responses
from api.tests.test_analysis_gates import _analyse, _scanned
from engine.agents import expert_rules as er

SME = {"X-User": "sme.vicky"}
VIEWER = {"X-User": "viewer.val"}
HEADER = ",".join(er.COLUMNS)


def _csv(*ids: str, bad: int = 0) -> bytes:
    rows = [f"{i},Domain,Name {i},High,Guide {i}" + ",,,Discovery Agent" + "," * 7 for i in ids]
    rows += [f"BAD{n},Domain,Name,Nope,Guide" + "," * 10 for n in range(bad)]
    return ("\r\n".join([HEADER, *rows]) + "\r\n").encode()


async def _import(client, pid, data: bytes, **params):
    return await client.post(f"/projects/{pid}/expert-rules/import", params=params, headers=SME,
                             files={"file": ("rules.csv", data, "text/csv")})


async def test_dry_run_reports_without_writing(client, project_id):
    r = await _import(client, project_id, _csv("TIB-1", "TIB-2", bad=1), dry_run="true")
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["imported"], body["updated"], body["skipped"]) == (2, 0, 1)
    assert body["errors"][0]["field"] == "rule_id"
    assert (await client.get(f"/projects/{project_id}/expert-rules")).json()["total"] == 0


async def test_merge_then_replace(client, app, project_id):
    r = await _import(client, project_id, _csv("TIB-1", "TIB-2"))
    assert r.json()["imported"] == 2
    r = await client.patch(f"/projects/{project_id}/expert-rules/TIB-1", json={"enabled": False}, headers=SME)
    assert r.status_code == 200 and r.json()["enabled"] is False
    r = await _import(client, project_id, _csv("TIB-2", "TIB-3"))
    assert (r.json()["imported"], r.json()["updated"]) == (1, 1)
    listed = (await client.get(f"/projects/{project_id}/expert-rules")).json()
    assert [x["rule_id"] for x in listed["rules"]] == ["TIB-1", "TIB-2", "TIB-3"]
    assert listed["enabled"] == 2 and listed["by_severity"]["High"] == 3
    r = await _import(client, project_id, _csv("TIB-9"), mode="replace")
    assert r.json()["removed"] == 3
    listed = (await client.get(f"/projects/{project_id}/expert-rules")).json()
    assert [x["rule_id"] for x in listed["rules"]] == ["TIB-9"]
    async with app.state.sessionmaker() as s:
        actions = (await s.scalars(select(AuditLog.action).where(AuditLog.project_id == project_id))).all()
    assert "expert rules imported" in actions and "expert rule disabled" in actions


async def test_no_valid_rows_or_bad_header_is_422(client, project_id):
    r = await _import(client, project_id, _csv(bad=2))
    assert r.status_code == 422 and len(r.json()["detail"]["errors"]) == 4
    r = await _import(client, project_id, b"a,b\r\n1,2\r\n")
    assert r.status_code == 422 and "missing required" in r.json()["detail"]["message"]


async def test_defaults_patch_clear_and_template(client, project_id):
    r = await client.post(f"/projects/{project_id}/expert-rules/defaults", headers=SME)
    assert r.status_code == 200, r.text
    assert r.json()["imported"] == 125 and r.json()["errors"] == []
    listed = (await client.get(f"/projects/{project_id}/expert-rules")).json()
    assert listed["total"] == 125 and listed["rules"][0]["source"] == "default"
    assert listed["by_severity"] == {"Critical": 35, "High": 76, "Medium": 14, "Low": 0}
    assert (await client.patch(f"/projects/{project_id}/expert-rules/NOPE-1", json={"enabled": False},
                               headers=SME)).status_code == 404
    r = await client.delete(f"/projects/{project_id}/expert-rules", headers=SME)
    assert r.json()["removed"] == 125
    r = await client.get("/expert-rules/template.csv")
    assert r.status_code == 200 and r.text.startswith("Rule ID,Domain")


async def test_viewer_is_read_only(client, project_id):
    assert (await _import(client, project_id, _csv("TIB-1"), dry_run="true")).status_code == 200
    r = await client.post(f"/projects/{project_id}/expert-rules/import", headers=VIEWER,
                          files={"file": ("r.csv", _csv("TIB-1"), "text/csv")})
    assert r.status_code == 403
    assert (await client.post(f"/projects/{project_id}/expert-rules/defaults", headers=VIEWER)).status_code == 403
    assert (await client.delete(f"/projects/{project_id}/expert-rules", headers=VIEWER)).status_code == 403
    assert (await client.get(f"/projects/{project_id}/expert-rules", headers=VIEWER)).status_code == 200


@pytest.fixture
def systems(monkeypatch):
    seen: list[tuple[str, str]] = []
    original = fake_responses.reply_for

    def reply_for(req, process):
        seen.append((str(req.tags.get("stage")), req.system))
        return original(req, process)

    monkeypatch.setattr(fake_responses, "reply_for", reply_for)
    return seen


async def test_analysis_prompt_carries_rules_and_summary_records_them(client, app, systems):
    pid, procs = await _scanned(client)
    proc = next(iter(procs.values()))
    await _analyse(client, proc["id"])
    assert systems and not any(er.SECTION_TITLE in s for _, s in systems)  # no rules: no section
    systems.clear()
    r = await client.post(f"/projects/{pid}/expert-rules/defaults", headers=SME)
    assert r.status_code == 200
    await _analyse(client, proc["id"])
    assert any(er.SECTION_TITLE in s and "TIB-001" in s for _, s in systems), [st for st, _ in systems]
    async with app.state.sessionmaker() as s:
        run = await s.scalar(select(AnalysisRun).where(AnalysisRun.process_id == proc["id"])
                             .order_by(AnalysisRun.created_at.desc()).limit(1))
    assert "TIB-001" in run.summary["expert_rules"]["applied"]
    assert "TIB-001" in run.summary["expert_rules"]["discovery_checked"]
    listed = (await client.get(f"/projects/{pid}/expert-rules")).json()
    tib1 = next(x for x in listed["rules"] if x["rule_id"] == "TIB-001")
    assert "analysis" in tib1["applied_in"] and tib1["last_applied"]["phase"] == "analysis"
