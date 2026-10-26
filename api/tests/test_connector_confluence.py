"""Confluence connector (Task 4): the architecture page renderer and the publisher, Data Center and
Cloud, against the Confluence fakes (``client_atlassian``). The publish job runs in the app's own
worker.

The golden page ``golden/confluence_dfr.xhtml`` is rendered from the real DealerFundingRequest design
skeleton (``engine.dotnet_gen``) of ``samples/synthetic-gmf``. When the design output or the page
changes on purpose, regenerate it with ``UPDATE_GOLDEN=1 .venv/bin/pytest
api/tests/test_connector_confluence.py -k golden`` and review the diff before committing it.
(The layout section's ``<name>.sln`` is .NET-specific: sub-project 5 routes it through a stack hook.)"""
from __future__ import annotations

import asyncio
import os
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from api.app.connectors.confluence import ConfluencePublisher, _Http
from api.app.connectors.confluence_page import (
    SECTIONS,
    PageContext,
    page_title,
    render_architecture_page,
)
from api.app.connectors.registry import AtlassianConnector, get_connector
from api.app.jobs.locks import try_advisory_lock
from api.app.models import (
    Application,
    AuditLog,
    Build,
    DesignRecord,
    Gate,
    Process,
    Project,
    Upload,
)
from api.app.services.confluence_publish import PUBLISH_KIND, page_context
from engine.domains import graph as graph_mod
from engine.dotnet_gen import design as design_mod
from engine.dotnet_gen.models import Decision, OpenQuestion
from engine.tibco_parser.scanner import scan_folder

REPO_ROOT = Path(__file__).resolve().parents[2]
GOLDEN = Path(__file__).parent / "golden"
GMF = REPO_ROOT / "samples" / "synthetic-gmf"
ARCHITECT = {"X-User": "architect.lee"}
ADMIN = {"X-User": "ops.sam"}
H2 = ["Overview", "Target stack and solution layout", "Decisions", "Open questions", "Mapping", "Contracts",
      "Configuration", "Messaging", "Resilience", "Sequence", "Data model", "Gate record", "Figma references",
      "Source"]
DECIDED_AT = datetime(2026, 10, 20, 9, 30, tzinfo=UTC)


# ------------------------------------------------------------------ the DFR design


def _dfr_design() -> tuple[dict, str, str, str]:
    """The DealerFundingRequest design skeleton (built by code from the sample), with a narrative,
    decisions and an answered question as the designer and the architect would add them."""
    apps = scan_folder(GMF).applications
    graph = graph_mod.build_graph(apps)
    proc, app = next((p, a) for a in apps for p in a.processes if p.name == "DealerFundingRequest")
    record = design_mod.build_design_skeleton(proc, app, [], [], graph_mod.io_card(proc, app, graph))
    record.narrative = ("The dealer funding request arrives on a queue and is checked against the dealer "
                        "and the deal before a funding row is written.\n\nA duplicate request is logged & "
                        "dropped; a funding event is published for downstream ledgers.")
    record.decisions = [Decision(title="Duplicate detection", choice="Check the funding table by deal id",
                                 alternatives=["Idempotency key table"], reason="Keeps TIBCO's behaviour")]
    record.open_questions = [*record.open_questions, OpenQuestion(
        question="Should inactive dealers be rejected <before> the deal lookup?",
        recommendation="Yes, as TIBCO does", decision="answered", answer="Yes", decided_by="architect.lee",
        decided_at="2026-10-20T09:30:00+00:00")]
    rel = Path(proc.source_path).resolve().relative_to(GMF.resolve()).as_posix()
    return record.model_dump(mode="json", by_alias=True), app.name, proc.id, rel


@pytest.fixture(scope="module")
def dfr() -> tuple[dict, str, str, str]:
    """``(design record, application name, process id, source path)``, built once per module."""
    return _dfr_design()


