"""Java tests first, ``MavenRunner`` and ``JavaLanguage`` (target stacks, Task 8).

The tests that need Java and Maven skip with the reason when ``scripts/toolchain.py`` does not find
them (like ``test_runner.py::needs_dotnet``); everything else runs without a toolchain. The golden
file is ``engine/tests/golden/java/DealerFundingRequestRulesTests.java``; regenerate it after an
intended change with ``UPDATE_GOLDEN=1`` and review the diff.
"""
from __future__ import annotations

import json
import os
import stat
import urllib.request
from pathlib import Path

import pytest

from engine.agents.builder import check_filled_file
from engine.agents.test_engineer import placeholder_methods
from engine.dotnet_gen.design import build_design_skeleton
from engine.stacks import get_stack
from engine.stacks.base import Mirrors, SourceLanguage
from engine.stacks.java import runner as runner_mod
from engine.stacks.java.generator import generate_java
from engine.stacks.java.language import JavaLanguage
from engine.stacks.java.runner import MavenRunner, find_tool, parse_maven_errors
from engine.stacks.testindex import INDEX_PATH, IndexEntry, read_index
from engine.tests.conftest import DFR, FSL, PROCESS_IDS

GOLDEN = Path(__file__).resolve().parent / "golden"
UPDATE = os.environ.get("UPDATE_GOLDEN") == "1"
PKG = "com.gmf.gmfcredit"
TESTS = "src/test/java/com/gmf/gmfcredit"

needs_maven = pytest.mark.skipif(not (find_tool("mvn") and find_tool("java")),
                                 reason="java/mvn not found (scripts/toolchain.py)")


def _golden(name: str, actual: str) -> None:
    path = GOLDEN / name
    if UPDATE:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(actual, encoding="utf-8")
    assert path.exists(), f"golden file {path} missing; run with UPDATE_GOLDEN=1"
    assert actual == path.read_text(encoding="utf-8"), f"{name} differs from the golden file"


def _file(solution, suffix: str):
    hits = [f for f in solution.files if f.path.endswith(suffix)]
    assert len(hits) == 1, (suffix, [f.path for f in hits])
    return hits[0]


# ------------------------------------------------------------------ fixtures
@pytest.fixture(scope="module")
def java_solution_with_tests(tmp_path_factory, gmf_apps, gmf_app, gmf_processes, gmf_rules, gmf_findings,
                             gmf_scenarios):
    from engine.domains import graph as graph_mod

    graph = graph_mod.build_graph(list(gmf_apps.values()))
    designs = {pid: build_design_skeleton(gmf_processes[pid], gmf_app, gmf_rules[pid], gmf_findings[pid],
                                          graph_mod.io_card(gmf_processes[pid], gmf_app, graph), stack="java")
               for pid in PROCESS_IDS}
    return generate_java(gmf_app, PROCESS_IDS, designs, gmf_rules, gmf_findings, gmf_scenarios,
                         tmp_path_factory.mktemp("javatests") / "GmfCredit",
                         Mirrors(maven=os.environ.get("MAVEN_MIRROR_URL", "")))  # restore refuses without one (X26)


@pytest.fixture
def test_database_url():
    url = os.environ.get("TEST_DATABASE_URL", "")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    return url


# ------------------------------------------------------------------ the brief's tests
def test_java_rules_tests_golden(java_solution_with_tests):
    _golden("java/DealerFundingRequestRulesTests.java",
            _file(java_solution_with_tests, "DealerFundingRequestRulesTests.java").content)


def test_index_names_every_generated_test(java_solution_with_tests):
    idx = read_index(java_solution_with_tests.root)
    assert {e.kind for e in idx} >= {"rule", "branch", "scenario"} and all(e.name.startswith("com.gmf.gmfcredit.") for e in idx)


def test_parse_maven_errors():
    n, errs = parse_maven_errors("[ERROR] /w/src/main/java/A.java:[12,5] cannot find symbol\n[ERROR] BUILD FAILURE\n")
    assert n == 1 and errs == ["src/main/java/A.java:12:5: cannot find symbol"]


