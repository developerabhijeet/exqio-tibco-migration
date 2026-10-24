"""Tests written first (design spec 2.5 step 2): xUnit tests from approved rules, IR branches and
parity scenarios, generated before any method body is filled.

Per process (``tests/<App>.Tests/``):

* ``<Process>RulesTests.cs``: one ``[Fact(DisplayName = "R1 <rule text>")] public Task R1_<slug>()``
  per approved (or edited) rule, body ``Assert.Fail("no test body for R1")`` until it is written;
* ``<Process>BranchTests.cs``: one test per conditional or otherwise transition, named from its
  expression; it drives the first scenario whose TIBCO trace takes that link, or fails with
  "no test body for branch <id>" when no scenario reaches it;
* ``<Process>ScenarioTests.cs``: one test per scenario, driving the controller through
  ``WebApplicationFactory`` (REST starters) or the consumer or job directly (JMS, timer) with the
  scenario input and comparing ``Fixtures/<Process>/<scenario>.json`` field by field, side effects
  against the test database and the in-memory bus.

Per solution: ``TestDatabase.cs`` (Testcontainers PostgreSQL, or ``TEST_DATABASE_URL``, seeded from
the estate's ``Database/*.sql``) and ``Support/`` (API host, fixture loader, field comparison).
The display names follow the convention ``trx.classify`` parses back into rule, branch and scenario ids.
"""
from __future__ import annotations

import json
import re
import urllib.parse
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from engine.analysis.models import Rule
from engine.dotnet_gen.models import DesignRecord
from engine.dotnet_gen.trx import BRANCH_PREFIX, SCENARIO_PREFIX
from engine.ir.models import Process, Transition
from engine.parity.models import Scenario

if TYPE_CHECKING:  # pragma: no cover
    from engine.dotnet_gen.generator import GenFile, ProcGen

APPROVED = ("approved", "edited")
MODEL_LABEL = "derived from the TIBCO model"
_WALL_CLOCK_SQL = re.compile(r"(?i)^\s*(current_timestamp|current_date|current_time|localtimestamp|localtime|now\(\))\s*$")
_WALL_CLOCK_XPATH = re.compile(r"current-(dateTime|date|time)\s*\(")
_SEED_RELATIVE = re.compile(r"(?i)current_timestamp|current_date|now\(\)|localtimestamp")
_RECORD_COL = re.compile(r"^\$([\w.\-]+)/Record(?:\[\d+\])?/([\w.\-]+)$")


def _g():
    from engine.dotnet_gen import generator
    return generator


# ------------------------------------------------------------------ naming
def branch_transitions(process: Process) -> list[Transition]:
    """The IR branches: conditional and otherwise transitions, in IR order."""
    return [t for t in process.transitions if t.kind in ("conditional", "otherwise")]


def scenario_stem(process: Process, scenario: Scenario) -> str:
    sid = scenario.id
    if sid.startswith(process.name + ":"):
        sid = sid[len(process.name) + 1:]
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", sid).strip("-") or "scenario"


def fixture_path(process: Process, scenario: Scenario) -> str:
    """Path of a scenario fixture relative to the Tests project."""
    return f"Fixtures/{_g().ident(process.name, 'Process')}/{scenario_stem(process, scenario)}.json"


def _unique(name: str, used: set[str]) -> str:
    base, n = name, 2
    while name in used:
        name, n = f"{base}_{n}", n + 1
    used.add(name)
    return name


def _method_id(text: str) -> str:
    out = re.sub(r"\W", "_", text)
    return out if out and not out[0].isdigit() else f"T_{out}"


