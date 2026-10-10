"""Tests for domain discovery, the estate graph and the per-workflow I/O card (Task 7)."""
import json
from pathlib import Path

import pytest

from engine.domains import discover
from engine.domains import graph as graph_mod
from engine.domains.models import DomainCandidate, Evidence, Graph, IOCard
from engine.ir.models import Activity, Application, Process, ProcessCall, Starter
from engine.tibco_parser.scanner import scan_folder

REPO_ROOT = Path(__file__).resolve().parents[2]
GMF = REPO_ROOT / "samples" / "synthetic-gmf"
ESTATE = json.loads((GMF / "estate.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def apps():
    return scan_folder(GMF).applications


@pytest.fixture(scope="module")
def graph(apps):
    return graph_mod.build_graph(apps)


def _proc(apps, pid):
    for app in apps:
        for p in app.processes:
            if p.id == pid:
                return p, app
    raise KeyError(pid)


def _targets(g: Graph, src_kind, src_name, edge_kind, dst_kind=None):
    names = {n.id: n for n in g.nodes}
    src = next(n for n in g.nodes if n.kind == src_kind and n.name == src_name)
    out = []
    for e in g.edges:
        if e.src == src.id and e.kind == edge_kind:
            dst = names[e.dst]
            if dst_kind is None or dst.kind == dst_kind:
                out.append(dst.name)
    return sorted(out)


# ---------------------------------------------------------------- discovery
def test_three_domains_from_modules(apps):
    cands = discover.discover_domains(apps)
    assert all(isinstance(c, DomainCandidate) for c in cands)
    names = {c.suggested_name for c in cands}
    assert names == {"Credit Origination", "Dealer Funding", "Loan Servicing", "Legacy Address"}
    by_name = {c.suggested_name: c for c in cands}
    assert set(by_name["Credit Origination"].process_ids) == {
        "creditorigination.CreditApplicationIntake", "creditorigination.BureauOrchestrator",
        "creditorigination.DecisionEngine", "creditorigination.ValidateVIN", "creditorigination.AuditLog"}
    assert set(by_name["Dealer Funding"].process_ids) == {
        "dealerfunding.DealerFundingRequest", "dealerfunding.FundingStatusLookup"}
    assert set(by_name["Loan Servicing"].process_ids) == {
        "loanservicing.PaymentPosting", "loanservicing.AccountLookup", "loanservicing.NightlyDelinquencyScan"}
    assert by_name["Legacy Address"].process_ids == ["LegacyAddressStandardize"]
    # every process lands in exactly one candidate
    all_ids = [pid for c in cands for pid in c.process_ids]
    assert len(all_ids) == len(set(all_ids)) == 11
    assert len({c.id for c in cands}) == 4


def test_evidence_names_module_tables_and_calls(apps):
    by_name = {c.suggested_name: c for c in discover.discover_domains(apps)}
    credit = by_name["Credit Origination"]
    kinds = {e.kind for e in credit.evidence}
    assert {"module", "call"} <= kinds
    assert all(isinstance(e, Evidence) for e in credit.evidence)
    assert any(e.kind == "module" and "CreditOrigination" in e.text for e in credit.evidence)
    assert any(e.kind == "call" and "BureauOrchestrator" in e.text for e in credit.evidence)
    funding = by_name["Dealer Funding"]
    assert any(e.kind == "table" and "funding" in e.text for e in funding.evidence)
    servicing = by_name["Loan Servicing"]
    assert any(e.kind == "table" and "loan_account" in e.text for e in servicing.evidence)


def _mk_app(app_id, procs):
    return Application(id=app_id, name=app_id, dialect="bw6", modules=sorted({p.module for p in procs}),
                       processes=procs)


def _sql_activity(aid, sql):
    return Activity(id=aid, name=aid, type="jdbc.query", native_type="x", config={"sql": sql})


def _mk_proc(pid, module, activities=(), calls=(), app="a"):
    return Process(id=pid, application=app, module=module, name=pid, dialect="bw6", source_path=f"{pid}.bwp",
                   activities=list(activities), calls=[ProcessCall(activity_id="c", target_process=t) for t in calls])


def test_shared_table_joins_modules():
    p1 = _mk_proc("m1.A", "ModOne", [_sql_activity("q", "select * from s.shared_t")])
    p2 = _mk_proc("m2.B", "ModTwo", [_sql_activity("q", "update shared_t set x = 1")])
    p3 = _mk_proc("m3.C", "ModThree", [_sql_activity("q", "select * from other_t")])
    cands = discover.discover_domains([_mk_app("a", [p1, p2, p3])])
    groups = sorted(sorted(c.process_ids) for c in cands)
    assert groups == [["m1.A", "m2.B"], ["m3.C"]]
    joined = next(c for c in cands if "m1.A" in c.process_ids)
    assert any(e.kind == "table" and "shared_t" in e.text for e in joined.evidence)


def test_call_edge_joins_modules():
    p1 = _mk_proc("m1.A", "ModOne", calls=["m2.B"])
    p2 = _mk_proc("m2.B", "ModTwo")
    cands = discover.discover_domains([_mk_app("a", [p1, p2])])
    assert len(cands) == 1
    assert any(e.kind == "call" and "m2.B" in e.text for e in cands[0].evidence)


def test_shared_queue_joins_modules():
    def jms(aid, typ, dest, style):
        return Activity(id=aid, name=aid, type=typ, native_type="x",
                        config={"destination": dest, "messaging_style": style})
    p1 = _mk_proc("m1.A", "ModOne", [jms("s", "jms.send", "q.x", "Queue")])
    p2 = _mk_proc("m2.B", "ModTwo", [jms("r", "jms.receive", "q.x", "Queue")])
    cands = discover.discover_domains([_mk_app("a", [p1, p2])])
    assert len(cands) == 1
    assert any(e.kind == "queue" and "q.x" in e.text for e in cands[0].evidence)


@pytest.mark.parametrize("module,expected", [
    ("CreditOrigination", "Credit Origination"), ("LegacyAddress", "Legacy Address"),
    ("GMFLoanServicing", "GMF Loan Servicing"), ("dealer_funding", "Dealer Funding"), ("Core", "Core")])
def test_suggested_name(module, expected):
    assert discover.suggest_name(module) == expected


# ---------------------------------------------------------------- sql tables
@pytest.mark.parametrize("sql,reads,writes", [
    ("select a from gmf.t1 x join t2 y on x.id = y.id where 1=1", {"t1", "t2"}, set()),
    ("insert into gmf.audit_log (a) values (?)", set(), {"audit_log"}),
    ("update gmf.loan_account set a = ? where b = ?", set(), {"loan_account"}),
    ("delete from t3 where a = 1", set(), {"t3"}),
    ("insert into t4 select * from t5", {"t5"}, {"t4"}),
    ("select * from (select 1) z", set(), set()),
    ("select a from t6 for update", {"t6"}, set()),
    ("-- from commented\nselect 'from literal' as x from t7", {"t7"}, set()),
    ('select * from "S"."T8"', {"t8"}, set()),
])
def test_sql_tables(sql, reads, writes):
    refs = discover.sql_tables(sql)
    assert {r.name for r in refs if r.operation == "read"} == reads
    assert {r.name for r in refs if r.operation != "read"} == writes


def test_sql_table_schema_attr():
    refs = discover.sql_tables("insert into gmf.audit_log (a) values (?)")
    assert refs[0].name == "audit_log" and refs[0].schema == "gmf" and refs[0].operation == "insert"


# --------------------------------------------------------------------- graph
def test_graph_edges(graph, apps):
    assert isinstance(graph, Graph)
    assert _targets(graph, "process", "dealerfunding.DealerFundingRequest", "writes", "table") == ["funding"]
    assert _targets(graph, "process", "dealerfunding.DealerFundingRequest", "publishes") == ["gmf.funding.events"]
    assert _targets(graph, "process", "dealerfunding.DealerFundingRequest", "consumes") == [
        "gmf.dealer.funding.request"]
    assert "creditorigination.BureauOrchestrator" in _targets(
        graph, "process", "creditorigination.CreditApplicationIntake", "calls", "process")
    kinds = {n.kind for n in graph.nodes}
    assert {"domain", "application", "process", "table", "queue", "topic", "endpoint", "resource"} <= kinds
    assert "activity" not in kinds
    assert {e.kind for e in graph.edges} >= {"contains", "calls", "reads", "writes", "publishes", "consumes",
                                             "exposes", "uses"}
    ids = [n.id for n in graph.nodes]
    assert len(ids) == len(set(ids))
    node_ids = set(ids)
    assert all(e.src in node_ids and e.dst in node_ids for e in graph.edges)


def test_topic_and_queue_kinds(graph):
    topic = next(n for n in graph.nodes if n.name == "gmf.funding.events")
    assert topic.kind == "topic"
    queue = next(n for n in graph.nodes if n.name == "gmf.collections.queue")
    assert queue.kind == "queue"
    assert _targets(graph, "process", "loanservicing.NightlyDelinquencyScan", "publishes", "queue") == [
        "gmf.collections.queue"]


def test_graph_matches_estate_oracle(graph):
    for oracle in ESTATE["processes"]:
        pid = oracle["id"]
        assert _targets(graph, "process", pid, "reads", "table") == sorted(oracle["tables_read"]), pid
        assert _targets(graph, "process", pid, "writes", "table") == sorted(oracle["tables_written"]), pid
        assert _targets(graph, "process", pid, "consumes", "queue") == sorted(oracle["queues_in"]), pid
        assert _targets(graph, "process", pid, "publishes", "queue") == sorted(oracle["queues_out"]), pid
        assert _targets(graph, "process", pid, "publishes", "topic") == sorted(oracle["topics_out"]), pid
        assert _targets(graph, "process", pid, "calls", "process") == sorted(oracle["calls"]), pid


def test_graph_domain_and_application_containment(graph):
    assert _targets(graph, "domain", "Dealer Funding", "contains", "process") == [
        "dealerfunding.DealerFundingRequest", "dealerfunding.FundingStatusLookup"]
    assert len(_targets(graph, "application", "gmf.credit", "contains", "process")) == 10
    assert _targets(graph, "process", "creditorigination.CreditApplicationIntake", "exposes") == ["POST /applications"]
    uses = _targets(graph, "process", "dealerfunding.DealerFundingRequest", "uses", "resource")
    assert any(u.endswith("GMFFundingDB") for u in uses) and any(u.endswith("GMFEMSConnection") for u in uses)


def test_graph_with_activities(apps):
    g = graph_mod.build_graph(apps, include_activities=True)
    acts = [n for n in g.nodes if n.kind == "activity"]
    assert len(acts) > 20
    assert any(e.kind == "contains" and e.src == "process:dealerfunding.DealerFundingRequest"
               and e.dst.startswith("activity:") for e in g.edges)


# ----------------------------------------------------------------- I/O cards
def _field_names(tree):
    return {c["name"] for c in tree.get("children", [])}


def test_io_card_intake(apps, graph):
    p, app = _proc(apps, "creditorigination.CreditApplicationIntake")
    card = graph_mod.io_card(p, app, graph)
    assert isinstance(card, IOCard)
    assert card.process_id == p.id
    assert card.trigger == {"kind": "rest", "verb": "POST", "path": "/applications"}
    assert {"decision", "tier", "apr"} <= _field_names(card.response_contract)
    assert {"applicationId", "applicant", "vehicle", "requestedAmount"} <= _field_names(card.request_contract)
    applicant = next(c for c in card.request_contract["children"] if c["name"] == "applicant")
    assert {"ssn", "address"} <= _field_names(applicant)
    address = next(c for c in applicant["children"] if c["name"] == "address")
    assert "postalCode" in _field_names(address)
    apr = next(c for c in card.response_contract["children"] if c["name"] == "apr")
    assert apr["type"] == "number" and apr["children"] == []
    assert set(card.subprocesses) == {"creditorigination.AuditLog", "creditorigination.ValidateVIN",
                                      "creditorigination.BureauOrchestrator", "creditorigination.DecisionEngine"}
    assert "DuplicateApplicationFault" in card.faults
    reads = [s for s in card.side_effects if s["kind"] == "table_read"]
    assert [s["table"] for s in reads] == ["credit_application"]
    assert "select" in reads[0]["sql"].lower()
    assert {s["process"] for s in card.side_effects if s["kind"] == "subprocess"} == set(card.subprocesses)


def test_io_card_jms_consumer(apps, graph):
    p, app = _proc(apps, "dealerfunding.DealerFundingRequest")
    card = graph_mod.io_card(p, app, graph)
    assert card.trigger["kind"] == "jms"
    assert card.trigger["queue"] == "gmf.dealer.funding.request"
    assert card.trigger["selector"] == "MsgType = 'FUNDING_REQUEST'"
    assert {"dealId", "fundingAmount"} <= _field_names(card.request_contract)
    assert card.response_contract == {}
    writes = [s for s in card.side_effects if s["kind"] == "table_write"]
    assert [(s["table"], s["operation"]) for s in writes] == [("funding", "insert")]
    pubs = [s for s in card.side_effects if s["kind"] == "publish"]
    assert {(s["destination"], s["messaging_style"]) for s in pubs} == {("gmf.funding.events", "Topic")}
    assert "DealerInactiveFault" in card.faults


def test_io_card_timer_and_queue_send(apps, graph):
    p, app = _proc(apps, "loanservicing.NightlyDelinquencyScan")
    card = graph_mod.io_card(p, app, graph)
    assert card.trigger["kind"] == "timer"
    assert card.trigger["start"] == "02:00" and card.trigger["interval"] == "1 Day"
    assert card.request_contract == {}
    sends = [s for s in card.side_effects if s["kind"] == "publish"]
    assert [(s["destination"], s["messaging_style"]) for s in sends] == [("gmf.collections.queue", "Queue")]


def test_io_card_rest_get_uses_descriptor(apps, graph):
    p, app = _proc(apps, "loanservicing.AccountLookup")
    card = graph_mod.io_card(p, app, graph)
    assert card.trigger == {"kind": "rest", "verb": "GET", "path": "/accounts/{accountNumber}"}
    assert _field_names(card.request_contract) == {"accountNumber"}
    assert {"accountNumber", "borrowerName"} <= _field_names(card.response_contract)


def test_io_card_service_calls_and_subprocess_card(apps, graph):
    p, app = _proc(apps, "creditorigination.BureauOrchestrator")
    card = graph_mod.io_card(p, app, graph)
    assert card.trigger == {"kind": "subprocess"}
    calls = [s for s in card.side_effects if s["kind"] == "service_call"]
    assert {s["resource"] for s in calls} == {"CreditOrigination.creditorigination.EquifaxClient",
                                              "CreditOrigination.creditorigination.TransUnionClient"}
    assert "BureauUnavailableFault" in card.faults


def test_io_card_bw5_http(apps, graph):
    p, app = _proc(apps, "LegacyAddressStandardize")
    card = graph_mod.io_card(p, app, graph)
    assert card.trigger["kind"] == "http"
    assert {"line1", "city"} <= _field_names(card.request_contract)
    assert "line1" in _field_names(card.response_contract) or "city" in _field_names(card.response_contract)


def test_io_card_all_processes_build(apps, graph):
    for app in apps:
        for p in app.processes:
            card = graph_mod.io_card(p, app, graph)
            assert card.process_id == p.id
            assert card.model_dump_json()


def test_xsd_tree_handles_named_types_repetition_and_extension():
    from engine.ir.models import Contract
    xsd = """<?xml version="1.0" encoding="UTF-8"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:t="urn:t">
  <xs:complexType name="Base"><xs:sequence><xs:element name="id" type="xs:string"/></xs:sequence></xs:complexType>
  <xs:complexType name="Line"><xs:complexContent><xs:extension base="t:Base"><xs:sequence>
    <xs:element name="qty" type="xs:int" minOccurs="0"/></xs:sequence></xs:extension></xs:complexContent></xs:complexType>
  <xs:element name="Order"><xs:complexType><xs:sequence>
    <xs:element name="line" type="t:Line" maxOccurs="unbounded"/></xs:sequence></xs:complexType></xs:element>
</xs:schema>"""
    contract = Contract(id="S/o.xsd", kind="xsd", path="o.xsd", root_elements=["Order"], text=xsd)
    proc = Process(id="p.OrderIn", application="a", module="M", name="OrderIn", dialect="bw6", source_path="x",
                   contracts_used=["S/o.xsd"], starter=Starter(activity_id="r", kind="jms"))
    app = Application(id="a", name="a", dialect="bw6", processes=[proc], contracts=[contract])
    req, _ = graph_mod._contracts(proc, app)
    line = req["children"][0]
    assert req["name"] == "Order" and line["repeated"] is True
    assert [c["name"] for c in line["children"]] == ["id", "qty"]
    assert line["children"][1]["required"] is False
