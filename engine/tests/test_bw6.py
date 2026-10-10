"""Tests for the BW6 .bwp parser and the completeness collector (Task 5).

The synthetic estate oracle (samples/synthetic-gmf/estate.json) is the source of
truth for ids, triggers, calls and messaging destinations.
"""
import hashlib
import json
import shutil
from pathlib import Path

import pytest
from lxml import etree

from engine.tibco_parser import bw6, resources
from engine.tibco_parser.completeness import CompletenessCollector, UnknownType

REPO_ROOT = Path(__file__).resolve().parents[2]
GMF = REPO_ROOT / "samples" / "synthetic-gmf"
PUBLIC = REPO_ROOT / "samples" / "public"
ESTATE = json.loads((GMF / "estate.json").read_text(encoding="utf-8"))
ORACLE = {p["id"]: p for p in ESTATE["processes"] if p["file"].endswith(".bwp")}

SYNTHETIC_BWP = sorted(GMF.glob("*/Processes/**/*.bwp"))


def _module(path: Path) -> str:
    return path.relative_to(GMF).parts[0]


def _parse(rel: str, collector: CompletenessCollector | None = None):
    path = GMF / rel
    collector = collector or CompletenessCollector()
    return bw6.parse_bwp(path, _module(path), "gmf.credit", collector), collector


def _by_id(items):
    return {i.id: i for i in items}


def _unique_public_bwp() -> list[Path]:
    seen: dict[str, Path] = {}
    for p in sorted(PUBLIC.glob("**/*.bwp")):
        digest = hashlib.sha256(p.read_bytes()).hexdigest()
        # keep the module copy over a packaged .application/ copy
        if digest not in seen or ".application/" in seen[digest].as_posix():
            seen[digest] = p
    return sorted(seen.values())


PUBLIC_BWP = _unique_public_bwp()


# ------------------------------------------------------------- synthetic estate
def test_parse_all_synthetic_bwp_zero_unknown():
    collector = CompletenessCollector()
    procs = [bw6.parse_bwp(p, _module(p), "gmf.credit", collector) for p in SYNTHETIC_BWP]
    report = collector.report()
    assert len(procs) == 10
    assert report.failures == []
    assert report.unknown_activity_types == []
    assert report.unknown_elements == []
    assert report.ok
    assert report.files_found == report.files_parsed == 10
    assert {p.id for p in procs} == set(ORACLE)
    for proc in procs:
        assert all(a.type != "unknown" for a in proc.activities), proc.id
        assert proc.unknown_elements == []
        assert proc.dialect == "bw6"
        assert proc.application == "gmf.credit"
        assert proc.module == ORACLE[proc.id]["module"]
        assert len(proc.raw_xml_sha256) == 64
        assert proc.complexity is not None


@pytest.mark.parametrize("pid", sorted(ORACLE))
def test_synthetic_process_matches_oracle(pid):
    oracle = ORACLE[pid]
    proc, _ = _parse(oracle["file"])
    trig = oracle["trigger"]
    assert proc.id == pid
    assert (proc.package, proc.name) == tuple(pid.rsplit(".", 1))
    assert proc.starter is not None
    assert proc.starter.kind == trig["kind"]
    acts = _by_id(proc.activities)
    assert proc.starter.activity_id in acts
    if trig["kind"] == "rest":
        assert proc.starter.config["verb"] == trig["verb"]
        assert proc.starter.config["path"] == trig["path"]
    if trig["kind"] == "jms":
        assert proc.starter.config["destination"] == trig["destination"]
        assert proc.starter.config["selector"] == trig["selector"]
    if trig["kind"] == "timer":
        assert proc.starter.config["start"] == trig["start"]
        assert proc.starter.config["interval"] == trig["interval"]
    assert sorted(c.target_process for c in proc.calls) == sorted(oracle["calls"])
    for call in proc.calls:
        assert acts[call.activity_id].type == "call.process"
    # messaging destinations
    sends = {a.config["destination"] for a in proc.activities if a.type == "jms.send"}
    pubs = {a.config["destination"] for a in proc.activities if a.type == "jms.publish"}
    assert sends == set(oracle["queues_out"])
    assert pubs == set(oracle["topics_out"])
    # every table the oracle names appears in some JDBC statement
    sql = " ".join(a.config.get("sql", "") for a in proc.activities if a.type.startswith("jdbc."))
    for table in oracle["tables_read"] + oracle["tables_written"]:
        assert f"gmf.{table}" in sql, table
    # every transition endpoint is a node of the process
    nodes = set(acts) | {g.id for g in proc.groups}
    for t in proc.transitions:
        assert t.from_id in nodes and t.to_id in nodes, t
    # oracle "where" references to activities and links resolve
    trans = {t.id for t in proc.transitions}
    for item in oracle["rules"] + oracle["findings"]:
        where = item["where"]
        if "activity" in where:
            assert where["activity"] in acts, where
        if "link" in where:
            assert where["link"] in trans, where