# ------------------------------------------------------------------ context
@dataclass
class TestContext:
    """What the tests need to know about the generated code of one process."""

    __test__ = False

    app: str
    process: str  # PascalCase process name
    starter_kind: str
    message_type: str = "string"
    namespaces: list[str] = field(default_factory=list)
    pg: Any = None  # generator.ProcGen, for noise and table resolution
    sql_texts: dict[str, str] = field(default_factory=dict)
    schemas: list[str] = field(default_factory=list)

    @classmethod
    def from_proc(cls, pg: ProcGen, sql_texts: dict[str, str], schemas: list[str]) -> TestContext:
        ns = [pg.message_shape.namespace] if pg.kind == "jms" and pg.message_shape is not None else []
        return cls(app=pg.app, process=pg.name, starter_kind=pg.kind, message_type=pg.message_type(),
                   namespaces=ns, pg=pg, sql_texts=sql_texts, schemas=schemas)

    @classmethod
    def minimal(cls, process: Process, design: DesignRecord) -> TestContext:
        kind = process.starter.kind if process.starter else "subprocess"
        return cls(app=design.solution.name, process=_g().ident(process.name, "Process"), starter_kind=kind)


# ------------------------------------------------------------------ fixtures
def _qualify(ctx: TestContext, process: Process, table: str) -> str:
    if "." in table:
        return table
    for act in process.activities:
        m = re.search(rf"(?i)\b([A-Za-z_]\w*)\.{re.escape(table)}\b", str(act.config.get("sql") or ""))
        if m:
            return f"{m.group(1)}.{table}"
    return f"{ctx.schemas[0]}.{table}" if ctx.schemas else table


def _output_noise(ctx: TestContext, process: Process) -> dict[str, str]:
    """Reply body fields whose expected value comes from the wall clock -> reason."""
    pg = ctx.pg
    if pg is None:
        return {}
    seed_relative = any(_SEED_RELATIVE.search(t) for t in ctx.sql_texts.values())
    acts = {a.name: a for a in process.activities}
    out: dict[str, str] = {}
    for act in process.activities:
        if act.type != "reply":
            continue
        for b in act.input_bindings:
            segs = [s.split(":")[-1] for s in b.target.split("/") if s]
            if len(segs) < 2 or segs[0] in ("client4XXError", "server5XXError"):
                continue
            path = "body." + ".".join(segs[1:])
            if _WALL_CLOCK_XPATH.search(b.expression):
                out[path] = "current-dateTime() in the TIBCO mapping: the wall clock differs per run"
                continue
            m = _RECORD_COL.match(b.expression.strip())
            src = acts.get(m.group(1)) if m else None
            if src is not None and src.type == "jdbc.query" and seed_relative and m.group(2) in pg.time_columns(src):
                out[path] = (f"{src.name}.{m.group(2)} is a date column seeded relative to the seed time "
                             "(current_timestamp in Database/*.sql)")
    return out


def _side_effects(ctx: TestContext, process: Process, scenario: Scenario) -> tuple[list[dict[str, Any]], dict[str, str]]:
    effects: list[dict[str, Any]] = []
    reasons: dict[str, str] = {}
    expected = scenario.expected
    if expected is None:
        return effects, reasons
    publish_noise: dict[str, set[str]] = {}
    for act in process.activities:
        if act.type in ("jms.publish", "jms.send"):
            dest = str(act.config.get("destination") or "")
            for b in act.input_bindings:
                if _WALL_CLOCK_XPATH.search(b.expression):
                    publish_noise.setdefault(dest, set()).add(b.target.split("/")[-1].split(":")[-1])
    for i, se in enumerate(expected.side_effects):
        item: dict[str, Any] = {"kind": se.kind, "target": se.target, "payload": se.payload}
        noise: list[str] = []
        if se.kind == "row_written":
            item["table"] = _qualify(ctx, process, se.target)
            for key, value in se.payload.items():
                if isinstance(value, str) and _WALL_CLOCK_SQL.match(value):
                    noise.append(key)
                    reasons[f"side_effects[{i}].payload.{key}"] = f"SQL {value.strip()} is evaluated by the database at run time"
        elif se.kind == "message_published":
            for key in sorted(publish_noise.get(se.target, set())):
                if key in se.payload:
                    noise.append(key)
                    reasons[f"side_effects[{i}].payload.{key}"] = "current-dateTime() in the TIBCO mapping: the wall clock differs per run"
        item["noise"] = noise
        effects.append(item)
    return effects, reasons


