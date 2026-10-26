"""Java tests written first (target-stacks spec section 4): JUnit 5 tests from approved rules, IR
branches and parity scenarios, generated before any method body is filled.

Per process (``src/test/java/com/gmf/<app>/``):

* ``<Process>RulesTests.java``: one ``@Test void r1_<slug>()`` per approved (or edited) rule, body
  ``fail("no test body for R1");`` until the Test Engineer writes it;
* ``<Process>BranchTests.java``: one test per conditional or otherwise transition; it runs the first
  scenario whose TIBCO trace takes that link, or fails with ``no test body for branch <id>``;
* ``<Process>ScenarioTests.java``: one test per scenario. ``ScenarioSupport`` (the shared base,
  ``@SpringBootTest(webEnvironment = RANDOM_PORT)``) drives the controller over HTTP, hands the
  input to the JMS consumer or runs the job once, and compares the run with
  ``src/test/resources/fixtures/<Process>/<scenario>.json`` field by field, side effects against
  the test database and the in-memory bus.

Per project: ``support/TestDatabase.java`` (the database at ``TEST_DATABASE_URL``, a skip without
it), ``support/Fixtures.java``, ``support/ScenarioSupport.java`` and the estate's SQL under
``src/test/resources/database/``. Surefire reports a test as ``<package>.<Class>`` and the method
name, so the test index (``.exiqo/test-index.json``) names every test ``<package>.<Class>.<method>``
with its rule, branch or scenario id; the Test Engineer finds the methods to fill through it.

The fixtures and the stack-neutral naming come from ``engine.dotnet_gen.tests_gen``; the fixture
documents are the .NET ones, byte for byte.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from engine.analysis.models import Rule
from engine.dotnet_gen import generator as dotnet
from engine.dotnet_gen import tests_gen
from engine.dotnet_gen.generator import GenFile
from engine.dotnet_gen.models import DesignRecord
from engine.dotnet_gen.trx import BRANCH_PREFIX, SCENARIO_PREFIX
from engine.ir.models import Activity, Process
from engine.parity.models import Scenario
from engine.stacks.java.text import doc, java_string, one_line
from engine.stacks.testindex import IndexEntry

if TYPE_CHECKING:
    from engine.stacks.plan import ServicePlan

_SELECTOR_ATOM = re.compile(r"^\s*([A-Za-z_][\w.]*)\s*=\s*'([^']*)'\s*$")
_KEYWORD_SAFE = re.compile(r"\W")

# Imported by every generated test file, used or not: a test body is written against this
# vocabulary, and an import the scaffold did not write is one the compile repair loop would have
# to add. Unused imports cost nothing (javac does not warn, Spotless keeps them).
_STATIC_IMPORTS = ("org.assertj.core.api.Assertions.assertThat", "org.junit.jupiter.api.Assertions.assertEquals",
                   "org.junit.jupiter.api.Assertions.assertFalse", "org.junit.jupiter.api.Assertions.assertNotNull",
                   "org.junit.jupiter.api.Assertions.assertThrows", "org.junit.jupiter.api.Assertions.assertTrue",
                   "org.junit.jupiter.api.Assertions.fail")


@dataclass
class _TimeColumns:
    """The part of the .NET ``ProcGen`` the shared fixture writer reads: a JDBC step's date columns
    (from its TIBCO column metadata), so seeded dates relative to the load time count as noise."""

    def time_columns(self, act: Activity) -> set[str]:
        return dotnet.ProcGen.time_columns(self, act)  # type: ignore[arg-type]  # reads only ``act``


@dataclass
class JavaTestContext:
    """What the tests need to know about the generated code of one process."""

    __test__ = False

    pkg: str  # com.gmf.<app>
    app: str  # the application type name (GmfCredit)
    process: Process
    name: str  # the process type name (DealerFundingRequest)
    test_folder: str  # src/test/java/com/gmf/<app>
    sql_texts: dict[str, str] = field(default_factory=dict)
    schemas: list[str] = field(default_factory=list)

    @property
    def service_class(self) -> str:
        return f"{self.name}Service"

    def fixture_context(self, kind: str) -> tests_gen.TestContext:
        return tests_gen.TestContext(app=self.app, process=self.name, starter_kind=kind, pg=_TimeColumns(),
                                     sql_texts=self.sql_texts, schemas=self.schemas)


def fixture_path(ctx: JavaTestContext, scenario: Scenario) -> str:
    """The fixture of a scenario, from the project root."""
    return f"src/test/resources/fixtures/{ctx.name}/{tests_gen.scenario_stem(ctx.process, scenario)}.json"


def _unique(name: str, used: set[str]) -> str:
    base, n = name, 2
    while name in used:
        name, n = f"{base}_{n}", n + 1
    used.add(name)
    return name


def _method(prefix: str, words: str) -> str:
    name = _KEYWORD_SAFE.sub("_", dotnet.method_name(prefix, words, lower=True))
    return name if name and not name[0].isdigit() else f"t_{name}"


def _header(ctx: JavaTestContext, imports: list[str]) -> list[str]:
    own = sorted({i for i in imports if i})
    return [f"package {ctx.pkg};", "", *[f"import static {i};" for i in _STATIC_IMPORTS], "",
            *[f"import {i};" for i in own], ""]


def _vocabulary(ctx: JavaTestContext) -> list[str]:
    return [f"{ctx.pkg}.core.{ctx.service_class}", f"{ctx.pkg}.support.Fixtures", f"{ctx.pkg}.support.ScenarioSupport",
            "com.fasterxml.jackson.databind.JsonNode", "java.math.BigDecimal", "java.net.http.HttpResponse",
            "java.util.List", "java.util.Map", "org.junit.jupiter.api.DisplayName", "org.junit.jupiter.api.Test"]


def _javadoc(summary: str, *paragraphs: str, pad: str = "") -> list[str]:
    lines = [f"{pad}/**", f"{pad} * {doc(summary)}"]
    for p in paragraphs:
        lines += [f"{pad} *", f"{pad} * <p>{doc(p)}"]
    return lines + [f"{pad} */"]


# ------------------------------------------------------------------ drivers
@dataclass
class _Driver:
    supported: bool
    imports: list[str]
    fields: list[str]
    body: list[str]  # the statements of ``run(String scenario)``
    what: str


def _selector_properties(selector: str | None) -> str:
    atoms = [_SELECTOR_ATOM.match(a) for a in re.split(r"(?i)\s+and\s+", selector)] if selector else []
    pairs = [f"{java_string(m.group(1))}, {java_string(m.group(2))}" for m in atoms if m] if all(atoms) else []
    return f"Map.of({', '.join(pairs)})"


def _driver(ctx: JavaTestContext, plan: ServicePlan) -> _Driver:
    kind = plan.starter.kind
    pad = "        "
    process = java_string(ctx.name)
    if kind in ("rest", "http"):
        return _Driver(True, [], [], [f"{pad}runRequest({process}, scenario);"],
                       "Sends the fixture request to the controller over HTTP")
    if kind == "jms":
        consumer = f"{ctx.name}Consumer"
        return _Driver(True, [f"{ctx.pkg}.infrastructure.messaging.{consumer}",
                              "org.springframework.beans.factory.annotation.Autowired"],
                       ["    @Autowired", f"    private {consumer} consumer;", ""],
                       [(f"{pad}runMessage({process}, scenario, {consumer}.DESTINATION, "
                        f"{_selector_properties(plan.starter.selector)}, consumer::handle);")],
                       f"Hands the fixture input to {consumer}.handle as a message")
    if kind == "timer":
        job = f"{ctx.name}Job"
        return _Driver(True, [f"{ctx.pkg}.infrastructure.jobs.{job}"], [],
                       [f"{pad}runJob({process}, scenario, () -> new {job}(bean({ctx.service_class}.class)).execute());"],
                       f"Runs {job} once")
    return _Driver(False, [], [], [f'{pad}fail("no test body for scenario " + scenario + ": no driver for a {kind} starter");'],
                   "No generated driver for this starter")


def _run_method(driver: _Driver) -> list[str]:
    return ["", f"    /** {doc(driver.what)} and compares the run with the fixture. */",
            "    private void run(String scenario) {", *driver.body, "    }"]


# ------------------------------------------------------------------ test files
@dataclass
class _File:
    text: str
    entries: list[IndexEntry]
    origin: Literal["code", "agent"]


def rules_tests(ctx: JavaTestContext, rules: list[Rule]) -> _File:
    approved = [r for r in rules if r.status in tests_gen.APPROVED]
    cls = f"{ctx.name}RulesTests"
    path = f"{ctx.test_folder}/{cls}.java"
    lines = _header(ctx, _vocabulary(ctx))
    lines += _javadoc(f"One test per approved rule of {ctx.process.name}, named in the rule's own words; written "
                      "before the code.", "A test fails with no test body until the Test Engineer writes it; gate G4 "
                      "refuses the build while any approved rule has no test body.")
    lines += [f"class {cls} extends ScenarioSupport {{"]
    used: set[str] = set()
    entries: list[IndexEntry] = []
    for i, r in enumerate(approved):
        if i:
            lines.append("")
        method = _unique(_method(r.id.lower(), r.text), used)
        text = one_line(r.text)
        cites = "; ".join(f"{c.activity_id} ({c.kind}): {one_line(c.text)}" for c in r.citations) or "none"
        display = f"{r.id} {text}"
        lines += [*_javadoc(f"{r.id}: {text}", f"Citations: {cites}", pad="    "),
                  "    @Test", f"    @DisplayName({java_string(display)})", f"    void {method}() {{",
                  f"        fail({java_string(f'no test body for {r.id}')});", "    }"]
        entries.append(IndexEntry(name=f"{ctx.pkg}.{cls}.{method}", kind="rule", rule_id=r.id, display_name=display,
                                  process_id=ctx.process.id, path=path, has_body=False))
    lines.append("}")
    return _File("\n".join(lines) + "\n", entries, "agent" if approved else "code")


def _covering(scenarios: list[Scenario], transition_id: str) -> Scenario | None:
    for s in scenarios:
        if s.expected and any(step.chosen_transition == transition_id for step in s.expected.trace):
            return s
    return None


def branch_tests(ctx: JavaTestContext, plan: ServicePlan, scenarios: list[Scenario]) -> _File:
    process = ctx.process
    names = {a.id: a.name for a in process.activities}
    branches = tests_gen.branch_transitions(process)
    covered = {t.id: _covering(scenarios, t.id) for t in branches}
    driver = _driver(ctx, plan)
    cls = f"{ctx.name}BranchTests"
    path = f"{ctx.test_folder}/{cls}.java"
    lines = _header(ctx, _vocabulary(ctx) + driver.imports)
    lines += _javadoc(f"One test per branch (conditional or otherwise link) of {process.name}.",
                      "A branch test runs the first scenario whose TIBCO trace takes the link; a branch no "
                      "scenario reaches fails until the Test Engineer writes its test.")
    lines += [f"class {cls} extends ScenarioSupport {{", *driver.fields]
    used: set[str] = {"run"}
    entries: list[IndexEntry] = []
    for i, t in enumerate(branches):
        if i:
            lines.append("")
        src, dst = names.get(t.from_id, t.from_id), names.get(t.to_id, t.to_id)
        when = f"when {one_line(t.expression or '')}" if t.kind == "conditional" else "otherwise"
        words = f"when {(t.expression or '').lower()}" if t.kind == "conditional" else "otherwise"
        method = _unique(_method(f"branch_{dotnet.ident(src)}_to_{dotnet.ident(dst)}", words), used)
        sc = covered[t.id]
        line = f" (line {t.source_line})" if t.source_line else ""
        display = f"{BRANCH_PREFIX} [{t.id}] {src} to {dst} {when}"
        lines += [*_javadoc(f"Link {t.id}{line}: {src} to {dst} {when}.",
                            f"Driven by scenario {sc.id}." if sc else "No scenario takes this link.", pad="    "),
                  "    @Test", f"    @DisplayName({java_string(display)})", f"    void {method}() {{"]
        if sc and driver.supported:
            lines.append(f"        run({java_string(tests_gen.scenario_stem(process, sc))});")
        else:
            why = "no scenario takes this link" if not sc else f"no driver for a {plan.starter.kind} starter"
            lines.append(f"        fail({java_string(f'no test body for branch {t.id}: {why}')});")
        lines.append("    }")
        entries.append(IndexEntry(name=f"{ctx.pkg}.{cls}.{method}", kind="branch", branch_id=t.id,
                                  display_name=display, process_id=process.id, path=path,
                                  has_body=bool(sc and driver.supported)))
    if any(covered.values()) and driver.supported:
        lines += _run_method(driver)
    lines.append("}")
    done = all(covered.values()) and (driver.supported or not branches)
    return _File("\n".join(lines) + "\n", entries, "code" if done else "agent")


def scenario_tests(ctx: JavaTestContext, plan: ServicePlan, scenarios: list[Scenario]) -> _File:
    process = ctx.process
    driver = _driver(ctx, plan)
    cls = f"{ctx.name}ScenarioTests"
    path = f"{ctx.test_folder}/{cls}.java"
    lines = _header(ctx, _vocabulary(ctx) + driver.imports)
    lines += _javadoc(f"One test per parity scenario of {process.name}: the scenario input goes to the generated "
                      "service and the output, fault and side effects are compared field by field with the "
                      f"expected fixture ({tests_gen.MODEL_LABEL}).")
    lines += [f"class {cls} extends ScenarioSupport {{", *driver.fields]
    used: set[str] = {"run"}
    entries: list[IndexEntry] = []
    for i, s in enumerate(scenarios):
        if i:
            lines.append("")
        stem = tests_gen.scenario_stem(process, s)
        method = _unique(_method("scenario", stem), used)
        display = f"{SCENARIO_PREFIX} [{s.id}] {one_line(s.name)}"
        lines += [f"    /** {doc(f'{s.kind} scenario {s.id}: {one_line(s.name)}.')} */",
                  "    @Test", f"    @DisplayName({java_string(display)})", f"    void {method}() {{",
                  f"        run({java_string(stem)});", "    }"]
        entries.append(IndexEntry(name=f"{ctx.pkg}.{cls}.{method}", kind="scenario", scenario_id=s.id,
                                  display_name=display, process_id=process.id, path=path,
                                  has_body=driver.supported))
    lines += _run_method(driver)
    lines.append("}")
    return _File("\n".join(lines) + "\n", entries, "code" if driver.supported else "agent")


def generate_java_tests(plan: ServicePlan, rules: list[Rule], scenarios: list[Scenario], design: DesignRecord,
                        ctx: JavaTestContext) -> tuple[list[GenFile], list[IndexEntry]]:
    """The rules, branch and scenario tests and the fixtures of one process (paths from the project
    root), with their test index entries."""
    if design.process_id != plan.process_id or ctx.process.id != plan.process_id:
        raise ValueError(f"the design, plan and context name different processes ({design.process_id}, "
                         f"{plan.process_id}, {ctx.process.id})")
    process = ctx.process
    scenarios = [s for s in scenarios if s.process_id == process.id and s.expected is not None]
    out: list[GenFile] = []
    entries: list[IndexEntry] = []
    for f, cls in ((rules_tests(ctx, rules), "RulesTests"), (branch_tests(ctx, plan, scenarios), "BranchTests"),
                   (scenario_tests(ctx, plan, scenarios), "ScenarioTests")):
        out.append(GenFile(path=f"{ctx.test_folder}/{ctx.name}{cls}.java", content=f.text, origin=f.origin,
                           process_id=process.id))
        entries += f.entries
    fx = ctx.fixture_context(plan.starter.kind)
    for s in scenarios:
        out.append(GenFile(path=fixture_path(ctx, s), content=tests_gen.fixture_json(fx, process, s),
                           process_id=process.id))
    return out, entries


def generate_test_support(pkg: str, test_folder: str, schemas: list[str], sql_texts: dict[str, str],
                          connections: list[str]) -> list[GenFile]:
    """``support/TestDatabase``, ``Fixtures`` and ``ScenarioSupport``, and the estate SQL they run."""
    from engine.stacks.java.generator import render

    out = [GenFile(path=f"{test_folder}/support/TestDatabase.java",
                   content=render("TestDatabase.java.j2", pkg=pkg, schemas=schemas, sql_files=list(sql_texts),
                                  connections=connections)),
           GenFile(path=f"{test_folder}/support/Fixtures.java", content=render("Fixtures.java.j2", pkg=pkg)),
           GenFile(path=f"{test_folder}/support/ScenarioSupport.java",
                   content=render("ScenarioSupport.java.j2", pkg=pkg))]
    out += [GenFile(path=f"src/test/resources/database/{name}", content=text) for name, text in sql_texts.items()]
    return out


__all__ = ["JavaTestContext", "fixture_path", "generate_java_tests", "generate_test_support"]