def _gates(design_hash: str, spec_hash: str) -> list[dict]:
    out = []
    for n in range(1, 7):
        decided = n <= 3
        out.append({"gate": f"G{n}", "state": "approved" if decided else "pending",
                    "decided_by": "architect.lee" if decided else None,
                    "decided_at": DECIDED_AT.isoformat() if decided else "",
                    "spec_hash": spec_hash if decided else None,
                    "design_hash": design_hash if n == 3 else None, "build_hash": None, "evidence_hash": None})
    return out


@pytest.fixture
def page_ctx_dfr(dfr) -> PageContext:
    record, app_name, ir_id, rel = dfr
    return PageContext(app_name=app_name, process_name="DealerFundingRequest", ir_id=ir_id, source_path=rel,
                       design=record, design_version=2,
                       gates=_gates(record["design_hash"], record["spec_hash"]),
                       build={"id": "b0f1c2d3-0000-4000-8000-000000000001", "status": "passed",
                              "build_hash": "c" * 64},
                       stack="dotnet", iteration=1,
                       figma=[{"name": "Funding form", "url": "https://www.figma.example/file/K?node-id=1-2"}])


def _golden(name: str, text: str) -> None:
    path = GOLDEN / name
    if os.environ.get("UPDATE_GOLDEN") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    assert path.read_text(encoding="utf-8") == text, f"{name} changed: re-run with UPDATE_GOLDEN=1 and review it"


# ------------------------------------------------------------------ app fixtures


async def _seed(app, dfr, *, with_design: bool = True) -> str:
    record, app_name, ir_id, rel = dfr
    async with app.state.sessionmaker() as s:
        project = Project(name="credit")
        s.add(project)
        await s.flush()
        upload = Upload(project_id=project.id, source_kind="path", source_name="x", root_dir="/tmp")
        s.add(upload)
        await s.flush()
        application = Application(project_id=project.id, upload_id=upload.id, app_key=app_name, name=app_name,
                                  dialect="bw6", ir={})
        s.add(application)
        await s.flush()
        proc = Process(project_id=project.id, application_id=application.id, upload_id=upload.id, ir_id=ir_id,
                       name="DealerFundingRequest", dialect="bw6", source_path=rel, ir={},
                       spec_hash=record["spec_hash"])
        s.add(proc)
        await s.flush()
        if with_design:
            s.add(DesignRecord(process_id=proc.id, version=1, spec_hash=record["spec_hash"],
                               design_hash=record["design_hash"], record=record, narrative=record["narrative"]))
            for g in _gates(record["design_hash"], record["spec_hash"]):
                s.add(Gate(process_id=proc.id, **{**g, "decided_at": DECIDED_AT if g["decided_by"] else None}))
            s.add(Build(process_id=proc.id, status="passed", build_hash="c" * 64))
        await s.commit()
        return proc.id


@pytest.fixture
async def designed_process(atlassian_app, dfr) -> str:
    return await _seed(atlassian_app, dfr)


@pytest.fixture
def architect() -> dict[str, str]:
    return ARCHITECT


async def _job(client: httpx.AsyncClient, job_id: str, timeout: float = 60.0) -> dict:
    async with asyncio.timeout(timeout):
        while True:
            job = (await client.get(f"/jobs/{job_id}")).json()
            if job["status"] in ("done", "failed"):
                return job
            await asyncio.sleep(0.05)


@pytest.fixture
def wait_terminal():
    """``wait_terminal(client, job_id, expect="done")``: the job's result when it is done; the job
    itself when ``expect="failed"``."""
    async def wait(client: httpx.AsyncClient, job_id: str, expect: str = "done") -> dict:
        job = await _job(client, job_id)
        assert job["status"] == expect, job.get("error") or job
        return job["result"] if expect == "done" else job

    return wait


@pytest.fixture
async def published_once(client_atlassian, designed_process, architect, wait_terminal) -> str:
    c, fake = client_atlassian("datacenter")
    r = await c.post(f"/processes/{designed_process}/confluence/publish", headers=architect)
    assert r.status_code == 202, r.text
    assert (await wait_terminal(c, r.json()["job_id"]))["version"] == 1
    assert fake.only_page().version == 1
    return designed_process


