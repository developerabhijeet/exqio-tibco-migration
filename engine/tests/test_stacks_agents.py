"""Agents follow the target stack (target-stacks spec section 3): prompt packs, the generic test-body
check, and the Code Builder, Test Engineer, review panel and repair gate reading their text checks
through ``get_stack(stack).language``.

A small Java-like stack (and Python- and Go-like ones) is registered here for the duration of a
test, so the agents' stack handling is checked independently of the real ``JavaLanguage``.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from engine import stacks
from engine.agents import builder, prompting
from engine.agents.builder import check_filled_file, fill_method_bodies
from engine.agents.prompting import STACK_PROMPTS, load_prompt, render_prompt
from engine.agents.provider import FakeProvider
from engine.agents.reviewers import reviewed_test_files
from engine.agents.routing import RoutedProvider, RoutingTable
from engine.agents.test_engineer import fill_test_bodies, placeholder_methods
from engine.analysis.models import Citation, Rule
from engine.dotnet_gen.generator import GeneratedSolution, GenFile
from engine.dotnet_gen.models import DesignRecord, MappingRow, ProjectLayout, SolutionLayout
from engine.dotnet_gen.repair import check_diff
from engine.ir.models import Activity, Complexity, Process, Transition
from engine.stacks.generic_checks import check_generic_test_body
from engine.stacks.java.language import JavaLanguage
from engine.stacks.testindex import IndexEntry, write_index

# ---------------------------------------------------------------- a Java-like stack for the tests
_DECL = re.compile(r"^(?:public|protected|private)\b[^;=]*\)\s*(?:throws [\w., ]+)?\s*\{?$")


class JavaLikeLanguage:
    comment_prefix = "//"
    import_word = "import"
    forbidden = JavaLanguage().forbidden
    placeholder = re.compile(r'UnsupportedOperationException\(\s*"agent fills:')

    def test_placeholder(self, test_id: str) -> str:
        return f'fail("no test body for {test_id}");'

    def strip_comments(self, text: str) -> str:
        return builder.strip_comments(text)

    def code_only(self, text: str) -> str:
        return builder.code_only(text)

    def declarations(self, text: str) -> list[str]:
        out = []
        for line in builder.strip_comments(text).splitlines():
            s = " ".join(line.split())
            if _DECL.match(s):
                out.append(s.rstrip("{").rstrip())
        return out

    def tibco_comments(self, text: str) -> list[str]:
        return builder.tibco_comments(text)

    def is_test_path(self, path: str) -> bool:
        return "src/test/" in path

    def check_test_body(self, body: str, stem: str, kind: str = "rule",
                        json_kinds: dict[str, str] | None = None) -> str | None:
        return check_generic_test_body(body, "java")


class JavaLikeStack:
    key = "java"
    label = "Java (test stand-in)"
    required_tools = ("java", "mvn")
    source_suffixes = (".java",)
    prompt_pack = "java"
    download_excluded = frozenset({"target"})
    service_process_marker = "java"
    language = JavaLikeLanguage()

    def layout(self, app_name):  # pragma: no cover - not used by the agents
        raise NotImplementedError

    def fixture_path(self, process, scenario):
        from engine.stacks.java.stack import JavaStack
        return JavaStack().fixture_path(process, scenario)

    def generate(self, *a, **k):  # pragma: no cover
        raise NotImplementedError

    def runner(self, *a, **k):  # pragma: no cover
        raise NotImplementedError


@pytest.fixture
def java_stack(monkeypatch):
    monkeypatch.setitem(stacks._FACTORIES, "java", JavaLikeStack)
    monkeypatch.delitem(stacks._INSTANCES, "java", raising=False)
    yield
    stacks._INSTANCES.pop("java", None)


@pytest.fixture
def tmp_prompts(tmp_path, monkeypatch):
    """A copy of the prompts with a Java pack holding only ``builder``."""
    root = tmp_path / "prompts"
    shutil.copytree(prompting.PROMPT_DIR, root, ignore=shutil.ignore_patterns("java", "go", "python"))
    (root / "java").mkdir()
    (root / "java" / "builder.md").write_text(
        "You fill Java 21 method bodies for {process_name}. Use BigDecimal, records and Optional.\n"
        "<!-- user -->\nTargets:\n{targets}\n", encoding="utf-8")
    monkeypatch.setattr(prompting, "PROMPT_DIR", root)
    return root


def routed(fake: FakeProvider) -> RoutedProvider:
    async def rec(_r) -> None:
        return None

    return RoutedProvider(fake, None, RoutingTable(), rec)


def user_text(fake: FakeProvider, i: int = 0) -> str:
    return "\n".join(str(m["content"]) for m in fake.requests[i].messages if m["role"] == "user")


# ---------------------------------------------------------------- prompt packs
def test_load_prompt_falls_back_to_top_level_for_dotnet():
    assert load_prompt("builder", "dotnet") == load_prompt("builder")
    assert render_prompt("repair", "dotnet", attempt="1") == render_prompt("repair", attempt="1")


def test_load_prompt_uses_the_stack_pack(tmp_prompts):
    system, user = load_prompt("builder", "java")
    assert "Java" in system and "C#" not in system and "{targets}" in user


def test_a_prompt_missing_from_the_pack_and_the_shared_prompts_come_from_the_top_level(tmp_prompts, java_stack):
    assert load_prompt("repair", "java") == load_prompt("repair")
    (tmp_prompts / "java" / "analyst.md").write_text("not used\n<!-- user -->\nx", encoding="utf-8")
    assert "analyst" not in STACK_PROMPTS
    assert load_prompt("analyst", "java") == load_prompt("analyst")


def test_load_prompt_refuses_an_unknown_stack():
    with pytest.raises(KeyError):
        load_prompt("builder", "cobol")


# ---------------------------------------------------------------- generic test-body check
@pytest.mark.parametrize("stack,body,reason", [
    ("python", "assert True", "no assertion on a value under test"),
    ("go", 'if false { t.Fatal("x") }', "no assertion on a value under test"),
    ("go", 't.Fatal("always")', "no assertion on a value under test"),
    ("java", "Thread.sleep(10); assertThat(x).isEqualTo(1);", "sleeps are not allowed"),
    ("java", "assertThat(true).isTrue();", "no assertion on a value under test"),
    ("java", '// assertThat(result).isEqualTo(1);\nString s = "assertThat(result)";', "no assertion on a value under test"),
    ("python", "pytest.fail('no test body for R1')", "placeholder still present"),
    ("python", "time.sleep(1)\nassert result.ok", "sleeps are not allowed"),
    ("go", "time.Sleep(time.Second)\nif got != 2 { t.Fatalf(\"got %d\", got) }", "sleeps are not allowed"),
    ("python", "data = open('/etc/passwd').read()\nassert data", "opens a socket or a file"),
    ("java", 'var f = new File("x");\nassertThat(f.exists()).isTrue();', "opens a socket or a file"),
    ("go", 'b, _ := os.ReadFile("x")\nif len(b) == 0 { t.Fatal("empty") }', "opens a socket or a file"),
    ("python", "\n".join(["x = 1"] * 121) + "\nassert x == 1", "longer than 120 lines"),
])
def test_generic_check_refusals(stack, body, reason):
    assert reason in (check_generic_test_body(body, stack) or "")


@pytest.mark.parametrize("stack,body", [
    ("java", 'var result = service.execute(input);\nassertThat(result.status()).isEqualTo(200);'),
    ("java", 'assertThrows(DealerInactiveException.class, () -> service.execute(input));'),
    ("go", 'got, err := svc.Execute(ctx, in)\nif err != nil || got.Status != 200 {\n\tt.Fatalf("got %v", got)\n}'),
    ("python", "result = service.execute(payload)\nassert result.status == 200"),
    ("python", "with pytest.raises(DealerInactive):\n    service.execute(payload)"),
])
def test_generic_check_accepts_an_assertion_on_the_result(stack, body):
    assert check_generic_test_body(body, stack) is None


def test_generic_check_is_not_for_dotnet():
    with pytest.raises(KeyError):
        check_generic_test_body("Assert.Equal(1, x);", "dotnet")


# ---------------------------------------------------------------- Code Builder
APP = "gmfcredit"
MAPPINGS = f"src/main/java/com/gmf/{APP}/mappings/LoanRatesMappings.java"
SIGNATURE = "public static Map<String, Object> countMapping(LoanRatesState state) {"
JAVA_MAPPINGS = f"""package com.gmf.{APP}.mappings;

