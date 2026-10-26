import pytest

from engine.citations.memory import cite_ref
from engine.citations.model import CitedText, CiteRef, CiteSpan, CiteTarget
from engine.citations.spans import from_markers, sentence_bounds
from engine.memory.model import Citation, MemoryHit


def ref(ref_id: str) -> CiteRef:
    return CiteRef(id=ref_id, phase="validate", kind="rule", label=f"Rule {ref_id}",
                   preview="", target=CiteTarget(kind="step", step="validate"))


def hit(uid: str, label: str, *, phase: str, artifact_type: str, path: str | None = None,
        kind: str | None = None, text: str = "some text", process_id: str | None = None,
        props: dict | None = None) -> MemoryHit:
    citation = Citation(uid=uid, label=label, phase=phase, revision=3,
                        artifact_type=artifact_type, process_id=process_id, path=path)
    return MemoryHit(uid=uid, label=label, kind=kind, text=text, score=1.0, keyword_score=1.0,
                     vector_score=0.0, citation=citation, props=props or {})


# ------------------------------------------------------------------ markers to spans
def test_markers_become_sentence_spans():
    refs = {"S1": ref("a"), "S2": ref("b")}
    ct, dropped = from_markers("Dealer must be ACTIVE [S1]. Amount within tolerance [S2][S1].", refs)
    assert ct.text == "Dealer must be ACTIVE. Amount within tolerance."
    # The brief wrote (22, 47), which would take in the space before "Amount" and the closing
    # period; its own span rule (and test_offsets_are_code_points) gives (23, 46).
    assert [(s.start, s.end) for s in ct.spans] == [(0, 21), (23, 46)]
    assert [ct.text[s.start:s.end] for s in ct.spans] == [
        "Dealer must be ACTIVE", "Amount within tolerance"]
    assert ct.spans[0].refs == ["c1"] and ct.spans[1].refs == ["c2", "c1"] and dropped == []
    assert [(r.id, r.label) for r in ct.refs] == [("c1", "Rule a"), ("c2", "Rule b")]


def test_unknown_marker_is_dropped():
    ct, dropped = from_markers("A claim [S9].", {"S1": ref("a")})
    assert ct.spans == [] and dropped == ["S9"] and ct.text == "A claim."


def test_offsets_are_code_points():
    ct, _ = from_markers("🚀 Launch first. Then fund [S1].", {"S1": ref("a")})
    s = ct.spans[0]
    assert ct.text[s.start:s.end] == "Then fund"


def test_grouped_marker_and_partly_unknown_group():
    ct, dropped = from_markers("Retry twice [S2, S7, S1].", {"S1": ref("a"), "S2": ref("b")})
    assert ct.text == "Retry twice." and ct.spans[0].refs == ["c1", "c2"] and dropped == ["S7"]
    assert [r.label for r in ct.refs] == ["Rule b", "Rule a"]


def test_marker_after_punctuation_and_line_breaks():
    ct, _ = from_markers("Intro line\nFirst claim. [S1] Second claim [S2]!", {"S1": ref("a"),
                                                                            "S2": ref("b")})
    assert ct.text == "Intro line\nFirst claim. Second claim!"
    assert [ct.text[s.start:s.end] for s in ct.spans] == ["First claim.", "Second claim"]


def test_two_markers_in_one_sentence_do_not_overlap():
    ct, _ = from_markers("Dealer is checked [S1], then funded [S2].", {"S1": ref("a"),
                                                                        "S2": ref("b")})
    assert [ct.text[s.start:s.end] for s in ct.spans] == ["Dealer is checked", "then funded"]


def test_marker_with_no_text_before_it_cites_nothing():
    ct, dropped = from_markers("[S1] Hello.", {"S1": ref("a")})
    assert ct.text == " Hello." and ct.spans == [] and ct.refs == [] and dropped == []


def test_sentence_bounds():
    text = "One. Two!  Three?\nFour 1.5 five"
    assert [text[a:b] for a, b in sentence_bounds(text)] == [
        "One.", "Two!", "Three?", "Four 1.5 five"]


# ------------------------------------------------------------------ model invariants
def test_invariants_are_enforced():
    with pytest.raises(ValueError, match="overlap"):
        CitedText(text="abcdef", spans=[CiteSpan(start=0, end=4, refs=["c1"]), CiteSpan(start=2, end=5, refs=["c1"])], refs=[ref("c1")])
    with pytest.raises(ValueError, match="unused"):
        CitedText(text="abc", spans=[], refs=[ref("c1")])
    with pytest.raises(ValueError, match="bounds"):
        CitedText(text="abc", spans=[CiteSpan(start=1, end=4, refs=["c1"])], refs=[ref("c1")])
    with pytest.raises(ValueError, match="bounds"):
        CitedText(text="abc", spans=[CiteSpan(start=2, end=2, refs=["c1"])], refs=[ref("c1")])
    with pytest.raises(ValueError, match="unknown ref"):
        CitedText(text="abc", spans=[CiteSpan(start=0, end=2, refs=["c2"])], refs=[ref("c1")])


