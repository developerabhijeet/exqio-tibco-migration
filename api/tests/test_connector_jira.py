"""Jira connector (Task 5): issues from findings, open questions and parity differences, Data Center
and Cloud, against the Jira fakes (``client_atlassian``). The issues job runs in the app's own
worker."""
from __future__ import annotations

import asyncio

import httpx
import pytest
from sqlalchemy import select

from api.app.connectors.config import STORED_KEY
from api.app.connectors.jira import (
    IssueItem,
    IssueSource,
    JiraConnector,
    issue_fields,
    ref_label,
    wiki_escape,
)
from api.app.connectors.registry import AtlassianConnector, connector_for
from api.app.models import (
    Application,
    AuditLog,
    Build,
    DesignRecord,
    FieldDiff,
    Finding,
    Process,
    Project,
    Scenario,
    ScenarioRun,
    Setting,
    Upload,
)
from api.app.models.connector import ConnectorLink
from api.app.services.jira_issues import ISSUES_KIND

SME = {"X-User": "sme.vicky"}
ADMIN = {"X-User": "ops.sam"}
IR_ID = "dealerfunding.DealerFundingRequest"
CITATION = {"activity_id": "CheckDealer", "kind": "sql", "text": "SELECT * FROM dealer WHERE id = ?"}


# ------------------------------------------------------------------ pure helpers


def proc_with_text(text: str) -> IssueSource:
    return IssueSource(project_id="P1", process_id="proc-1", ir_id=IR_ID, name="DealerFundingRequest",
                       findings={"F1": {"finding_key": "F1", "severity": "high", "text": text, "decision": None,
                                        "source": "static", "citations": [CITATION]}},
                       questions=[{"question": "Reject inactive dealers?", "recommendation": "Yes"}],
                       diffs={"d1": {"scenario": "happy path", "field": "funding.amount", "kind": "field",
                                     "expected": 100, "actual": 99, "classification": "defect"}})


def app() -> str:
    return "gmf.credit"


# ------------------------------------------------------------------ app fixtures


@pytest.fixture
async def jira_app(atlassian_app):
    """``atlassian_app`` with the Jira project key ``GMF`` set in the admin layer."""
    async with atlassian_app.state.sessionmaker() as s:
        row = await s.get(Setting, STORED_KEY)
        row.value = {**row.value, "atlassian": {**row.value["atlassian"], "project_key": "GMF"}}
        await s.commit()
    return atlassian_app


async def _seed(app_) -> str:
    async with app_.state.sessionmaker() as s:
        project = Project(name="credit")
        s.add(project)
        await s.flush()
        upload = Upload(project_id=project.id, source_kind="path", source_name="x", root_dir="/tmp")
        s.add(upload)
        await s.flush()
        application = Application(project_id=project.id, upload_id=upload.id, app_key="gmf.credit",
                                  name="gmf.credit", dialect="bw6", ir={})
        s.add(application)
        await s.flush()
        proc = Process(project_id=project.id, application_id=application.id, upload_id=upload.id, ir_id=IR_ID,
                       name="DealerFundingRequest", dialect="bw6", source_path="Processes/DFR.bwp", ir={},
                       spec_hash="s" * 64)
        s.add(proc)
        await s.flush()
        s.add_all([
            Finding(process_id=proc.id, finding_key="F1", severity="high", source="static", citations=[CITATION],
                    text="Inactive dealers are funded.\nThe check runs after the deal lookup."),
            Finding(process_id=proc.id, finding_key="F2", severity="low", source="ai", text="Kept quirk",
                    decision="keep", decided_by="sme.vicky"),
            Finding(process_id=proc.id, finding_key="F3", severity="medium", source="ai", text="Fix the rounding",
                    decision="fix", decided_by="sme.vicky"),
        ])
        s.add(DesignRecord(process_id=proc.id, version=1, spec_hash="s" * 64, design_hash="d" * 64, record={
            "open_questions": [
                {"question": "Should inactive dealers be rejected before the deal lookup?",
                 "recommendation": "Yes, as TIBCO does"},
                {"question": "Answered one", "recommendation": "No", "decision": "answered", "answer": "No"}]}))
        build = Build(process_id=proc.id, status="passed", build_hash="c" * 64)
        scenario = Scenario(process_id=proc.id, key="S1", name="happy path", data={})
        s.add_all([build, scenario])
        await s.flush()
        run = ScenarioRun(build_id=build.id, scenario_id=scenario.id, job_id="j1", status="differs", report={})
        s.add(run)
        await s.flush()
        s.add_all([FieldDiff(id="diff-defect", run_id=run.id, field="funding.amount", expected=100, actual=99,
                             classification="defect"),
                   FieldDiff(id="diff-intended", run_id=run.id, field="funding.note", expected="a", actual="b",
                             classification="intended")])
        await s.commit()
        return proc.id