def _request(ctx: TestContext, process: Process, scenario: Scenario) -> dict[str, Any] | None:
    if ctx.starter_kind not in ("rest", "http") or process.starter is None:
        return None
    cfg = process.starter.config
    verb = str(cfg.get("verb") or "POST").upper()
    path = str(cfg.get("path") or cfg.get("resource_path") or f"/{process.name}")
    params = dict(scenario.input.get("parameters") or {}) if isinstance(scenario.input, dict) else {}

    def sub(m: re.Match[str]) -> str:
        value = params.pop(m.group(1), "")
        return urllib.parse.quote(str(value), safe="")

    path = re.sub(r"\{([^}/]+)\}", sub, path)
    body = scenario.input.get("body") if isinstance(scenario.input, dict) else None
    if ctx.starter_kind == "http" and body is None:
        body = scenario.input
    return {"method": verb, "path": path, "query": {k: v for k, v in params.items()}, "body": body}


def fixture_json(ctx: TestContext, process: Process, scenario: Scenario) -> str:
    expected = scenario.expected
    noise = _output_noise(ctx, process)
    effects, reasons = _side_effects(ctx, process, scenario)
    output = expected.output if expected else {}
    output_noise = sorted(p for p in noise if _has_path(output, p))
    reasons.update({p: noise[p] for p in output_noise})
    doc: dict[str, Any] = {
        "scenario_id": scenario.id,
        "process_id": scenario.process_id,
        "name": scenario.name,
        "kind": scenario.kind,
        "source": expected.source if expected else "model",
        "label": MODEL_LABEL,
        "starter": ctx.starter_kind,
        "input": scenario.input,
    }
    request = _request(ctx, process, scenario)
    if request is not None:
        doc["request"] = request
    doc["seed_state"] = [{"table": _qualify(ctx, process, s.table), "row": s.row} for s in scenario.seed_state]
    doc["stub_responses"] = {k: v.model_dump(mode="json") for k, v in sorted(scenario.stub_responses.items())}
    doc["expected"] = {
        "output": output,
        "output_noise": output_noise,
        "fault": expected.fault.model_dump(mode="json") if expected and expected.fault else None,
        "side_effects": effects,
    }
    doc["noise_reasons"] = dict(sorted(reasons.items()))
    return json.dumps(doc, indent=2, ensure_ascii=False, default=str) + "\n"


def _has_path(doc: Any, dotted: str) -> bool:
    for part in dotted.split("."):
        if not isinstance(doc, dict) or part not in doc:
            return False
        doc = doc[part]
    return True


# --------------------------------------------------------------- test files
def _header(usings: list[str]) -> list[str]:
    system = sorted({u for u in usings if u.startswith("System")})
    other = sorted({u for u in usings if u and not u.startswith("System")})
    return [f"using {u};" for u in system + other] + [""]


def _vocabulary(ctx: TestContext) -> list[str]:
    """The usings every generated test file carries, filled body or not.

    A test body is written against one vocabulary: ``new ApiFactory(database)``, then
    ``Services.CreateScope()`` and ``GetRequiredService<TService>()`` to reach the service, and
    ``JsonObject`` for a sub-process entry point. Leaving the namespaces out of the scaffold makes
    the file compile only until a body uses one of them, and the compile repair loop then spends
    attempts re-adding a using the scaffold could have written once. Unused usings cost nothing
    here: the solution does not treat warnings as errors and format runs whitespace only.
    """
    return ["Xunit", "System.Text.Json.Nodes", "Microsoft.Extensions.DependencyInjection",
            f"{ctx.app}.Core.Services", f"{ctx.app}.Tests.Support", *ctx.namespaces]