def test_plain_has_no_spans():
    ct = CitedText.plain("Nothing to cite.")
    assert ct.text == "Nothing to cite." and ct.spans == [] and ct.refs == []


# ------------------------------------------------------------------ memory hits to refs
def test_memory_citation_maps_to_phase_and_target():
    act = cite_ref(hit("activity:p1/LookupDealer", "Activity", phase="discover", artifact_type="process"), "c1", {"p1": "api-p1"})
    assert act.phase == "discover" and act.target.kind == "activity" and act.target.activity_id == "LookupDealer" and act.target.process_id == "api-p1"
    rule = cite_ref(hit("chunk:p1:rule:R1", "Chunk", phase="analysis", artifact_type="analysis"), "c2", {"p1": "api-p1"})
    assert rule.phase == "validate" and rule.target.kind == "step" and rule.target.step == "validate"
    gate = cite_ref(hit("gate:p1:G3", "GateDecision", phase="gate", artifact_type="gate"), "c3", {})
    assert gate.phase == "design"
    method = cite_ref(hit("method:dotnet:p1/api/Svc.Run", "Method", phase="build", artifact_type="build_file", path="src/Svc.cs"), "c4", {})
    assert method.target.kind == "file" and method.target.path == "src/Svc.cs" and method.memory_uid == "method:dotnet:p1/api/Svc.Run"
    seed = cite_ref(hit("seed:retry", "Memory", phase="seed", artifact_type="memory"), "c5", {})
    assert seed.phase == "memory" and seed.target.kind == "memory" and seed.target.uid == "seed:retry"


def test_cite_ref_label_kind_preview_and_revision():
    r = cite_ref(hit("chunk:p1:rule:R1", "Chunk", phase="analysis", artifact_type="analysis",
                     kind="rule", text="ssn: 123-45-6789 " + "x" * 400), "c2", {"p1": "api-p1"})
    assert r.id == "c2" and r.kind == "rule" and r.label == "Chunk R1" and r.revision == 3
    assert r.target.process_id == "api-p1"
    assert len(r.preview) <= 280 and "123-45-6789" not in r.preview
    long = cite_ref(hit("activity:p1/" + "A" * 80, "Activity", phase="discover",
                        artifact_type="process"), "c1", {})
    assert len(long.label) <= 60 and long.kind == "activity"


@pytest.mark.parametrize(("uid", "step"), [
    ("gate:p1:G1", "validate"), ("gate:p1:G2", "validate"), ("gate:p1:G4", "build"),
    ("gate:p1:G5", "verify"), ("gate:p1:G6", "deploy"),
])
def test_gate_phase_comes_from_the_uid(uid, step):
    r = cite_ref(hit(uid, "GateDecision", phase="gate", artifact_type="gate"), "c1", {})
    assert r.phase == step and r.target.kind == "step" and r.target.step == step
    assert r.kind == "gate"


@pytest.mark.parametrize(("phase", "ui"), [
    ("discover", "discover"), ("analysis", "validate"), ("decision", "validate"),
    ("design", "design"), ("build", "build"), ("verify", "verify"), ("deploy", "deploy"),
    ("seed", "memory"), ("curation", "memory"), ("migration", "memory"),
    ("reingest", "memory"),
])
def test_every_memory_phase_maps(phase, ui):
    r = cite_ref(hit("memory:x", "Memory", phase=phase, artifact_type="memory"), "c1", {})
    assert r.phase == ui


def test_artifact_targets():
    proc = cite_ref(hit("process:p1", "Process", phase="discover", artifact_type="process"),
                    "c1", {"p1": "api-p1"})
    assert proc.target.kind == "step" and proc.target.step == "discover"
    assert proc.target.process_id == "api-p1" and proc.kind == "process"
    run = cite_ref(hit("chunk:p1:verify:s1", "Chunk", phase="verify",
                       artifact_type="verify_run", kind="finding"), "c1", {})
    assert run.target.kind == "step" and run.target.step == "verify" and run.kind == "finding"
    plan = cite_ref(hit("error:deploy:p1:pl", "Error", phase="deploy", artifact_type="plan"),
                    "c1", {})
    assert plan.target.kind == "step" and plan.target.step == "deploy" and plan.kind == "error"
    cur = cite_ref(hit("curation:e1", "Curation", phase="curation", artifact_type="curation"),
                   "c1", {})
    assert cur.target.kind == "memory" and cur.target.uid == "curation:e1"
    cls = cite_ref(hit("class:dotnet:p1/api/Svc", "Class", phase="build",
                       artifact_type="build_file", path="src/Svc.cs"), "c1", {})
    assert cls.target.kind == "file" and cls.kind == "code" and cls.label == "Class Svc"