async def _audits(app, action: str) -> list[AuditLog]:
    async with app.state.sessionmaker() as s:
        return list((await s.execute(select(AuditLog).where(AuditLog.action == action)
                                     .order_by(AuditLog.id))).scalars())


# ------------------------------------------------------------------ brief tests


def test_page_golden(page_ctx_dfr):
    _golden("confluence_dfr.xhtml", render_architecture_page(page_ctx_dfr))


def test_page_escapes_markup_from_any_field(page_ctx_dfr):
    ctx = page_ctx_dfr.model_copy(update={"design": {**page_ctx_dfr.design, "narrative": "<script>x</script> & <ac:macro/>"}})
    html = render_architecture_page(ctx)
    assert "<script>" not in html and "&lt;script&gt;" in html and "<ac:macro" not in html


def test_page_never_contains_config_values(page_ctx_dfr):
    assert "Password=" not in render_architecture_page(page_ctx_dfr)


@pytest.mark.parametrize("flavor", ["datacenter", "cloud"])
async def test_publish_creates_then_updates_in_place(client_atlassian, flavor, designed_process, architect, wait_terminal):
    c, fake = client_atlassian(flavor)
    await wait_terminal(c, (await c.post(f"/processes/{designed_process}/confluence/publish", headers=architect)).json()["job_id"])
    await wait_terminal(c, (await c.post(f"/processes/{designed_process}/confluence/publish", headers=architect)).json()["job_id"])
    assert fake.page_count() == 1 and fake.only_page().version == 2 and fake.only_page().attachments


async def test_concurrent_edit_is_merged_once_then_fails(client_atlassian, published_once, architect, wait_terminal):  # Review Focus 3
    c, fake = client_atlassian("datacenter")
    fake.bump_version(fake.only_page().id, by=2)
    ok = await wait_terminal(c, (await c.post(f"/processes/{published_once}/confluence/publish", headers=architect)).json()["job_id"])
    assert ok["version"] == 4
    fake.conflict_always = True
    failed = await wait_terminal(c, (await c.post(f"/processes/{published_once}/confluence/publish", headers=architect)).json()["job_id"], expect="failed")
    assert "rejected the update twice" in failed["error"]


# ------------------------------------------------------------------ the page


def test_page_sections_in_order_and_well_formed(page_ctx_dfr):
    html = render_architecture_page(page_ctx_dfr)
    root = ET.fromstring(f"<root>{html}</root>")  # storage format must be well-formed XHTML
    assert [h.text for h in root.iter("h2")] == H2 == [t for t, _ in SECTIONS]
    assert page_title("gmf.credit", "DealerFundingRequest") == "gmf.credit · DealerFundingRequest · Architecture"


def test_markup_in_every_kind_of_field_is_text(page_ctx_dfr):
    evil = '<ac:structured-macro ac:name="html"><ac:plain-text-body>x</ac:plain-text-body></ac:structured-macro>'
    design = {**page_ctx_dfr.design,
              "decisions": [{"title": evil, "choice": evil, "alternatives": [evil], "reason": evil}],
              "mapping": [{**page_ctx_dfr.design["mapping"][0], "activity_name": evil, "method": evil}],
              "views": {"tables": [{"name": evil, "verbs": [evil], "columns": [{"name": evil}]}]}}
    ctx = page_ctx_dfr.model_copy(update={
        "design": design, "app_name": evil, "process_name": evil, "source_path": evil,
        "gates": [{"gate": "G1", "state": evil, "decided_by": evil}],
        "figma": [{"name": evil, "url": 'https://x.example/"onmouseover="alert(1)'},
                  {"name": "js", "url": "javascript:alert(1)"}]})
    html = render_architecture_page(ctx)
    assert "<ac:" not in html and "&lt;ac:structured-macro" in html
    assert 'href="javascript' not in html and "<li>js</li>" in html  # only https becomes a link
    assert '"onmouseover' not in html
    ET.fromstring(f"<root>{html}</root>")


