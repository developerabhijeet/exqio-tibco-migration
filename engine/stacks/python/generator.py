"""Python generator (target-stacks spec section 4): the IR and the design records to a FastAPI service
on Python 3.12, one distribution, package ``<app snake>`` in a ``src`` layout.

Deterministic: the same inputs give byte-identical files and the same ``build_hash``. Each process
is printed from its ``ServicePlan`` (``printer``): a service, its mappings and link conditions; a
repository with the TIBCO SQL verbatim and its placeholders as psycopg ``%s`` bound in order; routes
for a REST or HTTP starter, a bus consumer for a JMS starter, an asyncio job for a timer. What code
cannot write keeps the TIBCO expression in ``# TIBCO`` comments above
``raise NotImplementedError("agent fills: <activity>")`` and the file is ``origin="agent"``.

Layout (``<pkg>`` = ``gmf_credit`` for ``gmf.credit``)::

    pyproject.toml, requirements.lock (hash-pinned, copied verbatim), .gitignore, .editorconfig,
    .exiqo/service-contract.json, .exiqo/test-index.json
    src/<pkg>/main.py (create_app), config.py (Settings), container.py (the wiring)
      core/            services, mappings, conditions, contracts/, exceptions/, bus.py (the port),
                       tib.py and tib_text.py (JSON values, ProcessState, ProcessFlow), masking.py
      infrastructure/  repositories, database.py, bus_memory.py, bus_servicebus.py, messaging/
                       (consumers), jobs/, clients/
      api/             <process>_routes.py, auth.py, devtoken.py, bus_dev.py, fault.py, http.py

The runtime contract (spec section 5) is the .NET one: ``/health``, ``/auth/dev-token``, the
``/_bus`` endpoints, faults as problem+json, and ``__``-separated environment names read by
``config``; ``PORT`` is the port.
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
from engine.stacks.plan import ServicePlan, build_plan
from engine.stacks.python import versions
from engine.stacks.python.contracts import render as render_contract
from engine.stacks.python.naming import PythonNaming, package_name, snake, upper_snake
from engine.stacks.python.printer import (
    AgentMember,
    Ctx,
    Origin,
    PythonProcess,
    class_module,
    row_layer,
)
from engine.stacks.python.text import (
    comment,
    doc,
    imports,
    placeholder,
    py_string,
    selector_properties,
    sql_literal,
)
from engine.stacks.sql import convert_placeholders
from engine.stacks.testindex import write_index

log = logging.getLogger(__name__)
TEMPLATES = Path(__file__).resolve().parent / "templates"
LOCK = Path(__file__).resolve().parent / "requirements.lock"
CONTRACT_FILE = ".exiqo/service-contract.json"
_JINJA = jinja2.Environment(
    loader=jinja2.FileSystemLoader(str(TEMPLATES)), keep_trailing_newline=True, trim_blocks=True,
    lstrip_blocks=True, undefined=jinja2.StrictUndefined, autoescape=False)
_JINJA.filters["py_string"] = py_string
_SQL_TYPE = {"VARCHAR": "string", "CHAR": "string", "LONGVARCHAR": "string", "NVARCHAR": "string",
             "NCHAR": "string", "CLOB": "string", "NUMERIC": "decimal", "DECIMAL": "decimal", "INTEGER": "int",
             "SMALLINT": "int", "TINYINT": "int", "BIGINT": "long", "DOUBLE": "double", "FLOAT": "double",
             "REAL": "double", "BIT": "bool", "BOOLEAN": "bool", "DATE": "date", "TIMESTAMP": "datetime",
             "TIME": "time"}
_REST_TYPE = {"integer": "integer", "int": "integer", "long": "long", "number": "number", "decimal": "number",
              "boolean": "boolean", "bool": "boolean"}


def render(template: str, **ctx: Any) -> str:
    return _JINJA.get_template(template).render(**ctx)


def layout_paths(app_name: str) -> dict[str, str]:
    """Paths of a generated project: the layout contract with infrastructure and Verify."""
    naming = PythonNaming()
    pkg = package_name(app_name)
    return {"package": pkg, "dist": pkg.replace("_", "-"), "src": f"src/{pkg}", "app": f"{pkg}.main:create_app",
            **{k: naming.layer_folder(k, app_name) for k in ("api", "core", "infrastructure", "tests")}}


# ------------------------------------------------------------------ inputs
def _designs(app: Application, process_ids: list[str], design: Any, rules: dict[str, list[Rule]],
             findings: dict[str, list[Finding]]) -> tuple[dict[str, DesignRecord], dict[str, Any]]:
    """The Python design of every process (``build_design_skeleton(..., stack="python")`` for a
    missing one) and its I/O card; a given record for another stack is refused, its names are not
    Python's."""
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
                                                       card, stack="python")
        if rec.stack != "python":
            raise ValueError(f"the design of {pid} is for {rec.stack}, not python: re-run Design")
        out[pid] = rec
    return out, cards