def test_dealer_funding_structure():
    proc, _ = _parse("DealerFunding/Processes/dealerfunding/DealerFundingRequest.bwp")
    assert proc.id == "dealerfunding.DealerFundingRequest"
    assert proc.package == "dealerfunding" and proc.name == "DealerFundingRequest"
    assert proc.starter.kind == "jms"
    assert proc.starter.config["destination"] == "gmf.dealer.funding.request"
    types = [a.type for a in proc.activities]
    queries = [a for a in proc.activities if a.type == "jdbc.query"]
    assert any("like" in a.config["sql"] for a in queries)
    assert "jdbc.update" in types
    pubs = [a for a in proc.activities if a.type == "jms.publish"]
    assert pubs and all(a.config["destination"] == "gmf.funding.events" for a in pubs)
    assert not any(g.kind == "transaction" for g in proc.groups)
    acts = _by_id(proc.activities)
    lookup = acts["LookupDealer"]
    assert lookup.native_type == "bw.jdbc.JDBCQuery"
    assert lookup.source_line == 1014
    assert lookup.config["resource_ref"] == "DealerFunding.dealerfunding.GMFFundingDB"
    assert lookup.config["parameters"] == [{"name": "dealerId", "type": "VARCHAR"}]
    binding = {b.target: b for b in lookup.input_bindings}["jdbcQueryActivityInput/dealerId"]
    assert binding.language == "xslt"  # concat/normalize-space are functions
    assert "normalize-space" in binding.expression
    assert lookup.config["input_xslt"][0].lstrip().startswith("<?xml")
    trans = _by_id(proc.transitions)
    assert trans["LookupDealerToLookupDeal"].kind == "conditional"
    assert trans["LookupDealerToLookupDeal"].expression == "$LookupDealer/Record[1]/status = 'ACTIVE'"
    assert trans["LookupDealerToThrowDealerInactive"].kind == "otherwise"
    assert trans["ReceiveFundingRequestToLookupDealer"].kind == "success"
    assert acts["ThrowDealerInactive"].type == "throw"
    assert "DealerFunding/Schemas/FundingRequest.xsd" in proc.contracts_used


def test_payment_posting_transaction_group():
    proc, _ = _parse("LoanServicing/Processes/loanservicing/PaymentPosting.bwp")
    tx = [g for g in proc.groups if g.kind == "transaction"]
    assert len(tx) == 1
    group = tx[0]
    assert group.id == "PostPaymentTransaction"
    acts = _by_id(proc.activities)
    assert {"LoadAccount", "InsertPayment", "UpdateLoanAccount"} <= set(group.member_ids)
    assert "GroupStart" not in acts and "GroupEnd" not in acts  # boundary markers are not activities
    for mid in group.member_ids:
        assert acts[mid].group_id == group.id
    assert group.config["entry_ids"] == ["LoadAccount"]
    assert group.config["exit_ids"] == ["UpdateLoanAccount"]
    assert group.config["source_line"] == 974
    trans = _by_id(proc.transitions)
    assert trans["ReceivePaymentToPostPaymentTransaction"].to_id == group.id
    assert trans["PostPaymentTransactionToLogPaymentPosted"].from_id == group.id


def test_iterate_group():
    proc, _ = _parse("LoanServicing/Processes/loanservicing/NightlyDelinquencyScan.bwp")
    (group,) = [g for g in proc.groups if g.kind == "iterate"]
    assert group.config["variable_list"] == "$FindDelinquentAccounts/Record"
    assert group.config["counter"] == "index"
    assert group.config["current_element"] == {
        "variable": "currentElement",
        "expression": "$FindDelinquentAccounts/Record[number($index)]",
    }
    assert set(group.member_ids) == {"RecordDelinquency", "SendToCollections"}
    assert any(v.name == "currentElement" and v.scope == "scope" for v in proc.variables)
    assert proc.starter.kind == "timer"