def rules_tests(ctx: TestContext, process: Process, rules: list[Rule]) -> str:
    g = _g()
    approved = [r for r in rules if r.status in APPROVED]
    lines = _header(_vocabulary(ctx))
    summary = f"One test per approved rule of {process.name}, named in the rule's own words; written before the code."
    lines += [f"namespace {ctx.app}.Tests;", "",
              f"/// <summary>{g.xml_doc(summary)}</summary>",
              f"/// <remarks>{g.xml_doc('A test fails with no test body until the Code Builder writes it; gate G4 refuses the build while any approved rule has no test body.')}</remarks>",
              "[Collection(TestDatabaseCollection.Name)]",
              f"public sealed class {ctx.process}RulesTests", "{"]
    used: set[str] = set()
    for i, r in enumerate(approved):
        if i:
            lines.append("")
        rid = _method_id(r.id)
        name = _unique(g.method_name(rid, r.text, lower=True), used)
        text = g.one_line(r.text)
        cites = "; ".join(f"{c.activity_id} ({c.kind}): {g.one_line(c.text)}" for c in r.citations) or "none"
        lines += [f"    /// <summary>{g.xml_doc(f'{r.id}: {text}')}</summary>",
                  f"    /// <remarks>{g.xml_doc(f'Citations: {cites}')}</remarks>",
                  f"    [Fact(DisplayName = {g.cs_string(f'{r.id} {text}')})]",
                  f"    [Trait(\"Rule\", {g.cs_string(r.id)})]",
                  f"    public Task {name}()",
                  "    {",
                  f"        Assert.Fail({g.cs_string(f'no test body for {r.id}')});",
                  "        return Task.CompletedTask;",
                  "    }"]
    lines.append("}")
    return "\n".join(lines) + "\n"


def _covering(scenarios: list[Scenario], transition_id: str) -> Scenario | None:
    for s in scenarios:
        if s.expected and any(step.chosen_transition == transition_id for step in s.expected.trace):
            return s
    return None


def branch_tests(ctx: TestContext, process: Process, scenarios: list[Scenario]) -> tuple[str, bool]:
    """(content, every branch has a driving scenario)."""
    g = _g()
    names = {a.id: a.name for a in process.activities}
    branches = branch_transitions(process)
    covered = {t.id: _covering(scenarios, t.id) for t in branches}
    uses_db = any(covered.values())
    lines = _header(_vocabulary(ctx))
    lines += [f"namespace {ctx.app}.Tests;", "",
              f"/// <summary>{g.xml_doc(f'One test per branch (conditional or otherwise link) of {process.name}.')}</summary>",
              f"/// <remarks>{g.xml_doc('A branch test runs the first scenario whose TIBCO trace takes the link; a branch no scenario reaches fails until the builder writes its test.')}</remarks>"]
    # Every test class shares the one Postgres that TestDatabase drops and recreates, so they all
    # belong in the same xUnit collection whether or not the scaffold itself takes the fixture.
    # xUnit runs collections in parallel: a class left out of this one resets the schema underneath
    # the others and the run dies on "23505 duplicate key ... pg_type_typname_nsp_index". The
    # scaffold cannot tell in advance either, because the body a model writes drives the process
    # through ApiFactory(database) whatever this file decided.
    lines.append("[Collection(TestDatabaseCollection.Name)]")
    ctor = "(TestDatabase database)" if uses_db else ""
    lines += [f"public sealed class {ctx.process}BranchTests{ctor}", "{"]
    used: set[str] = set()
    for i, t in enumerate(branches):
        if i:
            lines.append("")
        src, dst = names.get(t.from_id, t.from_id), names.get(t.to_id, t.to_id)
        when = f"when {g.one_line(t.expression or '')}" if t.kind == "conditional" else "otherwise"
        words = f"when {(t.expression or '').lower()}" if t.kind == "conditional" else "otherwise"
        name = _unique(_method_id(g.method_name(f"Branch_{g.ident(src)}_to_{g.ident(dst)}", words)), used)
        sc = covered[t.id]
        line = f" (line {t.source_line})" if t.source_line else ""
        driver = f"Driven by scenario {sc.id}." if sc else "No scenario takes this link."
        lines += [f"    /// <summary>{g.xml_doc(f'Link {t.id}{line}: {src} to {dst} {when}.')}</summary>",
                  f"    /// <remarks>{g.xml_doc(driver)}</remarks>",
                  f"    [Fact(DisplayName = {g.cs_string(f'{BRANCH_PREFIX} [{t.id}] {src} to {dst} {when}')})]",
                  f"    [Trait(\"Branch\", {g.cs_string(t.id)})]",
                  f"    public Task {name}()",
                  "    {"]
        if sc:
            lines.append(f"        return {ctx.process}ScenarioTests.RunAsync(database, {g.cs_string(scenario_stem(process, sc))});")
        else:
            lines += [f"        Assert.Fail({g.cs_string(f'no test body for branch {t.id}: no scenario takes this link')});",
                      "        return Task.CompletedTask;"]
        lines.append("    }")
    lines.append("}")
    return "\n".join(lines) + "\n", all(covered.values())


