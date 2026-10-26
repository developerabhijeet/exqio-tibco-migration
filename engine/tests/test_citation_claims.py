"""Claim anchoring (refs to the sentence naming them), entity linking and the memory uid mirror.

Spans follow the engine/citations sentence rule: a sentence span excludes its closing
punctuation (cross-ruling X4), so the expectations below also assert the sliced text."""
import pytest

from engine.citations.claims import HintedRef, anchor, hint_variants, link_entities, tail
from engine.citations.model import CiteRef, CiteTarget
from engine.citations.uids import (
    activity_uid,
    decision_chunk_uid,
    finding_chunk_uid,
    method_uid,
    process_uid,
    rule_chunk_uid,
    slug,
)
from engine.dotnet_gen.models import Decision
from engine.memory.projectors import analysis
from engine.memory.projectors.analysis import ChunkInput, ProcessRef, project_analysis
from engine.memory.projectors.build import project_build
from engine.memory.projectors.design import project_design
from engine.tests.test_memory_projectors_new import build_input
from engine.tests.test_memory_projectors_old import _design

REF = ProcessRef(ir_id="proc.One", api_id="api-1", name="One")


def r(ref_id: str) -> CiteRef:
    return CiteRef(id=ref_id, phase="validate", kind="activity", label=ref_id, preview="",
                   target=CiteTarget(kind="step", step="validate"))


def sliced(ct) -> list[str]:
    return [ct.text[s.start:s.end] for s in ct.spans]


# ------------------------------------------------------------------ anchoring
def test_chip_goes_on_the_sentence_naming_the_activity():
    ct = anchor("Funding starts at the queue. LookupDealer rejects inactive dealers.",
                [HintedRef(ref=r("LookupDealer"), hints=["LookupDealer"])])
    assert [(s.start, s.end, s.refs) for s in ct.spans] == [(29, 66, ["c1"])]
    assert sliced(ct) == ["LookupDealer rejects inactive dealers"]
    assert [x.id for x in ct.refs] == ["c1"] and ct.refs[0].label == "LookupDealer"


def test_refs_on_one_sentence_merge_in_order():
    ct = anchor("LookupDealer then CheckFunding.",
                [HintedRef(ref=r("a"), hints=["CheckFunding"]),
                 HintedRef(ref=r("b"), hints=["LookupDealer"])])
    assert len(ct.spans) == 1 and ct.spans[0].refs == ["c1", "c2"]
    assert sliced(ct) == ["LookupDealer then CheckFunding"]
    assert [x.label for x in ct.refs] == ["a", "b"]


def test_unmatched_ref_goes_to_tail():
    ct = anchor("First claim. Second claim.", [HintedRef(ref=r("x"), hints=["Nowhere"])])
    assert ct.spans[-1].start == 13 and ct.refs[0].id == "c1"
    assert sliced(ct) == ["Second claim"]


def test_spans_are_sorted_by_sentence():
    ct = anchor("Alpha runs. Beta runs! Gamma runs?",
                [HintedRef(ref=r("g"), hints=["Gamma"]), HintedRef(ref=r("a"), hints=["Alpha"])])
    assert sliced(ct) == ["Alpha runs", "Gamma runs"]
    assert [s.refs for s in ct.spans] == [["c2"], ["c1"]]


def test_matched_and_unmatched_share_the_last_sentence():
    ct = anchor("One. Two names Beta.",
                [HintedRef(ref=r("x"), hints=["zzz"]), HintedRef(ref=r("b"), hints=["Beta"])])
    assert sliced(ct) == ["Two names Beta"] and ct.spans[0].refs == ["c1", "c2"]


def test_hints_match_whole_words_only():
    ct = anchor("R10 applies. R1 applies.", [HintedRef(ref=r("R1"), hints=["R1"])])
    assert ct.text[ct.spans[0].start:ct.spans[0].end] == "R1 applies"
    ct = anchor("Lookup runs. LookupDealer runs.",
                [HintedRef(ref=r("LookupDealer"), hints=["LookupDealer"])])
    assert ct.text[ct.spans[0].start:ct.spans[0].end] == "LookupDealer runs"


def test_camel_case_hint_matches_spaced_prose():
    ct = anchor("The lookup dealer step checks status.",
                [HintedRef(ref=r("a"), hints=["LookupDealer"])])
    assert ct.spans[0].start == 0
    assert sliced(ct) == ["The lookup dealer step checks status"]


def test_any_hint_may_match():
    ct = anchor("Intro. The rule R7 holds.", [HintedRef(ref=r("a"), hints=["Missing", "R7"])])
    assert sliced(ct) == ["The rule R7 holds"]


def test_line_breaks_end_sentences_and_offsets_are_code_points():
    text = "Café opens ☕\nLookupDealer checks."
    ct = anchor(text, [HintedRef(ref=r("a"), hints=["LookupDealer"])])
    assert sliced(ct) == ["LookupDealer checks"] and ct.spans[0].start == 13