public final class LoanRatesMappings {{
    {SIGNATURE}
        // TIBCO: numofpulls = $start/pulls + 1
        throw new UnsupportedOperationException("agent fills: Count");
    }}
}}
"""
JAVA_FILLED = JAVA_MAPPINGS.replace(
    '        throw new UnsupportedOperationException("agent fills: Count");',
    '        return Map.of("numofpulls", state.pulls().add(BigDecimal.ONE));')


def _process() -> Process:
    return Process(
        id="LoanRates", application="GMF", module="loans", name="LoanRates", dialect="bw6",
        source_path="loans/LoanRates.bwp",
        activities=[Activity(id="start", name="Start", type="rest.receive", native_type="x"),
                    Activity(id="map", name="Count", type="mapper", native_type="x")],
        transitions=[Transition(id="t2", from_id="start", to_id="map", kind="conditional",
                                expression="$count > 1")],
        complexity=Complexity(score=1.0, band="low"))


def _design() -> DesignRecord:
    return DesignRecord(
        process_id="LoanRates", spec_hash="s", stack="java",
        solution=SolutionLayout(name=APP, projects=[ProjectLayout(name="app", kind="core", files=[])]),
        mapping=[MappingRow(activity_id="map", activity_name="Count", canonical_type="mapper", pattern="mapper",
                            project="main/java", file=f"com/gmf/{APP}/mappings/LoanRatesMappings.java",
                            class_name="LoanRatesMappings", method="countMapping", origin="agent")])


def _solution(root: Path, *files: GenFile) -> GeneratedSolution:
    for f in files:
        (root / f.path).parent.mkdir(parents=True, exist_ok=True)
        (root / f.path).write_text(f.content, encoding="utf-8")
    return GeneratedSolution(root=root, name=APP, files=list(files), build_hash="h")


def _files_reply(path: str, content: str) -> str:
    return json.dumps({"files": [{"path": path, "content": content}]})


async def test_builder_uses_stack_language(tmp_path, java_stack, tmp_prompts):
    solution = _solution(tmp_path, GenFile(path=MAPPINGS, content=JAVA_MAPPINGS, origin="agent",
                                           process_id="LoanRates"))
    fake = FakeProvider([_files_reply(MAPPINGS, JAVA_FILLED)])
    filled = await fill_method_bodies(solution, _design(), _process(), [], [], routed(fake), stack="java")
    assert [f.path for f in filled] == [MAPPINGS] and "BigDecimal.ONE" in filled[0].content
    prompt = user_text(fake)
    assert "UnsupportedOperationException" in prompt and SIGNATURE.rstrip(" {") in prompt
    assert "Java 21" in fake.requests[0].system  # the Java pack


async def test_the_dotnet_language_does_not_see_a_java_placeholder(tmp_path, java_stack):
    solution = _solution(tmp_path, GenFile(path=MAPPINGS, content=JAVA_MAPPINGS, origin="agent",
                                           process_id="LoanRates"))
    fake = FakeProvider([])
    assert await fill_method_bodies(solution, _design(), _process(), [], [], routed(fake)) == []
    assert fake.requests == []


def test_filled_file_checks_follow_the_stack(java_stack):
    kept = JAVA_FILLED.replace("// TIBCO: numofpulls = $start/pulls + 1\n", "")
    assert "TIBCO expression comment is missing" in check_filled_file(MAPPINGS, JAVA_MAPPINGS, kept, stack="java")
    renamed = JAVA_FILLED.replace("countMapping(", "count(")
    assert "signature changed" in check_filled_file(MAPPINGS, JAVA_MAPPINGS, renamed, stack="java")
    assert "placeholder is still in the file" in check_filled_file(MAPPINGS, JAVA_MAPPINGS, JAVA_MAPPINGS,
                                                                  stack="java")
    assert check_filled_file(MAPPINGS, JAVA_MAPPINGS, JAVA_FILLED, stack="java") is None
    test_path = f"src/test/java/com/gmf/{APP}/LoanRatesRulesTests.java"
    assert "is a test file" in check_filled_file(test_path, "", "", stack="java")


# ---------------------------------------------------------------- Test Engineer and test reviewer
TESTS = f"src/test/java/com/gmf/{APP}/LoanRatesRulesTests.java"
JAVA_TESTS = f"""package com.gmf.{APP};