def test_page_masks_secrets_and_omits_config_values(page_ctx_dfr):
    design = {**page_ctx_dfr.design,
              "narrative": "Connects with Server=db;Password=hunter2; borrower_ssn: 123-45-6789",
              "config": [{"substvar": "GMF.DB.URL", "key": "Gmf:Db", "secret": True,
                          "value": "Server=db;Password=hunter2"}]}
    html = render_architecture_page(page_ctx_dfr.model_copy(update={"design": design}))
    assert "hunter2" not in html and "123-45-6789" not in html and "Password=***" in html
    assert "Gmf:Db" in html and "value not shown" in html


async def test_page_context_comes_from_the_database(atlassian_app, designed_process, page_ctx_dfr):
    async with atlassian_app.state.sessionmaker() as s:
        proc = await s.get(Process, designed_process)
        ctx = await page_context(s, proc)
    assert ctx.app_name == page_ctx_dfr.app_name and ctx.ir_id == page_ctx_dfr.ir_id
    assert ctx.design["design_hash"] == page_ctx_dfr.design["design_hash"] and ctx.design_version == 1
    assert [g["state"] for g in ctx.gates] == ["approved"] * 3 + ["pending"] * 3
    assert ctx.build and ctx.build["build_hash"] == "c" * 64


# ------------------------------------------------------------------ the publisher


@pytest.mark.parametrize("flavor", ["datacenter", "cloud"])
async def test_connection_test_names_the_account(client_atlassian, atlassian_app, flavor):
    c, _ = client_atlassian(flavor)
    r = await c.post("/connectors/atlassian/test", headers=ADMIN)
    assert r.status_code == 200 and r.json()["ok"] and r.json()["account"] == "ExiQO bot", r.text


@pytest.mark.parametrize("flavor", ["datacenter", "cloud"])
async def test_publish_wire_format(client_atlassian, flavor, designed_process, architect, wait_terminal, dfr):
    c, fake = client_atlassian(flavor)
    out = await wait_terminal(c, (await c.post(f"/processes/{designed_process}/confluence/publish",
                                               headers=architect)).json()["job_id"])
    page = fake.only_page()
    record = dfr[0]
    assert out == {"page_id": page.id, "url": out["url"], "version": 1, "title": page.title}
    assert page.title == "gmf.credit · DealerFundingRequest · Architecture" and page.parent == "1001"
    assert out["url"].endswith(f"/pages/{page.id}")
    assert [a["title"] for a in page.attachments] == [f"design-{record['design_hash'][:12]}.json"]
    posts = [r for r in fake.requests if r.method == "POST" and r.path.endswith("/child/attachment")]
    assert posts and all(r.headers.get("x-atlassian-token") == "no-check" for r in posts)
    auth = {r.headers.get("authorization", "").split(" ")[0] for r in fake.requests}
    if flavor == "cloud":
        assert auth == {"Basic"}
        assert any(r.method == "POST" and r.path == "/wiki/api/v2/pages" for r in fake.requests)
        assert all(r.path.startswith("/wiki/") for r in fake.requests)
    else:
        assert auth == {"Bearer"}
        assert any(r.method == "POST" and r.path == "/rest/api/content" for r in fake.requests)
    assert "&lt;before&gt;" in page.body and "<before>" not in page.body
    link = (await c.get(f"/processes/{designed_process}/confluence")).json()
    assert link["remote_id"] == page.id and link["remote_version"] == 1 and link["object_type"] == "confluence_page"