@pytest.fixture
async def process_with_findings(jira_app) -> str:
    return await _seed(jira_app)


@pytest.fixture
async def process_with_kept_finding(jira_app) -> str:
    return await _seed(jira_app)


@pytest.fixture
def sme() -> dict[str, str]:
    return SME


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


async def _issues(c: httpx.AsyncClient, pid: str, items: list[dict], wait, *, create_again: bool = False,
                  expect: str = "done") -> dict:
    r = await c.post(f"/processes/{pid}/jira/issues", json={"items": items, "create_again": create_again},
                     headers=SME)
    assert r.status_code == 202, r.text
    return await wait(c, r.json()["job_id"], expect)


@pytest.fixture
async def jira_linked_finding(client_atlassian, process_with_findings, wait_terminal) -> str:
    c, fake = client_atlassian("datacenter")
    out = await _issues(c, process_with_findings, [{"type": "finding", "ref": "F1"}], wait_terminal)
    assert out["created"] == ["GMF-1"] and fake.jira.issue_count() == 1
    return process_with_findings


async def _audits(app_, action: str) -> list[AuditLog]:
    async with app_.state.sessionmaker() as s:
        return list((await s.execute(select(AuditLog).where(AuditLog.action == action)
                                     .order_by(AuditLog.id))).scalars())


async def _links(app_, pid: str) -> list[ConnectorLink]:
    async with app_.state.sessionmaker() as s:
        return list((await s.execute(select(ConnectorLink).where(ConnectorLink.process_id == pid)
                                     .order_by(ConnectorLink.local_ref))).scalars())


# ------------------------------------------------------------------ brief tests


async def test_create_then_republish_updates(client_atlassian, process_with_findings, sme, wait_terminal):
    c, _ = client_atlassian("datacenter"); body = {"items": [{"type": "finding", "ref": "F1"}, {"type": "question", "ref": "0"}]}
    first = await wait_terminal(c, (await c.post(f"/processes/{process_with_findings}/jira/issues", json=body, headers=sme)).json()["job_id"])
    second = await wait_terminal(c, (await c.post(f"/processes/{process_with_findings}/jira/issues", json=body, headers=sme)).json()["job_id"])
    assert first["created"] == ["GMF-1", "GMF-2"] and second["updated"] == ["GMF-1", "GMF-2"] and second["created"] == []


async def test_deleted_issue_is_stale_not_duplicated(client_atlassian, jira_linked_finding, sme, wait_terminal):  # Review Focus 4
    c, fake = client_atlassian("datacenter"); fake.jira.delete("GMF-1")
    out = await wait_terminal(c, (await c.post(f"/processes/{jira_linked_finding}/jira/issues", json={"items": [{"type": "finding", "ref": "F1"}]}, headers=sme)).json()["job_id"])
    assert out["stale"] == ["finding:F1"] and out["created"] == [] and fake.jira.issue_count() == 0


def test_description_is_escaped_and_has_back_reference():
    f = issue_fields(IssueItem(type="finding", ref="F1"), proc_with_text("{code}x[link|http://a]"), app(), project_key="GMF", issue_type="Task", labels=["exiqo"])
    assert "{code}" not in f["fields"]["description"] and f["fields"]["description"].endswith("ExiQO: project P1, process dealerfunding.DealerFundingRequest, finding F1")


