"""Go tests written first (target-stacks spec section 4): ``testing`` tests from approved rules, IR
branches and parity scenarios, generated before any method body is filled.

Per process (``tests/``, package ``tests``, black-box: it imports ``internal/`` from inside the
module):

* ``<process>_rules_test.go``: one ``func Test<Process>Rules_R1_<Slug>(t *testing.T)`` per approved
  (or edited) rule, body ``t.Fatal("no test body for R1")`` until the Test Engineer writes it;
* ``<process>_branch_test.go``: one test per conditional or otherwise transition; it runs the first
  scenario whose TIBCO trace takes that link, or fails with ``no test body for branch <id>``;
* ``<process>_scenario_test.go``: one test per scenario. The driver (``run<Process>Scenario``)
  sends the fixture request to ``httptest.NewServer(api.NewRouter(deps))``, hands the input to
  the JMS consumer or runs the job once, and compares the run with
  ``tests/fixtures/<Process>/<scenario>.json`` field by field, side effects against the test
  database and the in-memory bus.

Per project: ``testdb_test.go`` (the database at ``TEST_DATABASE_URL``, a skip without it),
``scenario_support_test.go`` and the estate's SQL under ``tests/database/``. ``go test -json``
reports a test as ``<module>/tests`` and its name, so the test index (``.exiqo/test-index.json``)
names every test ``<module>/tests.<TestName>`` with its rule, branch or scenario id; the Test
Engineer finds the functions to fill through it.

Go refuses an unused import, so every generated test file refers to its vocabulary once
(``var _ = http.MethodGet``): a body written later can use those packages without an import edit.
The fixtures and the stack-neutral naming come from ``engine.dotnet_gen.tests_gen``; the fixture
documents are the .NET ones, byte for byte.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from engine.analysis.models import Rule
from engine.dotnet_gen import tests_gen
from engine.dotnet_gen.generator import GenFile
from engine.dotnet_gen.models import DesignRecord
from engine.dotnet_gen.trx import BRANCH_PREFIX, SCENARIO_PREFIX
from engine.ir.models import Activity, Process
from engine.parity.models import Scenario
from engine.stacks.go.naming import snake
from engine.stacks.go.text import doc, go_string, import_block, one_line, selector_properties
from engine.stacks.sql import time_columns
from engine.stacks.testindex import IndexEntry

if TYPE_CHECKING:
    from engine.stacks.plan import ServicePlan

TEST_FOLDER = "tests"
_LIMIT = 100  # the longest test name


@dataclass
class _TimeColumns:
    """The part of the .NET ``ProcGen`` the shared fixture writer reads: a JDBC step's date columns
    (from its TIBCO column metadata), so seeded dates relative to the load time count as noise."""

    def time_columns(self, act: Activity) -> set[str]:
        return time_columns(act.config.get("settings"))


@dataclass
class GoTestContext:
    """What the tests need to know about the generated code of one process."""

    __test__ = False

    module: str  # the Go module path (gmf-credit)
    app: str  # the application type name (GmfCredit)
    process: Process
    name: str  # the process type name (DealerFundingRequest)
    sql_texts: dict[str, str] = field(default_factory=dict)
    schemas: list[str] = field(default_factory=list)

    @property
    def stem(self) -> str:
        return snake(self.name)

    def fixture_context(self, kind: str) -> tests_gen.TestContext:
        return tests_gen.TestContext(app=self.app, process=self.name, starter_kind=kind, pg=_TimeColumns(),
                                     sql_texts=self.sql_texts, schemas=self.schemas)

    def index_name(self, test: str) -> str:
        return f"{self.module}/{TEST_FOLDER}.{test}"


def scenario_fixture_path(type_name: str, process: Process, scenario: Scenario) -> str:
    """The fixture of a scenario of the process whose type is ``type_name``, from the project root
    (``GoStack.fixture_path``)."""
    return f"{TEST_FOLDER}/fixtures/{type_name}/{tests_gen.scenario_stem(process, scenario)}.json"


def fixture_path(ctx: GoTestContext, scenario: Scenario) -> str:
    return scenario_fixture_path(ctx.name, ctx.process, scenario)


def test_name(prefix: str, words: str, used: set[str]) -> str:
    """``<prefix>_<Words in PascalCase>`` cut at a word boundary, unique in the package."""
    name = re.sub(r"\W", "_", prefix)
    tail = ""
    for word in re.findall(r"[A-Za-z0-9]+", words):
        cand = tail + word[:1].upper() + word[1:]
        if len(name) + 1 + len(cand) > _LIMIT:
            break
        tail = cand
    name = f"{name}_{tail}" if tail else name
    base, n = name, 2
    while name in used:
        name, n = f"{base}_{n}", n + 1
    used.add(name)
    return name


def _doc_lines(name: str, summary: str, *paragraphs: str) -> list[str]:
    lines = [f"// {name} is {doc(summary)}"]
    for p in paragraphs:
        lines += ["//", f"// {doc(p)}"]
    return lines


# Every test file refers to these once, so a body may use them without an import edit.
_VOCABULARY = (("context", "context.Background"), ("encoding/json", "json.Marshal"), ("net/http", "http.MethodGet"),
               ("strings", "strings.Contains"), ("testing", "testing.Short"))


def _header(ctx: GoTestContext, extra: dict[str, str]) -> list[str]:
    """``package tests``, the imports and the blank uses of the vocabulary (import -> what it names)."""
    uses = {imp: ref for imp, ref in _VOCABULARY}
    uses.update({f"{ctx.module}/internal/core": "core.NewServices", f"{ctx.module}/internal/tib": "tib.Path"})
    uses.update(extra)
    lines = ["package tests", "", *import_block(set(uses), ctx.module), "",
             "// The vocabulary a test body is written against: the blank uses keep the imports while no body",
             "// needs them.", "var ("]
    lines += [f"\t_ = {ref}" for _, ref in sorted(uses.items()) if ref]
    return lines + [")"]


# ------------------------------------------------------------------ drivers
@dataclass
class _Driver:
    supported: bool
    imports: dict[str, str]  # import -> a name it declares (for the blank use)
    body: list[str]  # the statements of ``run<Process>Scenario(t, scenario)``
    what: str


def _driver(ctx: GoTestContext, plan: ServicePlan) -> _Driver:
    kind = plan.starter.kind
    process = go_string(ctx.name)
    n = ctx.name
    if kind in ("rest", "http"):
        return _Driver(True, {}, [f"\tnewScenario(t).runRequest({process}, scenario)"],
                       "sends the fixture request to the handler over HTTP")
    if kind == "jms":
        pairs = ", ".join(f"{go_string(k)}: {go_string(v)}" for k, v in selector_properties(plan.starter.selector) or [])
        return _Driver(True, {f"{ctx.module}/internal/infrastructure/messaging": f"messaging.{n}Entity"},
                       ["\te := newScenario(t)",
                        (f"\te.runMessage({process}, scenario, messaging.{n}Entity, map[string]string{{{pairs}}}, "
                         f"messaging.New{n}Consumer(e.deps.Services.{n}).Handle)")],
                       f"hands the fixture input to {n}Consumer.Handle as a message")
    if kind == "timer":
        return _Driver(True, {f"{ctx.module}/internal/infrastructure/jobs": f"jobs.New{n}Job"},
                       ["\te := newScenario(t)", f"\te.runJob({process}, scenario, jobs.New{n}Job(e.deps.Services.{n}).Run)"],
                       f"runs {n}Job once")
    return _Driver(False, {}, [f'\tt.Fatal("no test body for scenario " + scenario + ": no driver for a {kind} starter")'],
                   "has no generated driver for this starter")


def _run_function(ctx: GoTestContext, driver: _Driver) -> list[str]:
    name = f"run{ctx.name}Scenario"
    return ["", f"// {name} {doc(driver.what)} and compares the run with the fixture.",
            f"func {name}(t *testing.T, scenario string) {{", "\tt.Helper()", *driver.body, "}"]


# ------------------------------------------------------------------ test files
@dataclass
class _File:
    text: str
    entries: list[IndexEntry]
    origin: Literal["code", "agent"]


def rules_tests(ctx: GoTestContext, rules: list[Rule]) -> _File:
    approved = [r for r in rules if r.status in tests_gen.APPROVED]
    path = f"{TEST_FOLDER}/{ctx.stem}_rules_test.go"
    lines = _header(ctx, {})
    lines += ["", (f"// One test per approved rule of {doc(ctx.process.name)}, named in the rule's own words; written "
                  "before the code."),
              "// A test fails with no test body until the Test Engineer writes it; gate G4 refuses the build while any",
              "// approved rule has no test body."]
    used: set[str] = set()
    entries: list[IndexEntry] = []
    for r in approved:
        name = test_name(f"Test{ctx.name}Rules_{r.id}", r.text, used)
        text = one_line(r.text)
        cites = "; ".join(f"{c.activity_id} ({c.kind}): {one_line(c.text)}" for c in r.citations) or "none"
        display = f"{r.id} {text}"
        lines += ["", *_doc_lines(name, f"{r.id}: {text}", f"Citations: {cites}"),
                  f"func {name}(t *testing.T) {{", f"\tt.Fatal({go_string(f'no test body for {r.id}')})", "}"]
        entries.append(IndexEntry(name=ctx.index_name(name), kind="rule", rule_id=r.id, display_name=display,
                                  process_id=ctx.process.id, path=path, has_body=False))
    return _File("\n".join(lines) + "\n", entries, "agent" if approved else "code")


def _covering(scenarios: list[Scenario], transition_id: str) -> Scenario | None:
    for s in scenarios:
        if s.expected and any(step.chosen_transition == transition_id for step in s.expected.trace):
            return s
    return None


def branch_tests(ctx: GoTestContext, plan: ServicePlan, scenarios: list[Scenario]) -> _File:
    process = ctx.process
    names = {a.id: a.name for a in process.activities}
    branches = tests_gen.branch_transitions(process)
    covered = {t.id: _covering(scenarios, t.id) for t in branches}
    driver = _driver(ctx, plan)
    path = f"{TEST_FOLDER}/{ctx.stem}_branch_test.go"
    lines = _header(ctx, {})
    lines += ["", (f"// One test per branch (conditional or otherwise link) of {doc(process.name)}. A branch test runs the "
                  "first"),
              "// scenario whose TIBCO trace takes the link; a branch no scenario reaches fails until the Test",
              "// Engineer writes its test."]
    used: set[str] = set()
    entries: list[IndexEntry] = []
    for t in branches:
        src, dst = names.get(t.from_id, t.from_id), names.get(t.to_id, t.to_id)
        when = f"when {one_line(t.expression or '')}" if t.kind == "conditional" else "otherwise"
        words = f"{src} to {dst} {'when ' + (t.expression or '') if t.kind == 'conditional' else 'otherwise'}"
        name = test_name(f"Test{ctx.name}Branch", words, used)
        sc = covered[t.id]
        line = f" (line {t.source_line})" if t.source_line else ""
        display = f"{BRANCH_PREFIX} [{t.id}] {src} to {dst} {when}"
        lines += ["", *_doc_lines(name, f"link {t.id}{line}: {src} to {dst} {when}.",
                                  f"Driven by scenario {sc.id}." if sc else "No scenario takes this link."),
                  f"func {name}(t *testing.T) {{"]
        if sc and driver.supported:
            lines.append(f"\trun{ctx.name}Scenario(t, {go_string(tests_gen.scenario_stem(process, sc))})")
        else:
            why = "no scenario takes this link" if not sc else f"no driver for a {plan.starter.kind} starter"
            lines.append(f"\tt.Fatal({go_string(f'no test body for branch {t.id}: {why}')})")
        lines.append("}")
        entries.append(IndexEntry(name=ctx.index_name(name), kind="branch", branch_id=t.id,
                                  display_name=display, process_id=process.id, path=path,
                                  has_body=bool(sc and driver.supported)))
    done = all(covered.values()) and (driver.supported or not branches)
    return _File("\n".join(lines) + "\n", entries, "code" if done else "agent")


def scenario_tests(ctx: GoTestContext, plan: ServicePlan, scenarios: list[Scenario]) -> _File:
    process = ctx.process
    driver = _driver(ctx, plan)
    path = f"{TEST_FOLDER}/{ctx.stem}_scenario_test.go"
    lines = _header(ctx, driver.imports)
    lines += ["", (f"// One test per parity scenario of {doc(process.name)}: the scenario input goes to the generated "
                  "service and the"),
              "// output, fault and side effects are compared field by field with the expected fixture",
              f"// ({doc(tests_gen.MODEL_LABEL)})."]
    used: set[str] = set()
    entries: list[IndexEntry] = []
    for s in scenarios:
        stem = tests_gen.scenario_stem(process, s)
        name = test_name(f"Test{ctx.name}Scenario", stem, used)
        display = f"{SCENARIO_PREFIX} [{s.id}] {one_line(s.name)}"
        lines += ["", f"// {name} is the {doc(f'{s.kind} scenario {s.id}: {one_line(s.name)}.')}",
                  f"func {name}(t *testing.T) {{", f"\trun{ctx.name}Scenario(t, {go_string(stem)})", "}"]
        entries.append(IndexEntry(name=ctx.index_name(name), kind="scenario", scenario_id=s.id,
                                  display_name=display, process_id=process.id, path=path,
                                  has_body=driver.supported))
    lines += _run_function(ctx, driver)
    return _File("\n".join(lines) + "\n", entries, "code" if driver.supported else "agent")


def generate_go_tests(plan: ServicePlan, rules: list[Rule], scenarios: list[Scenario], design: DesignRecord,
                      ctx: GoTestContext) -> tuple[list[GenFile], list[IndexEntry]]:
    """The rules, branch and scenario tests and the fixtures of one process (paths from the project
    root), with their test index entries."""
    if design.process_id != plan.process_id or ctx.process.id != plan.process_id:
        raise ValueError(f"the design, plan and context name different processes ({design.process_id}, "
                         f"{plan.process_id}, {ctx.process.id})")
    process = ctx.process
    scenarios = [s for s in scenarios if s.process_id == process.id and s.expected is not None]
    out: list[GenFile] = []
    entries: list[IndexEntry] = []
    for f, kind in ((rules_tests(ctx, rules), "rules"), (branch_tests(ctx, plan, scenarios), "branch"),
                    (scenario_tests(ctx, plan, scenarios), "scenario")):
        out.append(GenFile(path=f"{TEST_FOLDER}/{ctx.stem}_{kind}_test.go", content=f.text, origin=f.origin,
                           process_id=process.id))
        entries += f.entries
    fx = ctx.fixture_context(plan.starter.kind)
    for s in scenarios:
        out.append(GenFile(path=fixture_path(ctx, s), content=tests_gen.fixture_json(fx, process, s),
                           process_id=process.id))
    return out, entries


def generate_test_support(module: str, schemas: list[str], sql_texts: dict[str, str],
                          connections: list[str]) -> list[GenFile]:
    """``testdb_test.go`` and ``scenario_support_test.go``, and the estate SQL they run."""
    from engine.stacks.go.generator import render

    out = [GenFile(path=f"{TEST_FOLDER}/testdb_test.go",
                   content=render("testdb.go.j2", module=module, schemas=schemas, sql_files=list(sql_texts),
                                  connections=connections)),
           GenFile(path=f"{TEST_FOLDER}/scenario_support_test.go",
                   content=render("scenario_support.go.j2", module=module))]
    out += [GenFile(path=f"{TEST_FOLDER}/database/{name}", content=text) for name, text in sql_texts.items()]
    return out


__all__ = ["GoTestContext", "fixture_path", "generate_go_tests", "generate_test_support", "scenario_fixture_path"]
