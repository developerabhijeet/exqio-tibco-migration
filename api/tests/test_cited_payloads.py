"""Inline citation spans (``cited``) on the Discover domains and the Validate rules and findings
(SP4 Task 3), and on the Design narrative and decisions, the Build review findings, the Verify
parity differences and the Readiness rows (Task 4): a sample scan and analysis through the fake
provider, as test_analysis_gates; build and verify rows are written directly."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from api.app.models import Finding, Rule
from api.app.services.citations import (
    activity_names,
    activity_ref,
    cite_design,
    cite_diff,
    cite_domain,
    cite_finding,
    cite_readiness_row,
    cite_review,
    cite_rule,
    design_entities,
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


# ================================================================== Task 4: Design, Build, Verify, Readiness
@pytest.fixture
async def designed(client) -> SimpleNamespace:
    """A validated FundingStatusLookup with a design record (fake provider: narrative and
    decisions)."""
    from api.tests.test_build_verify import _validated, _wait_job

    pid, proc = await _validated(client)
    r = await client.post(f"/processes/{proc}/design")
    assert r.status_code == 202, r.text
    assert (await _wait_job(client, r.json()["job_id"]))["status"] == "done"
    return SimpleNamespace(pid=pid, proc=proc)


async def _process_row(app, proc_id: str):
    from api.app.models import Process

    async with app.state.sessionmaker() as s:
        return await s.get(Process, proc_id)


@pytest.fixture
async def built(client, app, analysed) -> SimpleNamespace:
    """A finished build row of the analysed process whose review panel found four things, and the
    lineage of the file they point at (as the build writes it)."""
    from api.app.models import Build, BuildFile

    proc = await _process_row(app, analysed.proc)
    act = next(a for a in proc.ir["activities"] if hints_for(a["id"], a["name"], None))
    path = "src/App.Core/Mappings/FundingMapper.cs"
    reviews = [
        {"reviewer": "equivalence", "file": path, "line": 12,
         "text": f"The rounding differs. {act['name']} reads the wrong field.", "triage": "patch"},
        {"reviewer": "security", "file": path, "line": 40,
         "text": "The applicant ssn 123-45-6789 is written to the log.", "triage": "patch"},
        {"reviewer": "standards", "file": path, "line": 0, "text": "No header comment.",
         "triage": "defer"},
        {"reviewer": "standards", "file": "", "line": 0, "text": "The solution has no README.",
         "triage": "defer"},
        {"reviewer": "standards", "file": path, "line": 5, "text": "", "triage": "defer"},
    ]
    async with app.state.sessionmaker() as s:
        build = Build(process_id=analysed.proc, status="passed", reviews=reviews)
        s.add(build)
        await s.flush()
        s.add(BuildFile(build_id=build.id, path=path, origin="code", process_id=proc.ir_id, sha256="0" * 64,
                        tibco_ref={"process_id": proc.ir_id, "activity_ids": [act["id"]], "rows": [
                            {"activity_id": act["id"], "activity_name": act["name"],
                             "class_name": "FundingMapper", "method": "MapRequest"}]}))
        await s.commit()
        return SimpleNamespace(build=build.id, proc=analysed.proc, activity=act, path=path)


@pytest.fixture
async def verified_with_defect(client, app, analysed) -> SimpleNamespace:
    """A verify run of a build row of the analysed process: a defect a Fix finding touches
    through a branch decision, a defect no finding explains (an SSN in its value), an intended
    difference and a match."""
    from api.app.models import Build, Scenario, ScenarioRun

    key = (await client.get(f"/processes/{analysed.proc}/analysis")).json()["findings"][0]["finding_key"]
    report = {"scenario_id": "happy", "parity": 0.25, "unexplained": 2, "fields": [
        {"path": "output.body.status", "expected": "FUNDED", "actual": "PENDING", "status": "defect",
         "finding_id": key, "via": "control"},
        {"path": "output.body.ssn", "expected": "123-45-6789", "actual": None, "status": "defect",
         "finding_id": None},
        {"path": "output.body.amount", "expected": 10.5, "actual": 10.0, "status": "intended",
         "finding_id": key, "confirmed": False},
        {"path": "output.body.dealId", "expected": "DL-1", "actual": "DL-1", "status": "match",
         "finding_id": None}], "side_effects": []}
    async with app.state.sessionmaker() as s:
        build = Build(process_id=analysed.proc, status="passed")
        sc = Scenario(process_id=analysed.proc, key="happy", name="Happy path", data={})
        s.add_all([build, sc])
        await s.flush()
        s.add(ScenarioRun(build_id=build.id, scenario_id=sc.id, job_id="j1", status="differs",
                          report=report, parity=0.25))
        await s.commit()
        return SimpleNamespace(build=build.id, proc=analysed.proc, finding_key=key)


def all_field_diffs(view: dict) -> list[dict]:
    return [d for rep in view["reports"] for d in rep.get("fields", []) + rep.get("side_effects", [])]


def _slices_cleanly(ct: CitedText) -> None:
    for span in ct.spans:
        piece = ct.text[span.start:span.end]
        assert piece == piece.strip() and piece[-1] not in ".!?"


async def test_design_decisions_link_named_activities_and_rules(client, designed):
    d = (await client.get(f"/processes/{designed.proc}/design")).json()
    ct = CitedText.model_validate(d["decisions"][0]["cited"])
    assert ct.text == f'{d["decisions"][0]["choice"]}. {d["decisions"][0]["reason"]}'
    CitedText.model_validate(d["narrative_cited"])
    named = [r for r in CitedText.model_validate(d["narrative_cited"]).refs if r.target.kind == "activity"]
    assert all(r.label in d["narrative"] or r.target.activity_id in d["narrative"] for r in named)
    for dec in d["decisions"]:
        c = CitedText.model_validate(dec["cited"])
        assert bool(c.spans) == bool(c.refs)
        assert all(r.phase in ("design", "validate", "discover") for r in c.refs)
    assert {"narrative", "decisions", "mapping"} <= d.keys()  # nothing existing dropped


async def test_design_prose_naming_an_activity_and_a_rule_gets_chips(client, app, designed):
    """The fake designer's prose names nothing; a narrative and a decision that name an activity
    and a rule of this process get one chip each, on the sentence naming them."""
    from api.app.services import evidence

    proc = await _process_row(app, designed.proc)
    act = next(a for a in proc.ir["activities"] if hints_for(a["id"], a["name"], None))
    rule = (await client.get(f"/processes/{designed.proc}/analysis")).json()["rules"][0]["rule_key"]
    async with app.state.sessionmaker() as s:
        row = await evidence.latest_design(s, designed.proc)
        row.narrative = f"The service starts. {act['name']} is one method; {rule} holds."
        record = dict(row.record)
        record["decisions"] = [{**record["decisions"][0], "reason": f"{act['name']} reads it"}]
        row.record = record
        await s.commit()
    d = (await client.get(f"/processes/{designed.proc}/design")).json()
    n = CitedText.model_validate(d["narrative_cited"])
    assert [n.text[s.start:s.end] for s in n.spans] == [f"{act['name']} is one method; {rule} holds"]
    assert [(r.kind, r.phase) for r in n.refs] == [("activity", "design"), ("rule", "validate")]
    assert n.refs[0].target.activity_id == act["id"] and n.refs[1].target.step == "validate"
    dec = CitedText.model_validate(d["decisions"][0]["cited"])
    assert [r.target.activity_id for r in dec.refs] == [act["id"]]


async def test_review_findings_cite_their_file(client, built):
    findings = (await client.get(f"/builds/{built.build}/reviews")).json()
    for f in findings:
        if not f["text"]:
            assert f["cited"] is None  # no sentence to carry the file chip: no cited
            continue
        ct = CitedText.model_validate(f["cited"])
        if f["file"]:
            ref = next(r for r in f["cited"]["refs"] if r["target"]["kind"] == "file")
            # a line of 0 is "no line": the chip opens the file, not line 0
            assert ref["target"]["path"] == f["file"] and ref["target"]["line"] == (f["line"] or None)
            assert ref["phase"] == "build"
            assert ref["label"] == ("FundingMapper.cs:" + str(f["line"]) if f["line"] else "FundingMapper.cs")
        else:
            assert ct.refs == [] and ct.text == f["text"]
    first = CitedText.model_validate(findings[0]["cited"])
    act = next(r for r in first.refs if r.target.kind == "activity")
    assert act.target.activity_id == built.activity["id"]
    # the activity is named in the second sentence; the file is named nowhere, so the last
    assert [s.refs for s in first.spans] == [["c1", "c2"]]
    # deciding a finding keeps cited on the returned list
    r = await client.patch(f"/builds/{built.build}/reviews/0", json={"decision": "keep"},
                           headers={"X-User": "engineer.ravi"})
    assert r.status_code == 200 and all("cited" in x for x in r.json())


async def test_parity_differences_cite_their_finding(client, verified_with_defect):
    view = (await client.get(f"/builds/{verified_with_defect.build}/parity")).json()
    diff = next(d for d in all_field_diffs(view) if d["status"] == "defect")
    kinds = {r["phase"] for r in diff["cited"]["refs"]}
    assert "verify" in kinds and ("validate" in kinds) == bool(diff["finding_id"])
    match = next(d for d in all_field_diffs(view) if d["status"] == "match")
    assert "cited" not in match  # nothing to cite
    for d in all_field_diffs(view):
        if d["status"] == "match":
            continue
        ct = CitedText.model_validate(d["cited"])
        assert ("validate" in {r.phase for r in ct.refs}) == bool(d["finding_id"])
        verify = next(r for r in ct.refs if r.phase == "verify")
        assert verify.target.step == "verify" and verify.target.process_id == verified_with_defect.proc
        assert verify.label == "Happy path"
        assert ct.text.startswith(f'{d["path"]}: expected ')
    finding = next(r for r in diff["cited"]["refs"] if r["phase"] == "validate")
    assert finding["label"] == verified_with_defect.finding_key
    assert finding["target"] == {**finding["target"], "kind": "step", "step": "validate",
                                 "process_id": verified_with_defect.proc}


async def test_readiness_rows_cite_every_evidence_item(client, scanned):
    for row in (await client.get(f"/projects/{scanned}/readiness")).json()["rows"]:
        assert len(row["cited"]["refs"]) == len(row["evidence"])
        ct = CitedText.model_validate(row["cited"])
        assert ct.text.startswith(f'{row["expectation"]}: {row["status"]}')
        assert [r.label for r in ct.refs] == [cut(e["label"], 60) for e in row["evidence"]]


async def test_readiness_settings_and_history_evidence_are_page_targets(client, scanned):
    """X18: /settings and /history are no phase: their refs are ``page`` targets on that route."""
    rows = (await client.get(f"/projects/{scanned}/readiness")).json()["rows"]
    pages = {}
    for row in rows:
        for ev, ref in zip(row["evidence"], row["cited"]["refs"], strict=True):
            if ev["ref"].startswith(("/settings", "/history")):
                pages[ev["ref"].split("?")[0]] = ref["target"]
    assert set(pages) == {"/settings", "/history"}  # provider choice and routing; tokenomics
    assert all(t["kind"] == "page" and t["path"] == p and t["step"] is None for p, t in pages.items())


async def test_cited_text_on_reviews_and_parity_is_masked(client, built, verified_with_defect):
    """An SSN in a review finding or a parity value: ``cited.text`` is masked, differs from the raw
    field, and the spans slice ``cited.text`` cleanly."""
    leak = (await client.get(f"/builds/{built.build}/reviews")).json()[1]
    ct = CitedText.model_validate(leak["cited"])
    assert "123-45-6789" in leak["text"] and "123-45-6789" not in ct.text and "***-**-****" in ct.text
    assert ct.text != leak["text"] and ct.spans
    _slices_cleanly(ct)
    view = (await client.get(f"/builds/{verified_with_defect.build}/parity")).json()
    d = next(d for d in all_field_diffs(view) if d["path"] == "output.body.ssn")
    ct = CitedText.model_validate(d["cited"])
    assert d["expected"] == "123-45-6789"  # the raw field is untouched
    # a sensitive field's value is masked whole; the sentence around it is not
    assert ct.text == 'output.body.ssn: expected "***", got null.'
    assert [ct.text[s.start:s.end] for s in ct.spans] == ['output.body.ssn: expected "***", got null']
    _slices_cleanly(ct)


# ------------------------------------------------------------------ Task 4 builders
DPROC = SimpleNamespace(id="p1", ir_id="dealer.Funding", ir={
    "starter": {"activity_id": "a0", "kind": "jms", "config": {"destination": "gmf.funding.request"}},
    "activities": [
        {"id": "a1", "name": "LookupDealer", "type": "jdbc.query", "native_type": "x",
         "config": {"sql": "select * from dealer d join funding_request f on f.id = d.id"}},
        {"id": "a2", "name": "Map", "type": "mapper", "native_type": "x"}]})
RULES = [SimpleNamespace(rule_key="R1", text="The dealer must be active.")]
FINDINGS = [SimpleNamespace(finding_key="F2", text="ssn 123-45-6789 logged")]


def test_design_entities_are_this_process_s_names_without_common_words():
    ents = design_entities(DPROC, {"mapping": []}, RULES, FINDINGS)
    assert set(ents) == {"a1", "LookupDealer", "a2", "R1", "F2", "funding_request",
                         "gmf.funding.request"}  # Map, dealer: common words
    assert ents["a1"] is ents["LookupDealer"] and ents["a1"].phase == "design"
    assert ents["R1"].memory_uid == "chunk:dealer.Funding:rule:R1"
    assert ents["F2"].memory_uid == "chunk:dealer.Funding:finding:F2"
    assert ents["R1"].target.step == "validate" and ents["R1"].target.process_id == "p1"
    assert "123-45-6789" not in ents["F2"].preview
    assert ents["funding_request"].phase == "discover" and ents["funding_request"].kind == "table"


def test_cite_design_spans_and_plain_decisions():
    ents = design_entities(DPROC, {}, RULES, FINDINGS)
    out = cite_design({"narrative": "Funding starts. Lookup Dealer reads funding_request per R1.",
                       "decisions": [{"choice": "Use EF Core", "reason": "LookupDealer is a query"},
                                     {"choice": "Keep it simple", "reason": "nothing to say"}]}, ents)
    n = CitedText.model_validate(out["narrative_cited"])
    assert [(n.text[s.start:s.end], s.refs) for s in n.spans] == [
        ("Lookup Dealer reads funding_request per R1", ["c1", "c2", "c3"])]  # one chip per entity
    d0 = CitedText.model_validate(out["decisions"][0]["cited"])
    assert d0.text == "Use EF Core. LookupDealer is a query" and [r.label for r in d0.refs] == ["LookupDealer"]
    assert out["decisions"][1]["cited"] == {"text": "Keep it simple. nothing to say", "spans": [], "refs": []}
    assert cite_design({"narrative": None, "decisions": []}, ents)["narrative_cited"] is None


def test_cite_review_hints_class_and_method():
    f = {"reviewer": "equivalence", "file": "src/Core/FundingMapper.cs", "line": 7,
         "text": "Fine here. MapRequest drops a field."}
    ct = cite_review(f, "b1", DPROC, {"activity_ids": ["a1"], "rows": [
        {"activity_id": "a1", "class_name": "FundingMapper", "method": "MapRequest"}]})
    assert ct.text[ct.spans[0].start:ct.spans[0].end] == "MapRequest drops a field"
    assert ct.refs[0].target.kind == "file" and ct.refs[0].label == "FundingMapper.cs:7"
    assert len(ct.refs) == 1  # a1 is not named in the text: no activity chip
    assert cite_review({"file": "x.cs", "line": 0, "text": ""}, "b1", DPROC) is None


def test_cite_diff_note_and_no_finding():
    ct = cite_diff({"path": "out.a", "expected": 1, "actual": 2, "status": "defect", "note": "Rounded."},
                   {"scenario_key": "k1", "status": "differs"})
    assert ct.text == "out.a: expected 1, got 2. Rounded."
    assert [r.phase for r in ct.refs] == ["verify"] and ct.refs[0].label == "k1"
    assert ct.text[ct.spans[0].start:ct.spans[0].end] == "out.a: expected 1, got 2"
    assert cite_diff({"path": "x.note", "expected": "id 123-45-6789", "actual": "a"}, {}).text == (
        'x.note: expected "id ***-**-****", got "a".')


def test_cite_diff_masks_sensitive_objects_and_cuts_long_values():
    ct = cite_diff({"path": "out.ssn", "expected": {"area": "123", "rest": "45-6789"},
                    "actual": ["123-45-6789"]}, {})
    assert ct.text == 'out.ssn: expected "***", got "***".'
    ct = cite_diff({"path": "out.blob", "expected": "x" * 500 + " 123-45-6789", "actual": 1}, {})
    expected = ct.text.split(": expected ", 1)[1].split(", got ")[0]
    assert len(expected) == 120 and expected.endswith("…") and "123-45-6789" not in ct.text


def test_readiness_targets_follow_the_page():
    row = {"expectation": "Swagger", "status": "partial", "gap": "2 unexplained", "evidence": [
        {"kind": "parity", "ref": "/builds/b1/parity", "label": "runs"},
        {"kind": "build", "ref": "/builds/b1", "label": "log"},
        {"kind": "metrics", "ref": "/builds/b1", "label": "m"},
        {"kind": "tests", "ref": "/builds/b1/tests", "label": "t"},
        {"kind": "canvas", "ref": "/processes/p9/canvas", "label": "c"},
        {"kind": "scenarios", "ref": "/processes/p9/scenarios", "label": "sc"},
        {"kind": "domains", "ref": "/projects/x/domains", "label": "d"},
        {"kind": "report", "ref": "/projects/x/report", "label": "r"},
        {"kind": "settings", "ref": "/settings", "label": "s"},
        {"kind": "history", "ref": "/history?project_id=x", "label": "h"},
        {"kind": "ask", "ref": "/ask", "label": "k"},
        {"kind": "odd", "ref": "/somewhere", "label": "o"}]}
    ct = cite_readiness_row(row, {"b1": "p1"})
    assert ct.text == "Swagger: partial; 2 unexplained." and [s.refs for s in ct.spans] == [
        [f"c{i}" for i in range(1, 13)]]
    got = [(r.phase, r.target.kind, r.target.step, r.target.process_id, r.target.path, r.target.hash)
           for r in ct.refs]
    assert got == [
        ("verify", "step", "verify", "p1", None, "#parity"), ("build", "step", "build", "p1", None, None),
        ("build", "step", "build", "p1", None, "#metrics"), ("build", "step", "build", "p1", None, "#tests"),
        ("discover", "step", "discover", "p9", None, None),
        ("verify", "step", "verify", "p9", None, "#scenarios"),
        ("discover", "step", "discover", None, None, None), ("upload", "step", "upload", None, None, None),
        ("deploy", "page", None, None, "/settings", None), ("deploy", "page", None, None, "/history", None),
        ("memory", "page", None, None, "/", None), ("upload", "page", None, None, "/", None)]
    assert cite_readiness_row({"expectation": "X", "status": "missing", "gap": "none",
                               "evidence": []}) == CitedText.plain("X: missing; none.")
