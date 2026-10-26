"""Inline citation spans (``cited``) on the Discover domains and the Validate rules and findings
(SP4 Task 3): a sample scan and analysis through the fake provider, as test_analysis_gates."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from api.app.models import Finding, Rule
from api.app.services.citations import (
    activity_names,
    activity_ref,
    cite_domain,
    cite_finding,
    cite_rule,
    hints_for,
)
from api.tests.test_analysis_gates import DFR, SME, _analyse, _scanned
from engine.citations.model import CitedText, cut


@pytest.fixture
async def scanned(client) -> str:
    pid, _ = await _scanned(client)
    return pid


@pytest.fixture
async def analysed(client) -> SimpleNamespace:
    pid, procs = await _scanned(client)
    proc = procs[DFR]
    await _analyse(client, proc["id"])
    return SimpleNamespace(pid=pid, proc=proc["id"], ir_id=proc["ir_id"])


async def break_first_citation(client, app, analysed) -> str:
    """The first rule's first citation stops matching the IR; an edit re-verifies it."""
    rule = (await client.get(f"/processes/{analysed.proc}/analysis")).json()["rules"][0]
    async with app.state.sessionmaker() as s:
        row = await s.get(Rule, rule["id"])
        first = {k: rule["citations"][0][k] for k in ("activity_id", "kind", "text")}
        row.citations = [{**first, "text": "this text is nowhere in the process"},
                         *row.citations[1:]]
        await s.commit()
    r = await client.patch(f"/rules/{rule['id']}", json={"text": rule["text"] + " Checked."},
                           headers=SME)
    assert r.status_code == 200, r.text
    return rule["id"]


# ------------------------------------------------------------------ through the API
async def test_rules_and_findings_carry_valid_cited_text(client, analysed):
    a = (await client.get(f"/processes/{analysed.proc}/analysis")).json()
    assert a["rules"] and a["findings"]
    for item in a["rules"] + a["findings"]:
        ct = CitedText.model_validate(item["cited"])  # invariants hold
        assert ct.text == item["text"]
        assert len(ct.refs) == len(item["citations"])  # nothing lost
        assert {r.target.activity_id for r in ct.refs} == {c["activity_id"] for c in item["citations"]}
        assert all(r.phase == "validate" and r.kind == "activity" and r.target.kind == "activity"
                   and r.target.process_id == analysed.proc for r in ct.refs)


async def test_unverified_citation_keeps_its_reason(client, app, analysed):
    rule_id = await break_first_citation(client, app, analysed)
    rules = (await client.get(f"/processes/{analysed.proc}/analysis")).json()["rules"]
    rule = next(r for r in rules if r["id"] == rule_id)
    bad = rule["cited"]["refs"][0]
    assert bad["verified"] is False and bad["reason"] == rule["citations"][0]["reason"]
    assert bad["reason"] and bad["target"]["kind"] == "activity"
    good = [r for x in rules for r in x["cited"]["refs"] if r is not bad]
    assert good and all(r["verified"] is True and r["reason"] is None for r in good)


async def test_edited_rule_keeps_every_citation(client, analysed):
    rule = (await client.get(f"/processes/{analysed.proc}/analysis")).json()["rules"][0]
    r = await client.patch(f"/rules/{rule['id']}", json={"text": "Completely reworded."}, headers=SME)
    assert r.status_code == 200, r.text
    after = next(x for x in (await client.get(f"/processes/{analysed.proc}/analysis")).json()["rules"]
                 if x["id"] == rule["id"])
    ct = CitedText.model_validate(after["cited"])
    # X4: the span leaves the closing period out
    assert len(ct.refs) == len(after["citations"]) and ct.spans[-1].end == len("Completely reworded")


async def test_domain_rationale_cites_each_evidence_item(client, scanned):
    domains = (await client.get(f"/projects/{scanned}/domains")).json()
    assert domains
    for d in domains:
        ct = CitedText.model_validate(d["cited"])
        assert len(ct.refs) == len(d["evidence"]) and all(r.phase == "discover" for r in ct.refs)
        assert [r.preview for r in ct.refs] == [cut(e["text"], 280) for e in d["evidence"]]
        assert all(r.kind == "evidence" and r.target.kind == "step" and r.target.step == "discover"
                   for r in ct.refs)


async def test_memory_uids_follow_sp2_conventions(client, analysed):
    ref = (await client.get(f"/processes/{analysed.proc}/analysis")).json()["rules"][0]["cited"]["refs"][0]
    assert ref["memory_uid"] == f"activity:{analysed.ir_id}/{ref['target']['activity_id']}"


async def test_payloads_keep_existing_fields(client, analysed):
    a = (await client.get(f"/processes/{analysed.proc}/analysis")).json()
    rule, finding = a["rules"][0], a["findings"][0]
    assert {"text", "citations", "status", "rule_key"} <= rule.keys()
    assert {"verified", "reason"} <= rule["citations"][0].keys()
    assert {"text", "citations", "severity", "finding_key"} <= finding.keys()
    d = (await client.get(f"/projects/{analysed.pid}/domains")).json()[0]
    assert {"evidence", "name", "process_ids"} <= d.keys()


