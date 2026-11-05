"""Go generator (target-stacks spec section 4): the IR and the design records to a Go service on
``net/http`` (Go 1.22 method and wildcard patterns on ``http.ServeMux``), one Go module.

Deterministic: the same inputs give byte-identical files and the same ``build_hash``. Each process
is printed from its ``ServicePlan`` (``printer``): a service, its mappings and link conditions; a
repository with the TIBCO SQL verbatim in raw strings and its placeholders as pgx's ``$1, $2, ...``
bound in order; a handler for a REST or HTTP starter, a Service Bus consumer for a JMS starter, a
job for a timer. What code cannot write keeps the TIBCO expression in ``// TIBCO`` comments above
``panic("agent fills: <activity>")`` and the file is ``origin="agent"``.

Layout (module path ``<app slug>``, ``gmf.credit`` -> ``gmf-credit``)::

    go.mod (go <GO_MIN>, no toolchain line), go.sum (from engine/stacks/go/go.sum.lock),
    .exiqo/service-contract.json, .exiqo/test-index.json, .gitignore, .editorconfig
    cmd/server/main.go
    internal/tib/        values, XPath functions, state, flow, faults, masking
    internal/platform/   config (settings.go: the defaults), auth, dev token, health, buses
    internal/core/       services, mappings, conditions, faults, MessageBus, services.go, contracts/
    internal/infrastructure/  db.go, repositories; messaging/ (consumers), jobs/, clients/, ...
    internal/api/        router.go (wiring and routes), handlers
    tests/               rules, branch and scenario tests, test support, fixtures/, database/

The runtime contract (spec section 5) is the .NET one: ``/health``, ``/auth/dev-token``, the
``/_bus`` endpoints, faults as problem+json, and ``__``-separated environment names read by
``platform.Config``. The tests are generated first (``tests_gen``), with the test index that
names them.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import defaultdict
from pathlib import Path, PurePosixPath
from typing import Any

import jinja2
from lxml import etree

from engine.analysis.models import Finding, Rule
from engine.dotnet_gen import generator as dotnet
from engine.dotnet_gen.design import build_design_skeleton
from engine.dotnet_gen.generator import (
    RESERVED_FOR_INFRA,
    SENSITIVE_NAMES,
    GeneratedSolution,
    GenFile,
    write_solution,
)
from engine.dotnet_gen.models import DesignRecord
from engine.ir.models import Activity, Application, Process
from engine.parity.models import Scenario
from engine.stacks.base import Mirrors
from engine.stacks.contracts import contract_model, ident, plan_contracts
from engine.stacks.go import tests_gen, versions
from engine.stacks.go.contracts import render as render_contract
from engine.stacks.go.language import GoLanguage
from engine.stacks.go.naming import (
    GoNaming,
    env_key,
    lower_first,
    module_path,
    package_name_of,
    snake,
)
from engine.stacks.go.printer import (
    AgentMember,
    Ctx,
    Dep,
    GoProcess,
    Origin,
    placeholder,
    struct_lines,
)
from engine.stacks.go.text import (
    comment,
    doc,
    go_string,
    import_block,
    package_name,
    raw_string,
    selector_properties,
    tibco,
)
from engine.stacks.plan import ServicePlan, build_plan
from engine.stacks.sql import convert_placeholders
from engine.stacks.testindex import write_index

log = logging.getLogger(__name__)
TEMPLATES = Path(__file__).resolve().parent / "templates"
CONTRACT_FILE = ".exiqo/service-contract.json"
BINARY = ".exiqo-bin/server"
_JINJA = jinja2.Environment(
    loader=jinja2.FileSystemLoader(str(TEMPLATES)), keep_trailing_newline=True, trim_blocks=True,
    lstrip_blocks=True, undefined=jinja2.StrictUndefined, autoescape=False)
_JINJA.filters["go_string"] = go_string
_SQL_TYPE = {"VARCHAR": "string", "CHAR": "string", "LONGVARCHAR": "string", "NVARCHAR": "string",
             "NCHAR": "string", "CLOB": "string", "NUMERIC": "decimal", "DECIMAL": "decimal", "INTEGER": "int",
             "SMALLINT": "int", "TINYINT": "int", "BIGINT": "long", "DOUBLE": "double", "FLOAT": "double",
             "REAL": "double", "BIT": "bool", "BOOLEAN": "bool", "DATE": "date", "TIMESTAMP": "datetime",
             "TIME": "time"}
_REST_NUMERIC = {"integer", "int", "long", "number", "decimal"}
_LANG = GoLanguage()
_IMPORT_SPEC = re.compile(r'^\s*(?:(?P<alias>[A-Za-z_.]\w*)\s+)?"(?P<path>[^"]+)"')


def render(template: str, **ctx: Any) -> str:
    return _JINJA.get_template(template).render(**ctx)


def prune_imports(text: str) -> str:
    """Drops the imports a Go file never refers to (``name.`` in its code), keeping blank and
    dot imports and the block's grouping (a group left empty goes, one import left is one line)."""
    m = re.search(r"^import \((?:\n(?P<body>.*?))?\n\)\n|^import (?P<one>\"[^\"]+\")\n", text, re.MULTILINE | re.DOTALL)
    if m is None:
        return text
    head, tail = text[:m.start()], text[m.end():]
    code = _LANG.code_only(head + tail)
    lines = (m.group("body") or "").split("\n") if m.group("one") is None else [m.group("one")]

    def used(ln: str) -> bool:
        spec = _IMPORT_SPEC.match(ln)
        if spec is None:
            return False
        alias = spec.group("alias")
        name = alias or package_name(spec.group("path"))
        return alias in ("_", ".") or re.search(rf"(?<![\w.]){re.escape(name)}\s*\.", code) is not None

    kept: list[str] = []
    for ln in lines:
        if not ln.strip():
            if kept and kept[-1]:
                kept.append("")
        elif used(ln):
            kept.append(ln.strip())
    while kept and not kept[-1]:
        kept.pop()
    specs = [k for k in kept if k]
    if not specs:
        return head.rstrip("\n") + "\n\n" + tail.lstrip("\n")
    if len(specs) == 1 and _IMPORT_SPEC.match(specs[0]) and _IMPORT_SPEC.match(specs[0]).group("alias") is None:
        return head + f"import {specs[0]}\n" + tail
    return head + "import (\n" + "\n".join(f"\t{k}" if k else "" for k in kept) + "\n)\n" + tail


def layout_paths(app_name: str) -> dict[str, str]:
    """Paths of a generated project: the layout contract with infrastructure and Verify."""
    naming = GoNaming()
    return {"module": module_path(app_name), "binary": BINARY, "main": "cmd/server",
            **{k: naming.layer_folder(k, app_name) for k in ("api", "core", "infrastructure", "tests")}}