async def test_kept_finding_is_refused(client_atlassian, process_with_kept_finding, sme):
    c, _ = client_atlassian("datacenter")
    r = await c.post(f"/processes/{process_with_kept_finding}/jira/issues", json={"items": [{"type": "finding", "ref": "F2"}]}, headers=sme)
    assert r.status_code == 422 and "is decided keep" in r.json()["detail"]


# ------------------------------------------------------------------ issue text


def test_wiki_escape_neutralises_every_markup_character():
    raw = "{noformat}[~admin] |a| *b* _c_ -d- +e+ ^f^ ~g~ !img.png! \\\\ end\\"
    out = wiki_escape(raw)
    for token in ("{noformat}", "[~admin]", "|a|", "*b*", "_c_", "-d-", "+e+", "^f^", "~g~", "!img.png!"):
        assert token not in out, token
    assert "\\\\" not in out.replace("&#92;", "")  # a literal backslash pair is not a wiki line break
    assert out.startswith("\\{noformat\\}") and out.endswith("end&#92;")


def test_fields_per_source_summary_labels_and_masking():
    src = proc_with_text("Password=hunter2 for borrower_ssn: 123-45-6789")
    f = issue_fields(IssueItem(type="finding", ref="F1"), src, app(), project_key="GMF", issue_type="Bug",
                     labels=["exiqo", "gmf team"])["fields"]
    assert f["project"] == {"key": "GMF"} and f["issuetype"] == {"name": "Bug"}
    assert f["summary"].startswith("[DealerFundingRequest] ") and "hunter2" not in f["summary"]
    assert "hunter2" not in f["description"] and "123-45-6789" not in f["description"]
    assert f["labels"] == ["exiqo", "gmf-team", "finding", "high", ref_label("proc-1", "finding:F1")]
    assert "CheckDealer" in f["description"] and "SELECT \\* FROM dealer" in f["description"]

    q = issue_fields(IssueItem(type="question", ref="0"), src, app(), project_key="GMF", issue_type="Task",
                     labels=["exiqo"])["fields"]
    assert q["summary"] == "[DealerFundingRequest] Open question: Reject inactive dealers?"
    assert q["labels"][:2] == ["exiqo", "open-question"] and "Yes" in q["description"]
    assert q["description"].endswith(f"process {IR_ID}, question 0")

    p = issue_fields(IssueItem(type="parity", ref="d1"), src, app(), project_key="GMF", issue_type="Task",
                     labels=["exiqo"])["fields"]
    assert p["summary"] == "[DealerFundingRequest] Parity: happy path funding.amount"
    assert p["labels"][:2] == ["exiqo", "parity"] and "99" in p["description"] and "100" in p["description"]


def test_summary_is_one_line_of_at_most_255_characters():
    src = proc_with_text("x" * 600 + "\nsecond line")
    s = issue_fields(IssueItem(type="finding", ref="F1"), src, app(), project_key="GMF", issue_type="Task",
                     labels=[])["fields"]["summary"]
    assert len(s) == 255 and "\n" not in s and s.endswith("…")


def test_ref_label_is_stable_and_a_valid_label():
    a = ref_label("proc-1", "finding:F1")
    assert a == ref_label("proc-1", "finding:F1") != ref_label("proc-2", "finding:F1")
    assert a.startswith("exiqo-ref-") and " " not in a


# ------------------------------------------------------------------ the job against the fakes