def scenario_tests(ctx: TestContext, process: Process, scenarios: list[Scenario]) -> tuple[str, bool]:
    """(content, the starter has a generated driver)."""
    g = _g()
    kind = ctx.starter_kind
    usings = _vocabulary(ctx)
    driver: list[str]
    supported = True
    pad = "        "
    common = [f"{pad}var fixture = ScenarioFixture.Load({g.cs_string(ctx.process)}, scenario);",
              f"{pad}await database.ResetAsync(fixture);",
              f"{pad}await using var api = new ApiFactory(database);"]
    if kind in ("rest", "http"):
        driver = common + [f"{pad}using var response = await api.SendAsync(fixture);",
                           f"{pad}await ScenarioAssert.ResponseMatchesAsync(fixture, response);",
                           f"{pad}await ScenarioAssert.SideEffectsMatchAsync(fixture, database, api.Bus);"]
    elif kind == "jms":
        usings += [f"{ctx.app}.Infrastructure.Messaging", "Microsoft.Extensions.DependencyInjection", *ctx.namespaces]
        driver = common + [f"{pad}var consumer = api.Services.GetRequiredService<{ctx.process}Consumer>();",
                           f"{pad}var message = fixture.InputAs<{ctx.message_type}>();",
                           f"{pad}var error = await ScenarioAssert.CaptureAsync(() => consumer.HandleAsync(message, CancellationToken.None));",
                           f"{pad}ScenarioAssert.FaultMatches(fixture, error);",
                           f"{pad}await ScenarioAssert.SideEffectsMatchAsync(fixture, database, api.Bus);"]
    elif kind == "timer":
        usings += [f"{ctx.app}.Infrastructure.Jobs", "Microsoft.Extensions.DependencyInjection"]
        driver = common + [f"{pad}var job = api.Services.GetRequiredService<{ctx.process}Job>();",
                           f"{pad}var error = await ScenarioAssert.CaptureAsync(() => job.RunOnceAsync(CancellationToken.None));",
                           f"{pad}ScenarioAssert.FaultMatches(fixture, error);",
                           f"{pad}await ScenarioAssert.SideEffectsMatchAsync(fixture, database, api.Bus);"]
    else:
        supported = False
        driver = [f"{pad}ArgumentNullException.ThrowIfNull(database);",
                  f"{pad}await Task.Yield();",
                  f'{pad}Assert.Fail($"no test body for scenario {{scenario}}: no driver for a {kind} starter");']
    lines = _header(usings)
    lines += [f"namespace {ctx.app}.Tests;", "",
              f"/// <summary>{g.xml_doc(f'One test per parity scenario of {process.name}: the scenario input goes to the generated service and the output, fault and side effects are compared field by field with the expected fixture ({MODEL_LABEL}).')}</summary>"]
    lines.append("[Collection(TestDatabaseCollection.Name)]")
    ctor = "(TestDatabase database)" if scenarios else ""
    lines += [f"public sealed class {ctx.process}ScenarioTests{ctor}", "{"]
    used: set[str] = set()
    for s in scenarios:
        stem = scenario_stem(process, s)
        name = _unique(_method_id(g.method_name("Scenario", stem)), used)
        lines += [f"    /// <summary>{g.xml_doc(f'{s.kind} scenario {s.id}: {s.name}.')}</summary>",
                  f"    [Fact(DisplayName = {g.cs_string(f'{SCENARIO_PREFIX} [{s.id}] {g.one_line(s.name)}')})]",
                  f"    [Trait(\"Scenario\", {g.cs_string(s.id)})]",
                  f"    public Task {name}()",
                  "    {",
                  f"        return RunAsync(database, {g.cs_string(stem)});",
                  "    }",
                  ""]
    what = {"rest": "Sends the fixture request to the controller (WebApplicationFactory)",
            "http": "Sends the fixture request to the controller (WebApplicationFactory)",
            "jms": f"Calls {ctx.process}Consumer.HandleAsync with the fixture input",
            "timer": f"Runs {ctx.process}Job once"}.get(kind, "No generated driver for this starter")
    lines += [f"    /// <summary>{g.xml_doc(f'{what} and compares the run with the fixture.')}</summary>",
              # internal, not public: xUnit1013 warns about a public non-test method on a test
              # class, and that warning counts against the build's standards score. The branch
              # tests call it from the same assembly, so internal is all they need.
              "    internal static async Task RunAsync(TestDatabase database, string scenario)",
              "    {"]
    lines += driver
    lines += ["    }", "}"]
    return "\n".join(lines) + "\n", supported