def test_no_ref_is_ever_lost():
    refs = [HintedRef(ref=r(f"x{i}"), hints=[f"none{i}"]) for i in range(5)]
    ct = anchor("Only one sentence.", refs)
    assert {i for s in ct.spans for i in s.refs} == {f"c{i}" for i in range(1, 6)}
    assert sliced(ct) == ["Only one sentence"]


def test_text_without_closing_punctuation_and_punctuation_only_sentence():
    ct = anchor("No full stop here", [HintedRef(ref=r("a"))])
    assert sliced(ct) == ["No full stop here"]
    ct = anchor("...", [HintedRef(ref=r("a"))])
    assert sliced(ct) == ["..."]


def test_no_refs_is_plain_text():
    assert anchor("Some text.", []).spans == [] and anchor("", []).text == ""


@pytest.mark.parametrize("text", ["", "   \n "])
def test_empty_text_with_refs_raises(text):
    with pytest.raises(ValueError, match="cannot anchor citations on empty text"):
        anchor(text, [HintedRef(ref=r("a"))])
    with pytest.raises(ValueError, match="cannot anchor citations on empty text"):
        tail(text, [r("a")])


def test_tail_puts_every_ref_on_the_last_sentence():
    ct = tail("Completely reworded. Still here.", [r("a"), r("b")])
    assert sliced(ct) == ["Still here"] and ct.spans[0].refs == ["c1", "c2"]
    assert tail("Completely reworded", [r("a")]).spans[-1].end == len("Completely reworded")
    assert tail("x.", []).spans == []


# ------------------------------------------------------------------ hints and entities
@pytest.mark.parametrize("hint, variants", [
    ("LookupDealer", ["LookupDealer", "Lookup Dealer"]),
    ("num_of_pulls", ["num_of_pulls", "num of pulls"]),
    ("R1", ["R1"]),
    ("HTTPServer", ["HTTPServer", "HTTP Server"]),
    ("Plain words", ["Plain words"]),
    ("  ", []),
])
def test_hint_variants(hint, variants):
    assert hint_variants(hint) == variants


def test_link_entities_links_only_known_names():
    found = link_entities("Map LookupDealer per R1; ignore Foo.",
                          {"LookupDealer": r("act"), "R1": r("rule")})
    assert [h.ref.id for h in found] == ["act", "rule"]
    assert [h.hints for h in found] == [["LookupDealer"], ["R1"]]


def test_link_entities_orders_by_first_mention_and_matches_whole_words():
    found = link_entities("R10 is not R1, but the lookup dealer is named after R1.",
                          {"R10x": r("none"), "LookupDealer": r("act"), "R1": r("rule"),
                           "R10": r("r10")})
    assert [h.ref.id for h in found] == ["r10", "rule", "act"]
    assert link_entities("", {"R1": r("rule")}) == []


def test_link_entities_then_anchor():
    text = "Funding starts. LookupDealer applies R1."
    ct = anchor(text, link_entities(text, {"R1": r("rule"), "LookupDealer": r("act")}))
    assert sliced(ct) == ["LookupDealer applies R1"] and ct.spans[0].refs == ["c1", "c2"]
    assert [x.label for x in ct.refs] == ["act", "rule"]


# ------------------------------------------------------------------ memory uid mirror
def test_uids_match_sp2_projectors():
    d = project_analysis(REF, [
        ChunkInput(kind="rule", key="R1", text="rule one", activity_ids=["a1"]),
        ChunkInput(kind="finding", key="F1", text="finding one")])
    assert rule_chunk_uid(REF.ir_id, "R1") in {n.uid for n in d.nodes}
    assert finding_chunk_uid(REF.ir_id, "F1") in {n.uid for n in d.nodes}
    assert activity_uid(REF.ir_id, "a1") in {x.dst for x in d.rels}
    assert process_uid(REF.ir_id) in {x.dst for x in d.rels}
    dd = project_design(REF, _design(Decision(title="Use Dapper", choice="Dapper",
                                              alternatives=["EF Core"], reason="simple SQL")),
                        "dr1")
    assert decision_chunk_uid(REF.ir_id, "Use Dapper") == dd.nodes[0].uid


def test_duplicate_decision_titles_follow_the_used_set():
    same = Decision(title="Use Dapper", choice="a", reason="r")
    dd = project_design(REF, _design(same, same, Decision(title="!!", choice="b", reason="r")),
                        "dr1")
    used: set[str] = set()
    assert [decision_chunk_uid(REF.ir_id, t, used) for t in ["Use Dapper", "Use Dapper", "!!"]] \
        == [n.uid for n in dd.nodes]


def test_method_uid_carries_the_build_stack():
    d = project_build(REF, build_input())
    uids = {n.uid for n in d.nodes}
    assert method_uid(REF.ir_id, "Api", "Flow", "Run", stack="dotnet") in uids
    assert method_uid(REF.ir_id, "Api", "Flow", "Run", stack="java") == \
        "method:java:proc.One/Api/Flow.Run"


def test_slug_is_sp2s_rule():
    assert slug is analysis.slug and slug("Use Dapper!") == "use-dapper"