@pytest.mark.parametrize("flavor", ["datacenter", "cloud"])
async def test_wire_format(client_atlassian, flavor, process_with_findings, wait_terminal):
    c, fake = client_atlassian(flavor)
    out = await _issues(c, process_with_findings, [{"type": "parity", "ref": "diff-defect"},
                                                    {"type": "finding", "ref": "F3"}], wait_terminal)
    assert out == {"created": ["GMF-1", "GMF-2"], "updated": [], "stale": []}
    jira = fake.jira
    posts = [r for r in jira.requests if r.method == "POST"]
    assert [r.path for r in posts] == ["/rest/api/2/issue"] * 2
    first = posts[0].json["fields"]
    assert first["project"] == {"key": "GMF"} and first["issuetype"] == {"name": "Task"}
    assert first["summary"] == "[DealerFundingRequest] Parity: happy path funding.amount"
    auth = {r.headers.get("authorization", "").split(" ")[0] for r in jira.requests}
    assert auth == ({"Basic"} if flavor == "cloud" else {"Bearer"})
    search = "/rest/api/2/search/jql" if flavor == "cloud" else "/rest/api/2/search"
    assert {r.path for r in jira.requests if r.method == "GET"} == {search}  # the lost-answer check
    links = await _links(c.app, process_with_findings)
    assert [(lk.local_ref, lk.remote_id, lk.object_type, lk.kind) for lk in links] == [
        ("finding:F3", "GMF-2", "jira_issue", "atlassian"), ("parity:diff-defect", "GMF-1", "jira_issue", "atlassian")]
    assert links[0].remote_url == f"{jira.url}/browse/GMF-2"
    rows = await _audits(c.app, "connector jira create")
    assert [(r.detail["outcome"], r.detail["target"]) for r in rows] == [
        ("ok", f"{jira.url}/browse/GMF-1"), ("ok", f"{jira.url}/browse/GMF-2")]
    assert rows[0].actor == "sme.vicky" and rows[0].detail["local_ref"] == "parity:diff-defect"


async def test_republish_puts_summary_and_description_only(client_atlassian, jira_linked_finding, wait_terminal):
    c, fake = client_atlassian("datacenter")
    async with c.app.state.sessionmaker() as s:
        f = (await s.execute(select(Finding).where(Finding.finding_key == "F1"))).scalar_one()
        f.text = "Inactive dealers are funded {quietly}."
        await s.commit()
    out = await _issues(c, jira_linked_finding, [{"type": "finding", "ref": "F1"}], wait_terminal)
    assert out == {"created": [], "updated": ["GMF-1"], "stale": []}
    put = [r for r in fake.jira.requests if r.method == "PUT"][-1]
    assert put.path == "/rest/api/2/issue/GMF-1" and set(put.json["fields"]) == {"summary", "description"}
    assert "\\{quietly\\}" in fake.jira.issue("GMF-1")["description"]
    rows = await _audits(c.app, "connector jira update")
    assert [r.detail["outcome"] for r in rows] == ["ok"]


async def test_stale_link_is_marked_then_created_again_only_when_asked(client_atlassian, jira_linked_finding,
                                                                        wait_terminal):
    c, fake = client_atlassian("datacenter")
    fake.jira.delete("GMF-1")
    await _issues(c, jira_linked_finding, [{"type": "finding", "ref": "F1"}], wait_terminal)
    (link,) = await _links(c.app, jira_linked_finding)
    assert link.stale
    before = len(fake.jira.requests)
    again = await _issues(c, jira_linked_finding, [{"type": "finding", "ref": "F1"}], wait_terminal)
    assert again == {"created": [], "updated": [], "stale": ["finding:F1"]}
    assert len(fake.jira.requests) == before  # a stale link is skipped without a request
    out = await _issues(c, jira_linked_finding, [{"type": "finding", "ref": "F1"}], wait_terminal,
                        create_again=True)
    assert out == {"created": ["GMF-2"], "updated": [], "stale": []} and fake.jira.issue_count() == 1
    (link,) = await _links(c.app, jira_linked_finding)
    assert not link.stale and link.remote_id == "GMF-2"
    update_rows = await _audits(c.app, "connector jira update")
    assert update_rows[0].detail["outcome"] == "failed" and "marked stale" in update_rows[0].detail["message"]