# ------------------------------------------------------------------ record tests
# Every generated record that carries a [Sensitive] property also carries a masking ToString(), so
# an SSN or account number cannot reach a log in clear text. That is a control, and an untested
# control is a claim. These tests exercise it from the code the generator wrote, which is also the
# last part of a process's own code no behavioural test reaches: without them a build reports
# around 93% line coverage of its own files and the missing 7% is entirely this.
_RECORD_HEAD = re.compile(r"^public sealed record (\w+)(?:\(([^)]*)\))?\s*;?\s*$")
_RECORD_PROP = re.compile(r"^    public (required )?([\w.<>?\[\]]+) (\w+) \{ get; init; \}$")
_NAMESPACE = re.compile(r"^namespace ([\w.]+);")
_PARAM = re.compile(r"^([\w.<>?\[\]]+)\s+(\w+)$")

# A value per C# type the generated records use. A type that is not here is left unset (or, when
# the property is required, costs the record its test): a test that does not compile is worse than
# a line that is not covered.
_VALUE: dict[str, str] = {
    "string": '"sample"',
    "int": "7", "long": "7L", "short": "(short)7", "byte": "(byte)7",
    "decimal": "12.75m", "double": "12.75d", "float": "12.75f",
    "bool": "true",
    "DateTime": "new DateTime(2026, 1, 2, 3, 4, 5, DateTimeKind.Utc)",
    "DateTimeOffset": "new DateTimeOffset(2026, 1, 2, 3, 4, 5, TimeSpan.Zero)",
    "DateOnly": "new DateOnly(2026, 1, 2)", "TimeOnly": "new TimeOnly(3, 4, 5)",
    "Guid": "Guid.Empty",
}
# Obviously synthetic, and still long enough for the mask to keep a last four to assert on.
SENSITIVE_VALUE = "XXXX-XX-1234"
SENSITIVE_TAIL = "1234"


@dataclass
class _Prop:
    type: str
    name: str
    required: bool
    sensitive: bool


@dataclass
class _Record:
    name: str
    namespace: str
    positional: list[tuple[str, str]]
    props: list[_Prop]
    masked: bool


def _records(text: str) -> list[_Record]:
    """The records declared in one generated file, with their properties and masking."""
    namespace = ""
    out: list[_Record] = []
    current: _Record | None = None
    sensitive_next = False
    for line in text.split("\n"):
        if m := _NAMESPACE.match(line):
            namespace = m.group(1)
            continue
        if m := _RECORD_HEAD.match(line):
            # A parameter this does not recognise is kept as an unknown type, which costs the
            # record its test rather than producing one that does not compile.
            params = [(_PARAM.match(part.strip()).group(1), _PARAM.match(part.strip()).group(2))
                      if _PARAM.match(part.strip()) else ("?unknown", "?")
                      for part in (m.group(2) or "").split(",") if part.strip()]
            current = _Record(name=m.group(1), namespace=namespace, positional=params,
                              props=[], masked=False)
            out.append(current)
            sensitive_next = False
            continue
        if current is None:
            continue
        if line.strip() == "[Sensitive]":
            sensitive_next = True
            continue
        if m := _RECORD_PROP.match(line):
            current.props.append(_Prop(type=m.group(2), name=m.group(3),
                                       required=bool(m.group(1)), sensitive=sensitive_next))
            sensitive_next = False
            continue
        if _g().TO_STRING in line:
            current.masked = True
        elif line == "}":
            current = None
    return out