def test_bureau_parallel_flow():
    proc, _ = _parse("CreditOrigination/Processes/creditorigination/BureauOrchestrator.bwp")
    parallel = [g for g in proc.groups if g.kind == "parallel"]
    assert len(parallel) == 1
    group = parallel[0]
    assert group.config["synthesised"] is True
    assert group.config["fork"] == "RenderBureauRequest"
    assert group.config["join"] == "MergeScores"
    acts = _by_id(proc.activities)
    http = [acts[m] for m in group.member_ids if acts[m].type == "http.invoke"]
    assert sorted(a.id for a in http) == ["PullEquifax", "PullTransUnion"]
    res_dir = GMF / "CreditOrigination/Resources/creditorigination"
    expected = {
        resources.parse_resource(res_dir / "EquifaxClient.httpClientResource").id,
        resources.parse_resource(res_dir / "TransUnionClient.httpClientResource").id,
    }
    assert {a.config["resource_ref"] for a in http} == expected
    trans = _by_id(proc.transitions)
    assert trans["PullEquifaxToEquifaxUnavailable"].kind == "error"
    assert trans["PullTransUnionToTransUnionUnavailable"].kind == "error"
    assert acts["EquifaxUnavailable"].type == "noop"
    assert proc.starter.kind == "subprocess"


def test_decision_branches():
    proc, _ = _parse("CreditOrigination/Processes/creditorigination/DecisionEngine.bwp")
    out = [t for t in proc.transitions if t.from_id == "ComputeDTI"]
    cond = [t for t in out if t.kind == "conditional"]
    assert len(cond) == 3
    for score in ("740", "680", "620"):
        assert any(score in t.expression for t in cond), score
    assert len([t for t in out if t.kind == "otherwise"]) == 1


def test_rest_intake_fault_handlers_and_calls():
    proc, _ = _parse("CreditOrigination/Processes/creditorigination/CreditApplicationIntake.bwp")
    assert proc.starter.kind == "rest"
    acts = _by_id(proc.activities)
    starter = acts[proc.starter.activity_id]
    assert starter.type == "rest.receive"
    # the onMessage body is entered from the starter
    assert any(t.from_id == starter.id and t.to_id == "NormalizeApplicant" for t in proc.transitions)
    names = {fh.fault_name for fh in proc.fault_handlers}
    assert names == {"DuplicateApplicationFault", "MissingIncomeFault", None}
    dup = next(fh for fh in proc.fault_handlers if fh.fault_name == "DuplicateApplicationFault")
    assert dup.activity_ids == ["ReplyDuplicate"]
    assert acts["ReplyDuplicate"].type == "reply"
    assert any(b.target.endswith("statusCode") and b.expression == "409" for b in acts["ReplyDuplicate"].input_bindings)


def test_unknown_activity_type_is_reported(tmp_path):
    src = GMF / "DealerFunding/Processes/dealerfunding/DealerFundingRequest.bwp"
    dst = tmp_path / "DealerFunding/Processes/dealerfunding/DealerFundingRequest.bwp"
    dst.parent.mkdir(parents=True)
    text = src.read_text(encoding="utf-8")
    assert text.count('activityTypeID="bw.jdbc.update"') == 1
    dst.write_text(text.replace('activityTypeID="bw.jdbc.update"', 'activityTypeID="bw.custom.Mystery"'), encoding="utf-8")
    collector = CompletenessCollector()
    proc = bw6.parse_bwp(dst, "DealerFunding", "gmf.credit", collector)
    report = collector.report()
    assert report.unknown_activity_types == [UnknownType(process_id=proc.id, native_type="bw.custom.Mystery")]
    assert not report.ok
    act = _by_id(proc.activities)["InsertFunding"]
    assert act.type == "unknown"
    assert act.native_type == "bw.custom.Mystery"


def test_unknown_element_is_reported(tmp_path):
    src = GMF / "DealerFunding/Processes/dealerfunding/FundingStatusLookup.bwp"
    dst = tmp_path / "DealerFunding/Processes/dealerfunding/FundingStatusLookup.bwp"
    dst.parent.mkdir(parents=True)
    text = src.read_text(encoding="utf-8")
    dst.write_text(text.replace("<bpws:links", "<tibex:Surprise/>\n<bpws:links", 1), encoding="utf-8")
    collector = CompletenessCollector()
    proc = bw6.parse_bwp(dst, "DealerFunding", "gmf.credit", collector)
    report = collector.report()
    assert [u.tag for u in report.unknown_elements] == ["tibex:Surprise"]
    assert proc.unknown_elements == report.unknown_elements
    assert not report.ok


def test_unparseable_file_is_a_failure(tmp_path):
    bad = tmp_path / "M/Processes/p/Broken.bwp"
    bad.parent.mkdir(parents=True)
    bad.write_text("<bpws:process", encoding="utf-8")
    collector = CompletenessCollector()
    with pytest.raises(etree.XMLSyntaxError):
        bw6.parse_bwp(bad, "M", "A", collector)
    report = collector.report()
    assert report.files_found == 1 and report.files_parsed == 0
    assert [f.path for f in report.failures] == [str(bad)]
    assert not report.ok