async def test_create_again_reuses_a_stale_issue_that_exists_again(client_atlassian, jira_linked_finding,
                                                                   wait_terminal):
    c, fake = client_atlassian("datacenter")
    async with c.app.state.sessionmaker() as s:
        (await s.execute(select(ConnectorLink))).scalar_one().stale = True
        await s.commit()
    out = await _issues(c, jira_linked_finding, [{"type": "finding", "ref": "F1"}], wait_terminal,
                        create_again=True)
    assert out == {"created": [], "updated": ["GMF-1"], "stale": []} and fake.jira.issue_count() == 1
    assert not (await _links(c.app, jira_linked_finding))[0].stale


async def test_lost_create_answer_is_found_by_its_label_not_created_twice(client_atlassian, process_with_findings,
                                                                          wait_terminal):
    c, fake = client_atlassian("cloud")
    fake.jira.lose_create_answers = 1  # Jira stored the issue, then the gateway answered 502
    out = await _issues(c, process_with_findings, [{"type": "finding", "ref": "F1"}], wait_terminal)
    assert out["created"] == ["GMF-1"] and fake.jira.issue_count() == 1
    assert len([r for r in fake.jira.requests if r.method == "POST"]) == 1  # never re-posted
    assert (await _links(c.app, process_with_findings))[0].remote_id == "GMF-1"


async def test_lost_create_answer_not_yet_searchable_fails_then_next_run_adopts(client_atlassian,
                                                                                process_with_findings,
                                                                                wait_terminal):
    c, fake = client_atlassian("datacenter")
    fake.jira.lose_create_answers, fake.jira.search_lag = 1, True
    job = await _issues(c, process_with_findings, [{"type": "finding", "ref": "F1"}], wait_terminal,
                        expect="failed")
    assert "may have been created" in job["error"] and fake.jira.issue_count() == 1
    assert await _links(c.app, process_with_findings) == []
    rows = await _audits(c.app, "connector jira create")
    assert rows[-1].detail["outcome"] == "failed" and "atlassian-fake-token" not in str(rows[-1].detail)
    fake.jira.search_lag = False
    out = await _issues(c, process_with_findings, [{"type": "finding", "ref": "F1"}], wait_terminal)
    assert out["created"] == ["GMF-1"] and fake.jira.issue_count() == 1
    assert len([r for r in fake.jira.requests if r.method == "POST"]) == 1


async def test_rejected_create_fails_the_job_and_is_audited(client_atlassian, jira_app, process_with_findings,
                                                            wait_terminal):
    c, fake = client_atlassian("datacenter")
    async with jira_app.state.sessionmaker() as s:
        row = await s.get(Setting, STORED_KEY)
        row.value = {**row.value, "atlassian": {**row.value["atlassian"], "project_key": "OTHER"}}
        await s.commit()
    job = await _issues(c, process_with_findings, [{"type": "finding", "ref": "F1"}], wait_terminal,
                        expect="failed")
    assert "HTTP 400" in job["error"] and fake.jira.issue_count() == 0
    assert len([r for r in fake.jira.requests if r.method == "POST"]) == 1  # a 400 is not retried
    rows = await _audits(c.app, "connector jira create")
    assert [r.detail["outcome"] for r in rows] == ["failed"] and rows[0].detail["local_ref"] == "finding:F1"
    assert "atlassian-fake-token" not in str(rows[0].detail)


async def test_connector_off_after_the_202_fails_the_job_with_an_audit_row(client_atlassian, jira_app,
                                                                          process_with_findings, wait_terminal):
    c, fake = client_atlassian("datacenter")
    async with jira_app.state.sessionmaker() as s:  # turned off once the request was accepted
        row = await s.get(Setting, STORED_KEY)
        row.value = {**row.value, "atlassian": {**row.value["atlassian"], "enabled": False}}
        await s.commit()
    job_id = await jira_app.state.runner.submit(ISSUES_KIND, {
        "process_id": process_with_findings, "items": [{"type": "finding", "ref": "F1"}], "create_again": False,
        "actor": "sme.vicky"})
    job = await wait_terminal(c, job_id, expect="failed")
    assert "not enabled" in job["error"] and fake.jira.requests == []
    rows = await _audits(c.app, "connector jira create")
    assert [(r.detail["outcome"], r.process_id) for r in rows] == [("refused", process_with_findings)]