async def test_republish_versions_the_attachment_and_says_why(client_atlassian, published_once, architect,
                                                              wait_terminal):
    c, fake = client_atlassian("datacenter")
    await wait_terminal(c, (await c.post(f"/processes/{published_once}/confluence/publish", headers=architect)).json()["job_id"])
    page = fake.only_page()
    assert len(page.attachments) == 1 and page.attachments[0]["version"]["number"] == 2
    put = [r for r in fake.requests if r.method == "PUT"][-1].json
    assert put["version"] == {"number": 2, "message": "republished from design v1"}
    rows = await _audits(c.app, "connector confluence publish")
    assert [r.detail["outcome"] for r in rows] == ["ok", "ok"] and rows[-1].detail["version"] == 2
    assert rows[-1].actor == "architect.lee"


async def test_deleted_page_is_created_again(client_atlassian, published_once, architect, wait_terminal):
    c, fake = client_atlassian("datacenter")
    old = fake.only_page().id
    del fake.pages[old]
    out = await wait_terminal(c, (await c.post(f"/processes/{published_once}/confluence/publish", headers=architect)).json()["job_id"])
    assert fake.page_count() == 1 and fake.only_page().id != old and out["version"] == 1
    assert (await c.get(f"/processes/{published_once}/confluence")).json()["remote_id"] == out["page_id"]


async def test_unlinked_page_of_the_same_title_is_updated(client_atlassian, designed_process, architect,
                                                          wait_terminal):
    c, fake = client_atlassian("cloud")
    pid = fake.create_page("GMF", "gmf.credit · DealerFundingRequest · Architecture", "<p>by hand</p>", "1001")
    fake.seeded.discard(pid)
    out = await wait_terminal(c, (await c.post(f"/processes/{designed_process}/confluence/publish",
                                               headers=architect)).json()["job_id"])
    assert out["page_id"] == pid and out["version"] == 2 and fake.page_count() == 1


async def test_failed_publish_is_audited(client_atlassian, published_once, architect, wait_terminal):
    c, fake = client_atlassian("datacenter")
    fake.conflict_always = True
    await wait_terminal(c, (await c.post(f"/processes/{published_once}/confluence/publish", headers=architect)).json()["job_id"], expect="failed")
    rows = await _audits(c.app, "connector confluence publish")
    assert rows[-1].detail["outcome"] == "failed" and "rejected the update twice" in rows[-1].detail["message"]
    assert "atlassian-fake-token" not in str(rows[-1].detail)


# ------------------------------------------------------------------ the route


async def test_publish_without_design_is_409(client_atlassian, atlassian_app, architect, dfr):
    c, _ = client_atlassian("datacenter")
    pid = await _seed(atlassian_app, dfr, with_design=False)
    r = await c.post(f"/processes/{pid}/confluence/publish", headers=architect)
    assert r.status_code == 409 and r.json()["detail"] == "design the process before publishing it"


async def test_publish_needs_architect_or_admin(client_atlassian, designed_process, wait_terminal):
    c, fake = client_atlassian("datacenter")
    for user, status in (("viewer.val", 403), ("engineer.ravi", 403), ("ops.sam", 202)):
        r = await c.post(f"/processes/{designed_process}/confluence/publish", headers={"X-User": user})
        assert r.status_code == status, (user, r.text)
    assert (await wait_terminal(c, r.json()["job_id"]))["version"] == 1 and fake.page_count() == 1
    assert (await _audits(c.app, "connector confluence publish"))[-1].actor == "ops.sam"


async def test_connector_off_after_the_202_is_refused_and_audited(client_atlassian, atlassian_app,
                                                                   designed_process, wait_terminal):
    """The route accepted the publish; by the time the job runs the install has turned it off."""
    c, fake = client_atlassian("datacenter")
    atlassian_app.state.settings.connectors = ""
    job_id = await atlassian_app.state.runner.submit(PUBLISH_KIND, {"process_id": designed_process,
                                                                    "actor": "architect.lee"})
    failed = await wait_terminal(c, job_id, expect="failed")
    assert "not enabled" in failed["error"] and fake.requests == []
    row = (await _audits(c.app, "connector confluence publish"))[-1]
    assert row.detail["outcome"] == "refused" and row.actor == "architect.lee"
    assert row.process_id == designed_process