def _literal(prop: _Prop) -> str | None:
    bare = prop.type.rstrip("?")
    if prop.sensitive and bare == "string":
        return _g().cs_string(SENSITIVE_VALUE)
    return _VALUE.get(bare)


def _construct(record: _Record, known: dict[str, _Record], depth: int = 0) -> str | None:
    """``new Record { ... }`` (or ``new Record(...)`` for a positional one), or None when a value
    this record needs cannot be written."""
    if depth > 2:
        return None
    if record.positional:
        args = []
        for type_name, _ in record.positional:
            bare = type_name.rstrip("?")
            if bare in known:
                nested = _construct(known[bare], known, depth + 1)
                if nested is None:
                    return None
                args.append(nested)
            elif (value := _VALUE.get(bare)) is not None:
                args.append(value)
            else:
                return None
        return f"new {record.name}({', '.join(args)})"
    settings = []
    for prop in record.props:
        value = _literal(prop)
        if value is None:
            bare = prop.type.rstrip("?")
            if bare in known:
                value = _construct(known[bare], known, depth + 1)
            if value is None:
                if prop.required:
                    return None  # the record cannot be built without it
                continue
        settings.append(f"{prop.name} = {value}")
    if not settings:
        return None
    return f"new {record.name} {{ {', '.join(settings)} }}"


def record_tests(ctx: TestContext, sources: list[Any]) -> str | None:
    """A masking test per generated record of this process, or ``None`` when there is none to write.

    ``sources`` are the generated source files of the process (``GenFile``)."""
    g = _g()
    by_name: dict[str, _Record] = {}
    ordered: list[_Record] = []
    for f in sources:
        for record in _records(f.content):
            by_name[record.name] = record
            ordered.append(record)

    cases: list[list[str]] = []
    namespaces: set[str] = set()
    used: set[str] = set()
    for record in ordered:
        if not record.masked:
            continue
        built = _construct(record, by_name)
        if built is None:
            continue
        secrets = [p for p in record.props if p.sensitive and p.type.rstrip("?") == "string"]
        method = _unique(f"{record.name}_masks_its_sensitive_fields", used)
        body = [
            f'    [Fact(DisplayName = "{record.name} prints with its sensitive fields masked")]',
            f"    public void {method}()",
            "    {",
            f"        var record = {built};",
            "",
            "        var text = record.ToString();",
            "",
            f"        Assert.StartsWith({g.cs_string(record.name + ' {')}, text, StringComparison.Ordinal);",
        ]
        for secret in secrets:
            body += [
                f"        // {secret.name} is [Sensitive]: the value never prints, its last four do.",
                f"        Assert.DoesNotContain({g.cs_string(SENSITIVE_VALUE)}, text, StringComparison.Ordinal);",
                f"        Assert.Contains({g.cs_string(secret.name + ' = ')}, text, StringComparison.Ordinal);",
            ]
        if secrets:
            body.append(f"        Assert.Contains({g.cs_string(SENSITIVE_TAIL)}, text, StringComparison.Ordinal);")
        body.append("    }")
        cases.append(body)
        namespaces.add(record.namespace)

    state = _state_case(ctx, by_name, sources, used)
    if state is not None:
        cases.append(state)
        namespaces.add(f"{ctx.app}.Core.Services")
    if not cases:
        return None

    lines = _header(["System", "Xunit", *sorted(namespaces)])
    lines += [
        f"namespace {ctx.app}.Tests;",
        "",
        f"/// <summary>The generated records of {ctx.process}: what they print, and what they refuse to print.</summary>",
        ("/// <remarks>Masking is a control, not a convenience: a record with a [Sensitive] "
         "property prints its last four characters and nothing else of it.</remarks>"),
        # These tests touch no database, but every test class of the solution belongs to the one
        # collection: xUnit parallelises across collections, and the scenario database is shared.
        "[Collection(TestDatabaseCollection.Name)]",
        f"public sealed class {ctx.process}RecordTests",
        "{",
    ]
    for i, case in enumerate(cases):
        lines += case
        if i != len(cases) - 1:
            lines.append("")
    lines += ["}", ""]
    return "\n".join(lines)