async def test_item_decided_after_the_202_is_refused_by_the_job(client_atlassian, jira_app, process_with_findings,
                                                                wait_terminal):
    c, fake = client_atlassian("datacenter")
    job_id = await jira_app.state.runner.submit(ISSUES_KIND, {
        "process_id": process_with_findings, "items": [{"type": "finding", "ref": "F2"}], "create_again": False,
        "actor": "sme.vicky"})
    job = await wait_terminal(c, job_id, expect="failed")
    assert "is decided keep" in job["error"] and fake.jira.requests == []
    assert (await _audits(c.app, "connector jira create"))[-1].detail["outcome"] == "refused"


async def test_concurrent_jobs_do_not_duplicate(client_atlassian, jira_app, process_with_findings, wait_terminal):
    c, fake = client_atlassian("datacenter")
    items = [{"type": "finding", "ref": "F1"}, {"type": "question", "ref": "0"}]
    ids = [await jira_app.state.runner.submit(ISSUES_KIND, {  # no dedupe key: two jobs really run
        "process_id": process_with_findings, "items": items, "create_again": False, "actor": "sme.vicky"})
        for _ in range(3)]
    results = [await wait_terminal(c, i) for i in ids]
    assert fake.jira.issue_count() == 2
    assert sorted(k for r in results for k in r["created"]) == ["GMF-1", "GMF-2"]


async def test_same_request_twice_while_queued_is_409(client_atlassian, process_with_findings, wait_terminal):
    c, fake = client_atlassian("datacenter")
    body = {"items": [{"type": "finding", "ref": "F1"}]}
    statuses = [(await c.post(f"/processes/{process_with_findings}/jira/issues", json=body, headers=SME))
                for _ in range(2)]
    assert statuses[0].status_code == 202
    assert statuses[1].status_code in (202, 409)  # 409 while the first is still queued
    if statuses[1].status_code == 409:
        assert statuses[1].json()["detail"] == "the same issues are already queued for this process"
    for r in statuses:
        if r.status_code == 202:
            await wait_terminal(c, r.json()["job_id"])
    assert fake.jira.issue_count() == 1


async def test_duplicate_items_in_one_request_make_one_issue(client_atlassian, process_with_findings, wait_terminal):
    c, fake = client_atlassian("datacenter")
    out = await _issues(c, process_with_findings, [{"type": "finding", "ref": "F1"}] * 3, wait_terminal)
    assert out == {"created": ["GMF-1"], "updated": [], "stale": []} and fake.jira.issue_count() == 1


@pytest.mark.parametrize("flavor", ["datacenter", "cloud"])
async def test_connection_test_covers_jira(client_atlassian, jira_app, flavor):
    c, _ = client_atlassian(flavor)
    r = await c.post("/connectors/atlassian/test", headers=ADMIN)
    body = r.json()
    assert r.status_code == 200 and body["ok"] and body["account"] == "ExiQO bot", r.text
    assert "Confluence" in body["message"] and "Jira" in body["message"] and "project GMF" in body["message"]


async def test_jira_connector_calls(client_atlassian, jira_app):
    _, fake = client_atlassian("datacenter")
    async with jira_app.state.sessionmaker() as s:
        atl = await connector_for(s, jira_app.state.settings, "atlassian")
    assert isinstance(atl, AtlassianConnector)
    jira = atl.jira
    assert isinstance(jira, JiraConnector)
    fields = issue_fields(IssueItem(type="question", ref="0"), proc_with_text("t"), app(), project_key="GMF",
                          issue_type="Task", labels=["exiqo"])
    key, url = await jira.create(fields)
    assert key == "GMF-1" and url == f"{fake.jira.url}/browse/GMF-1"
    assert await jira.exists("GMF-1") and not await jira.exists("GMF-404")
    await jira.update("GMF-1", {"fields": {"summary": "changed"}})
    assert fake.jira.issue("GMF-1")["summary"] == "changed"
    assert await jira.find_by_label("GMF", ref_label("proc-1", "question:0")) == (key, url)
    assert await jira.find_by_label("GMF", "exiqo-ref-none") is None
    test = await jira.test()
    assert test.ok and test.account == "ExiQO bot"


