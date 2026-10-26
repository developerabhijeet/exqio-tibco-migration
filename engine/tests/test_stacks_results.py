from pathlib import Path

import pytest
from lxml import etree

from api.app.services.build import test_inventory as build_test_inventory
from engine.dotnet_gen import metrics as metrics_mod
from engine.dotnet_gen.runner import BuildResult, TestRun
from engine.stacks.coverage import parse_cobertura_xml, parse_go_cover, parse_jacoco
from engine.stacks.results import parse_junit, parse_test2json
from engine.stacks.testindex import IndexEntry, join, read_index, read_index_file, write_index

SAMPLES = Path(__file__).parent / "samples" / "stacks"


def test_junit_surefire_and_pytest_samples():
    s = parse_junit([SAMPLES / "surefire-TEST-sample.xml"])
    assert ("com.gmf.gmfcredit.DealerFundingRequestRulesTests.r1_dealerMustBeActive", "passed") in [(n, o) for n, o, _, _ in s]
    assert [o for _, o, _, _ in s] == ["passed", "failed", "skipped"]
    p = parse_junit([SAMPLES / "pytest-junit.xml"])
    assert {o for _, o, _, _ in p} == {"passed", "failed", "skipped"}


def test_junit_ignores_entities(tmp_path):
    f = tmp_path / "x.xml"
    f.write_text('<?xml version="1.0"?><!DOCTYPE t [<!ENTITY e SYSTEM "file:///etc/passwd">]>'
                 '<testsuite><testcase classname="a" name="b"><failure message="&e;"/></testcase></testsuite>')
    try:
        (_, _, _, msg), = parse_junit([f])
    except etree.XMLSyntaxError:  # refusing the document is as safe as not expanding it
        return
    assert "root:" not in msg


def test_test2json_ignores_subtests_and_output_lines():
    cases = parse_test2json(SAMPLES / "go-test2json.jsonl")
    assert len(cases) == 3 and all("/" not in n for n, *_ in cases)
    assert [o for _, o, _, _ in cases] == ["passed", "failed", "skipped"]
    assert "amount too high" in cases[1][3]


def test_join_maps_ids_from_the_index():
    idx = read_index_file(SAMPLES / "test-index.json")
    out = join([("tests.TestFundingStatusLookupBranch_t3", "passed", 1.0, ""), ("x.Unknown", "failed", 0.0, "m")], idx)
    assert out[0].branch_id == "t3" and out[0].display_name.startswith("Branch [t3]")
    assert out[1].rule_id is None and out[1].branch_id is None


def test_write_then_read_index(tmp_path):
    f = write_index([IndexEntry(name="a.B", kind="rule", rule_id="R1")])
    (tmp_path / ".exiqo").mkdir()
    (tmp_path / f.path).write_text(f.content)
    assert read_index(tmp_path)[0].rule_id == "R1" and read_index(tmp_path / "none") == []
    inv = build_test_inventory([f], "app", stack="go")
    assert inv[0]["kind"] == "rule" and inv[0]["rule_key"] == "R1" and inv[0]["outcome"] == "not_run"


def test_coverage_parsers():
    assert parse_jacoco(SAMPLES / "jacoco.xml", None) == pytest.approx(0.75)
    assert parse_jacoco(SAMPLES / "jacoco.xml", {"com/gmf/gmfcredit/FundingService.java"}) == pytest.approx(0.75)
    assert parse_jacoco(SAMPLES / "jacoco.xml", {"Other.java"}) is None
    assert parse_go_cover(SAMPLES / "go-cover.out", {"internal/core/funding_status_lookup_service.go"}) == pytest.approx(2 / 3)
    assert parse_go_cover(SAMPLES / "go-cover.out", None) == pytest.approx(2 / 8)
    assert parse_cobertura_xml([SAMPLES / "coverage.xml"]) == pytest.approx(0.5)


def test_metrics_use_source_file_count_when_given():
    run = TestRun(ok=True, cases=[], coverage_line=None, trx_path=None, log="")
    build = BuildResult(ok=True, warnings=2, errors=[], log="", files=1)
    m = metrics_mod.compute_metrics([], 0, run, build, True, None, source_files=10)
    assert m.standards_adherence == pytest.approx(0.8)
    assert "10 source files" in m.details["standards_adherence"]


def test_join_does_not_misattribute_by_short_name():
    idx = [IndexEntry(name="x.RulesTests.r1_x", kind="rule", rule_id="R1"),
           IndexEntry(name="tests.TestX", kind="branch", branch_id="t1")]
    out = join([("x.OtherTests.r1_x", "passed", 0, ""), ("pkg.TestX", "passed", 0, "")], idx)
    assert all(c.rule_id is None and c.branch_id is None for c in out)
    ok = join([("com.x.RulesTests.r1_x[1]", "passed", 0, "")], idx)[0]
    assert ok.rule_id == "R1" and ok.name == "x.RulesTests.r1_x"


def test_join_then_merge_results_gives_one_attributed_row():
    from api.app.services.build import merge_results
    idx = [IndexEntry(name="x.RulesTests.r1_x", kind="rule", rule_id="R1", display_name="R1 x", path="a")]
    inv = build_test_inventory([write_index(idx)], "app", stack="java")
    run = TestRun(ok=True, cases=join([("com.x.RulesTests.r1_x", "passed", 2.0, "")], idx),
                  coverage_line=None, trx_path=None, log="")
    rows = merge_results(inv, run)
    assert len(rows) == 1 and rows[0]["outcome"] == "passed" and rows[0]["rule_key"] == "R1"


def test_duplicates_keep_worst_and_bad_json_lines_skipped(tmp_path):
    f = tmp_path / "r.jsonl"
    f.write_text('[1]\n{"Action":"pass","Package":"p","Test":"T"}\n{"Action":"fail","Package":"p","Test":"T"}\n')
    assert [(n, o) for n, o, *_ in parse_test2json(f)] == [("p.T", "failed")]


def test_entity_expansion_bomb_is_not_expanded(tmp_path):
    f = tmp_path / "b.xml"
    f.write_text('<?xml version="1.0"?><!DOCTYPE t [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;">'
                 '<!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;">]><testsuite><testcase classname="k" name="n">'
                 '<failure message="&c;"/></testcase></testsuite>')
    try:
        (_, _, _, msg), = parse_junit([f])
    except etree.XMLSyntaxError:
        return
    assert len(msg) < 100