def _state_case(ctx: TestContext, known: dict[str, _Record], sources: list[Any],
                used: set[str]) -> list[str] | None:
    """The run-state test: the starter variables are readable and an error transition is recorded.

    Only the members the generated state actually declares are asserted, because which ones exist
    depends on the process's starter."""
    state_file = next((f for f in sources if f.path.endswith(f"/{ctx.process}State.cs")), None)
    if state_file is None:
        return None
    text = state_file.content
    if f"public sealed class {ctx.process}State(" not in text:
        return None
    input_record = known.get(f"{ctx.process}Input")
    if input_record is None:
        return None
    built = _construct(input_record, known)
    if built is None:
        return None
    has_error = "public Exception? LastError { get; set; }" in text
    starters = re.findall(r"^    public JsonNode\? (\w+) =>", text, re.MULTILINE)
    if not has_error and not starters:
        return None
    method = _unique(f"{ctx.process}State_carries_the_run_variables", used)
    body = [
        f'    [Fact(DisplayName = "{ctx.process}State carries the variables of one run")]',
        f"    public void {method}()",
        "    {",
        f"        var state = new {ctx.process}State({built});",
        "",
    ]
    for starter in starters:
        body.append(f"        Assert.NotNull(state.{starter});")
    if has_error:
        body += [
            "        // An activity that faults sends the run down its error transition, and the",
            "        // state is where the fault is kept for the step that handles it.",
            '        state.LastError = new InvalidOperationException("the step faulted");',
            '        Assert.Equal("the step faulted", state.LastError!.Message);',
        ]
    body.append("    }")
    return body


def generate_tests(process: Process, rules: list[Rule], scenarios: list[Scenario], design: DesignRecord,
                   ctx: TestContext | None = None, sources: list[Any] | None = None) -> list[GenFile]:
    """Rules, branch, record and scenario tests plus the scenario fixtures of one process (paths
    from the solution root). ``sources`` are the process's generated source files, which the
    record tests are written from."""
    g = _g()
    ctx = ctx or TestContext.minimal(process, design)
    base = f"tests/{ctx.app}.Tests"
    scenarios = [s for s in scenarios if s.process_id == process.id and s.expected is not None]
    approved = [r for r in rules if r.status in APPROVED]
    out = [g.GenFile(path=f"{base}/{ctx.process}RulesTests.cs", content=rules_tests(ctx, process, rules),
                     origin="agent" if approved else "code")]
    text, all_covered = branch_tests(ctx, process, scenarios)
    out.append(g.GenFile(path=f"{base}/{ctx.process}BranchTests.cs", content=text,
                         origin="code" if all_covered else "agent"))
    text, supported = scenario_tests(ctx, process, scenarios)
    out.append(g.GenFile(path=f"{base}/{ctx.process}ScenarioTests.cs", content=text,
                         origin="code" if supported else "agent"))
    records = record_tests(ctx, sources or [])
    if records is not None:
        out.append(g.GenFile(path=f"{base}/{ctx.process}RecordTests.cs", content=records))
    for s in scenarios:
        out.append(g.GenFile(path=f"{base}/{fixture_path(process, s)}", content=fixture_json(ctx, process, s)))
    return [f.model_copy(update={"process_id": process.id}) for f in out]


def generate_test_support(app: str, schemas: list[str], sql_files: list[str], connections: list[str]) -> list[GenFile]:
    """``TestDatabase.cs`` and the ``Support`` helpers shared by every process's tests."""
    g = _g()
    base = f"tests/{app}.Tests"
    return [
        g.GenFile(path=f"{base}/TestDatabase.cs", content=g.render("TestDatabase.cs.j2", app=app, schemas=schemas,
                                                                     sql_files=sql_files, connections=connections)),
        g.GenFile(path=f"{base}/Support/ApiFactory.cs", content=g.render("ApiFactory.cs.j2", app=app)),
        g.GenFile(path=f"{base}/Support/ScenarioSupport.cs", content=g.render("ScenarioSupport.cs.j2", app=app)),
    ]