def test_java_language_checks():
    lang = JavaLanguage()
    assert lang.placeholder.search('throw new UnsupportedOperationException("agent fills: X");')
    assert lang.strip_comments('String s = "// no"; // yes') == 'String s = "// no"; '


@needs_maven
def test_generated_java_builds_formats_and_tests(java_solution_with_tests, test_database_url):
    r = MavenRunner(java_solution_with_tests.root, env={"TEST_DATABASE_URL": test_database_url})
    assert r.restore().ok and r.build().ok
    assert r.format_write().ok  # the format gate is write-then-check
    assert r.format_check().ok
    run = r.test()
    assert run.cases and {c.rule_id for c in run.cases if c.rule_id} >= {"R1"} and run.coverage_line is not None
    passed = {c.scenario_id for c in run.cases if c.outcome == "passed"}
    assert {"FundingStatusLookup:happy", "FundingStatusLookup:branch:QueryFundingStatusToReplyNotFound"} <= passed


@needs_maven
def test_generated_java_service_answers_health_and_dev_token(java_solution_with_tests):
    r = MavenRunner(java_solution_with_tests.root)
    assert r.restore().ok and r.build().ok
    handle = r.run_service(18431, env={"ASPNETCORE_ENVIRONMENT": "Development"})
    try:
        with urllib.request.urlopen(handle.url + "/health", timeout=5) as resp:
            assert resp.status == 200
        req = urllib.request.Request(handle.url + "/auth/dev-token", data=b'{"subject":"t"}', method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert json.loads(resp.read())["token_type"] == "Bearer"
    finally:
        handle.stop()


# ------------------------------------------------------------------ generated tests and index
def test_rules_tests_have_one_placeholder_per_approved_rule(java_solution_with_tests, gmf_rules):
    text = _file(java_solution_with_tests, "DealerFundingRequestRulesTests.java").content
    approved = [r.id for r in gmf_rules[DFR] if r.status in ("approved", "edited")]
    assert approved == ["R1", "R2", "R3"]
    for rid in approved:
        assert f'fail("no test body for {rid}");' in text
    assert "R4" not in text  # a draft rule gets no test
    assert "class DealerFundingRequestRulesTests extends ScenarioSupport" in text


def test_index_entries_match_the_test_engineer_lookup(java_solution_with_tests):
    """The Test Engineer finds a test by path, process id, kind and the last segment of its name."""
    files = {f.path: f.content for f in java_solution_with_tests.files}
    idx = read_index(java_solution_with_tests.root)
    for e in idx:
        assert e.path in files and e.process_id in (DFR, FSL)
        cls, method = e.name.rsplit(".", 2)[-2:]
        assert e.path == f"{TESTS}/{cls}.java" and e.name == f"{PKG}.{cls}.{method}"
        assert f"void {method}()" in files[e.path]
    rules = [e for e in idx if e.kind == "rule" and e.process_id == DFR]
    assert [e.rule_id for e in rules] == ["R1", "R2", "R3"] and not any(e.has_body for e in rules)
    found = placeholder_methods(files[rules[0].path], "java", rules)
    assert [m.ref for m in found] == ["R1", "R2", "R3"]
    branches = [e for e in idx if e.kind == "branch" and e.process_id == FSL]
    assert branches and all(e.has_body for e in branches)  # every FSL branch has a driving scenario
    scenarios = {e.scenario_id for e in idx if e.kind == "scenario"}
    assert {"FundingStatusLookup:happy", "DealerFundingRequest:happy"} <= scenarios


def test_scenario_drivers_follow_the_starter(java_solution_with_tests):
    fsl = _file(java_solution_with_tests, "FundingStatusLookupScenarioTests.java").content
    assert 'runRequest("FundingStatusLookup", scenario);' in fsl
    dfr = _file(java_solution_with_tests, "DealerFundingRequestScenarioTests.java").content
    assert ('runMessage("DealerFundingRequest", scenario, DealerFundingRequestConsumer.ENTITY, '
            'Map.of("MsgType", "FUNDING_REQUEST"), consumer::handle);') in dfr
    branch = _file(java_solution_with_tests, "DealerFundingRequestBranchTests.java").content
    assert 'fail("no test body for branch LookupDealerToThrowDealerInactive: no scenario takes this link");' in branch
    assert 'run("happy");' in branch


def test_fixtures_are_the_dotnet_documents(java_solution_with_tests, tmp_path):
    files = {f.path: f.content for f in java_solution_with_tests.files}
    happy = json.loads(files["src/test/resources/fixtures/FundingStatusLookup/happy.json"])
    assert happy["request"]["path"] == "/funding/DL-50001" and happy["expected"]["output"]["status"] == 200
    assert "body.fundedAt" in happy["expected"]["output_noise"]  # seeded relative to the load time
    dfr = json.loads(files["src/test/resources/fixtures/DealerFundingRequest/happy.json"])
    row = next(e for e in dfr["expected"]["side_effects"] if e["kind"] == "row_written")
    assert row["table"] == "gmf.funding" and "funded_at" in row["noise"]
    assert "src/test/resources/database/schema.sql" in files and "src/test/resources/database/seed.sql" in files


def test_test_support_reads_the_test_database_and_skips_without_it(java_solution_with_tests):
    db = _file(java_solution_with_tests, "support/TestDatabase.java").content
    assert 'System.getenv("TEST_DATABASE_URL")' in db
    assert 'Assumptions.assumeTrue(false, "TEST_DATABASE_URL is not set");' in db
    assert 'CONNECTION_PROPERTIES = List.of("connection-strings.gmf-funding-db")' in db
    assert 'SCHEMAS = List.of("gmf")' in db and 'endsWith("_test")' in db
    support = _file(java_solution_with_tests, "support/ScenarioSupport.java").content
    assert "@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)" in support
    assert '@ActiveProfiles("development")' in support


def test_pom_lets_restore_cache_what_the_offline_steps_need(java_solution_with_tests):
    pom = _file(java_solution_with_tests, "pom.xml").content
    assert "<artifactId>surefire-junit-platform</artifactId>" in pom
    assert "removeUnusedImports/>" not in pom  # it would drop the tests' vocabulary imports


# ------------------------------------------------------------------ JavaLanguage
def test_java_language_is_the_stack_language():
    lang = get_stack("java").language
    assert isinstance(lang, JavaLanguage) and isinstance(lang, SourceLanguage)
    assert lang.test_placeholder("R1") == 'fail("no test body for R1");'
    assert lang.is_test_path(f"{TESTS}/ARulesTests.java") and not lang.is_test_path("src/main/java/A.java")


def test_java_lexer_handles_text_blocks_chars_and_block_comments():
    lang = JavaLanguage()
    text = 'String q = """\n  select 1 -- "x" // not a comment\n  """; char c = \'"\'; /* a\n b */ int x;'
    assert lang.strip_comments(text) == 'String q = """\n  select 1 -- "x" // not a comment\n  """; char c = \'"\';     \n      int x;'
    assert lang.code_only(text).startswith('String q = """\n' + " " * 34 + '\n  """; char c = \' \';')


def test_declarations_are_method_record_and_type_headers():
    lang = JavaLanguage()
    text = '''@Service
public class LoanService {
    private final Repo repo;
    public LoanService(Repo repo) { this.repo = repo; }
    /** doc */
    @GetMapping("/x")
    public ResponseEntity<JsonNode> get(@RequestParam(name = "id", required = false) String id) {
        if (id == null) { return null; }
        for (int i = 0; i < 2; i++) { call(i); }
        return service.execute(state);
    }
    void r1_counts() {
        fail("no test body for R1");
    }
    public record Item(String name) {}
    interface Port { JsonNode read(String id); }
}
'''
    assert lang.declarations(text) == [
        "@Service public class LoanService", "public LoanService(Repo repo)",
        '@GetMapping("/x") public ResponseEntity<JsonNode> get(@RequestParam(name = "id", required = false) String id)',
        "void r1_counts()", "public record Item(String name)", "interface Port", "JsonNode read(String id)"]


def test_a_formatter_s_line_breaks_do_not_change_declarations_or_tibco_comments():
    lang = JavaLanguage()
    expr = "abs(number($Start/amount) - number($Deal/Record[1]/approved_amount)) <= number($Deal/Record[1]/approved_amount) * 0.02"
    flat = f"""    public static boolean check(ProcessState state, JsonNode input, String name) {{
        // TIBCO condition: {expr}
        throw new UnsupportedOperationException("agent fills: check");
    }}
"""
    wrapped = """    public static boolean check(
            ProcessState state, JsonNode input, String name) {
        // TIBCO condition: abs(number($Start/amount) - number($Deal/Record[1]/approved_amount)) <=
        // number($Deal/Record[1]/approved_amount) * 0.02
        throw new UnsupportedOperationException("agent fills: check");
    }
"""
    assert lang.declarations(flat) == lang.declarations(wrapped)
    assert lang.tibco_comments(flat) == lang.tibco_comments(wrapped) == [f"// TIBCO condition: {expr}"]
    # a short comment line under a short TIBCO line is the agent's own, not a continuation
    own = "        // TIBCO: x = 1\n        // the agent's note\n"
    assert lang.tibco_comments(own) == ["// TIBCO: x = 1"]


MAIN = "src/main/java/com/gmf/gmfcredit/core/LoanMappings.java"
SCAFFOLD = """package com.gmf.gmfcredit.core;

public final class LoanMappings {
    public static JsonNode map(JsonNode input) {
        // TIBCO: total = $Start/a + $Start/b
        throw new UnsupportedOperationException("agent fills: map");
    }
}
"""


def _fill(body: str) -> str:
    return SCAFFOLD.replace('        throw new UnsupportedOperationException("agent fills: map");\n', body)


@pytest.mark.parametrize(("body", "reason"), [
    ("        assertThat(input).isNotNull();\n        return input;\n", "assertion"),
    ("        Assertions.fail();\n        return input;\n", "assertion"),
    ("        assert input != null : \"no input\";\n        return input;\n", "assertion"),
    ("        assert(input != null);\n        return input;\n", "assertion"),
    ("        return input;\n    }\n\n    @Test\n    void extra() {\n", "@Test"),
    ("        @SuppressWarnings(\"unchecked\") JsonNode x = input;\n        return x;\n", "@SuppressWarnings"),
    ("        try {\n            return input;\n        } catch (RuntimeException ex) {\n            return null;\n        }\n",
     "swallow a TIBCO fault"),
    ("        try {\n            return input;\n        } catch (DealerInactiveFaultException ex) {\n            return null;\n        }\n",
     "swallow a TIBCO fault"),
    ("        return TibValue.of(java.nio.file.Path.of(\"/etc/passwd\").toString());\n", "outside the project"),
])
def test_java_fill_may_not_introduce(body, reason):
    assert reason in check_filled_file(MAIN, SCAFFOLD, _fill(body), stack="java")


def test_java_fill_may_catch_a_checked_exception_and_mention_assert_in_a_comment():
    body = ("        // no assertThat( here, no assert statement and no @Test either\n        String note = \"assert nothing\";\n        try {\n            return TibValue.parse(read());\n"
            "        } catch (java.io.IOException ex) {\n            throw new java.io.UncheckedIOException(ex);\n"
            "        }\n")
    assert check_filled_file(MAIN, SCAFFOLD, _fill(body), stack="java") is None


def test_dotnet_forbidden_patterns_are_unchanged():
    lang = get_stack("dotnet").language
    reasons = [f.reason for f in lang.forbidden]
    assert reasons == ["the code contains Assert; assertions belong to tests",
                       "the code contains a [Fact] or [Theory] test attribute",
                       "the code introduces #pragma warning disable",
                       ("the code adds a catch clause; the fault handlers are generated from the TIBCO process, "
                        "so fill the body of the one that is there"),
                       "the code references a file outside the solution"]
    assert [f.in_code for f in lang.forbidden] == [False, False, False, True, False]


# ------------------------------------------------------------------ MavenRunner without Maven
def _fake_mvn(tmp_path: Path, code: int = 0, out: str = "") -> Path:
    log = tmp_path / "calls.txt"
    exe = tmp_path / "mvn"
    exe.write_text(f'#!/bin/sh\necho "$@" >> {log}\nenv >> {log}.env\nprintf %b "{out}"\nexit {code}\n')
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return exe


def test_runner_commands_are_the_spec_s(tmp_path):
    exe = _fake_mvn(tmp_path)
    (tmp_path / ".exiqo").mkdir()
    (tmp_path / ".exiqo" / "maven-settings.xml").write_text("<settings><mirrors><mirror>x</mirror></mirrors></settings>")
    r = MavenRunner(tmp_path, java="/opt/jdk/bin/java", mvn=str(exe))
    assert r.restore().ok and r.build().ok and r.format_write().ok and r.format_check().ok
    r.test()
    calls = (tmp_path / "calls.txt").read_text().splitlines()
    base = "-B -ntp -s .exiqo/maven-settings.xml"
    assert calls == [
        f"{base} dependency:resolve dependency:go-offline spotless:apply -DspotlessFiles=^$ surefire:test -DskipTests",
        f"{base} -o -DskipTests package", f"{base} -o spotless:apply", f"{base} -o spotless:check",
        f"{base} -o -Dmaven.test.failure.ignore=true test"]


def test_runner_environment_keeps_only_the_repository_and_drops_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("MAVEN_OPTS", "-Dmaven.repo.local=/cache/stacks/m2 -Dhttps.proxyPassword=secret")
    monkeypatch.setenv("JAVA_HOME", "/opt/java")
    monkeypatch.setenv("APP_SECRET", "s3cret")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db/x")
    env = MavenRunner(tmp_path, java="/opt/java/bin/java", mvn="/opt/maven/bin/mvn",
                      env={"TEST_DATABASE_URL": "postgresql://t"}).env({"X": "1"})
    assert env["MAVEN_OPTS"] == "-Dmaven.repo.local=/cache/stacks/m2" and env["JAVA_HOME"] == "/opt/java"
    assert "APP_SECRET" not in env and "DATABASE_URL" not in env
    assert env["TEST_DATABASE_URL"] == "postgresql://t" and env["X"] == "1"
    assert env["PATH"].split(os.pathsep)[:2] == ["/opt/java/bin", "/opt/maven/bin"]


def test_build_errors_come_from_javac_or_the_failure_summary(tmp_path):
    log = ("[ERROR] COMPILATION ERROR :\n"
           f"[ERROR] {tmp_path}/src/main/java/A.java:[3,9] cannot find symbol\n"
           "  symbol:   variable x\n  location: class A\n"
           f"[ERROR] {tmp_path}/src/main/java/A.java:[3,9] cannot find symbol\n"
           "[WARNING] /w/src/main/java/B.java:[1,1] deprecated\n")
    r = MavenRunner(tmp_path, mvn=str(_fake_mvn(tmp_path, 1, log)))
    b = r.build()
    assert not b.ok and b.warnings == 1
    assert b.errors == ["src/main/java/A.java:3:9: cannot find symbol (symbol: variable x; location: class A)"]
    r2 = MavenRunner(tmp_path, mvn=str(_fake_mvn(tmp_path, 1, "[ERROR] Failed to execute goal on project a: "
                                                             "Could not resolve dependencies\\n[ERROR] -> [Help 1]\\n")))
    assert r2.build().errors == ["[ERROR] Failed to execute goal on project a: Could not resolve dependencies"]


def test_test_run_joins_surefire_reports_to_the_index(tmp_path, monkeypatch):
    reports = tmp_path / "target" / "surefire-reports"
    reports.mkdir(parents=True)
    (reports / "TEST-com.gmf.a.ARulesTests.xml").write_text(
        '<testsuite><testcase classname="com.gmf.a.ARulesTests" name="r1_x" time="0.01">'
        '<failure message="no test body for R1"/></testcase></testsuite>')
    (tmp_path / ".exiqo").mkdir()
    (tmp_path / INDEX_PATH).write_text(json.dumps([IndexEntry(
        name="com.gmf.a.ARulesTests.r1_x", kind="rule", rule_id="R1", path="p").model_dump()]))
    cleaned: list[Path] = []
    # the reports above stand in for a run (the fake mvn writes none): keep them through the pre-run clean
    monkeypatch.setattr(runner_mod.shutil, "rmtree", lambda path, ignore_errors=False: cleaned.append(Path(path)))
    run = MavenRunner(tmp_path, mvn=str(_fake_mvn(tmp_path))).test()
    assert cleaned == [reports]
    assert not run.ok and [(c.rule_id, c.outcome) for c in run.cases] == [("R1", "failed")]
    assert run.coverage_line is None


# ------------------------------------------------------------------ Task 8 review follow-ups
def test_compile_rule_speaks_the_stack_language():
    from engine.dotnet_gen.repair import COMPILE_RULE, compile_rule

    assert compile_rule("dotnet") == COMPILE_RULE  # .NET prompts unchanged
    java = compile_rule("java")
    assert "a missing import" in java and "using" not in java


def test_the_stack_names_the_fixture_the_test_engineer_shows(java_solution_with_tests, gmf_processes, gmf_scenarios):
    from engine.agents.test_engineer import _scenarios_block

    stack = get_stack("java")
    paths = {f.path for f in java_solution_with_tests.files}
    scenarios = [s for s in gmf_scenarios if s.process_id == FSL]
    assert scenarios
    for s in scenarios:
        assert stack.fixture_path(gmf_processes[FSL], s) in paths
    block = _scenarios_block(java_solution_with_tests, gmf_processes[FSL], scenarios, "java")
    assert "src/test/resources/fixtures/FundingStatusLookup/" in block and '"scenario_id"' in block


def test_selector_properties_parses_simple_selectors_only():
    from engine.stacks.java.text import selector_properties

    assert selector_properties("MsgType = 'A' AND Region='EU'") == [("MsgType", "A"), ("Region", "EU")]
    assert selector_properties("") == [] and selector_properties(None) == []
    assert selector_properties("Amount > 5") is None


def test_test_database_names_are_java_string_literals():
    from engine.stacks.java.generator import render

    text = render("TestDatabase.java.j2", pkg="com.gmf.x", schemas=['we"ird'], sql_files=["a\\b.sql"],
                  connections=["connection-strings.db"])
    assert 'List.of("we\\"ird")' in text and 'List.of("a\\\\b.sql")' in text


def test_runner_environment_keeps_the_mirror_trust_store(tmp_path, monkeypatch):
    monkeypatch.setenv("MAVEN_OPTS", "-Dmaven.repo.local=/cache/stacks/m2 -Djavax.net.ssl.trustStore=/tmp/exiqo-cacerts "
                       "-Djavax.net.ssl.trustStorePassword=changeit -Dhttps.proxyPassword=secret")
    env = MavenRunner(tmp_path, java="/opt/java/bin/java", mvn="/opt/maven/bin/mvn").env()
    assert env["MAVEN_OPTS"] == ("-Dmaven.repo.local=/cache/stacks/m2 -Djavax.net.ssl.trustStore=/tmp/exiqo-cacerts "
                                 "-Djavax.net.ssl.trustStorePassword=changeit")


# ------------------------------------------------------------------ X26: no mirror, no restore
def test_restore_refuses_without_a_maven_mirror(tmp_path):
    """Never Maven Central: settings with no mirror (or none at all) stop the restore with a reason."""
    exe = _fake_mvn(tmp_path)
    (tmp_path / ".exiqo").mkdir()
    for settings in (None, '<settings xmlns="http://maven.apache.org/SETTINGS/1.2.0">\n</settings>\n'):
        if settings:
            (tmp_path / ".exiqo" / "maven-settings.xml").write_text(settings)
        step = MavenRunner(tmp_path, java="java", mvn=str(exe)).restore()
        assert not step.ok and "MAVEN_MIRROR_URL" in step.log and "Maven Central" in step.log
    assert not (tmp_path / "calls.txt").exists()  # mvn never ran


def test_restore_runs_with_a_maven_mirror(tmp_path):
    exe = _fake_mvn(tmp_path)
    (tmp_path / ".exiqo").mkdir()
    (tmp_path / ".exiqo" / "maven-settings.xml").write_text(
        "<settings><mirrors><mirror><id>gmf</id><mirrorOf>*</mirrorOf><url>https://m.gmf.example/maven</url>"
        "</mirror></mirrors></settings>")
    assert MavenRunner(tmp_path, java="java", mvn=str(exe)).restore().ok