# ------------------------------------------------------------------ inputs
def _designs(app: Application, process_ids: list[str], design: Any, rules: dict[str, list[Rule]],
             findings: dict[str, list[Finding]]) -> tuple[dict[str, DesignRecord], dict[str, Any]]:
    """The Go design of every process (``build_design_skeleton(..., stack="go")`` for a missing
    one) and its I/O card; a given record for another stack is refused, its names are not Go's."""
    from engine.domains import graph as graph_mod

    given: dict[str, DesignRecord] = {}
    if isinstance(design, DesignRecord):
        given[design.process_id] = design
    elif isinstance(design, dict):
        given.update(design)
    elif design:
        given.update({d.process_id: d for d in design})
    procs = {p.id: p for p in app.processes}
    graph = graph_mod.build_graph([app])
    out: dict[str, DesignRecord] = {}
    cards: dict[str, Any] = {}
    for pid in process_ids:
        card = graph_mod.io_card(procs[pid], app, graph)
        cards[pid] = card.model_dump(mode="json") if hasattr(card, "model_dump") else card
        rec = given.get(pid) or build_design_skeleton(procs[pid], app, rules.get(pid, []), findings.get(pid, []),
                                                       card, stack="go")
        if rec.stack != "go":
            raise ValueError(f"the design of {pid} is for {rec.stack}, not go: re-run Design")
        out[pid] = rec
    return out, cards


class _Files:
    def __init__(self) -> None:
        self.files: dict[str, GenFile] = {}

    def add(self, path: str, content: str, origin: Origin = "code", activity_id: str | None = None,
            process_id: str | None = None) -> None:
        if any(path == r or path.startswith(r) for r in RESERVED_FOR_INFRA):
            raise ValueError(f"{path} is reserved for the infrastructure generator")
        if path.endswith(".go") and not _LANG.is_test_path(path):
            # test files keep their whole vocabulary (tests_gen): a body is written against it later
            content = prune_imports(content)
        if path in self.files:  # a file two processes need: the first writer wins
            f = self.files[path]
            if f.content != content and f.process_id != process_id:
                log.warning("%s is written by %s and %s with different content (two processes of the same "
                            "name in different modules?); the first one is kept", path, f.process_id, process_id)
            update: dict[str, Any] = {}
            if origin == "agent" and f.origin == "code":
                update["origin"] = "agent"
            if f.process_id != process_id:
                update["process_id"] = None
                update["activity_id"] = None
            if update:
                self.files[path] = f.model_copy(update=update)
            return
        self.files[path] = GenFile(path=path, content=content, origin=origin, activity_id=activity_id,
                                   process_id=process_id, scaffold=content)


# ------------------------------------------------------------------ the generator
def generate_go(app: Application, process_ids: list[str], designs: Any, rules: Any, findings: Any,
                scenarios: list[Scenario], out_dir: Path, mirrors: Mirrors) -> GeneratedSolution:
    """Generate the Go project for ``process_ids`` of ``app`` into ``out_dir``.

    ``designs``, ``rules`` and ``findings`` are as for ``generate_solution`` (keyed by process id or
    flat lists); a missing design is built for the Go stack. ``scenarios`` feed the tests. The
    module proxy (``mirrors.goproxy``) is not written into the project: the runner and the image
    build pass ``GOPROXY``.
    """
    if not process_ids:
        raise ValueError("process_ids is empty")
    procs = {p.id: p for p in app.processes}
    missing = [pid for pid in process_ids if pid not in procs]
    if missing:
        raise KeyError(f"unknown process ids: {missing}")

    def cited(item: Any) -> list[str]:
        acts = {c.activity_id for c in getattr(item, "citations", [])}
        return [pid for pid in process_ids if acts & {a.id for a in procs[pid].activities}]

    rules_map = dotnet._as_map(rules, process_ids, cited)
    findings_map = dotnet._as_map(findings, process_ids, cited)
    records, cards = _designs(app, process_ids, designs, rules_map, findings_map)
    gen = _GoSolution(app, process_ids, records, cards, findings_map, mirrors, rules_map, list(scenarios or []))
    files = gen.generate()
    ordered = [files[k] for k in sorted(files)]
    digest = hashlib.sha256()
    for f in ordered:
        digest.update(f.path.encode("utf-8") + b"\0" + f.content.encode("utf-8") + b"\0")
    solution = GeneratedSolution(root=Path(out_dir).resolve(), name=gen.app_name, files=ordered,
                                 build_hash=digest.hexdigest(), designs=dict(records))
    write_solution(solution)
    return solution


