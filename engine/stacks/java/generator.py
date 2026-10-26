"""Java generator (target-stacks spec section 4): the IR and the design records to a Spring Boot 3.5
service on Java 21, one Maven module, package ``com.gmf.<app>``.

Deterministic: the same inputs give byte-identical files and the same ``build_hash``. Each process
is printed from its ``ServicePlan`` (``printer``): a service, its mappings and link conditions; a
repository with the TIBCO SQL verbatim in text blocks and its ``?`` placeholders bound in order;
a controller for a REST or HTTP starter, a Service Bus consumer for a JMS starter, a scheduled job
for a timer. What code cannot write keeps the TIBCO expression in ``// TIBCO`` comments above
``throw new UnsupportedOperationException("agent fills: <activity>");`` and the file is
``origin="agent"``.

Layout (``<app>`` = the application's lower-case alphanumerics)::

    pom.xml, .exiqo/maven-settings.xml (the GMF mirror), .exiqo/service-contract.json,
    .exiqo/test-index.json, .gitignore, .editorconfig
    src/main/java/com/gmf/<app>/Application.java, config/EnvBinding.java
      core/            services, mappings, conditions, contracts/, exceptions/, messaging/MessageBus,
                       tibco/ (TibValue, TibText, ProcessState, ProcessFlow, ProcessResult)
      infrastructure/  repositories, DataSources, messaging/ (buses, consumers), jobs/, clients/
      api/             controllers, SecurityConfig, DevTokenController, BusDevController, FaultHandler
    src/main/resources/application.yml, META-INF/spring.factories

The runtime contract (spec section 5) is the .NET one: ``/health``, ``/auth/dev-token``, the
``/_bus`` endpoints, faults as problem+json, and ``__``-separated environment names bound by
``EnvBinding``. Tests are generated first by Task 8 (``tests_gen``); this module writes an empty
test index.
"""
from __future__ import annotations

import hashlib
import json
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
from engine.stacks.java import versions
from engine.stacks.java.contracts import render as render_contract
from engine.stacks.java.naming import JavaNaming, kebab, package_name
from engine.stacks.java.printer import AgentMember, Ctx, JavaProcess, Origin, placeholder, row_layer
from engine.stacks.java.text import comment, doc, java_string, sort_imports, text_block, tibco
from engine.stacks.plan import ServicePlan, build_plan
from engine.stacks.sql import convert_placeholders
from engine.stacks.testindex import write_index

TEMPLATES = Path(__file__).resolve().parent / "templates"
CONTRACT_FILE = ".exiqo/service-contract.json"
SETTINGS_FILE = ".exiqo/maven-settings.xml"
_JINJA = jinja2.Environment(
    loader=jinja2.FileSystemLoader(str(TEMPLATES)), keep_trailing_newline=True, trim_blocks=True,
    lstrip_blocks=True, undefined=jinja2.StrictUndefined, autoescape=False)
_SQL_TYPE = {"VARCHAR": "string", "CHAR": "string", "LONGVARCHAR": "string", "NVARCHAR": "string",
             "NCHAR": "string", "CLOB": "string", "NUMERIC": "decimal", "DECIMAL": "decimal", "INTEGER": "int",
             "SMALLINT": "int", "TINYINT": "int", "BIGINT": "long", "DOUBLE": "double", "FLOAT": "double",
             "REAL": "double", "BIT": "bool", "BOOLEAN": "bool", "DATE": "date", "TIMESTAMP": "datetime",
             "TIME": "time"}
_SQL_TYPES = {"string": "VARCHAR", "int": "INTEGER", "long": "BIGINT", "decimal": "NUMERIC", "double": "DOUBLE",
              "bool": "BOOLEAN", "date": "DATE", "time": "TIME"}
_REST_TYPE = {"integer": "Integer", "int": "Integer", "long": "Long", "number": "BigDecimal", "decimal": "BigDecimal",
              "boolean": "Boolean", "bool": "Boolean"}
_MAPPINGS = {"GET": "GetMapping", "POST": "PostMapping", "PUT": "PutMapping", "DELETE": "DeleteMapping",
             "PATCH": "PatchMapping"}
_SELECTOR_ATOM = re.compile(r"^\s*([A-Za-z_][\w.]*)\s*=\s*'([^']*)'\s*$")
_IMPORT_LINE = re.compile(r"^import ([\w.]+)\.(\w+);$")


def render(template: str, **ctx: Any) -> str:
    return _JINJA.get_template(template).render(**ctx)


def prune_imports(text: str) -> str:
    """Drops imports whose simple name the file never uses, then orders the rest."""
    lines = text.split("\n")
    code = "\n".join(ln for ln in lines if not ln.startswith("import "))
    kept = []
    for ln in lines:
        m = _IMPORT_LINE.match(ln)
        if m and not re.search(rf"\b{re.escape(m.group(2))}\b", code):
            continue
        kept.append(ln)
    return sort_imports("\n".join(kept))


def layout_paths(app_name: str) -> dict[str, str]:
    """Paths of a generated project: the layout contract with infrastructure and Verify."""
    naming = JavaNaming()
    artifact = re.sub(r"[^a-z0-9]", "", app_name.lower()) or "app"
    return {"artifact": artifact, "jar": f"target/{artifact}.jar", "package": package_name(app_name),
            "main": f"src/main/java/{package_name(app_name).replace('.', '/')}",
            **{k: naming.layer_folder(k, app_name) for k in ("api", "core", "infrastructure", "tests")}}


