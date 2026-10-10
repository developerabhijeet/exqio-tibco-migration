"""Tests for the plain-code checks behind the AI output (Task 8): static risk rules,
the evidence index, the citation verifier and the coverage matrix."""
import json
from pathlib import Path

import pytest

from engine.analysis.coverage import coverage_matrix
from engine.analysis.evidence import EvidenceIndex
from engine.analysis.models import Citation, CoverageResult, Finding, Rule, VerificationResult
from engine.analysis.static_rules import run_static_rules
from engine.analysis.verifier import verify_citations
from engine.ir.models import FaultHandler
from engine.tibco_parser.scanner import scan_folder

REPO_ROOT = Path(__file__).resolve().parents[2]
GMF = REPO_ROOT / "samples" / "synthetic-gmf"
ESTATE = json.loads((GMF / "estate.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def scan():
    return scan_folder(GMF)


@pytest.fixture(scope="module")
def procs(scan):
    return {p.name: p for a in scan.applications for p in a.processes}


@pytest.fixture(scope="module")
def apps(scan):
    return {p.name: a for a in scan.applications for p in a.processes}


def _static(procs, apps, name):
    app = apps[name]
    return run_static_rules(procs[name], app.contracts, app.substvars)


def _by_rule(findings, rule):
    return [f for f in findings if f.id.startswith(f"{rule}-")]


def _as_rules(findings):
    return [Rule(id=f.id, text=f.text, citations=f.citations, confidence=1.0) for f in findings]


def _acts(finding):
    return {c.activity_id for c in finding.citations}


# ----------------------------------------------------------------- static rules
def test_static_dealer_funding_s1_s2(procs, apps):
    findings = _static(procs, apps, "DealerFundingRequest")
    s1 = _by_rule(findings, "S1")
    assert len(s1) == 1
    assert s1[0].id == "S1-DealerFundingRequest-1"
    assert s1[0].severity == "high" and s1[0].source == "static"
    assert _acts(s1[0]) == {"CheckExistingFunding", "InsertFunding"}
    assert "funding" in s1[0].text

    s2 = _by_rule(findings, "S2")
    assert len(s2) == 1
    assert s2[0].severity == "high" and s2[0].source == "static"
    assert "LookupDealer" in _acts(s2[0])
    assert any(c.kind == "sql" and "dealer_id like ?" in c.text for c in s2[0].citations)

    # both pairs must sit in the same finding set as the oracle says, nothing else fires
    assert {f.id.split("-")[0] for f in findings} == {"S1", "S2"}
    assert verify_citations(procs["DealerFundingRequest"], _as_rules(findings)).failures == []


def test_static_s1_not_raised_inside_transaction_group(procs, apps):
    # PaymentPosting reads and writes loan_account, but inside one transaction group.
    findings = _static(procs, apps, "PaymentPosting")
    assert _by_rule(findings, "S1") == []


def test_static_audit_log_s7(procs, apps):
    findings = _static(procs, apps, "AuditLog")
    s7 = _by_rule(findings, "S7")
    assert len(s7) == 1
    f = s7[0]
    assert f.id == "S7-AuditLog-1" and f.severity == "high" and f.source == "static"
    assert _acts(f) == {"WriteAuditRecord"}
    kinds = {c.kind for c in f.citations}
    assert kinds == {"sql", "expression"}
    assert any("$Start/tns:ssn" in c.text for c in f.citations if c.kind == "expression")
    assert verify_citations(procs["AuditLog"], _as_rules(findings)).failures == []


def test_static_s7_log_activity_sensitive_field(procs, apps):
    proc = procs["DealerFundingRequest"].model_copy(deep=True)
    log = next(a for a in proc.activities if a.id == "LogDuplicateFunding")
    for b in log.input_bindings:
        if b.target.endswith("/message"):
            b.expression = "concat('Account ', $ReceiveFundingRequest/Body/tns:FundingRequest/tns:account_number, ' failed')"
    findings = run_static_rules(proc, apps["DealerFundingRequest"].contracts, [])
    s7 = _by_rule(findings, "S7")
    assert len(s7) == 1 and _acts(s7[0]) == {"LogDuplicateFunding"}
    assert verify_citations(proc, _as_rules(s7)).failures == []


def test_static_legacy_no_otherwise_s5(procs, apps):
    findings = _static(procs, apps, "LegacyAddressStandardize")
    s5 = _by_rule(findings, "S5")
    assert len(s5) == 1
    f = s5[0]
    assert f.severity == "low" and f.source == "static"
    assert _acts(f) == {"Standardize Address"}
    assert all(c.kind == "condition" for c in f.citations)
    assert verify_citations(procs["LegacyAddressStandardize"], _as_rules(findings)).failures == []


def test_static_nightly_hardcoded_s3(procs, apps):
    findings = _static(procs, apps, "NightlyDelinquencyScan")
    s3 = _by_rule(findings, "S3")
    assert len(s3) == 1
    f = s3[0]
    assert f.severity == "medium" and f.source == "static"
    assert _acts(f) == {"SendToCollections"}
    assert f.citations[0].kind == "expression"
    assert f.citations[0].text == "http://coll-api-prd01.gmfin.local:8443/collections/v1/cases"
    assert "caseCallbackUrl" in f.text
    assert verify_citations(procs["NightlyDelinquencyScan"], _as_rules(findings)).failures == []


def test_static_intake_s4_unused_field(procs, apps):
    findings = _static(procs, apps, "CreditApplicationIntake")
    s4 = _by_rule(findings, "S4")
    assert len(s4) == 1
    f = s4[0]
    assert f.severity == "medium" and f.source == "static"
    assert "transUnionScore" in f.text
    assert _acts(f) == {"postOut"}
    assert verify_citations(procs["CreditApplicationIntake"], _as_rules(findings)).failures == []


def test_static_s6_catch_without_activities(procs, apps):
    proc = procs["CreditApplicationIntake"].model_copy(deep=True)
    proc.fault_handlers.append(FaultHandler(id="catch:EmptyFault", fault_name="EmptyFault", activity_ids=[]))
    findings = run_static_rules(proc, apps["CreditApplicationIntake"].contracts, [])
    s6 = _by_rule(findings, "S6")
    assert len(s6) == 1
    assert s6[0].severity == "low" and "EmptyFault" in s6[0].text
    assert verify_citations(proc, _as_rules(s6)).failures == []
    # the estate itself has no empty catch
    assert _by_rule(run_static_rules(procs["CreditApplicationIntake"], apps["CreditApplicationIntake"].contracts, []), "S6") == []


def test_static_rules_match_estate_oracle(scan):
    """Every seeded finding that names a static rule is found; nothing else fires."""
    expected = {}
    for p in ESTATE["processes"]:
        for f in p["findings"]:
            if f["static_rule"]:
                expected.setdefault(p["id"].split(".")[-1], set()).add(f["static_rule"])
    got = {}
    for app in scan.applications:
        for proc in app.processes:
            rules = {f.id.split("-")[0] for f in run_static_rules(proc, app.contracts, app.substvars)}
            if rules:
                got[proc.name] = rules
    assert got == expected


# ------------------------------------------------------------ evidence + verifier
def test_evidence_index_kinds(procs):
    idx = EvidenceIndex(procs["DealerFundingRequest"])
    assert idx.has("CheckExistingFunding", "sql", "from gmf.funding where deal_id = ?")
    assert idx.has("CheckExistingFunding", "condition", "zzz") is False
    assert idx.has("CheckExistingFundingX", "sql", "gmf.funding") is False
    # outgoing transition expression of the activity, any whitespace
    assert idx.has("CheckExistingFunding", "condition", "count($CheckExistingFunding/Record)=0")
    # binding expression
    assert idx.has("InsertFunding", "expression", "$ReceiveFundingRequest/Body/tns:FundingRequest/tns:fundingAmount")
    # nested config value
    assert idx.has("ReceiveFundingRequest", "config", "gmf.dealer.funding.request")
    assert idx.has("ReceiveFundingRequest", "config", "FUNDING_REQUEST")
    # output schema name
    assert idx.has("LookupDealer", "schema", "resultSet")
    # empty text never matches
    assert idx.has("InsertFunding", "sql", "   ") is False


def test_verifier_rejects_paraphrase(procs):
    proc = procs["DealerFundingRequest"]
    spaced = "count($CheckExistingFunding/Record) = 0"
    good = Rule(
        id="R1", text="Funds only when no funding exists", confidence=0.9,
        citations=[
            Citation(activity_id="CheckExistingFunding", kind="condition", text="count($CheckExistingFunding/Record)=0"),
            Citation(activity_id="CheckExistingFunding", kind="condition", text=spaced),
            Citation(activity_id="InsertFunding", kind="sql", text="insert into gmf.funding (deal_id,\n dealer_id, amount"),
        ],
    )
    paraphrased = Rule(
        id="R2", text="Funds when the funding count is zero", confidence=0.9,
        citations=[
            Citation(activity_id="CheckExistingFunding", kind="condition", text=spaced),
            Citation(activity_id="CheckExistingFunding", kind="condition", text="funding record count equals zero"),
        ],
    )
    result = verify_citations(proc, [good, paraphrased])
    assert isinstance(result, VerificationResult)
    assert good.status == "draft"
    assert paraphrased.status == "unverified"
    assert result.total == 5 and result.verified == 4
    assert [f.rule_id for f in result.failures] == ["R2"]
    assert result.failures[0].citation.text == "funding record count equals zero"
    assert "not found" in result.failures[0].reason


def test_verifier_kind_and_activity_restrictions(procs):
    proc = procs["DealerFundingRequest"]
    wrong_kind = Rule(id="K1", text="t", confidence=1, citations=[
        Citation(activity_id="LookupDealer", kind="expression", text="dealer_id like ?")])
    wrong_activity = Rule(id="K2", text="t", confidence=1, citations=[
        Citation(activity_id="InsertFunding", kind="sql", text="dealer_id like ?")])
    missing_activity = Rule(id="K3", text="t", confidence=1, citations=[
        Citation(activity_id="NoSuchActivity", kind="sql", text="select")])
    no_citations = Rule(id="K4", text="t", confidence=1, citations=[])
    rejected = Rule(id="K5", text="t", confidence=1, status="rejected", citations=[
        Citation(activity_id="LookupDealer", kind="sql", text="made up")])
    result = verify_citations(proc, [wrong_kind, wrong_activity, missing_activity, no_citations, rejected])
    assert [r.status for r in (wrong_kind, wrong_activity, missing_activity, no_citations)] == ["unverified"] * 4
    assert rejected.status == "rejected"  # a human decision is not overwritten
    assert {f.rule_id for f in result.failures} == {"K1", "K2", "K3", "K4", "K5"}
    reasons = {f.rule_id: f.reason for f in result.failures}
    assert "unknown activity" in reasons["K3"]
    assert "no citations" in reasons["K4"]


# ------------------------------------------------------------------- coverage
def test_coverage_counts(procs):
    proc = procs["DealerFundingRequest"]
    rules = [
        Rule(id="R-FUND", text="Dedup", confidence=1, citations=[
            Citation(activity_id="CheckExistingFunding", kind="condition", text="count($CheckExistingFunding/Record) = 0"),
            Citation(activity_id="InsertFunding", kind="sql", text="insert into gmf.funding"),
        ]),
        Rule(id="R-TOL", text="Tolerance", confidence=1, citations=[
            Citation(activity_id="PublishFundingRejected", kind="expression", text='"AMOUNT_OUTSIDE_TOLERANCE"'),
        ]),
        Rule(id="R-GONE", text="rejected rule is ignored", confidence=1, status="rejected", citations=[
            Citation(activity_id="LookupDeal", kind="sql", text="from gmf.deal"),
        ]),
    ]
    technical = {
        "ReceiveFundingRequest": "JMS plumbing",
        "PublishFundingEvent:ActivityInput/Body/FundingEvent/eventTime": "timestamp",
    }
    result = coverage_matrix(proc, rules, technical)
    assert isinstance(result, CoverageResult)
    items = {(i.kind, i.ref): i for i in result.items}

    # activities: everything except noop/end
    acts = {r for k, r in items if k == "activity"}
    assert acts == {a.id for a in proc.activities if a.type not in ("noop", "end")}
    # transitions: conditional/otherwise only (no success transitions)
    trans = {r for k, r in items if k == "transition"}
    assert trans == {t.id for t in proc.transitions if t.kind in ("conditional", "otherwise", "error")}
    # mappings: one per input binding
    maps = {r for k, r in items if k == "mapping"}
    assert maps == {f"{a.id}:{b.target}" for a in proc.activities for b in a.input_bindings}
    assert result.total == len(items)

    assert items[("activity", "CheckExistingFunding")].covered_by == ["R-FUND"]
    assert items[("activity", "InsertFunding")].covered_by == ["R-FUND"]
    assert items[("activity", "LookupDeal")].covered_by == []  # rejected rule does not count
    assert items[("activity", "ReceiveFundingRequest")].technical_reason == "JMS plumbing"
    # transition matched by its condition text; otherwise sibling follows the same source activity
    assert items[("transition", "CheckExistingFundingToInsertFunding")].covered_by == ["R-FUND"]
    assert items[("transition", "CheckExistingFundingToLogDuplicateFunding")].covered_by == ["R-FUND"]
    assert items[("transition", "LookupDealerToLookupDeal")].covered_by == []
    # mapping matched by its expression text
    rejected_status = items[("mapping", "PublishFundingRejected:ActivityInput/Body/FundingEvent/reason")]
    assert rejected_status.covered_by == ["R-TOL"]
    tech = items[("mapping", "PublishFundingEvent:ActivityInput/Body/FundingEvent/eventTime")]
    assert tech.technical_reason == "timestamp" and tech.covered_by == []

    expected_covered = sum(1 for i in result.items if i.covered_by or i.technical_reason)
    assert result.covered == expected_covered
    assert 0 < result.covered < result.total


def test_coverage_unverified_rules_do_not_count(procs):
    proc = procs["DealerFundingRequest"]
    rule = Rule(id="R-X", text="t", confidence=1, status="unverified", citations=[
        Citation(activity_id="LookupDeal", kind="sql", text="from gmf.deal")])
    result = coverage_matrix(proc, [rule], {})
    assert result.covered == 0


def test_finding_model_roundtrip():
    f = Finding(id="S1-x-1", severity="high", text="t", citations=[], source="static")
    assert Finding.model_validate_json(f.model_dump_json()) == f
