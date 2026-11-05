"""Python tests written first (target-stacks spec section 4): pytest tests from approved rules, IR
branches and parity scenarios, generated before any method body is filled.

Per process (``tests/``):

* ``test_<process>_rules.py``: one ``def test_r1_<slug>(support)`` per approved (or edited) rule,
  body ``pytest.fail("no test body for R1")`` until the Test Engineer writes it;
* ``test_<process>_branch.py``: one test per conditional or otherwise transition; it runs the first
  scenario whose TIBCO trace takes that link, or fails with ``no test body for branch <id>``;
* ``test_<process>_scenario.py``: one test per scenario. ``scenario_support`` drives the routes
  through FastAPI's ``TestClient(create_app(settings))``, hands the input to the JMS consumer or runs
  the job once, and compares the run with ``tests/fixtures/<Process>/<scenario>.json`` field by
  field, side effects against the test database and the in-memory bus.

Per project: ``tests/conftest.py`` (the client, the support, the database at ``TEST_DATABASE_URL``
with a skip without it), ``tests/scenario_support.py`` and the estate's SQL under ``tests/database/``.
pytest's JUnit XML reports a test as ``classname`` ``tests.test_<process>_rules`` and ``name``
``test_r1_<slug>``, so the test index (``.exiqo/test-index.json``) names every test
``tests.test_<process>_rules.test_r1_<slug>`` with its rule, branch or scenario id.

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
from engine.stacks.python.naming import snake
from engine.stacks.python.text import (
    comment,
    doc,
    imports,
    one_line,
    py_string,
    selector_properties,
)
from engine.stacks.sql import time_columns
from engine.stacks.testindex import IndexEntry

if TYPE_CHECKING:
    from engine.stacks.plan import ServicePlan

TESTS = "tests"
_NON_WORD = re.compile(r"\W")


@dataclass
class _TimeColumns:
    """The part of the .NET ``ProcGen`` the shared fixture writer reads: a JDBC step's date columns
    (from its TIBCO column metadata), so seeded dates relative to the load time count as noise."""

    def time_columns(self, act: Activity) -> set[str]:
        return time_columns(act.config.get("settings"))


@dataclass
class PythonTestContext:
    """What the tests need to know about the generated code of one process."""

    __test__ = False

    pkg: str  # gmf_credit
    app: str  # the application type name (GmfCredit)
    process: Process
    name: str  # the process type name (DealerFundingRequest)
    sql_texts: dict[str, str] = field(default_factory=dict)
    schemas: list[str] = field(default_factory=list)

    @property
    def module(self) -> str:
        return snake(self.name)

    @property
    def service_class(self) -> str:
        return f"{self.name}Service"

    def fixture_context(self, kind: str) -> tests_gen.TestContext:
        return tests_gen.TestContext(app=self.app, process=self.name, starter_kind=kind, pg=_TimeColumns(),
                                     sql_texts=self.sql_texts, schemas=self.schemas)


def scenario_fixture_path(type_name: str, process: Process, scenario: Scenario) -> str:
    """The fixture of a scenario of the process whose type is ``type_name``, from the project root
    (``PythonStack.fixture_path``)."""
    return f"{TESTS}/fixtures/{type_name}/{tests_gen.scenario_stem(process, scenario)}.json"


def _unique(name: str, used: set[str]) -> str:
    base, n = name, 2
    while name in used:
        name, n = f"{base}_{n}", n + 1
    used.add(name)
    return name


def _function(prefix: str, words: str) -> str:
    """``test_<prefix>_<words>``: a pytest test function named in the rule's or link's own words."""
    return "test_" + _NON_WORD.sub("_", dotnet.method_name(prefix, words, lower=True))


def _header(ctx: PythonTestContext, summary: str, extra: set[str]) -> list[str]:
    # Every test file imports the same vocabulary, used or not: a body is written against it later,
    # and an import the scaffold did not write is one the repair loop would have to add.
    vocabulary = {"from __future__ import annotations", "from decimal import Decimal", "from typing import Any",
                  "import pytest", f"from {ctx.pkg}.core import tib",
                  f"from {ctx.pkg}.core.{ctx.module}_service import {ctx.service_class}",
                  f"from {ctx.pkg}.core.tib import ProcessResult, ProcessState",
                  "from tests.scenario_support import ScenarioSupport"}
    return [f'"""{doc(summary)}"""', "", *imports(vocabulary | extra)]


def _comments(*paragraphs: str) -> list[str]:
    return [f"# {comment(p)}" for p in paragraphs if p]


# ------------------------------------------------------------------ drivers
@dataclass
class _Driver:
    supported: bool
    imports: set[str]
    body: list[str]  # the statements of ``_run(support, scenario)``
    what: str


def _driver(ctx: PythonTestContext, plan: ServicePlan) -> _Driver:
    kind = plan.starter.kind
    pad = "    "
    process = py_string(ctx.name)
    if kind in ("rest", "http"):
        return _Driver(True, set(), [f"{pad}support.run_request({process}, scenario)"],
                       "Sends the fixture request to the routes")
    if kind == "jms":
        consumer = f"{ctx.name}Consumer"
        props = ", ".join(f"{py_string(k)}: {py_string(v)}" for k, v in selector_properties(plan.starter.selector) or [])
        return _Driver(True, {f"from {ctx.pkg}.infrastructure.messaging.{ctx.module}_consumer import {consumer}"},
                       [f"{pad}consumer: {consumer} = support.component({py_string(snake(consumer))})",
                        f"{pad}support.run_message({process}, scenario, {consumer}.ENTITY, {{{props}}}, consumer.handle)"],
                       f"Hands the fixture input to {consumer}.handle as a message")
    if kind == "timer":
        job = f"{ctx.name}Job"
        return _Driver(True, {f"from {ctx.pkg}.infrastructure.jobs.{ctx.module}_job import {job}"},
                       [f"{pad}job: {job} = support.component({py_string(snake(job))})",
                        f"{pad}support.run_job({process}, scenario, job.execute)"],
                       f"Runs {job} once")
    return _Driver(False, set(),
                   [f'{pad}pytest.fail("no test body for scenario " + scenario + ": no driver for a {kind} starter")'],
                   "No generated driver for this starter")


def _run_function(driver: _Driver) -> list[str]:
    return ["", "", "def _run(support: ScenarioSupport, scenario: str) -> None:",
            f'    """{doc(driver.what)} and compares the run with the fixture."""', *driver.body]


# ------------------------------------------------------------------ test files
@dataclass
class _File:
    text: str
    entries: list[IndexEntry]
    origin: Literal["code", "agent"]


def rules_tests(ctx: PythonTestContext, rules: list[Rule]) -> _File:
    approved = [r for r in rules if r.status in tests_gen.APPROVED]
    module = f"test_{ctx.module}_rules"
    path = f"{TESTS}/{module}.py"
    lines = _header(ctx, f"One test per approved rule of {ctx.process.name}, named in the rule's own words; "
                    "written before the code. A test fails with no test body until the Test Engineer writes it; "
                    "gate G4 refuses the build while any approved rule has no test body.", set())
    used: set[str] = set()
    entries: list[IndexEntry] = []
    for r in approved:
        name = _unique(_function(r.id.lower(), r.text), used)
        text = one_line(r.text)
        cites = "; ".join(f"{c.activity_id} ({c.kind}): {one_line(c.text)}" for c in r.citations) or "none"
        display = f"{r.id} {text}"
        lines += ["", "", *_comments(f"{r.id}: {text}", f"Citations: {cites}"),
                  f"def {name}(support: ScenarioSupport) -> None:",
                  f"    pytest.fail({py_string(f'no test body for {r.id}')})"]
        entries.append(IndexEntry(name=f"{TESTS}.{module}.{name}", kind="rule", rule_id=r.id, display_name=display,
                                  process_id=ctx.process.id, path=path, has_body=False))
    return _File("\n".join(lines) + "\n", entries, "agent" if approved else "code")


def _covering(scenarios: list[Scenario], transition_id: str) -> Scenario | None:
    for s in scenarios:
        if s.expected and any(step.chosen_transition == transition_id for step in s.expected.trace):
            return s
    return None


def branch_tests(ctx: PythonTestContext, plan: ServicePlan, scenarios: list[Scenario]) -> _File:
    process = ctx.process
    names = {a.id: a.name for a in process.activities}
    branches = tests_gen.branch_transitions(process)
    covered = {t.id: _covering(scenarios, t.id) for t in branches}
    driver = _driver(ctx, plan)
    module = f"test_{ctx.module}_branch"
    path = f"{TESTS}/{module}.py"
    lines = _header(ctx, f"One test per branch (conditional or otherwise link) of {process.name}. A branch test "
                    "runs the first scenario whose TIBCO trace takes the link; a branch no scenario reaches fails "
                    "until the Test Engineer writes its test.", driver.imports)
    used: set[str] = set()
    entries: list[IndexEntry] = []
    for t in branches:
        src, dst = names.get(t.from_id, t.from_id), names.get(t.to_id, t.to_id)
        when = f"when {one_line(t.expression or '')}" if t.kind == "conditional" else "otherwise"
        words = f"when {(t.expression or '').lower()}" if t.kind == "conditional" else "otherwise"
        name = _unique(_function(f"branch_{dotnet.ident(src)}_to_{dotnet.ident(dst)}", words), used)
        sc = covered[t.id]
        line = f" (line {t.source_line})" if t.source_line else ""
        display = f"{BRANCH_PREFIX} [{t.id}] {src} to {dst} {when}"
        lines += ["", "", *_comments(f"Link {t.id}{line}: {src} to {dst} {when}.",
                                     f"Driven by scenario {sc.id}." if sc else "No scenario takes this link."),
                  f"def {name}(support: ScenarioSupport) -> None:"]
        if sc and driver.supported:
            lines.append(f"    _run(support, {py_string(tests_gen.scenario_stem(process, sc))})")
        else:
            why = "no scenario takes this link" if not sc else f"no driver for a {plan.starter.kind} starter"
            lines.append(f"    pytest.fail({py_string(f'no test body for branch {t.id}: {why}')})")
        entries.append(IndexEntry(name=f"{TESTS}.{module}.{name}", kind="branch", branch_id=t.id,
                                  display_name=display, process_id=process.id, path=path,
                                  has_body=bool(sc and driver.supported)))
    if any(covered.values()) and driver.supported:
        lines += _run_function(driver)
    done = all(covered.values()) and (driver.supported or not branches)
    return _File("\n".join(lines) + "\n", entries, "code" if done else "agent")


def scenario_tests(ctx: PythonTestContext, plan: ServicePlan, scenarios: list[Scenario]) -> _File:
    process = ctx.process
    driver = _driver(ctx, plan)
    module = f"test_{ctx.module}_scenario"
    path = f"{TESTS}/{module}.py"
    lines = _header(ctx, f"One test per parity scenario of {process.name}: the scenario input goes to the "
                    "generated service and the output, fault and side effects are compared field by field with "
                    f"the expected fixture ({tests_gen.MODEL_LABEL}).", driver.imports)
    used: set[str] = set()
    entries: list[IndexEntry] = []
    for s in scenarios:
        stem = tests_gen.scenario_stem(process, s)
        name = _unique(_function("scenario", stem), used)
        display = f"{SCENARIO_PREFIX} [{s.id}] {one_line(s.name)}"
        lines += ["", "", *_comments(f"{s.kind} scenario {s.id}: {one_line(s.name)}."),
                  f"def {name}(support: ScenarioSupport) -> None:", f"    _run(support, {py_string(stem)})"]
        entries.append(IndexEntry(name=f"{TESTS}.{module}.{name}", kind="scenario", scenario_id=s.id,
                                  display_name=display, process_id=process.id, path=path,
                                  has_body=driver.supported))
    lines += _run_function(driver)
    return _File("\n".join(lines) + "\n", entries, "code" if driver.supported else "agent")


def generate_python_tests(plan: ServicePlan, rules: list[Rule], scenarios: list[Scenario], design: DesignRecord,
                          ctx: PythonTestContext) -> tuple[list[GenFile], list[IndexEntry]]:
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
        out.append(GenFile(path=f"{TESTS}/test_{ctx.module}_{kind}.py", content=f.text, origin=f.origin,
                           process_id=process.id))
        entries += f.entries
    fx = ctx.fixture_context(plan.starter.kind)
    for s in scenarios:
        out.append(GenFile(path=scenario_fixture_path(ctx.name, process, s),
                           content=tests_gen.fixture_json(fx, process, s), process_id=process.id))
    return out, entries


def generate_test_support(pkg: str, schemas: list[str], sql_texts: dict[str, str],
                          connections: list[str]) -> list[GenFile]:
    """``tests/conftest.py``, ``tests/scenario_support.py`` and the estate SQL they run."""
    from engine.stacks.python.generator import render

    out = [GenFile(path=f"{TESTS}/__init__.py", content=""),
           GenFile(path=f"{TESTS}/conftest.py", content=render("conftest.py.j2", pkg=pkg, connections=connections)),
           GenFile(path=f"{TESTS}/scenario_support.py",
                   content=render("scenario_support.py.j2", pkg=pkg, schemas=schemas, sql_files=list(sql_texts)))]
    out += [GenFile(path=f"{TESTS}/database/{name}", content=text) for name, text in sql_texts.items()]
    return out


__all__ = ["PythonTestContext", "generate_python_tests", "generate_test_support", "scenario_fixture_path"]