# ------------------------------------------------------------------ inputs
def _designs(app: Application, process_ids: list[str], design: Any, rules: dict[str, list[Rule]],
             findings: dict[str, list[Finding]]) -> tuple[dict[str, DesignRecord], dict[str, Any]]:
    """The Java design of every process (``build_design_skeleton(..., stack="java")`` for a missing
    one) and its I/O card; a given record for another stack is refused, its names are not Java's."""
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
                                                       card, stack="java")
        if rec.stack != "java":
            raise ValueError(f"the design of {pid} is for {rec.stack}, not java: re-run Design")
        out[pid] = rec
    return out, cards


class _Files:
    def __init__(self) -> None:
        self.files: dict[str, GenFile] = {}

    def add(self, path: str, content: str, origin: Origin = "code", activity_id: str | None = None,
            process_id: str | None = None) -> None:
        if any(path == r or path.startswith(r) for r in RESERVED_FOR_INFRA):
            raise ValueError(f"{path} is reserved for the infrastructure generator")
        if path.endswith(".java"):
            content = prune_imports(content)
        if path in self.files:  # a file two processes need: the first writer wins
            f = self.files[path]
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
def generate_java(app: Application, process_ids: list[str], designs: Any, rules: Any, findings: Any,
                  scenarios: list[Scenario], out_dir: Path, mirrors: Mirrors) -> GeneratedSolution:
    """Generate the Java project for ``process_ids`` of ``app`` into ``out_dir``.

    ``designs``, ``rules`` and ``findings`` are as for ``generate_solution`` (keyed by process id or
    flat lists); a missing design is built for the Java stack. ``scenarios`` feed the tests, which
    Task 8 generates. ``mirrors.maven`` is the only repository source (``.exiqo/maven-settings.xml``);
    empty writes no mirror, so Maven's own settings apply.
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
    gen = _JavaSolution(app, process_ids, records, cards, findings_map, mirrors)
    files = gen.generate()
    ordered = [files[k] for k in sorted(files)]
    digest = hashlib.sha256()
    for f in ordered:
        digest.update(f.path.encode("utf-8") + b"\0" + f.content.encode("utf-8") + b"\0")
    solution = GeneratedSolution(root=Path(out_dir).resolve(), name=gen.app_name, files=ordered,
                                 build_hash=digest.hexdigest(), designs=dict(records))
    write_solution(solution)
    return solution


class _JavaSolution:
    def __init__(self, app: Application, process_ids: list[str], designs: dict[str, DesignRecord],
                 cards: dict[str, Any], findings: dict[str, list[Finding]], mirrors: Mirrors) -> None:
        self.app = app
        self.pids = process_ids
        self.designs = designs
        self.cards = cards
        self.findings = findings
        self.mirrors = mirrors
        self.naming = JavaNaming()
        self.app_name = self.naming.type_name(app.id, "App")
        self.paths = layout_paths(self.app_name)
        self.pkg = self.paths["package"]
        self.procs = {p.id: p for p in app.processes}
        self.resources = {r.id: r for r in app.resources}
        self.out = _Files()
        self.connections: dict[str, str] = {}  # contract name (ConnectionStrings__<name>) -> property
        self.clients: dict[str, str] = {}  # client class -> base address default
        self.topics: set[str] = set()
        self.timers: dict[str, int] = {}  # process type name -> interval ms

    # -------------------------------------------------------------- helpers
    def path(self, layer_file: str) -> str:
        """A file under the package root (``core/X.java``) as a project path."""
        return f"{self.paths['main']}/{layer_file}"

    def ctx(self, **extra: Any) -> dict[str, Any]:
        return {"pkg": self.pkg, "app": self.app_name, "app_id": self.app.id, **extra}

    def template(self, layer_file: str, template: str, **extra: Any) -> None:
        self.out.add(self.path(layer_file), render(template, **self.ctx(**extra)))

    def connection(self, act: Activity) -> tuple[str, str]:
        """(contract name, property) of a JDBC step's resource: the name is the .NET one
        (``ConnectionStrings__<name>``), the property its canonical form."""
        ref = str(act.config.get("resource_ref") or "")
        res = self.resources.get(ref)
        if res is None:
            hits = [r for r in self.resources.values() if r.id.endswith("." + ref) or r.id.endswith("/" + ref)]
            res = hits[0] if len(hits) == 1 else None
        raw = res.name if res else (ref.rsplit(".", 1)[-1] or "Default")
        name = ident(raw, "Resource")
        prop = self.naming.connection_key(raw)
        self.connections[name] = prop
        return name, prop

    # -------------------------------------------------------------- the run
    def generate(self) -> dict[str, GenFile]:
        self.project_files()
        self.runtime_files()
        self.contract_files()
        printers = self.process_files()
        self.agent_classes(printers)
        self.infrastructure_files()
        self.api_files()
        self.config_files(printers)
        return self.out.files

    def project_files(self) -> None:
        add = self.out.add
        add("pom.xml", render("pom.xml.j2", **self.ctx(artifact=self.paths["artifact"], v=versions)))
        add(SETTINGS_FILE, render("maven-settings.xml.j2", maven=self.mirrors.maven.strip()))
        add(".gitignore", render("gitignore.j2"))
        add(".editorconfig", render("editorconfig.j2"))
        add("src/main/resources/META-INF/spring.factories", render("META-INF/spring.factories.j2", **self.ctx()))
        self.template("Application.java", "Application.java.j2")
        self.template("config/EnvBinding.java", "EnvBinding.java.j2")
        add(write_index([]).path, write_index([]).content)

    def runtime_files(self) -> None:
        for name in ("TibValue", "TibText", "ProcessState", "ProcessFlow", "ProcessResult"):
            self.template(f"core/tibco/{name}.java", f"{name}.java.j2")
        self.template("core/contracts/TibcoPath.java", "TibcoPath.java.j2")
        self.template("core/contracts/Masking.java", "Masking.java.j2", names=sorted(SENSITIVE_NAMES))
        self.template("core/exceptions/ProcessFault.java", "ProcessFault.java.j2")
        self.template("core/messaging/MessageBus.java", "MessageBus.java.j2")

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
            holder = PurePosixPath(plan.file).stem
            self.out.add(self.path(f"core/{plan.file}"), render_contract(model, plan.namespace, holder, cid, self.pkg))

    # -------------------------------------------------------------- processes
    def process_files(self) -> list[JavaProcess]:
        in_build = {pid: f"{self.naming.type_name(self.procs[pid].name, 'Process')}Service" for pid in self.pids}
        ctx = Ctx(pkg=self.pkg, naming=self.naming, in_build=in_build)
        plans: dict[str, ServicePlan] = {pid: build_plan(self.procs[pid], self.app, self.designs[pid])
                                         for pid in self.pids}
        for pid in self.pids:  # sub-processes outside the build get a stub service
            for step in plans[pid].steps:
                if step.pattern == "call.process" and step.call_target and step.call_target not in in_build:
                    target = step.call_target
                    name = self.procs[target].name if target in self.procs else target.rsplit(".", 1)[-1]
                    cls = f"{self.naming.type_name(name, 'Process')}Service"
                    ctx.stubs[target] = cls
                    self.out.add(self.path(f"core/{cls}.java"), self.subprocess_stub(cls, target))
        printers: list[JavaProcess] = []
        for pid in self.pids:
            proc, design = self.procs[pid], self.designs[pid]
            decisions = {f.id: f.decision for f in self.findings.get(pid, [])}
            jp = JavaProcess(ctx, proc, design, plans[pid], decisions)
            printers.append(jp)
            mappings = jp.mappings()
            if mappings:
                self.out.add(self.path(f"core/{jp.name}Mappings.java"), mappings[0], mappings[1], process_id=pid)
            conditions = jp.conditions()
            if conditions:
                self.out.add(self.path(f"core/{jp.name}Conditions.java"), conditions[0], conditions[1], process_id=pid)
            service, origin = jp.service()
            self.out.add(self.path(f"core/{jp.service_class}.java"), service, origin, process_id=pid)
            for act_id, (cls, fault) in sorted(jp.exceptions.items()):
                self.out.add(self.path(f"core/exceptions/{cls}.java"), self.exception(cls, fault, proc, act_id),
                             "code", act_id, process_id=pid)
            if any(a.type.startswith("jdbc.") for a in proc.activities):
                text, origin = self.repository(jp)
                self.out.add(self.path(f"infrastructure/{jp.name}Repository.java"), text, origin, process_id=pid)
            self.starter_file(jp, plans[pid])
            for m in design.messaging:
                if m.kind == "topic":
                    self.topics.add(m.name)
        return printers

    def subprocess_stub(self, cls: str, target: str) -> str:
        return "\n".join([
            f"package {self.pkg}.core;", "",
            f"import {self.pkg}.core.tibco.ProcessResult;", f"import {self.pkg}.core.tibco.ProcessState;",
            "import org.springframework.stereotype.Service;", "",
            f"/** The TIBCO sub-process {doc(target)}: not part of this build, so a call to it fails visibly. */",
            "@Service", f"public class {cls} {{",
            "    public ProcessResult execute(ProcessState state) {",
            f"        throw new IllegalStateException({java_string(f'sub-process {target} is not migrated in this build')});",
            "    }", "}"]) + "\n"

    def exception(self, cls: str, fault: str, proc: Process, act_id: str) -> str:
        act = next(a for a in proc.activities if a.id == act_id)
        return "\n".join([
            f"package {self.pkg}.core.exceptions;", "",
            "import com.fasterxml.jackson.databind.JsonNode;", "",
            f"/** TIBCO fault {doc(fault)}, thrown by {doc(act.name)} ({doc(proc.name)}); its data is the fault element. */",
            f"public class {cls} extends ProcessFault {{",
            "    private static final long serialVersionUID = 1L;", "",
            f"    public static final String FAULT_NAME = {java_string(fault)};", "",
            f"    public {cls}(JsonNode faultData) {{",
            "        super(FAULT_NAME, faultData);", "    }", "}"]) + "\n"

    # -------------------------------------------------------------- repository
    def repository(self, jp: JavaProcess) -> tuple[str, Origin]:
        origin: Origin = "code"
        members: list[list[str]] = []
        for a in jp.p.activities:
            if not a.type.startswith("jdbc."):
                continue
            row = jp.primary.get(a.id)
            method = row.method if row and row.pattern.startswith("jdbc.") else self.naming.method_name("", a.name)
            name, prop = self.connection(a)
            sql = str(a.config.get("sql") or "")
            const = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", method.rstrip("_")).upper() + "_SQL"
            params = self.sql_params(a)
            timeout = str(a.config.get("timeout") or "").strip()
            max_rows = str(a.config.get("max_rows") or "").strip()
            t = int(timeout) if timeout.isdigit() else 0
            n = int(max_rows) if a.type == "jdbc.query" and max_rows.isdigit() else 0
            binding = ", ".join(f"{i + 1} = {p} ({typ})" for i, (p, typ) in enumerate(params)) or "none"
            doc_lines = ["    /**", f"     * TIBCO step {doc(a.name)} ({a.type}) on {doc(name)}: the SQL statement is kept verbatim.",
                         "     *", f"     * <p>{doc(jp.remarks(a.id))}", "     *",
                         f"     * <p>Parameters, bound in order to the JDBC ? placeholders: {doc(binding)}."]
            if n:
                doc_lines += ["     *", (f"     * <p>TIBCO maxRows {n}: rows past {n} are dropped (JdbcTemplate maxRows); "
                                         "the SQL stays verbatim.")]
            doc_lines.append("     */")
            member = [f"    private static final String {const} = {text_block(convert_placeholders(sql, 'jdbc'), '            ')};",
                      "", *doc_lines, f"    public JsonNode {method}(JsonNode input) {{"]
            if a.type == "jdbc.call":
                origin = "agent"
                member += [f"        // TIBCO stored procedure call ({const}); output parameters are mapped by the Code Builder.",
                           placeholder(a.name, "        "), "    }"]
                members.append(member)
                continue
            args = "".join(f",\n                {self.sql_arg(p, typ)}" for p, typ in params)
            member.append(f"        JdbcTemplate jdbc = dataSources.jdbc({java_string(name)}, {java_string(prop)}, {t}, {n});")
            if a.type == "jdbc.query":
                member.append(f"        return TibValue.records(jdbc.queryForList({const}{args}));")
            else:
                member.append(f"        return TibValue.updated(jdbc.update({const}{args}));")
            member.append("    }")
            members.append(member)
        cls = f"{jp.name}Repository"
        lines = [f"package {self.pkg}.infrastructure;", "",
                 f"import {self.pkg}.core.tibco.TibValue;", "import com.fasterxml.jackson.databind.JsonNode;",
                 "import java.sql.Types;", "import org.springframework.jdbc.core.JdbcTemplate;",
                 "import org.springframework.jdbc.core.SqlParameterValue;", "import org.springframework.stereotype.Repository;", "",
                 (f"/** Database steps of {doc(jp.p.name)} on Spring JDBC: every statement is the TIBCO SQL verbatim, "
                  "its ? placeholders bound in order. */"),
                 "@Repository", f"public class {cls} {{", "    private final DataSources dataSources;", "",
                 f"    public {cls}(DataSources dataSources) {{", "        this.dataSources = dataSources;", "    }"]
        for m in members:
            lines += [""] + m
        lines.append("}")
        return "\n".join(lines) + "\n", origin

    def sql_params(self, act: Activity) -> list[tuple[str, str]]:
        """(parameter name, type) per ``?`` placeholder, in order."""
        declared = act.config.get("parameters") or []
        out: list[tuple[str, str]] = []
        for i in range(dotnet.placeholder_count(str(act.config.get("sql") or ""))):
            d = declared[i] if i < len(declared) and isinstance(declared[i], dict) else {}
            out.append((str(d.get("name") or f"param{i + 1}"), _SQL_TYPE.get(str(d.get("type") or "").upper(), "string")))
        return out

    @staticmethod
    def sql_arg(name: str, typ: str) -> str:
        value = f"TibValue.jdbc(TibValue.path(input, {java_string(name)}), {java_string(typ)})"
        sql_type = _SQL_TYPES.get(typ)
        return f"new SqlParameterValue(Types.{sql_type}, {value})" if sql_type else value

    # -------------------------------------------------------------- starters
    def starter_file(self, jp: JavaProcess, plan: ServicePlan) -> None:
        p = jp.p
        if p.starter is None or jp.starter_act is None:
            return
        row = jp.primary.get(jp.starter_act.id)
        sid = jp.starter_act.id
        if jp.kind in ("rest", "http"):
            method = row.method if row else self.naming.method_name("", jp.starter_act.name)
            self.out.add(self.path(f"api/{jp.name}Controller.java"), self.controller(jp, plan, method), "code", sid,
                         process_id=p.id)
        elif jp.kind == "soap":
            method = row.method if row else self.naming.method_name("", jp.starter_act.name)
            self.out.add(self.path(f"api/{jp.name}SoapService.java"), self.soap_stub(jp, plan, method), "agent", sid,
                         process_id=p.id)
        elif jp.kind == "jms":
            text, origin = self.consumer(jp, plan)
            self.out.add(self.path(f"infrastructure/messaging/{jp.name}Consumer.java"), text, origin, sid, process_id=p.id)
        elif jp.kind == "timer":
            self.out.add(self.path(f"infrastructure/jobs/{jp.name}Job.java"), self.job(jp, plan), "code", sid,
                         process_id=p.id)

    def rest_parameters(self, jp: JavaProcess, route: str) -> list[tuple[str, str, str, bool]]:
        """(wire name, Java type, location, required) of a REST starter's parameters (as .NET reads them)."""
        if jp.kind != "rest":
            return []
        in_path = re.findall(r"\{([^}/]+)\}", route)
        card = self.cards.get(jp.p.id) or {}
        req = card.get("request_contract") if isinstance(card, dict) else None
        children = req.get("children", []) if isinstance(req, dict) and req.get("name") == "parameters" else []
        out: list[tuple[str, str, str, bool]] = []
        for ch in children:
            wire = str(ch.get("name") or "")
            if not wire:
                continue
            loc = str(ch.get("location") or ("path" if wire in in_path else "query"))
            out.append((wire, _REST_TYPE.get(str(ch.get("type") or "").lower(), "String"), loc,
                        bool(ch.get("required")) or loc == "path"))
        for name in sorted(set(in_path) - {w for w, *_ in out}):
            out.append((name, "String", "path", True))
        return out

    def controller(self, jp: JavaProcess, plan: ServicePlan, method: str) -> str:
        st = plan.starter
        route = st.route or f"/{jp.p.name}"
        verb = (st.verb or "POST").upper()
        sn = jp.starter_name() or "Start"
        params = self.rest_parameters(jp, route)
        args: list[str] = []
        puts: list[str] = []
        used: set[str] = {"parameters", "starter", "state", "result", "body", "request", "service"}  # the locals
        for wire, jtype, loc, required in params:
            name = self.naming.method_name("", wire)
            base, n = name, 2
            while name in used:
                name, n = f"{base}{n}", n + 1
            used.add(name)
            if loc == "path":
                args.append(f"@PathVariable({java_string(wire)}) {jtype} {name}")
            elif loc == "header":
                args.append(f"@RequestHeader(name = {java_string(wire)}, required = {str(required).lower()}) {jtype} {name}")
            else:
                args.append(f"@RequestParam(name = {java_string(wire)}, required = {str(required).lower()}) {jtype} {name}")
            puts.append(f"        parameters.put({java_string(wire)}, {name});")
        has_body = verb not in ("GET", "DELETE", "HEAD")
        http = jp.kind == "http"
        if has_body:
            args.append(f"@RequestBody(required = false) {'String' if http else 'JsonNode'} body")
        if http:
            args.append("HttpServletRequest request")
        annotation = f"@{_MAPPINGS[verb]}({java_string(route)})" if verb in _MAPPINGS else \
            f"@RequestMapping(path = {java_string(route)}, method = RequestMethod.{verb})"
        body: list[str] = []
        if http:
            body += ["        ObjectNode starter = TibValue.object();",
                     f"        starter.put(\"Method\", {java_string(verb)});",
                     "        starter.put(\"QueryString\", request.getQueryString());"]
            if has_body:
                body.append("        starter.put(\"PostData\", body);")
            body.append(f"        ProcessState state = ProcessState.of({java_string(sn)}, starter);")
        else:
            body += ["        ObjectNode parameters = TibValue.object();", *puts,
                     "        ObjectNode starter = TibValue.object();",
                     "        starter.set(\"parameters\", parameters);"]
            if has_body:
                body.append("        starter.set(\"item\", TibValue.of(body));")
            item = f"\n                .set({java_string(sn + '.item')}, body)" if has_body else ""
            body.append(f"        ProcessState state = ProcessState.of({java_string(sn)}, starter)\n"
                        f"                .set({java_string(sn + '.parameters')}, parameters){item};")
        body += ["        ProcessResult result = service.execute(state);",
                 "        return ResponseEntity.status(result.statusCode()).body(result.body());"]
        cls = f"{jp.name}Controller"
        svc = jp.service_class
        lines = [f"package {self.pkg}.api;", "",
                 f"import {self.pkg}.core.{svc};", f"import {self.pkg}.core.tibco.ProcessResult;",
                 f"import {self.pkg}.core.tibco.ProcessState;", f"import {self.pkg}.core.tibco.TibValue;",
                 "import com.fasterxml.jackson.databind.JsonNode;", "import com.fasterxml.jackson.databind.node.ObjectNode;",
                 "import jakarta.servlet.http.HttpServletRequest;", "import java.math.BigDecimal;",
                 "import org.springframework.http.ResponseEntity;",
                 *[f"import org.springframework.web.bind.annotation.{a};" for a in
                   ("DeleteMapping", "GetMapping", "PatchMapping", "PathVariable", "PostMapping", "PutMapping",
                    "RequestBody", "RequestHeader", "RequestMapping", "RequestMethod", "RequestParam", "RestController")],
                 "",
                 f"/** {doc(jp.kind.upper())} starter of {doc(jp.p.name)}: {doc(verb)} {doc(route)}. */",
                 "@RestController", f"public class {cls} {{", f"    private final {svc} service;", "",
                 f"    public {cls}({svc} service) {{", "        this.service = service;", "    }", "",
                 f"    /** {doc(verb)} {doc(route)} (TIBCO operation {doc(jp.starter_act.name)}). {doc(jp.remarks(jp.starter_act.id))} */",
                 f"    {annotation}",
                 f"    public ResponseEntity<JsonNode> {method}({', '.join(args)}) {{", *body, "    }", "}"]
        return "\n".join(lines) + "\n"

    def soap_stub(self, jp: JavaProcess, plan: ServicePlan, method: str) -> str:
        cls = f"{jp.name}SoapService"
        op = plan.starter.route or jp.p.name
        return "\n".join([
            f"package {self.pkg}.api;", "",
            f"import {self.pkg}.core.{jp.service_class};", "import com.fasterxml.jackson.databind.JsonNode;",
            "import org.springframework.stereotype.Component;", "",
            (f"/** SOAP starter of {doc(jp.p.name)} (operation {doc(op)}): the endpoint is drafted by the Code "
             "Builder; it calls the process service. */"),
            "@Component", f"public class {cls} {{", f"    private final {jp.service_class} service;", "",
            f"    public {cls}({jp.service_class} service) {{", "        this.service = service;", "    }", "",
            f"    /** TIBCO operation {doc(op)}. {doc(jp.remarks(jp.starter_act.id))} */",
            f"    public JsonNode {method}(JsonNode request) {{",
            f"        // TIBCO: SOAP operation {comment(op)} of {comment(jp.starter_act.name)}; the endpoint calls service.execute.",
            placeholder(jp.starter_act.name, "        "), "    }", "}"]) + "\n"

    def consumer(self, jp: JavaProcess, plan: ServicePlan) -> tuple[str, Origin]:
        st = plan.starter
        dest = st.destination or ""
        selector = st.selector or ""
        sn = jp.starter_name() or "Start"
        root = jp.message_root()
        origin: Origin = "code"
        atoms = [_SELECTOR_ATOM.match(a) for a in re.split(r"(?i)\s+and\s+", selector)] if selector else []
        if not selector:
            match = ["        return true;"]
        elif all(atoms):
            checks = " && ".join(f"{java_string(m.group(2))}.equals(properties.get({java_string(m.group(1))}))"
                                 for m in atoms if m)
            match = [f"        return {checks};"]
        else:
            origin = "agent"
            match = [f"        // TIBCO selector: {comment(selector)}", placeholder(f"{jp.starter_act.name} selector", "        ")]
        wrap = (f"        input.putObject(\"Body\").set({java_string(root)}, body);" if root
                else "        input.set(\"Body\", body);")
        cls = f"{jp.name}Consumer"
        svc = jp.service_class
        lines = [
            f"package {self.pkg}.infrastructure.messaging;", "",
            f"import {self.pkg}.core.{svc};", f"import {self.pkg}.core.contracts.Masking;",
            f"import {self.pkg}.core.exceptions.ProcessFault;", f"import {self.pkg}.core.messaging.MessageBus;",
            f"import {self.pkg}.core.tibco.ProcessState;", f"import {self.pkg}.core.tibco.TibValue;",
            "import com.fasterxml.jackson.databind.JsonNode;", "import com.fasterxml.jackson.databind.node.ObjectNode;",
            "import jakarta.annotation.PreDestroy;", "import java.util.Map;", "import org.slf4j.Logger;",
            "import org.slf4j.LoggerFactory;", "import org.springframework.boot.context.event.ApplicationReadyEvent;",
            "import org.springframework.context.event.EventListener;", "import org.springframework.stereotype.Component;", "",
            "/**", f" * JMS starter of {doc(jp.p.name)}: each message on {doc(dest)} runs the process once.",
            " *",
            " * <p>A message the TIBCO selector would not deliver is dead-lettered (SelectorMismatch), so is a body",
            " * that is not JSON (InvalidBody) and a run that ends in a TIBCO fault (the fault name); any other",
            " * exception abandons the message so the bus redelivers it.", " */",
            "@Component", f"public class {cls} {{",
            f"    private static final Logger LOG = LoggerFactory.getLogger({cls}.class);", "",
            "    /** The TIBCO destination, verbatim. */", f"    public static final String DESTINATION = {java_string(dest)};", "",
            "    /** The TIBCO message selector, verbatim (empty: every message). */",
            f"    public static final String SELECTOR = {java_string(selector)};", "",
            "    private final MessageBus bus;", f"    private final {svc} service;", "    private AutoCloseable subscription;", "",
            f"    public {cls}(MessageBus bus, {svc} service) {{", "        this.bus = bus;", "        this.service = service;",
            "    }", "",
            "    @EventListener(ApplicationReadyEvent.class)", "    public void start() {",
            "        subscription = bus.subscribe(DESTINATION, this::handle);", "    }", "",
            "    @PreDestroy", "    public void stop() throws Exception {", "        if (subscription != null) {",
            "            subscription.close();", "        }", "    }", "",
            f"    /** Settles one message. {doc(jp.remarks(jp.starter_act.id))} */",
            "    public MessageBus.Outcome handle(MessageBus.Message message) {",
            "        if (!selectorMatches(message.properties())) {",
            "            return MessageBus.Outcome.deadLetter(\"SelectorMismatch\", \"the message does not match \" + SELECTOR);",
            "        }", "        JsonNode body;", "        try {", "            body = TibValue.parse(message.body());",
            "        } catch (IllegalArgumentException ex) {",
            "            return MessageBus.Outcome.deadLetter(\"InvalidBody\", Masking.maskText(ex.getMessage()));", "        }",
            "        ObjectNode input = TibValue.object();", wrap,
            "        input.set(\"JMSProperties\", TibValue.of(message.properties()));", "        try {",
            f"            service.execute(ProcessState.of({java_string(sn)}, input).message(message));",
            "            return MessageBus.Outcome.COMPLETED;", "        } catch (ProcessFault fault) {",
            f"            LOG.warn(\"{{}} ended in fault {{}}\", {java_string(jp.p.name)}, fault.faultName());",
            "            return MessageBus.Outcome.deadLetter(fault.faultName(), Masking.maskText(fault.getMessage()));",
            "        }", "    }", "",
            "    /** Whether the TIBCO selector would deliver a message with these properties. */",
            "    static boolean selectorMatches(Map<String, String> properties) {", *match, "    }", "}"]
        return "\n".join(lines) + "\n", origin

    def job(self, jp: JavaProcess, plan: ServicePlan) -> str:
        interval = plan.starter.interval_ms or 86_400_000
        key = kebab(jp.name)
        self.timers[jp.name] = interval
        cls = f"{jp.name}Job"
        sn = jp.starter_name() or "Timer"
        return "\n".join([
            f"package {self.pkg}.infrastructure.jobs;", "",
            f"import {self.pkg}.core.{jp.service_class};", f"import {self.pkg}.core.tibco.ProcessState;",
            f"import {self.pkg}.core.tibco.TibValue;", "import com.fasterxml.jackson.databind.node.ObjectNode;",
            "import java.time.OffsetDateTime;", "import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;",
            "import org.springframework.scheduling.annotation.Scheduled;", "import org.springframework.stereotype.Component;", "",
            (f"/** Timer starter of {doc(jp.p.name)}: every jobs.{key}.interval-ms (Jobs__{jp.name}__IntervalMs), "
             "when jobs.enabled is true. */"),
            "@Component", "@ConditionalOnProperty(name = \"jobs.enabled\", havingValue = \"true\")",
            f"public class {cls} {{", f"    private final {jp.service_class} service;", "",
            f"    public {cls}({jp.service_class} service) {{", "        this.service = service;", "    }", "",
            f"    /** One TIBCO timer tick. {doc(jp.remarks(jp.starter_act.id))} */",
            f"    @Scheduled(fixedRateString = \"${{jobs.{key}.interval-ms:{interval}}}\")",
            "    public void execute() {", "        ObjectNode timer = TibValue.object();",
            "        timer.put(\"Time\", TibValue.dateTime(OffsetDateTime.now()));",
            f"        service.execute(ProcessState.of({java_string(sn)}, timer));", "    }", "}"]) + "\n"

    # -------------------------------------------------------------- agent classes
    def agent_classes(self, printers: list[JavaProcess]) -> None:
        """One class per file the steps call (HTTP clients, transforms, gateways), and a stub class
        for every design row whose file nothing else wrote, so each row names code that exists."""
        members: dict[str, list[tuple[AgentMember, str]]] = defaultdict(list)
        owners: dict[str, set[str]] = defaultdict(set)
        for jp in printers:
            for m in jp.agent_members:
                members[m.file].append((m, jp.p.id))
                owners[m.file].add(jp.p.id)
        for jp in printers:
            for r in jp.design.mapping:
                file = f"{row_layer(r)}/{r.file}"
                if self.path(file) in self.out.files or r.pattern == "input.mapping":
                    continue
                act = jp.acts.get(r.activity_id)
                if act is None:
                    continue
                if not any(m.method == r.method for m, _ in members[file]):
                    members[file].append((AgentMember(file, r.class_name, r.method, act, jp.remarks(act.id), r.pattern),
                                          jp.p.id))
                    owners[file].add(jp.p.id)
        for file in sorted(members):
            if self.path(file) in self.out.files:
                continue
            items = members[file]
            cls = items[0][0].class_name
            client = items[0][0].kind in ("http.invoke", "rest.invoke", "soap.invoke")
            owner = next(iter(owners[file])) if len(owners[file]) == 1 else None
            self.out.add(self.path(file), self.agent_class(file, cls, items, client), "agent", process_id=owner)

    def agent_class(self, file: str, cls: str, items: list[tuple[AgentMember, str]], client: bool) -> str:
        package = f"{self.pkg}.{str(PurePosixPath(file).parent).replace('/', '.')}"
        seen: dict[str, int] = {}
        methods: list[str] = []
        for m, _ in items:
            if m.method in seen:
                continue
            seen[m.method] = 1
            methods += ["", f"    /** TIBCO step {doc(m.activity.name)} ({m.activity.type}). {doc(m.remarks)} */",
                        f"    public JsonNode {m.method}(JsonNode input) {{",
                        f"        // TIBCO: {comment(m.activity.name)} ({m.activity.type}), native type {comment(m.activity.native_type)}"]
            for b in m.activity.input_bindings:
                methods.append(tibco(b.target, b.expression, "        "))
            methods += [placeholder(m.activity.name, "        "), "    }"]
        lines = [f"package {package};", "", "import com.fasterxml.jackson.databind.JsonNode;"]
        if client:
            key = f"clients.{kebab(cls)}.base-address"
            self.clients.setdefault(cls, "")
            lines += ["import org.springframework.beans.factory.annotation.Value;", "import org.springframework.stereotype.Component;",
                      "", (f"/** TIBCO client {doc(cls)}: one method per step that calls it; the Code Builder writes the "
                           f"calls against the base address {key} (Clients__{cls}__BaseAddress). */"),
                      "@Component", f"public class {cls} {{", "    private final String baseAddress;", "",
                      f"    public {cls}(@Value(\"${{{key}:}}\") String baseAddress) {{",
                      "        this.baseAddress = baseAddress;", "    }", "",
                      "    /** The base address the calls go to. */", "    public String baseAddress() {",
                      "        return baseAddress;", "    }"]
        else:
            lines += ["import org.springframework.stereotype.Component;", "",
                      f"/** TIBCO steps the Code Builder writes in {doc(cls)}: one method per step. */",
                      "@Component", f"public class {cls} {{"]
        lines += methods + ["}"]
        return "\n".join(lines) + "\n"

    # -------------------------------------------------------------- shared files
    def infrastructure_files(self) -> None:
        if self.connections:
            self.template("infrastructure/DataSources.java", "DataSources.java.j2")
        self.template("infrastructure/messaging/InMemoryMessageBus.java", "InMemoryMessageBus.java.j2")
        self.template("infrastructure/messaging/ServiceBusMessageBus.java", "ServiceBusMessageBus.java.j2",
                      topics=sorted(self.topics))

    def api_files(self) -> None:
        for name in ("SecurityConfig", "DevTokenController", "FaultHandler", "BusDevController"):
            self.template(f"api/{name}.java", f"{name}.java.j2")

    # -------------------------------------------------------------- configuration
    def config_files(self, printers: list[JavaProcess]) -> None:
        main, dev, secrets, plain, not_carried = self.settings(printers)
        yml = ["# Generated by ExiQO Migrate from TIBCO BusinessWorks application " + self.app.id + ".",
               "# Keys follow the runtime contract: an environment name such as Messaging__Mode,",
               "# ConnectionStrings__<Resource> or Gmf__<Module>__<Name> binds to the key below in its",
               "# canonical form (config/EnvBinding.java); secrets come from Key Vault (KeyVault__Uri).",
               "# The development document applies with ASPNETCORE_ENVIRONMENT=Development or the",
               "# development profile."]
        if not_carried:
            yml.append("# TIBCO substitution variables not carried over (nothing in the generated code reads them):")
            yml += [f"#   {comment(name)}: {comment(reason)}" for name, reason in not_carried]
        yml += _yaml(main)
        yml += ["---", *_yaml({"spring": {"config": {"activate": {"on-profile": "development"}}}, **dev})]
        self.out.add("src/main/resources/application.yml", "\n".join(yml) + "\n")
        contract = {"connections": sorted(self.connections), "clients": sorted(self.clients),
                    "secrets": [{"name": n, "key": k} for n, k in secrets], "plain_keys": plain,
                    "port_env": None, "health": "/health"}
        self.out.add(CONTRACT_FILE, json.dumps(contract, indent=2, sort_keys=True) + "\n")

    def settings(self, printers: list[JavaProcess]) -> tuple[dict, dict, list[tuple[str, str]], list[str],
                                                              list[tuple[str, str]]]:
        """(main document, development document, Key Vault (name, env key) secrets, plain env keys,
        substitution variables not carried) of application.yml, as the .NET generator decides them."""
        seed = hashlib.sha256(f"exiqo-dev:{self.app.id}".encode()).hexdigest()
        main: dict[str, Any] = {
            "spring": {"application": {"name": self.paths["artifact"]}, "main": {"banner-mode": "off"}},
            "server": {"port": 8080},
            "management": {"endpoints": {"web": {"base-path": "/", "exposure": {"include": "health"}}},
                           "endpoint": {"health": {"show-details": "never"}}},
            "auth": {"mode": "Production", "entra-id": {"instance": "https://login.microsoftonline.com/",
                                                        "tenant-id": "<entra-tenant-id>",
                                                        "audience": "api://<app-registration-client-id>"}},
            "messaging": {"mode": "ServiceBus",
                          "service-bus": {"fully-qualified-namespace": "<service-bus-namespace>.servicebus.windows.net"}},
            "key-vault": {"uri": ""},
            "jobs": {"enabled": True},
        }
        dev: dict[str, Any] = {
            "auth": {"mode": "Development", "dev-token": {"issuer": "exiqo-dev", "audience": f"{self.app_name.lower()}-api",
                                                          "signing-key": f"development-only-{seed}"}},
            "cors": {"origins": ["http://localhost:3000"]},
            "messaging": {"mode": "InMemory"},
            "jobs": {"enabled": False},
        }
        secrets: dict[str, str] = {}
        plain: set[str] = set()
        not_carried: dict[str, str] = {}
        resources = {r.name: r for r in self.app.resources}
        used = {p for p in self.connections.values()}
        for jp in printers:
            module = jp.p.module or self.app.id
            for row in jp.design.config:
                if row.key.startswith("connection-strings."):
                    if row.key not in used:
                        continue
                    name = next(n for n, p in self.connections.items() if p == row.key)
                    res = resources.get(row.substvar[len("resource:"):].rpartition(".")[0])
                    settings = res.settings if res else {}
                    secrets[f"ConnectionStrings--{name}"] = f"ConnectionStrings__{name}"
                    _put(dev, row.key, _npgsql(str(settings.get("jdbc_url") or ""), str(settings.get("username") or "")))
                    continue
                if row.substvar.startswith("resource:") or not _references(jp.p, row.substvar):
                    not_carried.setdefault(row.substvar, self.not_carried(row.substvar))
                    continue
                env = f"Gmf__{ident(module, 'Module')}__{ident(row.substvar, 'Var')}"
                found = dotnet._substvar_value(self.app, jp.p.module, row.substvar)
                value, typ = found if found else ("", "String")
                local_value = dotnet._typed(value, typ) if value else "change-me-local"
                if row.secret:
                    secrets[env.replace("__", "--")] = env
                    _put(dev, row.key, local_value)
                else:
                    plain.add(env)
                    _put(main, row.key, local_value)
        for name, interval in sorted(self.timers.items()):
            _put(main, f"jobs.{kebab(name)}.interval-ms", interval)
            plain.add(f"Jobs__{name}__IntervalMs")
        for cls in sorted(self.clients):
            _put(main, f"clients.{kebab(cls)}.base-address", "http://localhost/")
            plain.add(f"Clients__{cls}__BaseAddress")
        return main, dev, sorted(secrets.items()), sorted(plain), sorted(not_carried.items())


    def not_carried(self, substvar: str) -> str:
        """Why a TIBCO substitution variable is not in application.yml (the .NET reasons, in Spring words)."""
        resources = list(self.app.resources)
        if substvar.startswith("resource:"):
            rname = substvar[len("resource:"):].rpartition(".")[0]
            res = next((r for r in resources if r.name == rname), None)
            if res is not None and res.kind == "jms":
                return "JMS connection setting: replaced by Azure Service Bus (messaging.*)"
            if res is not None and res.kind == "jdbc":
                return f"JDBC setting: replaced by {self.naming.connection_key(rname)}"
            return "resource setting not read by the generated code"
        for res in resources:
            bindings = res.settings.get("substitution_bindings") if isinstance(res.settings, dict) else None
            for field_name, var in (bindings or {}).items():
                if var != substvar:
                    continue
                if res.kind == "jms":
                    return "JMS connection setting: replaced by Azure Service Bus (messaging.*)"
                if res.kind == "jdbc":
                    key = self.naming.connection_key(res.name)
                    return f"JDBC setting: replaced by {key}" if key in self.connections.values() else \
                        "JDBC setting of a resource the selected processes do not use"
                if res.kind == "http" and res.settings.get("role") == "server" and field_name in ("port", "host"):
                    return "HTTP listener address: set by the host (server.port, container ingress)"
                if res.kind == "http":
                    return f"HTTP client setting: carried as clients.{kebab(ident(res.name, 'Resource'))}.base-address"
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


def _put(doc_: dict[str, Any], key: str, value: Any) -> None:
    parts = key.split(".")
    for part in parts[:-1]:
        nxt = doc_.setdefault(part, {})
        if not isinstance(nxt, dict):
            nxt = doc_[part] = {}
        doc_ = nxt
    doc_[parts[-1]] = value


def _yaml(value: dict[str, Any], indent: int = 0) -> list[str]:
    """Block YAML with every string JSON-quoted (YAML reads JSON strings), keys in insertion order."""
    pad = "  " * indent
    out: list[str] = []
    for k, v in value.items():
        if isinstance(v, dict):
            out.append(f"{pad}{k}:")
            out += _yaml(v, indent + 1)
        elif isinstance(v, list):
            out.append(f"{pad}{k}:")
            out += [f"{pad}  - {_scalar(i)}" for i in v]
        else:
            out.append(f"{pad}{k}: {_scalar(v)}")
    return out


def _scalar(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int | float):
        return str(v)
    return json.dumps(str(v), ensure_ascii=False)


__all__ = ["CONTRACT_FILE", "SETTINGS_FILE", "generate_java", "layout_paths", "prune_imports"]