class _GoSolution:
    def __init__(self, app: Application, process_ids: list[str], designs: dict[str, DesignRecord],
                 cards: dict[str, Any], findings: dict[str, list[Finding]], mirrors: Mirrors,
                 rules: dict[str, list[Rule]] | None = None, scenarios: list[Scenario] | None = None) -> None:
        self.app = app
        self.rules = rules or {}
        self.scenarios = scenarios or []
        self.pids = process_ids
        self.designs = designs
        self.cards = cards
        self.findings = findings
        self.mirrors = mirrors
        self.naming = GoNaming()
        self.app_name = self.naming.type_name(app.id, "App")
        self.paths = layout_paths(self.app_name)
        self.module = self.paths["module"]
        self.procs = {p.id: p for p in app.processes}
        self.resources = {r.id: r for r in app.resources}
        self.out = _Files()
        self.connections: dict[str, str] = {}  # contract name (ConnectionStrings__<name>) -> config key
        self.clients: dict[str, str] = {}  # client type -> base address default
        self.topics: set[str] = set()
        self.timers: dict[str, int] = {}  # process type name -> interval ms
        self.timer_starts: dict[str, str] = {}  # process type name -> TIBCO start time of day
        self.consumers: list[str] = []  # process type names with a JMS consumer
        self.jobs: list[str] = []  # process type names with a timer job
        self.handlers: list[dict[str, Any]] = []  # router entries
        self.wiring: list[tuple[GoProcess, list[Dep]]] = []
        self.stub_services: list[str] = []
        # global element -> the contract struct it reads as (import path, type); None when two
        # contracts declare it, so no struct is guessed
        self.roots: dict[str, tuple[str, str] | None] = {}

    # -------------------------------------------------------------- helpers
    def ctx(self, **extra: Any) -> dict[str, Any]:
        return {"module": self.module, "app": self.app_name, "app_id": self.app.id, **extra}

    def template(self, path: str, template: str, **extra: Any) -> None:
        self.out.add(path, render(template, **self.ctx(**extra)))

    def connection(self, act: Activity) -> tuple[str, str]:
        """(contract name, config key) of a JDBC step's resource: the name is the .NET one
        (``ConnectionStrings__<name>``), the key what ``platform.Config`` reads it as."""
        ref = str(act.config.get("resource_ref") or "")
        res = self.resources.get(ref)
        if res is None:
            hits = [r for r in self.resources.values() if r.id.endswith("." + ref) or r.id.endswith("/" + ref)]
            res = hits[0] if len(hits) == 1 else None
        raw = res.name if res else (ref.rsplit(".", 1)[-1] or "Default")
        name = ident(raw, "Resource")
        key = self.naming.connection_key(raw)
        self.connections[name] = key
        return name, key

    # -------------------------------------------------------------- the run
    def generate(self) -> dict[str, GenFile]:
        self.project_files()
        self.runtime_files()
        self.contract_files()
        printers = self.process_files()
        self.agent_classes(printers)
        self.wiring_files(printers)
        self.config_files(printers)
        self.test_files(printers)
        return self.out.files

    def project_files(self) -> None:
        add = self.out.add
        add("go.mod", render("go.mod.j2", module=self.module, go_min=versions.GO_MIN, direct=versions.DIRECT,
                             indirect=versions.INDIRECT))
        add("go.sum", versions.go_sum())
        add(".gitignore", render("gitignore.j2"))
        add(".editorconfig", render("editorconfig.j2"))

    def runtime_files(self) -> None:
        self.template("internal/tib/tib.go", "tib.go.j2")
        self.template("internal/tib/text.go", "tib_text.go.j2")
        self.template("internal/tib/state.go", "tib_state.go.j2")
        self.template("internal/tib/fault.go", "fault.go.j2")
        self.template("internal/tib/masking.go", "masking.go.j2", names=sorted(SENSITIVE_NAMES))
        self.template("internal/tib/lenient.go", "lenient.go.j2")
        for name in ("config", "auth", "devtoken", "health", "bus", "bus_memory", "bus_servicebus", "bus_dev"):
            self.template(f"internal/platform/{name}.go", f"{name}.go.j2")
        self.template("internal/core/message_bus.go", "message_bus.go.j2")
        self.template("internal/infrastructure/db.go", "db.go.j2")

    def contract_files(self) -> None:
        plans = plan_contracts(self.app_name, self.app.contracts, self.naming)
        by_id = {c.id: c for c in self.app.contracts}
        ids = sorted({cm.contract_id for d in self.designs.values() for cm in d.contracts})
        for cid in ids:
            plan, contract = plans.get(cid), by_id.get(cid)
            if plan is None or contract is None:
                continue
            try:
                model = contract_model(contract, plan.reserved)
            except (etree.XMLSyntaxError, ValueError):
                continue  # an unparsable contract has no records (``contracts.record_names`` gives [])
            self.out.add(f"internal/core/{plan.file}", render_contract(model, plan.namespace, cid))
            for record, path in model.root_paths:
                element = path.strip("/").split("/")[0]
                qualified = (plan.namespace, record)
                self.roots[element] = qualified if self.roots.get(element, qualified) == qualified else None

    # -------------------------------------------------------------- processes
    def process_files(self) -> list[GoProcess]:
        in_build = {pid: f"{self.naming.type_name(self.procs[pid].name, 'Process')}Service" for pid in self.pids}
        fields = {pid: self.naming.type_name(self.procs[pid].name, "Process") for pid in self.pids}
        ctx = Ctx(module=self.module, naming=self.naming, in_build=in_build, fields=fields)
        plans: dict[str, ServicePlan] = {pid: build_plan(self.procs[pid], self.app, self.designs[pid])
                                         for pid in self.pids}
        for pid in self.pids:  # sub-processes outside the build get a stub service
            for step in plans[pid].steps:
                if step.pattern == "call.process" and step.call_target and step.call_target not in in_build:
                    target = step.call_target
                    name = self.procs[target].name if target in self.procs else target.rsplit(".", 1)[-1]
                    cls = f"{self.naming.type_name(name, 'Process')}Service"
                    ctx.stubs[target] = cls
                    self.out.add(f"internal/core/{snake(cls)}.go", self.subprocess_stub(cls, target))
        printers: list[GoProcess] = []
        for pid in self.pids:
            proc, design = self.procs[pid], self.designs[pid]
            decisions = {f.id: f.decision for f in self.findings.get(pid, [])}
            gp = GoProcess(ctx, proc, design, plans[pid], decisions)
            printers.append(gp)
            stem = snake(gp.name)
            mappings = gp.mappings()
            if mappings:
                self.out.add(f"internal/core/{stem}_mappings.go", mappings[0], mappings[1], process_id=pid)
            conditions = gp.conditions()
            if conditions:
                self.out.add(f"internal/core/{stem}_conditions.go", conditions[0], conditions[1], process_id=pid)
            service, origin, deps = gp.service()
            self.out.add(f"internal/core/{stem}_service.go", service, origin, process_id=pid)
            self.wiring.append((gp, deps))
            for act_id, (cls, fault) in sorted(gp.exceptions.items()):
                self.out.add(f"internal/core/{snake(cls)}.go", self.exception(cls, fault, proc, act_id),
                             "code", act_id, process_id=pid)
            if any(a.type.startswith("jdbc.") for a in proc.activities):
                text, origin = self.repository(gp)
                self.out.add(f"internal/infrastructure/{stem}_repository.go", text, origin, process_id=pid)
            self.starter_file(gp, plans[pid])
            for m in design.messaging:
                if m.kind == "topic":
                    self.topics.add(m.name)
        return printers

    def subprocess_stub(self, cls: str, target: str) -> str:
        return "\n".join([
            "package core", "", "import (", '\t"context"', '\t"fmt"', "", f'\t"{self.module}/internal/tib"', ")", "",
            f"// {cls} is the TIBCO sub-process {doc(target)}: not part of this build, so a call to it fails visibly.",
            f"type {cls} struct{{}}", "",
            "// Execute fails: the sub-process is not migrated in this build.",
            f"func (s *{cls}) Execute(ctx context.Context, state *tib.State) (tib.Result, error) {{",
            f"\treturn tib.Result{{}}, fmt.Errorf({go_string(f'sub-process {target} is not migrated in this build')})",
            "}"]) + "\n"

    def exception(self, cls: str, fault: str, proc: Process, act_id: str) -> str:
        act = next(a for a in proc.activities if a.id == act_id)
        return "\n".join([
            "package core", "", f'import "{self.module}/internal/tib"', "",
            f"// {cls}Name is the TIBCO fault name.", f"const {cls}Name = {go_string(fault)}", "",
            (f"// {cls} is TIBCO fault {doc(fault)}, thrown by {doc(act.name)} ({doc(proc.name)}); its data is the fault "
            "element."),
            f"type {cls} struct {{", "\t*tib.Fault", "}", "",
            f"// New{cls} is the fault with its data.",
            f"func New{cls}(data tib.Value) *{cls} {{", f"\treturn &{cls}{{tib.NewFault({cls}Name, data)}}", "}"]) + "\n"

    # -------------------------------------------------------------- repository
    def repository(self, gp: GoProcess) -> tuple[str, Origin]:
        origin: Origin = "code"
        members: list[list[str]] = []
        consts: list[str] = []
        methods = gp.repository_methods()
        cls = f"{gp.name}Repository"
        for a in gp.p.activities:
            if not a.type.startswith("jdbc."):
                continue
            method = methods[a.id]
            name, key = self.connection(a)
            sql = str(a.config.get("sql") or "")
            const = lower_first(gp.name) + method + "SQL"
            params = self.sql_params(a)
            timeout = str(a.config.get("timeout") or "").strip()
            max_rows = str(a.config.get("max_rows") or "").strip()
            t = int(timeout) if timeout.isdigit() else 0
            n = int(max_rows) if a.type == "jdbc.query" and max_rows.isdigit() else 0
            binding = ", ".join(f"${i + 1} = {p} ({typ})" for i, (p, typ) in enumerate(params)) or "none"
            consts += ["", f"// {const} is the SQL of TIBCO step {doc(a.name)}, verbatim.",
                       f"const {const} = {raw_string(chr(10) + convert_placeholders(sql, 'dollar') + chr(10))}"]
            doc_lines = [(f"// {method} is TIBCO step {doc(a.name)} ({a.type}) on {doc(name)}: the SQL statement is kept "
                         "verbatim."), "//", f"// {doc(gp.remarks(a.id))}", "//",
                         f"// Parameters, bound in order to the placeholders: {doc(binding)}."]
            if n:
                doc_lines += ["//", f"// TIBCO maxRows {n}: rows past {n} are dropped; the SQL stays verbatim."]
            member = [*doc_lines, f"func (s *{cls}) {method}(ctx context.Context, input tib.Value) (tib.Value, error) {{"]
            if a.type == "jdbc.call":
                origin = "agent"
                member += [f"\t// TIBCO stored procedure call ({const}); output parameters are mapped by the Code Builder.",
                           placeholder(a.name, "\t"), "}"]
                members.append(member)
                continue
            args = "".join(f",\n\t\ttib.JDBC(tib.Path(input, {go_string(p)}), {go_string(typ)})" for p, typ in params)
            if a.type == "jdbc.query":
                member.append(f"\treturn s.db.Query(ctx, {go_string(name)}, {go_string(key)}, {t}, {n}, {const}{args})")
            else:
                member.append(f"\treturn s.db.Exec(ctx, {go_string(name)}, {go_string(key)}, {t}, {const}{args})")
            member.append("}")
            members.append(member)
        lines = ["package infrastructure", "", "import (", '\t"context"', "", f'\t"{self.module}/internal/tib"', ")",
                 *consts, "",
                 (f"// {cls} holds the database steps of {doc(gp.p.name)}: every statement is the TIBCO SQL verbatim, its "
                  "placeholders bound in order."),
                 f"type {cls} struct {{", "\tdb *DataSources", "}", "",
                 f"// New{cls} is the repository over the data sources.",
                 f"func New{cls}(db *DataSources) *{cls} {{", f"\treturn &{cls}{{db: db}}", "}"]
        for m in members:
            lines += [""] + m
        return "\n".join(lines) + "\n", origin

    def sql_params(self, act: Activity) -> list[tuple[str, str]]:
        """(parameter name, type) per ``?`` placeholder, in order."""
        declared = act.config.get("parameters") or []
        out: list[tuple[str, str]] = []
        for i in range(dotnet.placeholder_count(str(act.config.get("sql") or ""))):
            d = declared[i] if i < len(declared) and isinstance(declared[i], dict) else {}
            out.append((str(d.get("name") or f"param{i + 1}"), _SQL_TYPE.get(str(d.get("type") or "").upper(), "string")))
        return out

    # -------------------------------------------------------------- starters
    def starter_file(self, gp: GoProcess, plan: ServicePlan) -> None:
        p = gp.p
        if p.starter is None or gp.starter_act is None:
            return
        row = gp.primary.get(gp.starter_act.id)
        sid = gp.starter_act.id
        stem = snake(gp.name)
        if gp.kind in ("rest", "http"):
            method = row.method if row else self.naming.method_name("", gp.starter_act.name)
            text, route = self.handler(gp, plan, method)
            self.out.add(f"internal/api/{stem}_handler.go", text, "code", sid, process_id=p.id)
            self.handlers.append({"var": lower_first(gp.name) + "Handler", "type": f"{gp.name}Handler",
                                  "field": gp.name, "routes": [{"pattern": route, "method": method}]})
        elif gp.kind == "soap":
            method = row.method if row else self.naming.method_name("", gp.starter_act.name)
            self.out.add(f"internal/api/{stem}_soap.go", self.soap_stub(gp, plan, method), "agent", sid,
                         process_id=p.id)
        elif gp.kind == "jms":
            text, origin = self.consumer(gp, plan)
            self.out.add(f"internal/infrastructure/messaging/{stem}_consumer.go", text, origin, sid, process_id=p.id)
            self.consumers.append(gp.name)
        elif gp.kind == "timer":
            self.out.add(f"internal/infrastructure/jobs/{stem}_job.go", self.job(gp, plan), "code", sid,
                         process_id=p.id)
            self.jobs.append(gp.name)

    def rest_parameters(self, gp: GoProcess, route: str) -> list[tuple[str, bool, str, bool]]:
        """(wire name, numeric, location, required) of a REST starter's parameters (as .NET reads them)."""
        if gp.kind != "rest":
            return []
        in_path = re.findall(r"\{([^}/]+)\}", route)
        card = self.cards.get(gp.p.id) or {}
        req = card.get("request_contract") if isinstance(card, dict) else None
        children = req.get("children", []) if isinstance(req, dict) and req.get("name") == "parameters" else []
        out: list[tuple[str, bool, str, bool]] = []
        for ch in children:
            wire = str(ch.get("name") or "")
            if not wire:
                continue
            loc = str(ch.get("location") or ("path" if wire in in_path else "query"))
            out.append((wire, str(ch.get("type") or "").lower() in _REST_NUMERIC, loc,
                        bool(ch.get("required")) or loc == "path"))
        for name in sorted(set(in_path) - {w for w, *_ in out}):
            out.append((name, False, "path", True))
        return out

    @staticmethod
    def wildcard(name: str, used: set[str]) -> str:
        """A ServeMux wildcard for a TIBCO path parameter: a Go identifier."""
        w = re.sub(r"\W", "_", name) or "p"
        if w[0].isdigit():
            w = f"p{w}"
        base, n = w, 2
        while w in used:
            w, n = f"{base}{n}", n + 1
        used.add(w)
        return w

    def handler(self, gp: GoProcess, plan: ServicePlan, method: str) -> tuple[str, str]:
        st = plan.starter
        route = st.route or f"/{gp.p.name}"
        if not route.startswith("/"):
            route = f"/{route}"
        verb = (st.verb or "POST").upper()
        sn = gp.starter_name() or "Start"
        params = self.rest_parameters(gp, route)
        wildcards: dict[str, str] = {}
        used: set[str] = set()
        for name in re.findall(r"\{([^}/]+)\}", route):
            wildcards[name] = self.wildcard(name, used)
        pattern = re.sub(r"\{([^}/]+)\}", lambda m: "{" + wildcards[m.group(1)] + "}", route)
        has_body = verb not in ("GET", "DELETE", "HEAD")
        http = gp.kind == "http"
        record = self.request_record(gp) if has_body and not http else None
        body: list[str] = []
        if has_body:
            body += ["\tbody, err := readBody(r)", "\tif err != nil {", "\t\tplatform.WriteError(w, r, err)",
                     "\t\treturn", "\t}"]
        if record:
            # X19: the request is checked against its contract struct at the boundary; the process
            # itself runs on the JSON tree, as the TIBCO process did.
            pkg = record[0].rsplit("/", 1)[-1]
            body += [f"\tif invalid := tib.Invalid(body, &{pkg}.{record[1]}{{}}); invalid != \"\" {{",
                     "\t\tplatform.WriteProblem(w, http.StatusBadRequest, \"Bad Request\", invalid)", "\t\treturn",
                     "\t}"]
        if http:
            body += ["\tstarter := tib.Object()", f"\tstarter[\"Method\"] = {go_string(verb)}",
                     "\tstarter[\"QueryString\"] = r.URL.RawQuery"]
            if has_body:
                body.append("\tstarter[\"PostData\"] = tib.Text(body)")
            body.append(f"\tstate := tib.NewState({go_string(sn)}, starter)")
        else:
            body.append("\tparameters := tib.Object()")
            for wire, numeric, loc, required in params:
                if loc == "path":
                    source = f"r.PathValue({go_string(wildcards.get(wire, wire))})"
                elif loc == "header":
                    source = f"r.Header.Get({go_string(wire)})"
                else:
                    source = f"r.URL.Query().Get({go_string(wire)})"
                body.append(f"\tif err := parameter(parameters, {go_string(wire)}, {source}, {str(numeric).lower()}, "
                            f"{str(required).lower()}); err != nil {{")
                body += ["\t\tplatform.WriteError(w, r, err)", "\t\treturn", "\t}"]
            body += ["\tstarter := tib.Object()", "\tstarter[\"parameters\"] = parameters"]
            if has_body:
                body.append("\tstarter[\"item\"] = body")
            item = f".\n\t\tSet({go_string(sn + '.item')}, body)" if has_body else ""
            body.append(f"\tstate := tib.NewState({go_string(sn)}, starter).\n"
                        f"\t\tSet({go_string(sn + '.parameters')}, parameters){item}")
        body += ["\tresult, err := s.service.Execute(r.Context(), state)", "\tif err != nil {",
                 "\t\tplatform.WriteError(w, r, err)", "\t\treturn", "\t}", "\tplatform.WriteResult(w, result)"]
        cls = f"{gp.name}Handler"
        imports = {"net/http", f"{self.module}/internal/core", f"{self.module}/internal/platform",
                   f"{self.module}/internal/tib"}
        if record:
            imports.add(record[0])
        lines = ["package api", "", *import_block(imports, self.module), "",
                 f"// {cls} is the {doc(gp.kind.upper())} starter of {doc(gp.p.name)}: {doc(verb)} {doc(route)}.",
                 f"type {cls} struct {{", f"\tservice *core.{gp.service_class}", "}", "",
                 (f"// {method} answers {doc(verb)} {doc(route)} (TIBCO operation {doc(gp.starter_act.name)}). "
                  f"{doc(gp.remarks(gp.starter_act.id))}"),
                 f"func (s *{cls}) {method}(w http.ResponseWriter, r *http.Request) {{", *body, "}"]
        if not any(f.path == "internal/api/request.go" for f in self.out.files.values()):
            self.template("internal/api/request.go", "request.go.j2")
        return "\n".join(lines) + "\n", f"{verb} {pattern}"

    def request_record(self, gp: GoProcess) -> tuple[str, str] | None:
        """The contract struct a REST starter's body reads as (import path, type): the request
        element of its I/O card, as .NET types its ``[FromBody]``."""
        card = self.cards.get(gp.p.id) or {}
        req = card.get("request_contract") if isinstance(card, dict) else None
        name = req.get("name") if isinstance(req, dict) else None
        if not name or name == "parameters":
            return None
        return self.roots.get(str(name).split(":")[-1])

    def soap_stub(self, gp: GoProcess, plan: ServicePlan, method: str) -> str:
        cls = f"{gp.name}SoapService"
        op = plan.starter.route or gp.p.name
        return "\n".join([
            "package api", "", "import (", '\t"context"', "", f'\t"{self.module}/internal/core"',
            f'\t"{self.module}/internal/tib"', ")", "",
            (f"// {cls} is the SOAP starter of {doc(gp.p.name)} (operation {doc(op)}): the endpoint is drafted by the "
             "Code Builder; it calls the process service."),
            f"type {cls} struct {{", f"\tservice *core.{gp.service_class}", "}", "",
            f"// {method} is TIBCO operation {doc(op)}. {doc(gp.remarks(gp.starter_act.id))}",
            f"func (s *{cls}) {method}(ctx context.Context, request tib.Value) (tib.Value, error) {{",
            f"\t// TIBCO: SOAP operation {comment(op)} of {comment(gp.starter_act.name)}; the endpoint calls s.service.Execute.",
            placeholder(gp.starter_act.name, "\t"), "}"]) + "\n"

    def consumer(self, gp: GoProcess, plan: ServicePlan) -> tuple[str, Origin]:
        st = plan.starter
        dest = st.destination or ""
        # A topic starter receives on its own subscription, named after the process (as in .NET
        # and the design's messaging entities): <topic>/<process>.
        style = str((gp.p.starter.config if gp.p.starter else {}).get("messaging_style") or "").lower()
        entity = f"{dest}/{gp.p.name}" if style == "topic" else dest
        selector = st.selector or ""
        sn = gp.starter_name() or "Start"
        root = gp.message_root()
        origin: Origin = "code"
        pairs = selector_properties(selector)
        if not selector:
            match = ["\treturn true"]
        elif pairs is not None:
            checks = " && ".join(f"properties[{go_string(k)}] == {go_string(v)}" for k, v in pairs)
            match = [f"\treturn {checks}"]
        else:
            origin = "agent"
            match = [f"\t// TIBCO selector: {comment(selector)}", placeholder(f"{gp.starter_act.name} selector", "\t")]
        wrap = (f"\tinput[\"Body\"] = map[string]any{{{go_string(root)}: body}}" if root else "\tinput[\"Body\"] = body")
        cls = f"{gp.name}Consumer"
        n = gp.name
        lines = [
            "package messaging", "", "import (", '\t"context"', '\t"log/slog"', "",
            f'\t"{self.module}/internal/core"', f'\t"{self.module}/internal/platform"', f'\t"{self.module}/internal/tib"',
            ")", "",
            f"// {n}Destination is the TIBCO destination of {doc(gp.p.name)}, verbatim.",
            f"const {n}Destination = {go_string(dest)}", "",
            f"// {n}Entity is the bus entity {cls} receives on: the queue, or the topic subscription <topic>/<process>.",
            f"const {n}Entity = {go_string(entity)}", "",
            f"// {n}Selector is the TIBCO message selector, verbatim (empty: every message).",
            f"const {n}Selector = {go_string(selector)}", "",
            f"// {cls} is the JMS starter of {doc(gp.p.name)}: each message on {doc(dest)} runs the process once.",
            "//",
            "// A message the TIBCO selector would not deliver is dead-lettered (SelectorMismatch), so is a body",
            "// that is not JSON (InvalidBody) and a run that ends in a TIBCO fault (the fault name); any other",
            "// error abandons the message so the bus redelivers it.",
            f"type {cls} struct {{", f"\tservice *core.{gp.service_class}", "}", "",
            f"// New{cls} is the consumer over the process service.",
            f"func New{cls}(service *core.{gp.service_class}) *{cls} {{", f"\treturn &{cls}{{service: service}}", "}", "",
            f"// Handle settles one message. {doc(gp.remarks(gp.starter_act.id))}",
            f"func (s *{cls}) Handle(ctx context.Context, message *tib.Message) platform.Outcome {{",
            "\tif !selectorMatches" + n + "(message.Properties) {",
            f"\t\treturn platform.DeadLettered(\"SelectorMismatch\", \"the message does not match \"+{n}Selector)",
            "\t}", "\tbody, err := tib.Parse(message.Body)", "\tif err != nil {",
            "\t\treturn platform.DeadLettered(\"InvalidBody\", tib.MaskText(err.Error()))", "\t}",
            "\tinput := tib.Object()", wrap,
            "\tinput[\"JMSProperties\"] = platform.ApplicationProperties(message)",
            "\tinput[\"JMSHeaders\"] = platform.JMSHeaders(message)",
            f"\tif _, err := s.service.Execute(ctx, tib.NewState({go_string(sn)}, input).WithMessage(message)); err != nil {{",
            "\t\tif fault, ok := tib.AsFault(err); ok {",
            (f"\t\t\tslog.WarnContext(ctx, \"the run ended in a fault\", \"process\", {go_string(gp.p.name)}, "
            "\"fault\", fault.FaultName())"),
            "\t\t\treturn platform.DeadLettered(fault.FaultName(), tib.MaskText(fault.Error()))", "\t\t}",
            "\t\treturn platform.Abandoned(err)", "\t}", "\treturn platform.Completed", "}", "",
            f"// selectorMatches{n} is whether the TIBCO selector would deliver a message with these properties.",
            f"func selectorMatches{n}(properties map[string]string) bool {{", *match, "}"]
        return "\n".join(lines) + "\n", origin

    def job(self, gp: GoProcess, plan: ServicePlan) -> str:
        interval = plan.starter.interval_ms or 86_400_000
        self.timers[gp.name] = interval
        start = str((gp.p.starter.config if gp.p.starter else {}).get("start") or "").strip()
        if start:
            self.timer_starts[gp.name] = start
        cls = f"{gp.name}Job"
        sn = gp.starter_name() or "Timer"
        key = f"JOBS_{env_key(gp.name)}"
        return "\n".join([
            "package jobs", "", "import (", '\t"context"', '\t"time"', "", f'\t"{self.module}/internal/core"',
            f'\t"{self.module}/internal/tib"', ")", "",
            (f"// {cls} is the timer starter of {doc(gp.p.name)}: every {key}_INTERVAL_MS (Jobs__{gp.name}__IntervalMs), "
             f"first at {key}_START (Jobs__{gp.name}__Start, local time) when set, when JOBS_ENABLED is true."),
            f"type {cls} struct {{", f"\tservice *core.{gp.service_class}", "}", "",
            f"// New{cls} is the job over the process service.",
            f"func New{cls}(service *core.{gp.service_class}) *{cls} {{", f"\treturn &{cls}{{service: service}}", "}", "",
            f"// {cls}Interval is the TIBCO timer interval in milliseconds, the default of {key}_INTERVAL_MS.",
            f"const {cls}Interval = {interval}", "",
            f"// Run is one TIBCO timer tick. {doc(gp.remarks(gp.starter_act.id))}",
            f"func (s *{cls}) Run(ctx context.Context) error {{", "\ttimer := tib.Object()",
            "\ttimer[\"Time\"] = tib.DateTime(time.Now())",
            f"\t_, err := s.service.Execute(ctx, tib.NewState({go_string(sn)}, timer))", "\treturn err", "}"]) + "\n"

    # -------------------------------------------------------------- agent classes
    def agent_classes(self, printers: list[GoProcess]) -> None:
        """One type per file the steps call (HTTP clients, transforms, gateways), and a stub type
        for every design row whose file nothing else wrote, so each row names code that exists."""
        members: dict[str, list[tuple[AgentMember, str]]] = defaultdict(list)
        owners: dict[str, set[str]] = defaultdict(set)
        for gp in printers:
            for m in gp.agent_members:
                members[m.file].append((m, gp.p.id))
                owners[m.file].add(gp.p.id)
        for gp in printers:
            for r in gp.design.mapping:
                file = f"{r.project}/{r.file}"
                if file in self.out.files or r.pattern == "input.mapping":
                    continue
                act = gp.acts.get(r.activity_id)
                if act is None:
                    continue
                if not any(m.method == r.method for m, _ in members[file]):
                    members[file].append((AgentMember(file, r.class_name, r.method, act, gp.remarks(act.id), r.pattern),
                                          gp.p.id))
                    owners[file].add(gp.p.id)
        for file in sorted(members):
            if file in self.out.files:
                continue
            items = members[file]
            cls = items[0][0].class_name
            client = items[0][0].kind in ("http.invoke", "rest.invoke", "soap.invoke")
            owner = next(iter(owners[file])) if len(owners[file]) == 1 else None
            self.out.add(file, self.agent_class(file, cls, items, client), "agent", process_id=owner)

    def agent_class(self, file: str, cls: str, items: list[tuple[AgentMember, str]], client: bool) -> str:
        package = package_name_of(str(PurePosixPath(file).parent))
        seen: set[str] = set()
        methods: list[str] = []
        for m, _ in items:
            if m.method in seen:
                continue
            seen.add(m.method)
            methods += ["", f"// {m.method} is TIBCO step {doc(m.activity.name)} ({m.activity.type}). {doc(m.remarks)}",
                        f"func (s *{cls}) {m.method}(ctx context.Context, input tib.Value) (tib.Value, error) {{",
                        (f"\t// TIBCO: {comment(m.activity.name)} ({m.activity.type}), native type "
                        f"{comment(m.activity.native_type)}")]
            for b in m.activity.input_bindings:
                methods.append(tibco(b.target, b.expression, "\t"))
            methods += [placeholder(m.activity.name, "\t"), "}"]
        imports = {"context", f"{self.module}/internal/tib"}
        if client:
            imports.add(f"{self.module}/internal/platform")
            key = f"CLIENTS_{env_key(cls)}_BASE_ADDRESS"
            self.clients.setdefault(cls, "")
            head = [(f"// {cls} is the TIBCO client {doc(cls)}: one method per step that calls it; the Code Builder "
                    f"writes the calls against the base address {key} (Clients__{cls}__BaseAddress)."),
                    f"type {cls} struct {{", "\tbaseAddress string", "}", "",
                    f"// New{cls} reads the base address from config.",
                    f"func New{cls}(config *platform.Config) *{cls} {{",
                    f"\treturn &{cls}{{baseAddress: config.Get({go_string(key)})}}", "}", "",
                    "// BaseAddress is the base address the calls go to.",
                    f"func (s *{cls}) BaseAddress() string {{", "\treturn s.baseAddress", "}"]
        else:
            head = [f"// {cls} holds the TIBCO steps the Code Builder writes: one method per step.",
                    f"type {cls} struct{{}}"]
        lines = [f"package {package}", "", *import_block(imports, self.module), "", *head, *methods]
        return "\n".join(lines) + "\n"

    # -------------------------------------------------------------- wiring
    def wiring_files(self, printers: list[GoProcess]) -> None:
        """``core/services.go`` (every process service, wired), ``api/router.go`` (the routes and
        the dependencies), ``cmd/server/main.go``, and the consumers' and jobs' start functions."""
        mod = self.module
        imports = {f"{mod}/internal/infrastructure", f"{mod}/internal/platform"}
        fields = [(gp.name, f"*{gp.service_class}") for gp, _ in self.wiring]
        body = ["\ts := &Services{}"]
        body += [f"\ts.{gp.name} = &{gp.service_class}{{}}" for gp, _ in self.wiring]
        for gp, deps in self.wiring:
            for d in deps:
                if d.import_path:
                    imports.add(d.import_path)
                build = d.build.replace("{services}", "s")
                body.append(f"\ts.{gp.name}.{d.field} = {build}")
        body.append("\treturn s")
        lines = ["package core", "", *import_block(imports, mod), "",
                 "// Services are the process services of this build, wired together.",
                 *struct_lines("Services", fields), "",
                 "// NewServices builds every process service over the configuration, the data sources and the bus.",
                 "func NewServices(config *platform.Config, db *infrastructure.DataSources, bus platform.Bus) *Services {",
                 *body, "}"]
        self.out.add("internal/core/services.go", "\n".join(lines) + "\n")
        self.template("internal/api/router.go", "router.go.j2", topics=sorted(self.topics), handlers=self.handlers)
        self.template("cmd/server/main.go", "main.go.j2", consumers=bool(self.consumers), jobs=bool(self.jobs))
        if self.consumers:
            start = ["// Start subscribes the JMS consumers to their entities; call the returned function to stop them.",
                     "func Start(bus platform.Bus, services *core.Services) (func(), error) {", "\tvar stops []func()",
                     "\tstop := func() {", "\t\tfor _, s := range stops {", "\t\t\ts()", "\t\t}", "\t}"]
            for n in self.consumers:
                start += [f"\tif s, err := bus.Subscribe({n}Entity, New{n}Consumer(services.{n}).Handle); err != nil {{",
                          "\t\tstop()", "\t\treturn nil, err", "\t} else {", "\t\tstops = append(stops, s)", "\t}"]
            start += ["\treturn stop, nil", "}"]
            lines = ["package messaging", "", *import_block({f"{mod}/internal/core", f"{mod}/internal/platform"}, mod), "",
                     *start]
            self.out.add("internal/infrastructure/messaging/consumers.go", "\n".join(lines) + "\n")
        if self.jobs:
            start = [("// Start runs the timer jobs when JOBS_ENABLED is true (Jobs__Enabled; false in development), each "
                     "every JOBS_<JOB>_INTERVAL_MS, first at JOBS_<JOB>_START (local HH:MM or HH:MM:SS) when set."),
                     "func Start(ctx context.Context, config *platform.Config, services *core.Services) {",
                     "\tif !config.Bool(\"JOBS_ENABLED\", true) {", "\t\treturn", "\t}"]
            for n in self.jobs:
                key = f"JOBS_{env_key(n)}"
                start.append(f"\tschedule(ctx, config.Int({go_string(key + '_INTERVAL_MS')}, {n}JobInterval), "
                             f"config.Get({go_string(key + '_START')}), New{n}Job(services.{n}).Run)")
            start.append("}")
            imports = {"context", f"{mod}/internal/core", f"{mod}/internal/platform"}
            lines = ["package jobs", "", *import_block(imports, mod), "", *start]
            self.out.add("internal/infrastructure/jobs/jobs.go", "\n".join(lines) + "\n")
            self.template("internal/infrastructure/jobs/schedule.go", "schedule.go.j2")

    # -------------------------------------------------------------- tests (written before the bodies)
    def test_files(self, printers: list[GoProcess]) -> None:
        """The tests of every process, the shared test support, the estate SQL and the test index."""
        sql_texts: dict[str, str] = {}
        estate = dotnet._estate_root(self.app)
        for rel in self.app.sql_files:
            path = (estate / rel) if estate else None
            if path and path.is_file():
                sql_texts[PurePosixPath(rel).name] = path.read_text(encoding="utf-8")
        schemas = sorted({m.lower() for t in sql_texts.values()
                          for m in re.findall(r"(?i)create\s+table\s+(?:if\s+not\s+exists\s+)?([A-Za-z_]\w*)\.", t)})
        for f in tests_gen.generate_test_support(self.module, schemas, sql_texts, sorted(set(self.connections.values()))):
            self.out.add(f.path, f.content, f.origin)
        entries = []
        for gp in printers:
            ctx = tests_gen.GoTestContext(module=self.module, app=self.app_name, process=gp.p, name=gp.name,
                                          sql_texts=sql_texts, schemas=schemas)
            files, found = tests_gen.generate_go_tests(gp.plan, self.rules.get(gp.p.id, []), self.scenarios,
                                                       gp.design, ctx)
            for f in files:
                self.out.add(f.path, f.content, f.origin, f.activity_id, process_id=gp.p.id)
            entries += found
        index = write_index(entries)
        self.out.add(index.path, index.content)

    # -------------------------------------------------------------- configuration
    def config_files(self, printers: list[GoProcess]) -> None:
        main, dev, secrets, plain, not_carried = self.settings(printers)
        lines = ["package platform", "",
                 ("// The configuration defaults, generated by ExiQO Migrate from TIBCO BusinessWorks application "
                 f"{doc(self.app.id)}."),
                 "// Keys follow the runtime contract: an environment name such as Messaging__Mode,",
                 "// ConnectionStrings__<Resource> or Gmf__<Module>__<Name> is read as the key below",
                 "// (MESSAGING_MODE, CONNECTION_STRINGS_<RESOURCE>, GMF_<MODULE>_<NAME>); secrets come from",
                 "// Key Vault (KeyVault__Uri). The development defaults apply with ASPNETCORE_ENVIRONMENT=Development.",
                 "", "// defaults are the configuration every environment starts from.",
                 "func defaults() map[string]string {", "\td := map[string]string{}"]
        if not_carried:
            lines.append("\t// TIBCO substitution variables not carried over (nothing in the generated code reads them):")
            lines += [f"\t//   {comment(name)}: {comment(reason)}" for name, reason in not_carried]
        lines += [f"\td[{go_string(k)}] = {go_string(v)}" for k, v in main.items()]
        lines += ["\treturn d", "}", "", "// developmentDefaults apply over defaults with ASPNETCORE_ENVIRONMENT=Development.",
                  "func developmentDefaults() map[string]string {", "\td := map[string]string{}"]
        lines += [f"\td[{go_string(k)}] = {go_string(v)}" for k, v in dev.items()]
        lines += ["\treturn d", "}"]
        self.out.add("internal/platform/settings.go", "\n".join(lines) + "\n")
        contract = {"connections": sorted(self.connections), "clients": sorted(self.clients),
                    "secrets": [{"name": n, "key": k} for n, k in secrets], "plain_keys": plain,
                    "port_env": "PORT", "health": "/health"}
        self.out.add(CONTRACT_FILE, json.dumps(contract, indent=2, sort_keys=True) + "\n")

    def settings(self, printers: list[GoProcess]) -> tuple[dict[str, str], dict[str, str], list[tuple[str, str]],
                                                            list[str], list[tuple[str, str]]]:
        """(defaults, development defaults, Key Vault (name, env key) secrets, plain env keys,
        substitution variables not carried) of settings.go, as the .NET generator decides them."""
        seed = hashlib.sha256(f"exiqo-dev:{self.app.id}".encode()).hexdigest()
        main: dict[str, str] = {
            "AUTH_MODE": "Production", "AUTH_ENTRA_ID_INSTANCE": "https://login.microsoftonline.com/",
            "AUTH_ENTRA_ID_TENANT_ID": "<entra-tenant-id>", "AUTH_ENTRA_ID_AUDIENCE": "api://<app-registration-client-id>",
            "MESSAGING_MODE": "ServiceBus",
            "MESSAGING_SERVICE_BUS_FULLY_QUALIFIED_NAMESPACE": "<service-bus-namespace>.servicebus.windows.net",
            "KEY_VAULT_URI": "", "JOBS_ENABLED": "true",
        }
        dev: dict[str, str] = {
            "AUTH_MODE": "Development", "AUTH_DEV_TOKEN_ISSUER": "exiqo-dev",
            "AUTH_DEV_TOKEN_AUDIENCE": f"{self.app_name.lower()}-api",
            "AUTH_DEV_TOKEN_SIGNING_KEY": f"development-only-{seed}",
            "CORS_ORIGINS_0": "http://localhost:3000", "MESSAGING_MODE": "InMemory", "JOBS_ENABLED": "false",
        }
        secrets: dict[str, str] = {}
        plain: set[str] = set()
        not_carried: dict[str, str] = {}
        resources = {r.name: r for r in self.app.resources}
        used = set(self.connections.values())
        for gp in printers:
            module = gp.p.module or self.app.id
            for row in gp.design.config:
                if row.key.startswith("CONNECTION_STRINGS_"):
                    if row.key not in used:
                        continue
                    name = next(n for n, k in self.connections.items() if k == row.key)
                    res = resources.get(row.substvar[len("resource:"):].rpartition(".")[0])
                    settings = res.settings if res else {}
                    secrets[f"ConnectionStrings--{name}"] = f"ConnectionStrings__{name}"
                    dev[row.key] = _npgsql(str(settings.get("jdbc_url") or ""), str(settings.get("username") or ""))
                    continue
                if row.substvar.startswith("resource:") or not _references(gp.p, row.substvar):
                    not_carried.setdefault(row.substvar, self.not_carried(row.substvar))
                    continue
                env = f"Gmf__{ident(module, 'Module')}__{ident(row.substvar, 'Var')}"
                found = dotnet._substvar_value(self.app, gp.p.module, row.substvar)
                value, typ = found if found else ("", "String")
                local_value = str(dotnet._typed(value, typ)) if value else "change-me-local"
                if isinstance(dotnet._typed(value, typ) if value else None, bool):
                    local_value = local_value.lower()
                if row.secret:
                    secrets[env.replace("__", "--")] = env
                    dev[row.key] = local_value
                else:
                    plain.add(env)
                    main[row.key] = local_value
        for name, interval in sorted(self.timers.items()):
            main[f"JOBS_{env_key(name)}_INTERVAL_MS"] = str(interval)
            plain.add(f"Jobs__{name}__IntervalMs")
            if name in self.timer_starts:
                main[f"JOBS_{env_key(name)}_START"] = self.timer_starts[name]
                plain.add(f"Jobs__{name}__Start")
        for cls in sorted(self.clients):
            main[f"CLIENTS_{env_key(cls)}_BASE_ADDRESS"] = "http://localhost/"
            plain.add(f"Clients__{cls}__BaseAddress")
        return main, dev, sorted(secrets.items()), sorted(plain), sorted(not_carried.items())

    def not_carried(self, substvar: str) -> str:
        """Why a TIBCO substitution variable is not in settings.go (the .NET reasons, in Go words)."""
        resources = list(self.app.resources)
        if substvar.startswith("resource:"):
            rname = substvar[len("resource:"):].rpartition(".")[0]
            res = next((r for r in resources if r.name == rname), None)
            if res is not None and res.kind == "jms":
                return "JMS connection setting: replaced by Azure Service Bus (MESSAGING_*)"
            if res is not None and res.kind == "jdbc":
                return f"JDBC setting: replaced by {self.naming.connection_key(rname)}"
            return "resource setting not read by the generated code"
        for res in resources:
            bindings = res.settings.get("substitution_bindings") if isinstance(res.settings, dict) else None
            for field_name, var in (bindings or {}).items():
                if var != substvar:
                    continue
                if res.kind == "jms":
                    return "JMS connection setting: replaced by Azure Service Bus (MESSAGING_*)"
                if res.kind == "jdbc":
                    key = self.naming.connection_key(res.name)
                    return f"JDBC setting: replaced by {key}" if key in self.connections.values() else \
                        "JDBC setting of a resource the selected processes do not use"
                if res.kind == "http" and res.settings.get("role") == "server" and field_name in ("port", "host"):
                    return "HTTP listener address: set by the host (PORT, container ingress)"
                if res.kind == "http":
                    return f"HTTP client setting: carried as CLIENTS_{env_key(ident(res.name, 'Resource'))}_BASE_ADDRESS"
        return "not read by the generated code"


# ------------------------------------------------------------------ helpers
def _references(process: Process, name: str) -> bool:
    text = json.dumps([[a.config, [b.expression for b in a.input_bindings]] for a in process.activities],
                      sort_keys=True, default=str)
    return re.search(rf"(?<![\w.]){re.escape(name)}(?![\w.])", text) is not None


def _npgsql(url: str, user: str) -> str:
    m = re.match(r"jdbc:postgresql://([^:/]+)(?::(\d+))?/([^?;]+)", url or "")
    host, port, db = (m.group(1), m.group(2) or "5432", m.group(3)) if m else ("localhost", "5432", "postgres")
    return f"Host={host};Port={port};Database={db};Username={user or 'postgres'};Password=change-me-local"


__all__ = ["BINARY", "CONTRACT_FILE", "generate_go", "layout_paths", "prune_imports"]