def test_collector_explain_is_recorded():
    _, collector = _parse("DealerFunding/Processes/dealerfunding/DealerFundingRequest.bwp")
    reasons = {e.path.rsplit("/", 1)[-1].split("[")[0]: e.reason for e in collector.report().explained}
    assert "notation:Diagram" in reasons
    assert "layout" in reasons["notation:Diagram"].lower()


# ------------------------------------------------------------- public samples
def test_public_sample_count():
    assert len(PUBLIC_BWP) == 18


@pytest.mark.parametrize("path", PUBLIC_BWP, ids=lambda p: p.relative_to(PUBLIC).as_posix())
def test_public_sample_parses(path):
    collector = CompletenessCollector()
    proc = bw6.parse_bwp(path, path.parent.name, "public", collector)
    report = collector.report()
    assert report.failures == []
    assert report.files_parsed == 1
    assert proc.activities
    # unknown types are allowed here but must be reported, never dropped
    reported = {u.native_type for u in report.unknown_activity_types}
    assert {a.native_type for a in proc.activities if a.type == "unknown"} == reported
    # every element is accounted for; the only known gap is a starter kind the IR lacks (mail)
    assert all("no IR starter kind" in u.reason for u in report.unknown_elements), report.unknown_elements
    print(proc.id, "unknown types:", sorted(reported), "unknown elements:", [u.tag for u in report.unknown_elements])


def test_public_dynamic_call_and_rest_reference():
    base = PUBLIC / "GMF_PoC_2_BW5_BW6_tibcobwblog/bw6.x/tibco_bw_dynamic_call_process"
    path = base / "TIBCOBWBLOG_DynamicInvoation.module/Processes/tibcobwblog_dynamicinvoation/module/Process.bwp"
    proc = bw6.parse_bwp(path, "TIBCOBWBLOG_DynamicInvoation.module", "public", CompletenessCollector())
    assert proc.id == "tibcobwblog_dynamicinvoation.module.Process"
    assert proc.starter.kind == "soap"
    invoke = _by_id(proc.activities)["Invoke"]
    assert invoke.type == "call.process"
    assert invoke.config["dynamic"] is True

    eq = PUBLIC / (
        "GMF_PoC_1_BW6_CreditApp_TN2018/TN2018_CreditApp/CreditAppService/CreditApp.module/"
        "Processes/creditapp/module/EquifaxScore.bwp"
    )
    proc = bw6.parse_bwp(eq, "CreditApp.module", "public", CompletenessCollector())
    rest = [a for a in proc.activities if a.type == "rest.invoke"]
    assert len(rest) == 1
    assert rest[0].config["verb"] == "POST"


def test_public_packaged_copy_matches_module(tmp_path):
    # a byte-identical copy parses to the same IR apart from source_path
    src = next(p for p in PUBLIC_BWP if p.name == "LookupDatabase.bwp")
    dst = tmp_path / "CreditCheckService/Processes/creditcheckservice/LookupDatabase.bwp"
    dst.parent.mkdir(parents=True)
    shutil.copy(src, dst)
    a = bw6.parse_bwp(src, "CreditCheckService", "x", CompletenessCollector())
    b = bw6.parse_bwp(dst, "CreditCheckService", "x", CompletenessCollector())
    assert a.model_dump(exclude={"source_path"}) == b.model_dump(exclude={"source_path"})


def test_xslt_bindings_targets_and_language():
    xslt = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform" xmlns:t="urn:t" version="2.0">'
        '<xsl:param name="In"/><xsl:template name="X-input" match="/"><t:Req>'
        "<code>ACH</code>"
        '<id><xsl:value-of select="$In/id"/></id>'
        '<xsl:for-each select="$In/line"><line><xsl:value-of select="amount"/></line></xsl:for-each>'
        '<when><xsl:attribute name="tz">UTC</xsl:attribute><xsl:value-of select="current-date()"/></when>'
        "</t:Req></xsl:template></xsl:stylesheet>"
    )
    got = [(b.target, b.expression, b.language, b.source_line) for b in bw6.xslt_bindings(xslt, 7)]
    assert got == [
        ("Req/code", '"ACH"', "xpath", 7),
        ("Req/id", "$In/id", "xpath", 7),
        ("Req/line", "amount", "xslt", 7),
        ("Req/when/@tz", '"UTC"', "xpath", 7),
        ("Req/when", "current-date()", "xslt", 7),
    ]