class LoanRatesRulesTests {{
    @Test
    public void r1_counts_pulls() {{
        fail("no test body for R1");
    }}

    @Test
    public void r2_waits() {{
        fail("no test body for R2");
    }}

    @Test
    public void branch_t2() {{
        var kept = 1;
        assertThat(kept).isEqualTo(1);
    }}
}}
"""


def _index() -> GenFile:
    pkg = f"com.gmf.{APP}.LoanRatesRulesTests"
    return write_index([
        IndexEntry(name=f"{pkg}.r1_counts_pulls", kind="rule", rule_id="R1", process_id="LoanRates",
                   path=TESTS, has_body=False),
        IndexEntry(name=f"{pkg}.r2_waits", kind="rule", rule_id="R2", process_id="LoanRates", path=TESTS,
                   has_body=False),
        IndexEntry(name=f"{pkg}.branch_t2", kind="branch", branch_id="t2", process_id="LoanRates", path=TESTS),
    ])


def _rules() -> list[Rule]:
    return [Rule(id=rid, text=text, status="approved", confidence=0.9,
                 citations=[Citation(activity_id="map", kind="expression", text="$start/pulls + 1")])
            for rid, text in (("R1", "pulls are counted plus one"), ("R2", "the count is returned"))]


def test_placeholder_methods_come_from_the_index(java_stack):
    entries = [e for e in (IndexEntry.model_validate(d) for d in json.loads(_index().content))]
    methods = placeholder_methods(JAVA_TESTS, "java", entries)
    assert [(m.name, m.kind, m.ref, m.label) for m in methods] == [
        ("r1_counts_pulls", "rule", "R1", "R1"), ("r2_waits", "rule", "R2", "R2")]


async def test_test_engineer_checks_and_splices_with_the_stack_language(tmp_path, java_stack):
    solution = _solution(tmp_path, GenFile(path=TESTS, content=JAVA_TESTS, origin="agent", process_id="LoanRates"),
                         _index())
    good = "var result = LoanRatesMappings.countMapping(state);\nassertThat(result.get(\"numofpulls\")).isEqualTo(3);"
    reply = json.dumps({"tests": [{"method": "r1_counts_pulls", "body": good},
                                  {"method": "r2_waits", "body": "Thread.sleep(5);\nassertThat(x).isEqualTo(1);"}]})
    retry = json.dumps({"tests": [{"method": "r2_waits", "body": "Thread.sleep(5);"}]})
    fake = FakeProvider([reply, retry])
    written = await fill_test_bodies(solution, _process(), _rules(), [], _design(), [], routed(fake), stack="java")
    assert len(written) == 1
    text = written[0].content
    assert '        assertThat(result.get("numofpulls")).isEqualTo(3);' in text
    assert 'fail("no test body for R2: sleeps are not allowed' in text
    assert "        var kept = 1;" in text  # the written test is left alone
    assert "fail(\"no test body for R1\")" not in text
    assert "r2_waits" in user_text(fake, 1) and "sleeps are not allowed" in user_text(fake, 1)

    # the test reviewer now sees the file: it has written tests, the placeholder one aside
    solution.files = [written[0] if f.path == TESTS else f for f in solution.files]
    assert list(reviewed_test_files(solution, _process(), "java")) == [TESTS]


def test_reviewer_skips_a_test_file_with_only_placeholders(tmp_path, java_stack):
    only = JAVA_TESTS.split("    @Test\n    public void branch_t2")[0].rstrip() + "\n}\n"
    index = write_index([e for e in (IndexEntry.model_validate(d) for d in json.loads(_index().content))
                         if e.kind == "rule"])
    solution = _solution(tmp_path, GenFile(path=TESTS, content=only, origin="agent"), index)
    assert reviewed_test_files(solution, _process(), "java") == {}


# ---------------------------------------------------------------- repair gate
def test_repair_gate_reads_test_paths_through_the_stack(tmp_path, java_stack):
    solution = _solution(tmp_path, GenFile(path=TESTS, content=JAVA_TESTS, origin="agent"),
                         GenFile(path=MAPPINGS, content=JAVA_FILLED, origin="agent"))
    diff = (f"--- a/{TESTS}\n+++ b/{TESTS}\n@@ -1,1 +1,1 @@\n-package com.gmf.{APP};\n+package com.gmf.{APP}.x;\n")
    _, reason = check_diff(diff, solution, stack="java")
    assert "is a test file" in reason
    # .NET reads "tests/" folders only, so the same path is not a test path there
    _, reason = check_diff(diff, solution)
    assert "is a test file" not in reason



async def test_a_rejection_reason_with_quotes_and_backslashes_stays_one_literal(tmp_path, java_stack, monkeypatch):
    monkeypatch.setattr(JavaLikeLanguage, "check_test_body",
                        lambda self, body, stem, kind="rule", json_kinds=None: 'the body said "x" and \\e `y`\nz')
    solution = _solution(tmp_path, GenFile(path=TESTS, content=JAVA_TESTS, origin="agent", process_id="LoanRates"),
                         _index())
    reply = json.dumps({"tests": [{"method": "r1_counts_pulls", "body": "assertThat(a).isEqualTo(1);"},
                                  {"method": "r2_waits", "body": "assertThat(b).isEqualTo(2);"}]})
    fake = FakeProvider([reply, reply])
    written = await fill_test_bodies(solution, _process(), _rules(), [], _design(), [], routed(fake), stack="java")
    text = written[0].content
    assert 'fail("no test body for R1: the body said x and e y z");' in text
    entries = [IndexEntry.model_validate(d) for d in json.loads(_index().content)]
    assert [m.label for m in placeholder_methods(text, "java", entries)] == ["R1", "R2"]  # still found


# ---------------------------------------------------------------- indentation languages (Python, Go)
class PythonLikeLanguage(JavaLikeLanguage):
    comment_prefix = "#"
    import_word = "import"

    def test_placeholder(self, test_id: str) -> str:
        return f'pytest.fail("no test body for {test_id}")'

    def declarations(self, text: str) -> list[str]:
        return [" ".join(ln.split()) for ln in text.splitlines() if re.match(r"^\s*(?:async\s+)?def \w+\(", ln)]

    def is_test_path(self, path: str) -> bool:
        return path.startswith("tests/")

    def check_test_body(self, body, stem, kind="rule", json_kinds=None):
        return check_generic_test_body(body, "python")


class GoLikeLanguage(JavaLikeLanguage):
    def test_placeholder(self, test_id: str) -> str:
        return f't.Fatal("no test body for {test_id}")'

    def declarations(self, text: str) -> list[str]:
        return [" ".join(ln.split()).rstrip("{ ") for ln in text.splitlines() if re.match(r"^func \w+\(", ln)]

    def is_test_path(self, path: str) -> bool:
        return path.endswith("_test.go")

    def check_test_body(self, body, stem, kind="rule", json_kinds=None):
        return check_generic_test_body(body, "go")


def _register(monkeypatch, key: str, lang) -> None:
    stack = type(f"{key}Stack", (JavaLikeStack,), {"key": key, "prompt_pack": key, "language": lang,
                                                   "source_suffixes": (".py",) if key == "python" else (".go",)})
    monkeypatch.setitem(stacks._FACTORIES, key, stack)
    monkeypatch.delitem(stacks._INSTANCES, key, raising=False)


PY_TESTS = "tests/test_loan_rates_rules.py"
PY_TEXT = '''import pytest


def test_r1_counts_pulls(service):
    pytest.fail("no test body for R1")


def test_r2_kept(service):
    assert service.count(1) == 2
'''
GO_TESTS = "internal/core/loan_rates_rules_test.go"
GO_TEXT = '''package core

func TestR1CountsPulls(t *testing.T) {
\tt.Fatal("no test body for R1")
}

func TestR2Kept(t *testing.T) {
\tif Count(1) != 2 {
\t\tt.Fatal("count")
\t}
}
'''


def _entries(path: str, *names: str) -> GenFile:
    return write_index([IndexEntry(name=n, kind="rule", rule_id=f"R{i}", process_id="LoanRates", path=path)
                        for i, n in enumerate(names, 1)])


async def test_python_body_is_spliced_by_indentation(tmp_path, monkeypatch):
    _register(monkeypatch, "python", PythonLikeLanguage())
    solution = _solution(tmp_path, GenFile(path=PY_TESTS, content=PY_TEXT, origin="agent"),
                         _entries(PY_TESTS, "tests.test_loan_rates_rules.test_r1_counts_pulls",
                                  "tests.test_loan_rates_rules.test_r2_kept"))
    body = "def helper(x):\n    return x\n\nresult = service.count(helper(2))\nassert result == 3"
    fake = FakeProvider([json.dumps({"tests": [{"method": "test_r1_counts_pulls", "body": body}]})])
    written = await fill_test_bodies(solution, _process(), _rules(), [], _design(), [], routed(fake), stack="python")
    assert written, "a nested def is not a changed number of tests"
    assert ("def test_r1_counts_pulls(service):\n    def helper(x):\n        return x\n\n"
            "    result = service.count(helper(2))\n    assert result == 3\n\n\ndef test_r2_kept") in written[0].content
    stacks._INSTANCES.pop("python", None)


async def test_go_body_is_spliced_with_the_file_s_tabs(tmp_path, monkeypatch):
    _register(monkeypatch, "go", GoLikeLanguage())
    solution = _solution(tmp_path, GenFile(path=GO_TESTS, content=GO_TEXT, origin="agent"),
                         _entries(GO_TESTS, "core.TestR1CountsPulls", "core.TestR2Kept"))
    body = 'got := Count(2)\nif got != 3 {\n    t.Fatalf("got %d", got)\n}'
    fake = FakeProvider([json.dumps({"tests": [{"method": "TestR1CountsPulls", "body": body}]})])
    written = await fill_test_bodies(solution, _process(), _rules(), [], _design(), [], routed(fake), stack="go")
    assert ('func TestR1CountsPulls(t *testing.T) {\n\tgot := Count(2)\n\tif got != 3 {\n\t    t.Fatalf("got %d", got)\n'
            '\t}\n}\n') in written[0].content
    stacks._INSTANCES.pop("go", None)


def test_go_fatal_after_a_closed_if_is_not_an_assertion():
    assert "no assertion" in check_generic_test_body('if got != 1 { log(got) }\nt.Fatal("x")', "go")
    assert check_generic_test_body('if got != 1 {\n\tlog(got)\n\tt.Fatal("x")\n}', "go") is None