async def test_cited_text_is_masked_and_spans_slice_it(client, app, analysed):
    """An SSN in a rule or finding: ``cited.text`` is masked, differs from ``text``, and every
    span slices ``cited.text`` cleanly."""
    a = (await client.get(f"/processes/{analysed.proc}/analysis")).json()
    rule, finding = a["rules"][0], a["findings"][0]
    async with app.state.sessionmaker() as s:
        (await s.get(Rule, rule["id"])).text = "Applicant ssn 123-45-6789 is checked. Then funded."
        (await s.get(Finding, finding["id"])).text = "Leaks 987-65-4321 in logs. Fix it."
        await s.commit()
    a = (await client.get(f"/processes/{analysed.proc}/analysis")).json()
    for item, raw in ((next(r for r in a["rules"] if r["id"] == rule["id"]), "123-45-6789"),
                      (next(f for f in a["findings"] if f["id"] == finding["id"]), "987-65-4321")):
        ct = CitedText.model_validate(item["cited"])
        assert raw in item["text"] and raw not in ct.text and ct.text != item["text"]
        assert "***-**-****" in ct.text
        assert ct.spans and len(ct.refs) == len(item["citations"])
        sentences = [x.strip().rstrip(".") for x in ct.text.split(". ") if x.strip()]
        for span in ct.spans:
            assert ct.text[span.start:span.end] in sentences


# ------------------------------------------------------------------ builders
PROC = SimpleNamespace(id="p1", ir_id="dealer.Funding", ir={"activities": [
    {"id": "a1", "name": "LookupDealer"}, {"id": "a2", "name": "Map"},
    {"id": "a3", "name": "Check Credit"}]})


def test_activity_ref_label_preview_and_uid():
    ref = activity_ref("c1", "p1", "ir", "a1", "x" * 70, "ssn=123-45-6789 " + "q" * 300,
                       verified=True, reason=None, phase="validate")
    assert ref.label == "x" * 59 + "…" and len(ref.preview) == 280
    assert "123-45-6789" not in ref.preview
    assert ref.memory_uid == "activity:ir/a1" and ref.target.activity_id == "a1"
    assert activity_ref("c1", "p1", "ir", "a1", "", "", verified=None, reason=None,
                        phase="validate").label == "a1"


def test_rule_refs_anchor_on_the_sentence_naming_the_activity():
    rule = SimpleNamespace(rule_key="R1", text="Funding starts. Lookup Dealer reads the dealer.",
                           citations=[{"activity_id": "a1", "kind": "sql", "text": "select 1"}])
    ct = cite_rule(rule, PROC)
    assert [(s.start, s.end) for s in ct.spans] == [(16, 46)]
    assert ct.refs[0].label == "LookupDealer" and ct.refs[0].verified is None


def test_common_word_names_do_not_anchor():
    """``Map`` names no sentence (a common word): the ref falls to the last sentence."""
    rule = SimpleNamespace(rule_key="R2", text="A map is built. The amount is checked.",
                           citations=[{"activity_id": "a2", "kind": "expression", "text": "x"}])
    ct = cite_rule(rule, PROC)
    assert ct.text[ct.spans[0].start:ct.spans[0].end] == "The amount is checked"
    assert hints_for("a2", "Map", "x") == ["a2"]
    assert hints_for("a3", "Check Credit", "$Start/root/dealer_id > 0 and `limitAmt`") == [
        "a3", "Check Credit", "dealer_id", "limitAmt"]


def test_quoted_identifiers_anchor():
    rule = SimpleNamespace(rule_key="R3", text="Funding starts. The dealer_id must be set.",
                           citations=[{"activity_id": "a2", "kind": "condition",
                                       "text": "$Start/dealer_id != ''"}])
    ct = cite_rule(rule, PROC)
    assert ct.text[ct.spans[0].start:ct.spans[0].end] == "The dealer_id must be set"


def test_empty_text_anchors_on_the_key():
    finding = SimpleNamespace(finding_key="F1", text="",
                              citations=[{"activity_id": "a1", "kind": "sql", "text": "q"}])
    ct = cite_finding(finding, PROC)
    assert ct.text == "F1" and ct.spans[0].refs == ["c1"]


def test_no_citations_is_plain():
    rule = SimpleNamespace(rule_key="R4", text="No source.", citations=[])
    assert cite_rule(rule, PROC) == CitedText.plain("No source.")


def test_domain_sentences_in_order_of_first_appearance():
    domain = SimpleNamespace(evidence=[
        {"kind": "table", "text": "DEALER"}, {"kind": "call", "text": "a calls b"},
        {"kind": "table", "text": "FUNDING"}, {"kind": "weird", "text": "w1"}])
    ct = cite_domain(domain)
    assert ct.text == "Shares the tables DEALER, FUNDING. Calls between a calls b. Weird: w1."
    spans = [ct.text[s.start:s.end] for s in ct.spans]
    assert spans == ["Shares the tables DEALER, FUNDING", "Calls between a calls b", "Weird: w1"]
    assert [s.refs for s in ct.spans] == [["c1", "c3"], ["c2"], ["c4"]]
    assert cite_domain(SimpleNamespace(evidence=[])) == CitedText.plain("")


def test_queues_and_topics_share_one_sentence():
    domain = SimpleNamespace(evidence=[
        {"kind": "queue", "text": "Q.FUND"}, {"kind": "table", "text": "DEALER"},
        {"kind": "topic", "text": "T.CREDIT"}])
    ct = cite_domain(domain)
    assert ct.text == "Uses the queues Q.FUND, T.CREDIT. Shares the tables DEALER."
    assert [s.refs for s in ct.spans] == [["c1", "c3"], ["c2"]]


def test_names_built_once_are_used():
    names = activity_names(PROC)
    assert names == {"a1": "LookupDealer", "a2": "Map", "a3": "Check Credit"}
    rule = SimpleNamespace(rule_key="R1", text="Renamed runs.",
                           citations=[{"activity_id": "a1", "kind": "sql", "text": "q"}])
    assert cite_rule(rule, PROC, names={"a1": "Renamed"}).refs[0].label == "Renamed"