# ------------------------------------------------------------------ the route


@pytest.mark.parametrize(("items", "detail"), [
    ([{"type": "finding", "ref": "F9"}], "finding F9 not found"),
    ([{"type": "question", "ref": "1"}], "open question 1 is answered: nothing to track"),
    ([{"type": "question", "ref": "7"}], "open question 7 not found"),
    ([{"type": "question", "ref": "x"}], "open question x not found"),
    ([{"type": "parity", "ref": "diff-intended"}], "parity difference diff-intended is intended: nothing to track"),
    ([{"type": "parity", "ref": "nope"}], "parity difference nope is not in the latest verify run"),
])
async def test_items_are_checked_before_queueing(client_atlassian, process_with_findings, items, detail):
    c, fake = client_atlassian("datacenter")
    r = await c.post(f"/processes/{process_with_findings}/jira/issues", json={"items": items}, headers=SME)
    assert r.status_code == 422 and r.json()["detail"] == detail, r.text
    assert fake.jira.requests == []


async def test_body_limits(client_atlassian, process_with_findings):
    c, _ = client_atlassian("datacenter")
    for items in ([], [{"type": "finding", "ref": "F1"}] * 51, [{"type": "bug", "ref": "F1"}]):
        r = await c.post(f"/processes/{process_with_findings}/jira/issues", json={"items": items}, headers=SME)
        assert r.status_code == 422, r.text


async def test_roles(client_atlassian, process_with_findings, wait_terminal):
    c, _ = client_atlassian("datacenter")
    refs = iter([("finding", "F1"), ("finding", "F3"), ("question", "0"), ("parity", "diff-defect")])
    for user, status in (("viewer.val", 403), ("engineer.ravi", 403), ("architect.lee", 202), ("qa.priya", 202),
                         ("sme.vicky", 202), ("ops.sam", 202)):
        kind, ref = next(refs) if status == 202 else ("finding", "F1")  # one request each: none deduped
        r = await c.post(f"/processes/{process_with_findings}/jira/issues",
                         json={"items": [{"type": kind, "ref": ref}]}, headers={"X-User": user})
        assert r.status_code == status, (user, r.text)
        if status == 403:
            assert r.json()["detail"] in ("viewer is read only", "this needs one of: architect, sme, qa, admin")
        else:
            await wait_terminal(c, r.json()["job_id"])


async def test_disabled_connector_is_409_and_calls_nothing(client_atlassian, jira_app, process_with_findings):
    c, fake = client_atlassian("datacenter")
    jira_app.state.settings.connectors = ""
    r = await c.post(f"/processes/{process_with_findings}/jira/issues",
                     json={"items": [{"type": "finding", "ref": "F1"}]}, headers=SME)
    assert r.status_code == 409 and r.json()["detail"] == "the Atlassian connector is not enabled"
    assert fake.jira.requests == []
    assert (await _audits(c.app, "connector jira create"))[-1].detail["outcome"] == "refused"


async def test_no_project_key_is_409(client_atlassian, atlassian_app):
    c, fake = client_atlassian("datacenter")
    pid = await _seed(atlassian_app)  # atlassian_app alone: no project key
    r = await c.post(f"/processes/{pid}/jira/issues", json={"items": [{"type": "finding", "ref": "F1"}]},
                     headers=SME)
    assert r.status_code == 409 and r.json()["detail"] == "set the Jira project key in Settings → Connectors"
    assert fake.jira.requests == []


async def test_unknown_process_is_404(client_atlassian):
    c, _ = client_atlassian("datacenter")
    r = await c.post("/processes/nope/jira/issues", json={"items": [{"type": "finding", "ref": "F1"}]}, headers=SME)
    assert r.status_code == 404