class _Files:
    def __init__(self) -> None:
        self.files: dict[str, GenFile] = {}

    def add(self, path: str, content: str, origin: Origin = "code", activity_id: str | None = None,
            process_id: str | None = None) -> None:
        if any(path == r or path.startswith(r) for r in RESERVED_FOR_INFRA):
            raise ValueError(f"{path} is reserved for the infrastructure generator")
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
def generate_python(app: Application, process_ids: list[str], designs: Any, rules: Any, findings: Any,
                    scenarios: list[Scenario], out_dir: Path, mirrors: Mirrors) -> GeneratedSolution:
    """Generate the Python project for ``process_ids`` of ``app`` into ``out_dir``.

    ``designs``, ``rules`` and ``findings`` are as for ``generate_solution`` (keyed by process id or
    flat lists); a missing design is built for the Python stack. ``scenarios`` feed the tests.
    ``mirrors.pypi`` is not written into the project: the index is the build's ``UV_INDEX_URL``
    (the runner and the Dockerfile pass it), and ``requirements.lock`` names no index.
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
    gen = _PythonSolution(app, process_ids, records, cards, findings_map, rules_map, list(scenarios or []))
    files = gen.generate()
    ordered = [files[k] for k in sorted(files)]
    digest = hashlib.sha256()
    for f in ordered:
        digest.update(f.path.encode("utf-8") + b"\0" + f.content.encode("utf-8") + b"\0")
    solution = GeneratedSolution(root=Path(out_dir).resolve(), name=gen.app_name, files=ordered,
                                 build_hash=digest.hexdigest(), designs=dict(records))
    write_solution(solution)
    return solution


class _PythonSolution:
    def __init__(self, app: Application, process_ids: list[str], designs: dict[str, DesignRecord],
                 cards: dict[str, Any], findings: dict[str, list[Finding]],
                 rules: dict[str, list[Rule]] | None = None, scenarios: list[Scenario] | None = None) -> None:
        self.app = app
        self.rules = rules or {}
        self.scenarios = scenarios or []
        self.pids = process_ids
        self.designs = designs
        self.cards = cards
        self.findings = findings
        self.naming = PythonNaming()
        self.app_name = self.naming.type_name(app.id, "App")
        self.paths = layout_paths(self.app_name)
        self.pkg = self.paths["package"]
        self.procs = {p.id: p for p in app.processes}
        self.resources = {r.id: r for r in app.resources}
        self.out = _Files()
        self.connections: dict[str, str] = {}  # contract name (ConnectionStrings__<name>) -> setting key
        self.clients: dict[str, str] = {}  # client class -> its module
        self.topics: set[str] = set()
        self.timers: dict[str, int] = {}  # process type name -> interval ms
        self.timer_starts: dict[str, str] = {}  # process type name -> TIBCO start time of day
        # global element -> (module, class) of the contract model it reads as; None when two
        # contracts declare it, so no model is guessed
        self.roots: dict[str, tuple[str, str] | None] = {}
        # container attribute -> (module, class, constructor expression)
        self.components: dict[str, tuple[str, str, str]] = {}
        self.route_attrs: list[str] = []
        self.consumer_attrs: list[str] = []
        self.job_attrs: list[str] = []

    # -------------------------------------------------------------- helpers
    def path(self, layer_file: str) -> str:
        """A file under the package root (``core/x.py``) as a project path."""
        return f"{self.paths['src']}/{layer_file}"

    def ctx(self, **extra: Any) -> dict[str, Any]:
        return {"pkg": self.pkg, "app": self.app_name, "app_id": self.app.id, **extra}

    def template(self, layer_file: str, template: str, **extra: Any) -> None:
        self.out.add(self.path(layer_file), render(template, **self.ctx(**extra)))

    def component(self, layer_file: str, cls: str, expr: str) -> str:
        """Registers ``cls`` (in ``layer_file``) with the container; returns its attribute."""
        attr = snake(cls)
        self.components.setdefault(attr, (class_module(self.pkg, layer_file), cls, expr))
        return attr

    def connection(self, act: Activity) -> tuple[str, str]:
        """(contract name, setting key) of a JDBC step's resource: the name is the .NET one
        (``ConnectionStrings__<name>``), the key its canonical form."""
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
        self.infrastructure_files()
        self.api_files()
        self.config_files(printers)
        self.container_file()
        self.test_files(printers)
        self.package_files()
        return self.out.files

    def project_files(self) -> None:
        add = self.out.add
        minor = int(versions.PYTHON.split(".")[1])
        add("pyproject.toml", render("pyproject.toml.j2", **self.ctx(dist=self.paths["dist"], v=versions,
                                                                   next_python=f"3.{minor + 1}")))
        add("requirements.lock", LOCK.read_text(encoding="utf-8"))
        add(".gitignore", render("gitignore.j2"))
        add(".editorconfig", render("editorconfig.j2"))
        self.template("main.py", "main.py.j2")

    def runtime_files(self) -> None:
        self.template("core/tib.py", "tib.py.j2")
        self.template("core/tib_text.py", "tib_text.py.j2")
        self.template("core/masking.py", "masking.py.j2", names=sorted(SENSITIVE_NAMES))
        self.template("core/bus.py", "bus.py.j2")
        self.template("core/exceptions/__init__.py", "faults.py.j2")

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
            file = f"core/{plan.file}"
            self.out.add(self.path(file), render_contract(model, cid, self.pkg))
            module = class_module(self.pkg, file)
            for record, path in model.root_paths:
                element = path.strip("/").split("/")[0]
                target = (module, record)
                self.roots[element] = target if self.roots.get(element, target) == target else None

    # -------------------------------------------------------------- processes
    def process_files(self) -> list[PythonProcess]:
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
                    file = f"core/{snake(cls)}.py"
                    self.out.add(self.path(file), self.subprocess_stub(cls, target))
                    self.component(file, cls, f"{cls}()")
        printers: list[PythonProcess] = []
        for pid in self.pids:
            proc, design = self.procs[pid], self.designs[pid]
            decisions = {f.id: f.decision for f in self.findings.get(pid, [])}
            pp = PythonProcess(ctx, proc, design, plans[pid], decisions)
            printers.append(pp)
            mappings = pp.mappings()
            if mappings:
                self.out.add(self.path(f"core/{pp.module}_mappings.py"), mappings[0], mappings[1], process_id=pid)
            conditions = pp.conditions()
            if conditions:
                self.out.add(self.path(f"core/{pp.module}_conditions.py"), conditions[0], conditions[1],
                             process_id=pid)
            service, origin = pp.service()
            self.out.add(self.path(f"core/{pp.module}_service.py"), service, origin, process_id=pid)
            for act_id, (cls, fault) in sorted(pp.exceptions.items()):
                self.out.add(self.path(f"core/exceptions/{snake(cls)}.py"), self.exception(cls, fault, proc, act_id),
                             "code", act_id, process_id=pid)
            if any(a.type.startswith("jdbc.") for a in proc.activities):
                text, origin = self.repository(pp)
                file = f"infrastructure/{pp.module}_repository.py"
                self.out.add(self.path(file), text, origin, process_id=pid)
                self.component(file, f"{pp.name}Repository", f"{pp.name}Repository(self.database)")
            self.starter_file(pp, plans[pid])
            for m in design.messaging:
                if m.kind == "topic":
                    self.topics.add(m.name)
        for pp in printers:  # the services, once every component they take is known
            args = []
            for d in sorted(pp.deps.values(), key=lambda d: d.field):
                attr = "bus" if d.type == "MessageBus" else snake(d.type)
                args.append(f"{d.field}=lambda: self.{attr}" if d.lazy else f"{d.field}=self.{attr}")
            self.component(f"core/{pp.module}_service.py", pp.service_class, f"{pp.service_class}({', '.join(args)})")
        return printers

    def subprocess_stub(self, cls: str, target: str) -> str:
        pkg = self.pkg
        return "\n".join([
            f'"""The TIBCO sub-process {doc(target)}: not part of this build, so a call to it fails visibly."""', "",
            "from __future__ import annotations", "", f"from {pkg}.core.tib import ProcessResult, ProcessState", "", "",
            f"class {cls}:", f'    """Stands in for {doc(target)}."""', "",
            "    async def execute(self, state: ProcessState) -> ProcessResult:",
            f"        raise RuntimeError({py_string(f'sub-process {target} is not migrated in this build')})"]) + "\n"

    def exception(self, cls: str, fault: str, proc: Process, act_id: str) -> str:
        act = next(a for a in proc.activities if a.id == act_id)
        return "\n".join([
            f'"""TIBCO fault {doc(fault)}, thrown by {doc(act.name)} ({doc(proc.name)})."""', "",
            "from __future__ import annotations", "", "from typing import Any", "",
            f"from {self.pkg}.core.exceptions import ProcessFault", "", "",
            f"class {cls}(ProcessFault):", '    """Its data is the fault element."""', "",
            f"    FAULT_NAME = {py_string(fault)}", "",
            "    def __init__(self, fault_data: Any = None) -> None:",
            "        super().__init__(self.FAULT_NAME, fault_data)"]) + "\n"

    # -------------------------------------------------------------- repository
    def repository(self, pp: PythonProcess) -> tuple[str, Origin]:
        origin: Origin = "code"
        constants: list[str] = []
        members: list[list[str]] = []
        methods = pp.repository_methods()
        for a in pp.p.activities:
            if not a.type.startswith("jdbc."):
                continue
            method = methods[a.id]
            name, key = self.connection(a)
            sql = str(a.config.get("sql") or "")
            const = method.rstrip("_").upper() + "_SQL"
            params = self.sql_params(a)
            timeout = str(a.config.get("timeout") or "").strip()
            max_rows = str(a.config.get("max_rows") or "").strip()
            t = int(timeout) if timeout.isdigit() else 0
            n = int(max_rows) if a.type == "jdbc.query" and max_rows.isdigit() else 0
            binding = ", ".join(f"{i + 1} = {p} ({typ})" for i, (p, typ) in enumerate(params)) or "none"
            constants += ["", f"# TIBCO step {comment(a.name)} ({a.type}) on {comment(name)}: the SQL statement verbatim.",
                          f"{const} = {sql_literal(convert_placeholders(sql, 'pyformat'))}"]
            docs = [f"TIBCO step {doc(a.name)} ({a.type}) on {doc(name)}. {doc(pp.remarks(a.id))}",
                    f"Parameters, bound in order to the %s placeholders: {doc(binding)}."]
            if n:
                docs.append(f"TIBCO maxRows {n}: rows past {n} are dropped; the SQL stays verbatim.")
            member = [f"    async def {method}(self, values: dict[str, Any]) -> dict[str, Any]:",
                      f'        """{docs[0]}', "", *[f"        {d}" for d in docs[1:]], '        """']
            if a.type == "jdbc.call":
                origin = "agent"
                member += [(f"        # TIBCO stored procedure call ({const}); output parameters are mapped by the "
                           "Code Builder."), placeholder(a.name, "        ")]
                members.append(member)
                continue
            args = "".join(f"\n            {self.sql_arg(p, typ)}," for p, typ in params)
            bound = f"[{args}\n        ]" if params else "[]"
            target = f"{py_string(name)}, {py_string(key)}, {const}, {bound}"
            if a.type == "jdbc.query":
                member += [f"        rows = await self.database.query({target}, {t}, {n})", "        return tib.records(rows)"]
            else:
                member += [f"        count = await self.database.update({target}, {t})", "        return tib.updated(count)"]
            members.append(member)
        cls = f"{pp.name}Repository"
        lines = [(f'"""Database steps of {doc(pp.p.name)} on psycopg 3: every statement is the TIBCO SQL verbatim, '
                 'its placeholders bound in order."""'), "",
                 *imports({"from __future__ import annotations", "from typing import Any",
                           f"from {self.pkg}.core import tib", f"from {self.pkg}.infrastructure.database import Database"}),
                 *constants, "", "", f"class {cls}:", f'    """The JDBC activities of {doc(pp.p.name)}."""', "",
                 "    def __init__(self, database: Database) -> None:", "        self.database = database"]
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

    @staticmethod
    def sql_arg(name: str, typ: str) -> str:
        return f"tib.jdbc(tib.path(values, {py_string(name)}), {py_string(typ)})"

    # -------------------------------------------------------------- starters
    def starter_file(self, pp: PythonProcess, plan: ServicePlan) -> None:
        p = pp.p
        if p.starter is None or pp.starter_act is None:
            return
        row = pp.primary.get(pp.starter_act.id)
        sid = pp.starter_act.id
        service = snake(pp.service_class)
        if pp.kind in ("rest", "http"):
            method = row.method if row else self.naming.method_name("", pp.starter_act.name)
            file = f"api/{pp.module}_routes.py"
            self.out.add(self.path(file), self.routes(pp, plan, method), "code", sid, process_id=p.id)
            self.route_attrs.append(self.component(file, f"{pp.name}Routes", f"{pp.name}Routes(self.{service})"))
        elif pp.kind == "soap":
            method = row.method if row else self.naming.method_name("", pp.starter_act.name)
            file = f"api/{pp.module}_soap_service.py"
            self.out.add(self.path(file), self.soap_stub(pp, plan, method), "agent", sid, process_id=p.id)
            self.component(file, f"{pp.name}SoapService", f"{pp.name}SoapService(self.{service})")
        elif pp.kind == "jms":
            text, origin = self.consumer(pp, plan)
            file = f"infrastructure/messaging/{pp.module}_consumer.py"
            self.out.add(self.path(file), text, origin, sid, process_id=p.id)
            self.consumer_attrs.append(self.component(file, f"{pp.name}Consumer",
                                                      f"{pp.name}Consumer(self.bus, self.{service})"))
        elif pp.kind == "timer":
            file = f"infrastructure/jobs/{pp.module}_job.py"
            self.out.add(self.path(file), self.job(pp, plan), "code", sid, process_id=p.id)
            self.job_attrs.append(self.component(file, f"{pp.name}Job", f"{pp.name}Job(self.{service}, self.settings)"))

    def rest_parameters(self, pp: PythonProcess, route: str) -> list[tuple[str, str, str, bool]]:
        """(wire name, kind, location, required) of a REST starter's parameters (as .NET reads them)."""
        if pp.kind != "rest":
            return []
        in_path = re.findall(r"\{([^}/]+)\}", route)
        card = self.cards.get(pp.p.id) or {}
        req = card.get("request_contract") if isinstance(card, dict) else None
        children = req.get("children", []) if isinstance(req, dict) and req.get("name") == "parameters" else []
        out: list[tuple[str, str, str, bool]] = []
        for ch in children:
            wire = str(ch.get("name") or "")
            if not wire:
                continue
            loc = str(ch.get("location") or ("path" if wire in in_path else "query"))
            out.append((wire, _REST_TYPE.get(str(ch.get("type") or "").lower(), "string"), loc,
                        bool(ch.get("required")) or loc == "path"))
        for name in sorted(set(in_path) - {w for w, *_ in out}):
            out.append((name, "string", "path", True))
        return out

    def request_model(self, pp: PythonProcess) -> tuple[str, str] | None:
        """The contract model a REST starter's body reads as: the request element of its I/O card."""
        card = self.cards.get(pp.p.id) or {}
        req = card.get("request_contract") if isinstance(card, dict) else None
        name = req.get("name") if isinstance(req, dict) else None
        if not name or name == "parameters":
            return None
        return self.roots.get(str(name).split(":")[-1])

    def routes(self, pp: PythonProcess, plan: ServicePlan, method: str) -> str:
        st = plan.starter
        route = st.route or f"/{pp.p.name}"
        verb = (st.verb or "POST").upper()
        sn = pp.starter_name() or "Start"
        params = self.rest_parameters(pp, route)
        has_body = verb not in ("GET", "DELETE", "HEAD")
        http = pp.kind == "http"
        model = self.request_model(pp) if has_body and not http else None
        sources = {"path": "request.path_params", "header": "request.headers", "query": "request.query_params"}
        body: list[str] = ["        try:"]
        if http:
            body += [(f'            starter: dict[str, Any] = {{"Method": {py_string(verb)}, '
                     '"QueryString": request.url.query or None}')]
            if has_body:
                body.append('            starter["PostData"] = (await request.body()).decode("utf-8") or None')
            body.append(f"            state = ProcessState.of({py_string(sn)}, starter)")
        else:
            body.append("            parameters: dict[str, Any] = {}")
            for wire, kind, loc, required in params:
                body.append(f"            parameters[{py_string(wire)}] = parameter({sources.get(loc, sources['query'])}"
                            f".get({py_string(wire)}), {py_string(kind)}, {py_string(wire)}, {required})")
            if has_body:
                body.append("            item = await json_body(request)")
            if model:
                # X19: the request is checked against its contract model at the boundary; the process
                # itself runs on the JSON tree, as the TIBCO process did.
                body += [f"            invalid = tib.invalid(item, {model[1]})", "            if invalid is not None:",
                         "                raise BadRequest(masking.mask_text(invalid))"]
            starter = '{"parameters": parameters, "item": item}' if has_body else '{"parameters": parameters}'
            body += [f"            starter: dict[str, Any] = {starter}",
                     (f"            state = ProcessState.of({py_string(sn)}, starter).set({py_string(sn + '.parameters')}, "
                     "parameters)")]
            if has_body:
                body.append(f"            state.set({py_string(sn + '.item')}, item)")
        body += ["        except BadRequest as ex:", "            return bad_request(str(ex))",
                 "        return reply(await self.service.execute(state))"]
        cls = f"{pp.name}Routes"
        svc = pp.service_class
        extra = {"from __future__ import annotations", "from typing import Any", "from fastapi import FastAPI, Request",
                 "from fastapi.responses import Response", f"from {self.pkg}.core.{pp.module}_service import {svc}",
                 f"from {self.pkg}.core.tib import ProcessState",
                 f"from {self.pkg}.api.http import BadRequest, bad_request, json_body, parameter, reply"}
        if model:
            extra |= {f"from {model[0]} import {model[1]}", f"from {self.pkg}.core import masking, tib"}
        lines = [f'"""{doc(pp.kind.upper())} starter of {doc(pp.p.name)}: {doc(verb)} {doc(route)}."""', "",
                 *imports(extra), "", "",
                 f"class {cls}:", f'    """The route of {doc(pp.p.name)}; ``register`` adds it to the app."""', "",
                 f"    def __init__(self, service: {svc}) -> None:", "        self.service = service", "",
                 "    def register(self, app: FastAPI, dependencies: list[Any]) -> None:",
                 (f"        app.add_api_route({py_string(route)}, self.{method}, methods=[{py_string(verb)}], "
                 "dependencies=dependencies)"), "",
                 f"    async def {method}(self, request: Request) -> Response:",
                 (f'        """{doc(verb)} {doc(route)} (TIBCO operation {doc(pp.starter_act.name)}). '
                  f'{doc(pp.remarks(pp.starter_act.id))}"""'), *body]
        return "\n".join(lines) + "\n"

    def soap_stub(self, pp: PythonProcess, plan: ServicePlan, method: str) -> str:
        cls = f"{pp.name}SoapService"
        op = plan.starter.route or pp.p.name
        return "\n".join([
            (f'"""SOAP starter of {doc(pp.p.name)} (operation {doc(op)}): the endpoint is drafted by the Code '
             'Builder; it calls the process service."""'), "",
            *imports({"from __future__ import annotations", "from typing import Any",
                      f"from {self.pkg}.core.{pp.module}_service import {pp.service_class}"}), "", "",
            f"class {cls}:", f'    """TIBCO operation {doc(op)}."""', "",
            f"    def __init__(self, service: {pp.service_class}) -> None:", "        self.service = service", "",
            f"    async def {method}(self, request: Any) -> Any:",
            f'        """TIBCO operation {doc(op)}. {doc(pp.remarks(pp.starter_act.id))}"""',
            (f"        # TIBCO: SOAP operation {comment(op)} of {comment(pp.starter_act.name)}; the endpoint calls "
            "service.execute."),
            placeholder(pp.starter_act.name, "        ")]) + "\n"

    def consumer(self, pp: PythonProcess, plan: ServicePlan) -> tuple[str, Origin]:
        st = plan.starter
        dest = st.destination or ""
        # A topic starter receives on its own subscription, named after the process (as in .NET
        # and the design's messaging entities): <topic>/<process>.
        style = str((pp.p.starter.config if pp.p.starter else {}).get("messaging_style") or "").lower()
        entity = f"{dest}/{pp.p.name}" if style == "topic" else dest
        selector = st.selector or ""
        sn = pp.starter_name() or "Start"
        root = pp.message_root()
        origin: Origin = "code"
        pairs = selector_properties(selector)
        if not selector:
            match = ["        return True"]
        elif pairs is not None:
            checks = " and ".join(f"properties.get({py_string(k)}) == {py_string(v)}" for k, v in pairs)
            match = [f"        return {checks}"]
        else:
            origin = "agent"
            match = [f"        # TIBCO selector: {comment(selector)}", placeholder(f"{pp.starter_act.name} selector", "        ")]
        wrapped = f"{{{py_string(root)}: body}}" if root else "body"
        cls = f"{pp.name}Consumer"
        svc = pp.service_class
        lines = [
            (f'"""JMS starter of {doc(pp.p.name)}: each message on {doc(dest)} runs the process once. A message the '
             "TIBCO selector would not deliver is dead-lettered (SelectorMismatch), so is a body that is not JSON "
             "(InvalidBody) and a run that ends in a TIBCO fault (the fault name); any other exception abandons "
             'the message so the bus redelivers it."""'), "",
            *imports({"from __future__ import annotations", "import logging", "from collections.abc import Callable",
                      f"from {self.pkg}.core import masking, tib",
                      f"from {self.pkg}.core.bus import Message, MessageBus, Outcome, application_properties, jms_headers",
                      f"from {self.pkg}.core.exceptions import ProcessFault",
                      f"from {self.pkg}.core.{pp.module}_service import {svc}",
                      f"from {self.pkg}.core.tib import ProcessState"}),
            "", "LOG = logging.getLogger(__name__)", "", "",
            f"class {cls}:", '    """Receives on ENTITY and settles each message."""', "",
            "    DESTINATION = " + py_string(dest) + "  # the TIBCO destination, verbatim",
            "    ENTITY = " + py_string(entity) + "  # the queue, or the topic subscription <topic>/<process>",
            "    SELECTOR = " + py_string(selector) + "  # the TIBCO message selector, verbatim (empty: every message)",
            "", f"    def __init__(self, bus: MessageBus, service: {svc}) -> None:", "        self.bus = bus",
            "        self.service = service", "        self._stop: Callable[[], None] | None = None", "",
            "    def start(self) -> None:", "        self._stop = self.bus.subscribe(self.ENTITY, self.handle)", "",
            "    def stop(self) -> None:", "        if self._stop is not None:", "            self._stop()",
            "            self._stop = None", "",
            "    async def handle(self, message: Message) -> Outcome:",
            f'        """Settles one message. {doc(pp.remarks(pp.starter_act.id))}"""',
            "        if not self.selector_matches(message.properties):",
            '            return Outcome.dead_letter("SelectorMismatch", f"the message does not match {self.SELECTOR}")',
            "        try:", "            body = tib.loads(message.body)", "        except ValueError as ex:",
            '            return Outcome.dead_letter("InvalidBody", masking.mask_text(str(ex)))',
            (f'        values = {{"Body": {wrapped}, "JMSProperties": application_properties(message), '
            '"JMSHeaders": jms_headers(message)}'),
            "        try:",
            f"            await self.service.execute(ProcessState.of({py_string(sn)}, values).with_message(message))",
            "        except ProcessFault as fault:",
            f'            LOG.warning("%s ended in fault %s", {py_string(pp.p.name)}, fault.fault_name)',
            "            return Outcome.dead_letter(fault.fault_name, masking.mask_text(str(fault)))",
            "        return Outcome.COMPLETED", "",
            "    @staticmethod", "    def selector_matches(properties: dict[str, str]) -> bool:",
            '        """Whether the TIBCO selector would deliver a message with these properties."""', *match]
        return "\n".join(lines) + "\n", origin

    def job(self, pp: PythonProcess, plan: ServicePlan) -> str:
        interval = plan.starter.interval_ms or 86_400_000
        self.timers[pp.name] = interval
        start = str((pp.p.starter.config if pp.p.starter else {}).get("start") or "").strip()
        if start:
            self.timer_starts[pp.name] = start
        cls = f"{pp.name}Job"
        sn = pp.starter_name() or "Timer"
        key = f"JOBS_{upper_snake(pp.name)}"
        return "\n".join([
            (f'"""Timer starter of {doc(pp.p.name)}: every {key}_INTERVAL_MS (Jobs__{pp.name}__IntervalMs) milliseconds, '
             f"first at {key}_START (Jobs__{pp.name}__Start, local time) when set; started with the app when "
             'JOBS_ENABLED is true."""'), "",
            *imports({"from __future__ import annotations", "import asyncio", "import datetime as _dt", "import logging",
                      f"from {self.pkg}.config import Settings", f"from {self.pkg}.core import tib",
                      f"from {self.pkg}.core.{pp.module}_service import {pp.service_class}",
                      f"from {self.pkg}.core.tib import ProcessState"}),
            "", "LOG = logging.getLogger(__name__)", "", "",
            f"class {cls}:", f'    """Runs {doc(pp.p.name)} on its TIBCO timer."""', "",
            f"    INTERVAL_KEY = {py_string(key + '_INTERVAL_MS')}", f"    START_KEY = {py_string(key + '_START')}", "",
            f"    def __init__(self, service: {pp.service_class}, settings: Settings) -> None:",
            "        self.service = service", f"        self.interval_ms = settings.integer(self.INTERVAL_KEY, {interval})",
            "        self.start_at = settings.text(self.START_KEY)", "        self._task: asyncio.Task[None] | None = None", "",
            "    async def execute(self) -> None:",
            f'        """One TIBCO timer tick. {doc(pp.remarks(pp.starter_act.id))}"""',
            '        timer = {"Time": tib.date_time(_dt.datetime.now(_dt.UTC).astimezone())}',
            f"        await self.service.execute(ProcessState.of({py_string(sn)}, timer))", "",
            "    def start(self) -> None:", "        self._task = asyncio.create_task(self._run())", "",
            "    def stop(self) -> None:", "        if self._task is not None:", "            self._task.cancel()",
            "            self._task = None", "",
            "    async def _run(self) -> None:", "        await asyncio.sleep(self.initial_delay_s(self.start_at))",
            "        while True:", "            try:", "                await self.execute()",
            "            except Exception:  # a failed tick is logged; the next one runs on time",
            f'                LOG.exception("%s tick failed", {py_string(pp.p.name)})',
            "            await asyncio.sleep(self.interval_ms / 1000)", "",
            "    @staticmethod", "    def initial_delay_s(start: str) -> float:",
            '        """Seconds until the next local ``start`` (HH:MM or HH:MM:SS); 0 when none is set."""',
            "        if not start.strip():", "            return 0.0", "        try:",
            "            at = _dt.time.fromisoformat(start.strip())", "        except ValueError:", "            return 0.0",
            "        now = _dt.datetime.now()", "        first = _dt.datetime.combine(now.date(), at)",
            "        if first <= now:", "            first += _dt.timedelta(days=1)",
            "        return (first - now).total_seconds()"]) + "\n"

    # -------------------------------------------------------------- agent classes
    def agent_classes(self, printers: list[PythonProcess]) -> None:
        """One class per file the steps call (HTTP clients, transforms, gateways), and a stub class
        for every design row whose file nothing else wrote, so each row names code that exists."""
        members: dict[str, list[tuple[AgentMember, str]]] = defaultdict(list)
        owners: dict[str, set[str]] = defaultdict(set)
        for pp in printers:
            for m in pp.agent_members:
                members[m.file].append((m, pp.p.id))
                owners[m.file].add(pp.p.id)
        for pp in printers:
            for r in pp.design.mapping:
                file = f"{row_layer(r)}/{r.file}"
                if self.path(file) in self.out.files or r.pattern == "input.mapping":
                    continue
                act = pp.acts.get(r.activity_id)
                if act is None:
                    continue
                if not any(m.method == r.method for m, _ in members[file]):
                    members[file].append((AgentMember(file, r.class_name, r.method, act, pp.remarks(act.id), r.pattern),
                                          pp.p.id))
                    owners[file].add(pp.p.id)
        for file in sorted(members):
            if self.path(file) in self.out.files:
                continue
            items = members[file]
            cls = items[0][0].class_name
            client = items[0][0].kind in ("http.invoke", "rest.invoke", "soap.invoke")
            owner = next(iter(owners[file])) if len(owners[file]) == 1 else None
            self.out.add(self.path(file), self.agent_class(cls, items, client), "agent", process_id=owner)
            if client:
                self.clients[cls] = class_module(self.pkg, file)
                expr = f"{cls}(self.settings.text({py_string(self.client_key(cls))}))"
            else:
                expr = f"{cls}()"
            self.component(file, cls, expr)

    @staticmethod
    def client_key(class_name: str) -> str:
        return f"CLIENTS_{upper_snake(class_name)}_BASE_ADDRESS"

    def agent_class(self, cls: str, items: list[tuple[AgentMember, str]], client: bool) -> str:
        seen: set[str] = set()
        methods: list[str] = []
        for m, _ in items:
            if m.method in seen:
                continue
            seen.add(m.method)
            if m.method == "__init__":
                continue  # a constructor row (an exception's) is not a step method
            methods += ["", f"    async def {m.method}(self, values: dict[str, Any]) -> Any:",
                        f'        """TIBCO step {doc(m.activity.name)} ({m.activity.type}). {doc(m.remarks)}"""',
                        (f"        # TIBCO: {comment(m.activity.name)} ({m.activity.type}), native type "
                        f"{comment(m.activity.native_type)}")]
            for b in m.activity.input_bindings:
                methods.append(f"        # TIBCO: {comment(b.target)} = {comment(b.expression)}")
            methods.append(placeholder(m.activity.name, "        "))
        lines = ['"""' + (f"TIBCO client {doc(cls)}: one method per step that calls it." if client else
                          f"TIBCO steps the Code Builder writes in {doc(cls)}: one method per step.") + '"""', "",
                 "from __future__ import annotations", "", "from typing import Any", "", "", f"class {cls}:"]
        if client:
            key = self.client_key(cls)
            lines += [(f'    """The calls go to the base address {key} (Clients__{cls}__BaseAddress); the Code '
                       'Builder writes them with httpx."""'), "",
                      "    def __init__(self, base_address: str) -> None:", "        self.base_address = base_address"]
        else:
            lines.append(f'    """The steps of {doc(cls)}."""')
        lines += methods
        return "\n".join(lines) + "\n"

    # -------------------------------------------------------------- shared files
    def infrastructure_files(self) -> None:
        if self.connections:
            self.template("infrastructure/database.py", "database.py.j2")
        self.template("infrastructure/bus_memory.py", "bus_memory.py.j2")
        self.template("infrastructure/bus_servicebus.py", "bus_servicebus.py.j2", topics=sorted(self.topics))

    def api_files(self) -> None:
        for name in ("auth", "devtoken", "bus_dev", "fault", "http"):
            self.template(f"api/{name}.py", f"{name}.py.j2")

    def container_file(self) -> None:
        """``container.py``: every component the app needs, built once and lazily, by attribute."""
        stmts = {"from __future__ import annotations", "from functools import cached_property",
                 f"from {self.pkg}.config import Settings", f"from {self.pkg}.core.bus import MessageBus",
                 f"from {self.pkg}.infrastructure.bus_memory import InMemoryMessageBus"}
        if self.connections:
            stmts.add(f"from {self.pkg}.infrastructure.database import Database")
        body: list[str] = []
        for attr in sorted(self.components):
            module, cls, expr = self.components[attr]
            stmts.add(f"from {module} import {cls}")
            body += ["", "    @cached_property", f"    def {attr}(self) -> {cls}:", f"        return {expr}"]

        def listing(name: str, attrs: list[str], kind: str) -> list[str]:
            items = ", ".join(f"self.{a}" for a in attrs)
            return ["", f"    def {name}(self) -> list[Any]:", f'        """{kind}."""', f"        return [{items}]"]

        stmts.add("from typing import Any")
        lines = [('"""The wiring of the service: the bus, the database and every component, built on first use.'
                 ' Generated; a component\'s constructor arguments are fixed by the design."""'), "",
                 *imports(stmts), "", "",
                 "class Container:", "    def __init__(self, settings: Settings) -> None:",
                 "        self.settings = settings",
                 '        self.in_memory_bus = settings.text("MESSAGING_MODE", "InMemory").lower() != "servicebus"',
                 "        self.bus: MessageBus = InMemoryMessageBus() if self.in_memory_bus else self._service_bus()"]
        if self.connections:
            lines.append("        self.database = Database(settings)")
        lines += ["", "    def _service_bus(self) -> MessageBus:",
                  f"        from {self.pkg}.infrastructure.bus_servicebus import ServiceBusMessageBus", "",
                  '        return ServiceBusMessageBus(self.settings.text("MESSAGING_SERVICE_BUS_FULLY_QUALIFIED_NAMESPACE"))']
        lines += body
        lines += listing("routes", self.route_attrs, "The routes of the REST and HTTP starters")
        lines += listing("consumers", self.consumer_attrs, "The consumers of the JMS starters")
        lines += listing("jobs", self.job_attrs, "The jobs of the timer starters")
        lines += ["", "    async def start(self) -> None:", '        """Subscribes the consumers and, with JOBS_ENABLED, starts the jobs."""',
                  "        for consumer in self.consumers():", "            consumer.start()",
                  '        if self.settings.flag("JOBS_ENABLED"):', "            for job in self.jobs():", "                job.start()",
                  "", "    async def stop(self) -> None:", "        for job in self.jobs():", "            job.stop()",
                  "        for consumer in self.consumers():", "            consumer.stop()", "        await self.bus.close()"]
        self.out.add(self.path("container.py"), "\n".join(lines) + "\n")

    def package_files(self) -> None:
        """An ``__init__.py`` in every package folder under ``src`` that has none."""
        folders: set[str] = set()
        for path in list(self.out.files):
            if not path.startswith("src/") or not path.endswith(".py"):
                continue
            parent = PurePosixPath(path).parent
            while str(parent) not in ("src", "."):
                folders.add(str(parent))
                parent = parent.parent
        for folder in sorted(folders):
            init = f"{folder}/__init__.py"
            if init not in self.out.files:
                text = (f'"""The {self.app_name} service, migrated from TIBCO BusinessWorks application '
                        f'{doc(self.app.id)}."""\n' if folder == self.paths["src"] else "")
                self.out.add(init, text)

    # -------------------------------------------------------------- tests (written before the bodies)
    def test_files(self, printers: list[PythonProcess]) -> None:
        """The test index (the tests themselves arrive with ``tests_gen``)."""
        index = write_index([])
        self.out.add(index.path, index.content)

    # -------------------------------------------------------------- configuration
    def config_files(self, printers: list[PythonProcess]) -> None:
        main, dev, secrets, plain, not_carried = self.settings(printers)
        self.template("config.py", "config.py.j2", defaults=[f"{py_string(k)}: {_literal(v)}," for k, v in main.items()],
                      development=[f"{py_string(k)}: {_literal(v)}," for k, v in dev.items()],
                      not_carried=[(doc(n), doc(r)) for n, r in not_carried])
        contract = {"connections": sorted(self.connections), "clients": sorted(self.clients),
                    "secrets": [{"name": n, "key": k} for n, k in secrets], "plain_keys": plain,
                    "port_env": "PORT", "health": "/health"}
        self.out.add(CONTRACT_FILE, json.dumps(contract, indent=2, sort_keys=True) + "\n")

    def settings(self, printers: list[PythonProcess]) -> tuple[dict, dict, list[tuple[str, str]], list[str],
                                                                 list[tuple[str, str]]]:
        """(defaults, development defaults, Key Vault (name, env key) secrets, plain env keys,
        substitution variables not carried) of ``config.py``, as the .NET generator decides them."""
        seed = hashlib.sha256(f"exiqo-dev:{self.app.id}".encode()).hexdigest()
        main: dict[str, Any] = {
            "AUTH_MODE": "Production", "AUTH_ENTRA_ID_INSTANCE": "https://login.microsoftonline.com/",
            "AUTH_ENTRA_ID_TENANT_ID": "<entra-tenant-id>", "AUTH_ENTRA_ID_AUDIENCE": "api://<app-registration-client-id>",
            "MESSAGING_MODE": "ServiceBus",
            "MESSAGING_SERVICE_BUS_FULLY_QUALIFIED_NAMESPACE": "<service-bus-namespace>.servicebus.windows.net",
            "KEY_VAULT_URI": "", "JOBS_ENABLED": True,
        }
        dev: dict[str, Any] = {
            "AUTH_MODE": "Development", "AUTH_DEV_TOKEN_ISSUER": "exiqo-dev",
            "AUTH_DEV_TOKEN_AUDIENCE": f"{self.app_name.lower()}-api",
            "AUTH_DEV_TOKEN_SIGNING_KEY": f"development-only-{seed}", "CORS_ORIGINS_0": "http://localhost:3000",
            "MESSAGING_MODE": "InMemory", "JOBS_ENABLED": False,
        }
        secrets: dict[str, str] = {}
        plain: set[str] = set()
        not_carried: dict[str, str] = {}
        resources = {r.name: r for r in self.app.resources}
        used = set(self.connections.values())
        for pp in printers:
            module = pp.p.module or self.app.id
            for row in pp.design.config:
                if row.key.startswith("CONNECTION_STRINGS_"):
                    if row.key not in used:
                        continue
                    name = next(n for n, k in self.connections.items() if k == row.key)
                    res = resources.get(row.substvar[len("resource:"):].rpartition(".")[0])
                    settings = res.settings if res else {}
                    secrets[f"ConnectionStrings--{name}"] = f"ConnectionStrings__{name}"
                    dev[row.key] = _npgsql(str(settings.get("jdbc_url") or ""), str(settings.get("username") or ""))
                    continue
                if row.substvar.startswith("resource:") or not _references(pp.p, row.substvar):
                    not_carried.setdefault(row.substvar, self.not_carried(row.substvar))
                    continue
                env = f"Gmf__{ident(module, 'Module')}__{ident(row.substvar, 'Var')}"
                found = dotnet._substvar_value(self.app, pp.p.module, row.substvar)
                value, typ = found if found else ("", "String")
                local_value = dotnet._typed(value, typ) if value else "change-me-local"
                if row.secret:
                    secrets[env.replace("__", "--")] = env
                    dev[row.key] = local_value
                else:
                    plain.add(env)
                    main[row.key] = local_value
        for name, interval in sorted(self.timers.items()):
            main[f"JOBS_{upper_snake(name)}_INTERVAL_MS"] = interval
            plain.add(f"Jobs__{name}__IntervalMs")
            if name in self.timer_starts:
                main[f"JOBS_{upper_snake(name)}_START"] = self.timer_starts[name]
                plain.add(f"Jobs__{name}__Start")
        for cls in sorted(self.clients):
            main[self.client_key(cls)] = "http://localhost/"
            plain.add(f"Clients__{cls}__BaseAddress")
        return main, dev, sorted(secrets.items()), sorted(plain), sorted(not_carried.items())

    def not_carried(self, substvar: str) -> str:
        """Why a TIBCO substitution variable is not in config.py (the .NET reasons, in Python words)."""
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
                    return f"HTTP client setting: carried as {self.client_key(ident(res.name, 'Resource'))}"
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


def _literal(v: Any) -> str:
    if isinstance(v, bool):
        return "True" if v else "False"
    if isinstance(v, int | float):
        return str(v)
    return py_string(str(v))


__all__ = ["CONTRACT_FILE", "generate_python", "layout_paths"]