async def test_attachment_failure_keeps_the_link_and_is_audited(client_atlassian, designed_process, architect,
                                                               wait_terminal, monkeypatch):
    c, fake = client_atlassian("datacenter")

    async def broken(self, page_id, ctx):
        raise RuntimeError(f"upload failed with {fake.token}")

    monkeypatch.setattr(ConfluencePublisher, "attach_design", broken)
    await wait_terminal(c, (await c.post(f"/processes/{designed_process}/confluence/publish",
                                         headers=architect)).json()["job_id"], expect="failed")
    link = (await c.get(f"/processes/{designed_process}/confluence")).json()
    assert link["remote_id"] == fake.only_page().id
    row = (await _audits(c.app, "connector confluence publish"))[-1]
    assert row.detail["outcome"] == "failed" and fake.token not in row.detail["message"]
    assert "***" in row.detail["message"]


async def test_one_publish_per_process_at_a_time(client_atlassian, atlassian_app, designed_process, architect,
                                                 wait_terminal):
    """A second publish waits for the lock and then updates the page; a queued one is not doubled."""
    c, fake = client_atlassian("datacenter")
    async with try_advisory_lock(atlassian_app.state.sessionmaker, f"confluence:{designed_process}") as got:
        assert got
        first = (await c.post(f"/processes/{designed_process}/confluence/publish", headers=architect)).json()
        await asyncio.sleep(0.6)
        assert (await c.get(f"/jobs/{first['job_id']}")).json()["status"] == "queued"  # waiting, not failed
        again = await c.post(f"/processes/{designed_process}/confluence/publish", headers=architect)
        assert again.status_code == 409 and "already queued" in again.json()["detail"]
        assert fake.page_count() == 0
    assert (await wait_terminal(c, first["job_id"]))["version"] == 1 and fake.page_count() == 1


def test_page_url_stays_on_the_configured_site():
    http = _Http(client=None, secrets=[], site="https://wiki.gmf.example")  # type: ignore[arg-type]
    on = {"_links": {"base": "https://wiki.gmf.example", "webui": "/spaces/G/pages/1"}}
    off = {"_links": {"base": "https://evil.example", "webui": "/spaces/G/pages/1"}}
    assert http.page_url(on, "https://wiki.gmf.example") == "https://wiki.gmf.example/spaces/G/pages/1"
    assert http.page_url(off, "https://wiki.gmf.example") == "https://wiki.gmf.example/spaces/G/pages/1"


async def test_disabled_connector_is_409_and_calls_nothing(client_atlassian, atlassian_app, designed_process,
                                                            architect):
    c, fake = client_atlassian("datacenter")
    atlassian_app.state.settings.connectors = ""  # the install default: off
    r = await c.post(f"/processes/{designed_process}/confluence/publish", headers=architect)
    assert r.status_code == 409 and r.json()["detail"] == "the Atlassian connector is not enabled"
    assert fake.requests == []
    assert (await _audits(c.app, "connector confluence publish"))[-1].detail["outcome"] == "refused"


async def test_link_routes_404(client_atlassian, designed_process):
    c, _ = client_atlassian("datacenter")
    assert (await c.get(f"/processes/{designed_process}/confluence")).status_code == 404
    assert (await c.get("/processes/nope/confluence")).status_code == 404
    assert (await c.post("/processes/nope/confluence/publish", headers=ARCHITECT)).status_code == 404


def test_atlassian_connector_auth_by_flavor(connectors_settings, fake_confluence_cloud):
    s = connectors_settings(confluence=fake_confluence_cloud)
    conn = get_connector("atlassian", settings=s, stored={"connectors": {"atlassian": {"enabled": True}}})
    assert isinstance(conn, AtlassianConnector)
    assert conn.auth_header()["Authorization"].startswith("Basic ")
