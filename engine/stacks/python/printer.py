"""Prints one process's ``ServicePlan`` as Python: the service, its mappings and its link conditions.

The generated code keeps every process variable as JSON (a ``dict``/``list``/scalar tree) in a
``ProcessState`` (``core/tib.py``): a TIBCO path ``$LookupDealer/Record[1]/status`` is
``tib.path(state.get("LookupDealer"), "Record", 1, "status")``, so code writes every expression whose
shape ``expr.parse_simple`` reads and whose path roots name a variable of the process (ruling X19).
The plan's agent flags are a lower bound (``plan`` module docstring): this printer also hands a
mapping, a condition or a step to the Code Builder when one of its paths names no variable it
knows, a mapping target it cannot place, a loop it cannot drive or a link that crosses a group.
Everything left to the agent keeps its ``# TIBCO`` lines above
``raise NotImplementedError("agent fills: <activity>")``.

Flow: ``Seq`` and ``If`` print as straight-line code (an ``if`` per conditional link with its
otherwise branch, a guard clause when the otherwise branch ends the run); a ``Queue`` runs on
``ProcessFlow`` with a ``match`` over the steps; a ``Loop`` is a method of the service named like its
design row; a ``Try`` catches ``Exception`` and dispatches on the TIBCO fault name. Every service
method is ``async def``; mappings and conditions are plain static functions.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, Literal

from engine.dotnet_gen.models import DesignRecord, MappingRow
from engine.ir.models import Activity, Process
from engine.stacks import expr as X
from engine.stacks.plan import If, Loop, PlanStep, Queue, Run, Seq, ServicePlan, Try
from engine.stacks.python.naming import PythonNaming, snake
from engine.stacks.python.text import comment, doc, one_line, placeholder, py_string, tibco

if TYPE_CHECKING:
    from engine.stacks.plan import FlowNode

Origin = Literal["code", "agent"]

FUNCTIONS: dict[str, str] = {
    "concat": "tib_text.concat", "string": "tib_text.string", "number": "tib_text.number",
    "upper-case": "tib_text.upper_case", "lower-case": "tib_text.lower_case",
    "normalize-space": "tib_text.normalize_space", "string-length": "tib_text.string_length",
    "substring": "tib_text.substring", "substring-before": "tib_text.substring_before",
    "substring-after": "tib_text.substring_after", "translate": "tib_text.translate",
    "round": "tib_text.round_", "floor": "tib_text.floor", "ceiling": "tib_text.ceiling",
    "current-dateTime": "tib_text.current_date_time", "current-date": "tib_text.current_date",
}
log = logging.getLogger(__name__)
_PATH_IN_TEXT = re.compile(r"\$[\w.\-]+(?:/@?(?:[\w.\-]+:)?[\w.\-]+(?:\[\d+\])?)*")
_HREF_ROOT = re.compile(r"#//([^;\"']+)")
_INVOKE = frozenset({"http.invoke", "rest.invoke", "soap.invoke"})
_MAPPING_ONLY = frozenset({"noop", "catch", "confirm", "throw", "reply", "end", "log", "mapper", "assign",
                           "call.process"})
CALLABLE = frozenset({"http.invoke", "rest.invoke", "soap.invoke", "java.invoke", "xslt.transform",
                      "json.parse", "json.render", "xml.parse", "xml.render", "shared.get", "shared.set",
                      "file.read", "file.write", "file.list", "ftp.get", "ftp.put", "mail.send", "cmd.exec"})


def local(segment: str) -> str:
    """A path or target step without its namespace prefix."""
    return segment.split(":", 1)[1] if ":" in segment else segment


# ----------------------------------------------------------------- expressions
class ExprPrinter:
    """Python for a parsed expression; ``None`` when a path root names no known variable."""

    def __init__(self, roots: dict[str, str], check: dict[str, tuple[str, ...]]):
        self.roots = roots  # TIBCO variable -> ProcessState name
        self.check = check  # ProcessState name -> allowed first steps (empty: any)

    def path(self, p: X.Path) -> str | None:
        var = self.roots.get(p.root)
        if var is None:
            return None
        allowed = self.check.get(var, ())
        if allowed and p.steps and p.steps[0][0] not in allowed:
            return None
        args: list[str] = []
        for name, index in p.steps:
            args.append(py_string(name))
            if index is not None:
                args.append(str(index))
        base = f"state.get({py_string(var)})"
        return f"tib.path({base}, {', '.join(args)})" if args else base

    def value(self, e: X.Expr) -> str | None:
        if isinstance(e, X.Path):
            return self.path(e)
        if isinstance(e, X.Lit):
            return literal(e.value)
        if isinstance(e, X.Count):
            inner = self.path(e.path)
            return None if inner is None else f"tib.count({inner})"
        if isinstance(e, X.Call):
            args = [self.value(a) for a in e.args]
            if any(a is None for a in args) or e.name not in FUNCTIONS:
                return None
            return f"{FUNCTIONS[e.name]}({', '.join(args)})"  # type: ignore[arg-type]
        return self.cond(e)

    def cond(self, e: X.Expr) -> str | None:
        if isinstance(e, X.Compare):
            left, right = self.value(e.left), self.value(e.right)
            if left is None or right is None:
                return None
            return f"tib.compare({left}, {py_string(e.op)}, {right})"
        if isinstance(e, (X.And, X.Or)):
            items = [self.cond(i) for i in e.items]
            if any(i is None for i in items):
                return None
            op = " and " if isinstance(e, X.And) else " or "
            return "(" + op.join(items) + ")"  # type: ignore[arg-type]
        if isinstance(e, X.Not):
            inner = self.cond(e.item)
            return None if inner is None else f"not {inner if inner.startswith('(') else f'({inner})'}"
        if isinstance(e, X.Lit) and isinstance(e.value, bool):
            return "True" if e.value else "False"
        v = self.value(e)
        return None if v is None else f"tib.boolean({v})"


def literal(value: str | int | Decimal | bool) -> str:
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, Decimal):
        return f'Decimal("{value}")'
    return py_string(value)


# ------------------------------------------------------------------- the process
@dataclass
class Dep:
    """A collaborator handed to a service by the container."""

    type: str
    module: str  # the module it is imported from
    field: str  # the attribute (and the constructor keyword)
    lazy: bool = False  # a sub-process service: passed as a zero-argument callable


@dataclass
class AgentMember:
    """A method of an agent class (an HTTP client, a transform, a gateway) a step calls."""

    file: str  # under the package root, e.g. ``infrastructure/clients/equifax_client.py``
    class_name: str
    method: str
    activity: Activity
    remarks: str
    kind: str  # the pattern


@dataclass
class Ctx:
    """What the solution knows that one process needs."""

    pkg: str
    naming: PythonNaming
    in_build: dict[str, str]  # process id -> service class of every process in the build
    stubs: dict[str, str] = field(default_factory=dict)  # call target -> stub service class (not in the build)


def class_module(pkg: str, layer_file: str) -> str:
    """The module of a file under the package root (``core/x_service.py`` -> ``gmf_credit.core.x_service``)."""
    return f"{pkg}.{layer_file[:-len('.py')].replace('/', '.')}"


@dataclass
class _Printed:
    lines: list[str]
    abrupt: bool = False  # the block cannot complete normally (ends with return or raise)


class PythonProcess:
    """Everything printed for one process."""

    def __init__(self, ctx: Ctx, process: Process, design: DesignRecord, plan: ServicePlan,
                 finding_decisions: dict[str, str]):
        self.ctx = ctx
        self.p = process
        self.design = design
        self.plan = plan
        self.naming = ctx.naming
        self.decisions = finding_decisions
        self.name = self.naming.type_name(process.name, "Process")
        self.module = snake(self.name)  # dealer_funding_request
        self.service_class = f"{self.name}Service"
        self.acts = {a.id: a for a in process.activities}
        self.groups = {g.id: g for g in process.groups}
        self.steps: dict[str, PlanStep] = {s.activity_id: s for s in plan.steps}
        self.outs: dict[str, list] = {}
        for t in process.transitions:
            self.outs.setdefault(t.from_id, []).append(t)
        self.primary: dict[str, MappingRow] = {}
        self.map_row: dict[str, MappingRow] = {}
        for r in design.mapping:
            if r.pattern == "input.mapping" or (r.pattern in ("mapper", "assign") and r.activity_id in self.acts):
                self.map_row.setdefault(r.activity_id, r)
            if r.pattern != "input.mapping":
                self.primary.setdefault(r.activity_id, r)
        self.starter_act = self.acts.get(process.starter.activity_id) if process.starter else None
        self.kind = process.starter.kind if process.starter else "subprocess"
        self.roots, self.check = self._roots()
        self.expr = ExprPrinter(self.roots, self.check)
        # collected while printing
        self.deps: dict[str, Dep] = {}
        self.agent_members: list[AgentMember] = []
        self.agent_methods: list[list[str]] = []
        self.group_methods: list[list[str]] = []
        self.method_names: set[str] = {"execute", "__init__"}
        self.service_origin: Origin = "code"
        self.mapping_origin: Origin = "code"
        self.condition_origin: Origin = "code"
        self.mapping_codes: dict[str, bool] = {}
        self.condition_names = self._condition_names()
        self.uses: set[str] = set()  # import lines the service needs beyond the always-there ones
        self.flows = 0
        self.try_depth = 0  # nesting of TIBCO catch handlers being printed (exception variable names)
        self.exceptions: dict[str, tuple[str, str]] = {}  # activity id -> (exception class, fault name)
        for a in process.activities:
            if a.type == "throw":
                row = self.primary.get(a.id)
                fault = str(a.config.get("fault_name") or a.name)
                cls = row.class_name if row and row.pattern == "throw" else self.naming.exception_name(fault)
                self.exceptions[a.id] = (cls, fault)

    def repository_methods(self) -> dict[str, str]:
        """Activity id -> repository method of every JDBC step, unique within the repository: a
        second step whose name gives a method already taken gets a numbered one, with a warning."""
        if hasattr(self, "_repository_methods"):
            return self._repository_methods
        out: dict[str, str] = {}
        used: set[str] = set()
        for a in self.p.activities:
            if not a.type.startswith("jdbc."):
                continue
            row = self.primary.get(a.id)
            method = row.method if row and row.pattern.startswith("jdbc.") else self.naming.method_name("", a.name)
            if method in used:
                base, n = method, 2
                while f"{base}{n}" in used:
                    n += 1
                log.warning("%s: repository method %s is taken; step %s is written as %s%d", self.p.id, method,
                            a.name, base, n)
                method = f"{base}{n}"
            used.add(method)
            out[a.id] = method
        self._repository_methods = out
        return out

    # ---------------------------------------------------------------- variables
    def starter_name(self) -> str | None:
        return (self.starter_act.name or self.starter_act.id) if self.starter_act else None

    def _roots(self) -> tuple[dict[str, str], dict[str, tuple[str, ...]]]:
        roots: dict[str, str] = {"_error": "_error"}
        check: dict[str, tuple[str, ...]] = {}
        sn = self.starter_name()
        for a in self.p.activities:
            if self.starter_act is not None and a.id == self.starter_act.id:
                continue
            var = a.name or a.id
            for key in (a.id, a.name.replace(" ", "-"), a.name):
                if key:
                    roots[key] = var
            if a.type == "jdbc.query":
                check[var] = ("Record",)
            elif a.type == "jdbc.update":
                check[var] = ("noOfUpdates",)
        if sn is None:
            roots["Start"] = "Start"
        elif self.kind in ("rest", "http", "soap"):
            for key in (sn, f"{sn}.parameters", f"{sn}.item", f"{sn}.httpHeaders"):
                roots[key] = key
        else:
            roots[sn] = sn
            if self.kind == "jms":
                check[sn] = ("Body", "JMSProperties", "JMSHeaders")
        for loop in _loops(self.plan.flow):
            for var in (loop.counter, loop.element):
                if var:
                    roots.setdefault(var, var)
        return roots, check

    def message_root(self) -> str | None:
        """The element a JMS starter's body is: the editor element, else what the paths read."""
        if self.starter_act is None:
            return None
        settings = self.starter_act.config.get("settings")
        refs = settings.get("bodyEditorElement") if isinstance(settings, dict) else None
        for ref in refs if isinstance(refs, list) else []:
            m = _HREF_ROOT.search(str(ref.get("href") if isinstance(ref, dict) else ref))
            if m:
                return m.group(1)
        sn = self.starter_name()
        for a in self.p.activities:
            for b in a.input_bindings:
                for path in _PATH_IN_TEXT.findall(b.expression or ""):
                    parts = path[1:].split("/")
                    if parts[0] == sn and len(parts) > 2 and parts[1] == "Body":
                        return local(re.sub(r"\[\d+\]$", "", parts[2]))
        return None

    # ---------------------------------------------------------------- lineage
    def remarks(self, act_id: str) -> str:
        row = self.primary.get(act_id)
        rules = ", ".join(row.rule_ids) if row and row.rule_ids else "none"
        parts = []
        for fid in row.finding_ids if row else []:
            label = {"keep": "kept", "fix": "fixed", "ask": "asked"}.get(self.decisions.get(fid) or "", "undecided")
            parts.append(f"{fid} ({label})")
        act = self.acts.get(act_id)
        name = act.name if act else act_id
        line = f" line {act.source_line}" if act and act.source_line else ""
        return f"Rules: {rules}. Findings: {', '.join(parts) or 'none'}. TIBCO: {name}{line}"

    def header(self, act: Activity) -> str:
        line = f" line {act.source_line}" if act.source_line else ""
        lineage = self.remarks(act.id).split(". TIBCO:")[0]
        return f"# TIBCO: {comment(act.name)} ({act.type}){line}. {lineage}."

    # ---------------------------------------------------------------- mappings
    def _targets(self, act: Activity) -> list[list[str]] | None:
        """Each binding's target as element steps, the common root element dropped; None when one
        cannot be placed (an index, an empty step, two bindings on one target)."""
        segs = [[local(s).lstrip("@") for s in b.target.split("/") if s] for b in act.input_bindings]
        if any(not s or any("[" in x or not x for x in s) for s in segs):
            return None
        if len({s[0] for s in segs}) == 1 and all(len(s) > 1 for s in segs):
            segs = [s[1:] for s in segs]
        if len({tuple(s) for s in segs}) != len(segs):
            return None
        return segs

    def mapping_method(self, act: Activity, row: MappingRow) -> list[str]:
        step = self.steps.get(act.id)
        inputs = step.inputs if step else []
        pad = "        "
        targets = self._targets(act)
        values = [self.expr.value(a.source) if a.source is not None else None for a in inputs]
        code = targets is not None and all(v is not None for v in values) and len(inputs) == len(act.input_bindings)
        self.mapping_codes[act.id] = code
        lines = ["    @staticmethod", f"    def {row.method}(state: ProcessState) -> dict[str, Any]:",
                 (f'        """Mapping of {doc(act.name)} ({act.type}): the TIBCO input bindings, each kept as '
                  f'a comment. {doc(self.remarks(act.id))}"""')]
        if code:
            lines.append(f"{pad}out: dict[str, Any] = {{}}")
            for a, value, target in zip(inputs, values, targets or [], strict=True):
                lines.append(tibco(a.target, a.tibco, pad))
                lines.append(f"{pad}tib.put(out, {value}, {', '.join(py_string(t) for t in target)})")
            lines.append(f"{pad}return out")
            return lines
        self.mapping_origin = "agent"
        for b in act.input_bindings:
            lines.append(tibco(b.target, b.expression, pad))
        legend = self.legend(b.expression for b in act.input_bindings)
        if legend:
            lines.append(f"{pad}# Paths code resolves (TIBCO path: Python):")
            lines += [f"{pad}#   {comment(p)}: {comment(j)}" for p, j in legend]
        lines.append(placeholder(f"{act.name} mapping", pad))
        return lines

    def legend(self, expressions) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        seen: set[str] = set()
        for e in expressions:
            for path in _PATH_IN_TEXT.findall(one_line(e or "")):
                if path in seen:
                    continue
                seen.add(path)
                parsed = X.parse_simple(path)
                py = self.expr.path(parsed) if isinstance(parsed, X.Path) else None
                if py is not None:
                    out.append((path, py))
        return out

    def _module_header(self, summary: str, extra: set[str]) -> list[str]:
        from engine.stacks.python.text import imports

        base = {"from __future__ import annotations", "from decimal import Decimal", "from typing import Any",
                f"from {self.ctx.pkg}.core import tib, tib_text", f"from {self.ctx.pkg}.core.tib import ProcessState"}
        return [f'"""{doc(summary)}"""', "", *imports(base | extra)]

    def mappings(self) -> tuple[str, Origin] | None:
        if not self.map_row:
            return None
        methods: list[list[str]] = []
        for act in self.p.activities:
            row = self.map_row.get(act.id)
            if row is not None:
                methods.append(self.mapping_method(act, row))
        lines = self._module_header(f"Input mappings of {self.p.name}: simple paths generated by code, the rest "
                                    "filled by the Code Builder.", set())
        lines += ["", "", f"class {self.name}Mappings:",
                  f'    """The input mappings of {doc(self.p.name)}, one static function per step."""']
        for m in methods:
            lines += [""] + m
        return "\n".join(lines) + "\n", self.mapping_origin

    def mcall(self, act_id: str) -> str | None:
        row = self.map_row.get(act_id)
        return f"{self.name}Mappings.{row.method}(state)" if row else None

    # ---------------------------------------------------------------- conditions
    def _condition_names(self) -> dict[str, str]:
        out: dict[str, str] = {}
        used: set[str] = set()
        for t in self.p.transitions:
            if t.kind != "conditional":
                continue
            name, n = self.naming.method_name("", t.id), 2
            while name in used:
                name, n = f"{self.naming.method_name('', t.id)}_{n}", n + 1
            used.add(name)
            out[t.id] = name
        return out

    def condition_call(self, link_id: str) -> str:
        return f"{self.name}Conditions.{self.condition_names[link_id]}(state)"

    def conditions(self) -> tuple[str, Origin] | None:
        if not self.condition_names:
            return None
        lines = self._module_header(f"Conditions of the {self.p.name} links, from the TIBCO XPath expressions.",
                                    set())
        lines += ["", "", f"class {self.name}Conditions:",
                  f'    """The link conditions of {doc(self.p.name)}, one static function per conditional link."""']
        for t in self.p.transitions:
            if t.kind != "conditional":
                continue
            text = t.expression or ""
            parsed = X.parse_simple(text)
            py = self.expr.cond(parsed) if parsed is not None else None
            line = f" line {t.source_line}" if t.source_line else ""
            lines += ["", "    @staticmethod", f"    def {self.condition_names[t.id]}(state: ProcessState) -> bool:",
                      f'        """Link {doc(t.id)}: {doc(t.from_id)} to {doc(t.to_id)} (TIBCO condition{line})."""',
                      f"        # TIBCO condition: {comment(text)}"]
            if py is not None:
                lines.append(f"        return {py}")
            else:
                self.condition_origin = "agent"
                lines.append(placeholder(f"transition {t.id}", "        "))
        return "\n".join(lines) + "\n", self.condition_origin

    # ---------------------------------------------------------------- dependencies
    def dep(self, type_: str, module: str, lazy: bool = False) -> str:
        if type_ not in self.deps:
            short = {f"{self.name}Repository": "repository", "MessageBus": "bus"}.get(type_)
            name = short or self.naming.escape(snake(type_))
            self.deps[type_] = Dep(type_, module, name, lazy)
        return self.deps[type_].field

    def unique_method(self, base: str) -> str:
        name, n = base, 2
        while name in self.method_names:
            name, n = f"{base}_step{n if n > 2 else ''}", n + 1
        self.method_names.add(name)
        return name

    def agent_step(self, act: Activity, why: str) -> list[str]:
        """A method of the service the Code Builder fills; the step awaits it."""
        row = self.primary.get(act.id)
        if row and row.class_name == self.service_class and row.method not in self.method_names:
            method = row.method
            self.method_names.add(method)
        else:
            method = self.unique_method(self.naming.method_name("", act.name or act.id))
        self.service_origin = "agent"
        body = [f"    async def {method}(self, state: ProcessState) -> None:",
                f'        """TIBCO step {doc(act.name)} ({act.type}): {doc(why)}. {doc(self.remarks(act.id))}"""',
                f"        # TIBCO: {comment(act.name)} ({act.type}), native type {comment(act.native_type)}"]
        for b in act.input_bindings:
            body.append(tibco(b.target, b.expression, "        "))
        mapping = self.mcall(act.id)
        if mapping:
            body.append(f"        # The step's input: {comment(mapping)}")
        body.append(placeholder(act.name or act.id, "        "))
        self.agent_methods.append(body)
        return [f"await self.{method}(state)"]

    # ---------------------------------------------------------------- steps
    def step(self, node_id: str, final: bool, mode: str) -> _Printed:
        """The statements of one activity; ``final`` (no outgoing link) lets a reply return directly
        in the service's own body (``mode`` "direct")."""
        act = self.acts[node_id]
        t = act.type
        var = py_string(act.name or act.id)
        st = self.steps.get(node_id)
        m = self.mcall(node_id)
        arg = m or "{}"
        if self.starter_act is not None and act.id == self.starter_act.id:
            return _Printed([f"# Starter: its output is in the state as {comment(self.starter_vars())}."])
        row = self.primary.get(node_id)
        if t == "throw":
            cls, _ = self.exceptions[node_id]
            self.uses.add(f"from {self.ctx.pkg}.core.exceptions.{snake(cls)} import {cls}")
            return _Printed([f"raise {cls}({arg})"], abrupt=True)
        if t in ("reply", "end"):
            error = t == "reply" and self.kind in ("rest", "http") and m is not None and self._error_reply(act)
            if error:
                value = f"ProcessResult.error({m})"
            elif m:
                value = f"ProcessResult.ok({m})"
            else:
                value = "ProcessResult.ok(None)" if t == "reply" else "ProcessResult.COMPLETED"
            if final and mode == "direct":
                return _Printed([f"return {value}"], abrupt=True)
            return _Printed([f"state.reply({value})"])
        if t in ("noop", "catch", "confirm"):
            return _Printed([f"# TIBCO {t}: no code; kept for lineage."])
        if st is not None and st.agent and self._own_agent(st, row):
            return _Printed(self.agent_step(act, "left to the Code Builder (no code template)"))
        if t.startswith("jdbc."):
            field_ = self.dep(f"{self.name}Repository", f"{self.ctx.pkg}.infrastructure.{self.module}_repository")
            method = self.repository_methods()[act.id]
            return _Printed([f"state.set({var}, await self.{field_}.{method}({arg}))"])
        if t in ("jms.send", "jms.publish"):
            body = self._publish_body(act, m)
            dest = st.call_target if st else None
            if body is None or not dest:
                return _Printed(self.agent_step(act, self._publish_problem(act, m, dest)))
            bus = self.dep("MessageBus", f"{self.ctx.pkg}.core.bus")
            return _Printed([f"await self.{bus}.publish({py_string(dest)}, {body})"])
        if t == "log":
            if m is None:
                return _Printed(["# TIBCO log step without input."])
            self.uses.add(f"from {self.ctx.pkg}.core import masking")
            return _Printed([(f'LOG.info("%s %s: %s", {py_string(self.p.name)}, {py_string(act.name)}, '
                             f"masking.mask_json({m}))")])
        if t in ("mapper", "assign"):
            if m is None:
                return _Printed([f"# TIBCO {t} without bindings."])
            return _Printed([f"state.set({var}, {m})"])
        if t == "call.process":
            target = st.call_target if st else None
            cls = self.ctx.in_build.get(target or "") or self.ctx.stubs.get(target or "")
            if cls is None:
                return _Printed(self.agent_step(act, f"calls {target or 'a sub-process'}, which has no service"))
            sub = self.dep(cls, f"{self.ctx.pkg}.core.{snake(cls)}", lazy=True)
            return _Printed([f'state.set({var}, (await self.{sub}().execute(ProcessState.of("Start", {arg}))).body)'])
        if row and t in CALLABLE and row.class_name != self.service_class and row.method:
            file = f"{row_layer(row)}/{row.file}"
            self.agent_members.append(AgentMember(file, row.class_name, row.method, act, self.remarks(act.id), t))
            dep = self.dep(row.class_name, class_module(self.ctx.pkg, file))
            return _Printed([f"state.set({var}, await self.{dep}.{row.method}({arg}))"])
        return _Printed(self.agent_step(act, "left to the Code Builder (no code template)"))

    def _own_agent(self, st: PlanStep, row: MappingRow | None) -> bool:
        """Whether the plan flags the step for its own sake, not only for its mapping (as the Java
        printer decides it): a step whose mapping is the only untranslatable part calls the mapping,
        and the mapping is the agent's."""
        t = st.pattern
        if t.startswith("jdbc."):
            return False
        if t in ("jms.send", "jms.publish"):
            return not st.inputs or not st.call_target
        if t in _INVOKE:
            return not (row and row.class_name and row.method)
        return t not in _MAPPING_ONLY

    def _error_reply(self, act: Activity) -> bool:
        roots = {local(b.target.split("/")[0]) for b in act.input_bindings if b.target.strip("/")}
        return bool(roots) and roots <= {"client4XXError", "server5XXError"}

    def _publish_body(self, act: Activity, m: str | None) -> str | None:
        """The message a JMS send or publish sends: the element under ``ActivityInput/Body``."""
        if m is None:
            return None
        segs = [[local(s) for s in b.target.split("/") if s] for b in act.input_bindings]
        roots = {tuple(s[:3]) for s in segs if len(s) >= 4 and s[0] == "ActivityInput" and s[1] == "Body"}
        if len(roots) != 1 or any(tuple(s[:3]) not in roots for s in segs):
            return None
        element = next(iter(roots))[2]
        return f'tib.path({m}, "Body", {py_string(element)})'

    def _publish_problem(self, act: Activity, m: str | None, dest: str | None) -> str:
        """Why code cannot write a JMS send or publish (the agent step's reason)."""
        if not dest:
            return "the destination of the message is not known"
        if m is None:
            return "the step has no input bindings"
        segs = [[local(x) for x in b.target.split("/") if x] for b in act.input_bindings]
        others = sorted({s[1] for s in segs if len(s) >= 2 and s[0] == "ActivityInput" and s[1] != "Body"})
        if others:
            return (f"the bindings set {', '.join(others)}, which the bus publish does not carry "
                    "(only the body element is sent)")
        roots = {tuple(s[:3]) for s in segs if len(s) >= 4 and s[0] == "ActivityInput" and s[1] == "Body"}
        if len(roots) > 1:
            return f"the bindings write more than one body element ({', '.join(sorted(r[2] for r in roots))})"
        return "the bindings do not write one element under ActivityInput/Body"

    def starter_vars(self) -> str:
        sn = self.starter_name()
        if sn is None:
            return "$Start"
        if self.kind in ("rest", "http", "soap"):
            return f"${sn}, ${sn}.parameters and ${sn}.item"
        return f"${sn}"

    # ---------------------------------------------------------------- flow
    def print_flow(self, node: FlowNode, mode: str) -> _Printed:
        if isinstance(node, Seq):
            return self._seq(list(node.items), mode)
        if isinstance(node, Run):
            if node.step_id not in self.acts:
                return _Printed([f"# TIBCO node {comment(node.step_id)} is not an activity."])
            act = self.acts[node.step_id]
            out = self.step(node.step_id, final=not self.outs.get(node.step_id), mode=mode)
            return _Printed([self.header(act), *out.lines], out.abrupt)
        if isinstance(node, If):
            return self._if(node, mode)
        if isinstance(node, Loop):
            return _Printed(self._loop_call(node))
        if isinstance(node, Queue):
            return self._queue(node)
        if isinstance(node, Try):
            return self._try(node, mode)
        raise TypeError(f"unknown flow node {node!r}")

    def _seq(self, items: list[FlowNode], mode: str) -> _Printed:
        lines: list[str] = []
        for item in items:
            printed = self.print_flow(item, mode)
            if not printed.lines:
                continue
            if lines:
                lines.append("")
            lines += printed.lines
            if printed.abrupt:
                return _Printed(lines, True)
        return _Printed(lines, False)

    def _if(self, node: If, mode: str) -> _Printed:
        call = self.condition_call(node.link_id) if node.link_id in self.condition_names else None
        cond = call or "False"
        then = self.print_flow(node.then, mode)
        other = self.print_flow(node.otherwise, mode)
        link = f"# Link {comment(node.link_id)}: {comment(node.tibco)}"
        otherwise = [f"# Link {comment(node.otherwise_id)}: otherwise"] if node.otherwise_id else []
        if not other.lines:
            if not then.lines:
                return _Printed([link])
            return _Printed([link, f"if {cond}:", *_block(then.lines)])
        if other.abrupt and not then.abrupt:
            # a guard clause: the otherwise branch ends the run, the conditional one carries on
            out = [link, f"if not {cond}:", *_block(otherwise + other.lines)]
            if then.lines:
                out += ["", *then.lines]
            return _Printed(out, False)
        if not then.lines:
            return _Printed([link, f"if not {cond}:", *_block(otherwise + other.lines)])
        out = [link, f"if {cond}:", *_block(then.lines), "else:", *_block(otherwise + other.lines)]
        return _Printed(out, then.abrupt and other.abrupt)

    def _try(self, node: Try, mode: str) -> _Printed:
        self.try_depth += 1
        var = "fault" if self.try_depth == 1 else f"fault{self.try_depth}"
        try:
            body = self.print_flow(node.body, mode)
            lines = ["try:", *_block(body.lines), f"except Exception as {var}:", f"    state.record_error({var})"]
            abrupt = body.abrupt
            branches = 0
            all_abrupt = True
            for i, (fault, flow) in enumerate(node.catch):
                printed = self.print_flow(flow, mode)
                self.uses.add(f"from {self.ctx.pkg}.core.exceptions import ProcessFault")
                kw = "if" if i == 0 else "elif"
                lines.append(f"    {kw} ProcessFault.named({var}, {py_string(fault)}):")
                lines += _block([f"# TIBCO catch {comment(fault)}", *printed.lines], 8)
                all_abrupt = all_abrupt and printed.abrupt
                branches += 1
            rest = self.print_flow(node.catch_all, mode) if node.catch_all is not None else None
        finally:
            self.try_depth -= 1
        tail = ["# TIBCO catchAll", *rest.lines] if rest is not None else ["raise"]
        tail_abrupt = rest.abrupt if rest is not None else True
        if branches:
            lines.append("    else:")
            lines += _block(tail, 8)
        else:
            lines += _block(tail, 4)
        return _Printed(lines, abrupt and all_abrupt and tail_abrupt)

    # ------------------------------------------------------------- queue
    def _queue(self, node: Queue) -> _Printed:
        if any(lk.crosses for lk in node.links):
            self.service_origin = "agent"
            ids = ", ".join(lk.id for lk in node.links if lk.crosses)
            lines = [f"# TIBCO links {comment(ids)} cross a group boundary; the Code Builder writes this flow."]
            for item in node.nodes:  # the steps and groups it runs, each kept for the Code Builder to call
                if isinstance(item, Loop):
                    lines += [_commented(ln) for ln in self._loop_call(item)]
                elif isinstance(item, Run) and item.step_id in self.acts:
                    lines.append(f"# {self.header(self.acts[item.step_id])[2:]}")
            return _Printed([*lines, placeholder(f"links of {self.p.name} that cross a group", "")], True)
        self.flows += 1
        flow = "flow" if self.flows == 1 else f"flow{self.flows}"
        lines = [f"{flow} = ProcessFlow(["]
        lines += [f"    Link({py_string(lk.id)}, {py_string(lk.from_id)}, {py_string(lk.to_id)})," for lk in node.links]
        lines.append("])")
        lines += [f"{flow}.start({py_string(s)})" for s in node.starts]
        cases: list[str] = []
        by_from: dict[str, list] = {}
        for lk in node.links:
            by_from.setdefault(lk.from_id, []).append(lk)
        for item in node.nodes:
            node_id = item.step_id if isinstance(item, Run) else item.group_id  # type: ignore[union-attr]
            printed = self.print_flow(item, "queue")
            outs = by_from.get(node_id, [])
            succ = [lk.id for lk in outs if lk.kind == "success"]
            cond = [lk for lk in outs if lk.kind == "conditional"]
            other = [lk.id for lk in outs if lk.kind == "otherwise"]
            errors = [lk.id for lk in outs if lk.kind == "error"]
            body: list[str] = []
            if errors and not printed.abrupt:
                body += ["try:", *_block(printed.lines), "except Exception as ex:", "    state.record_error(ex)",
                         f"    {flow}.complete({py_string(node_id)}, [{', '.join(py_string(e) for e in errors)}])",
                         "    continue"]
            else:
                body += printed.lines
            if not printed.abrupt:
                if not cond:
                    taken = ", ".join(py_string(x) for x in succ + other)
                    body.append(f"{flow}.complete({py_string(node_id)}, [{taken}])")
                else:
                    init = ", ".join(py_string(x) for x in succ)
                    body.append(f"taken = [{init}]")
                    for lk in cond:
                        call = self.condition_call(lk.id) if lk.id in self.condition_names else "False"
                        body += [f"# Link {comment(lk.id)}: {comment(lk.tibco)}", f"if {call}:",
                                 f"    taken.append({py_string(lk.id)})"]
                    if other:
                        body += [f"if len(taken) == {len(succ)}:"]
                        body += [f"    taken.append({py_string(o)})  # otherwise" for o in other]
                    body.append(f"{flow}.complete({py_string(node_id)}, taken)")
            cases += [f"case {py_string(node_id)}:", *_block(body)]
        cases += ["case _:", '    raise RuntimeError(f"unknown step {step}")']
        lines += [f"while (step := {flow}.next()) is not None:", "    match step:", *_indent(cases, 8)]
        self.uses.add(f"from {self.ctx.pkg}.core.tib import Link, ProcessFlow")
        return _Printed(lines, False)

    # -------------------------------------------------------------- loops
    def _loop_call(self, node: Loop) -> list[str]:
        g = self.groups.get(node.group_id)
        row = self.primary.get(node.group_id)
        method = row.method if row and row.class_name == self.service_class else self.naming.method_name("", node.group_id)
        if method in self.method_names:
            method = self.unique_method(method)
        else:
            self.method_names.add(method)
        members = ", ".join(g.member_ids) if g else ""
        header = f"# TIBCO: group {comment(node.group_id)} ({node.kind or 'group'}) over {comment(members)}."
        self.group_methods.append(self._loop_method(node, method))
        return [header, f"await self.{method}(state)"]

    def _loop_method(self, node: Loop, method: str) -> list[str]:
        pad = "        "
        settings = [f"{pad}# TIBCO {comment(k)}: {comment(v)}" for k, v in node.tibco]
        out = [f"    async def {method}(self, state: ProcessState) -> None:",
               (f'        """TIBCO group {doc(node.group_id)} ({doc(node.kind or "group")}'
                f'{", " + doc(node.style) if node.style else ""})."""'), *settings]
        body = self.print_flow(node.body, "group")
        inner = _stmts(list(body.lines))
        counter = [f"{pad}    state.set({py_string(node.counter)}, position)"] if node.counter else []
        if node.kind == "scope":
            return out + _indent(inner, 8)
        if node.kind == "iterate" or (node.kind == "loop" and node.style == "forEach"):
            if node.over is not None and (over := self.expr.value(node.over)) is not None:
                element = [f"{pad}    state.set({py_string(node.element)}, item)"] if node.element else []
                parallel = [f"{pad}# TIBCO runs the iterations in parallel; they run here in order."] \
                    if node.parallel else []
                return out + parallel + [f"{pad}position = 0", f"{pad}for item in tib.items({over}):",
                                         f"{pad}    position += 1", *counter, *element, *_indent(inner, 12)]
            first = self.expr.value(node.start) if node.start is not None else None
            last = self.expr.value(node.final) if node.final is not None else None
            if node.over is None and first is not None and last is not None:
                return out + [f"{pad}last = tib.integer({last}, 0)",
                              f"{pad}for position in range(tib.integer({first}, 1), last + 1):",
                              *counter, *_indent(inner, 12)]
        elif node.kind == "loop" and node.style in ("while", "repeatUntil") and node.condition is not None:
            cond = self.expr.cond(node.condition)
            if cond is not None:
                if node.style == "while":
                    return out + [f"{pad}position = 0", f"{pad}while {cond}:", f"{pad}    position += 1", *counter,
                                  *_indent(inner, 12)]
                return out + [f"{pad}position = 0", f"{pad}while True:", f"{pad}    position += 1", *counter,
                              *_indent(body.lines, 12), f"{pad}    if {cond}:", f"{pad}        break"]
        # transaction, critical section, parallel, or a loop code cannot drive: the Code Builder
        self.service_origin = "agent"
        members = ", ".join(self.groups[node.group_id].member_ids) if node.group_id in self.groups else ""
        what = f"group {node.group_id} ({node.kind or 'group'}) over {members}"
        return out + [f"{pad}# The members as code would run them in order:",
                      *[f"{pad}{_commented(ln)}" if ln else f"{pad}#" for ln in body.lines],
                      placeholder(what, pad)]

    # ------------------------------------------------------------- service
    def service(self) -> tuple[str, Origin]:
        from engine.stacks.python.text import imports

        body = self.print_flow(self.plan.flow, "direct")
        lines = list(body.lines)
        if not body.abrupt:
            if lines:
                lines.append("")
            lines.append("return state.finish()")
        pkg = self.ctx.pkg
        extra = {"from __future__ import annotations", "import logging", "from decimal import Decimal",
                 "from typing import Any", f"from {pkg}.core import tib, tib_text",
                 f"from {pkg}.core.tib import ProcessResult, ProcessState"}
        extra |= self.uses
        deps = sorted(self.deps.values(), key=lambda d: d.field)
        for d in deps:
            extra.add(f"from {d.module} import {d.type}")
        if any(d.lazy for d in deps):
            extra.add("from collections.abc import Callable")
        how = ("The TIBCO links form sequences and if/else regions, so the process reads as straight-line "
               "code; each if is a conditional link and its otherwise link, with the link ids in comments."
               if not _has(self.plan.flow, Queue) else
               "The TIBCO links are not straight-line (parallel links, joins or error links), so the steps "
               "run on ProcessFlow: a step runs once every live incoming link is decided.")
        cls = self.service_class
        mapping_mod = f"from {pkg}.core.{self.module}_mappings import {self.name}Mappings"
        cond_mod = f"from {pkg}.core.{self.module}_conditions import {self.name}Conditions"
        everything = "\n".join(lines + [x for m in self.group_methods + self.agent_methods for x in m])
        if f"{self.name}Mappings." in everything:
            extra.add(mapping_mod)
        if f"{self.name}Conditions." in everything:
            extra.add(cond_mod)
        out = [f'"""The TIBCO process {doc(self.p.name)} ({doc(self.p.id)}) as a service. {doc(how)}"""', "",
               *imports(extra), "", "LOG = logging.getLogger(__name__)", "", "",
               f"class {cls}:", f'    """Runs {doc(self.p.name)}; one instance per service, shared by every run."""', ""]
        if deps:
            params = ", ".join(f"{d.field}: Callable[[], {d.type}]" if d.lazy else f"{d.field}: {d.type}"
                               for d in deps)
            out += [f"    def __init__(self, *, {params}) -> None:"]
            out += [f"        self.{d.field} = {d.field}" for d in deps]
            out.append("")
        out += ["    async def execute(self, state: ProcessState) -> ProcessResult:",
                f'        """Runs one {doc(self.p.name)} job; the state holds what started it ({doc(self.starter_vars())})."""']
        out += _indent(lines, 8)
        for m in self.group_methods + self.agent_methods:
            out += [""] + m
        return "\n".join(out) + "\n", self.service_origin


# ------------------------------------------------------------------ helpers
def row_layer(row: MappingRow) -> str:
    """The layer folder name of a row's project (the last segment of ``src/<pkg>/<layer>``)."""
    return row.project.rsplit("/", 1)[-1]


def _commented(line: str) -> str:
    """``line`` commented out; a line that is already a comment keeps a single ``#``."""
    text = line.lstrip()
    if text.startswith("#"):
        return f"# {line[:len(line) - len(text)]}{text[1:].lstrip()}"
    return f"# {line}"


def _indent(lines: list[str], n: int = 4) -> list[str]:
    pad = " " * n
    return [f"{pad}{ln}" if ln else "" for ln in lines]


def _stmts(lines: list[str]) -> list[str]:
    """``lines`` with ``pass`` added when they hold no statement (only comments, or nothing)."""
    code = [ln for ln in lines if ln.strip() and not ln.lstrip().startswith("#")]
    return lines + ([] if code else ["pass"])


def _block(lines: list[str], n: int = 4) -> list[str]:
    """``lines`` as the body of a block: indented, ``pass`` added when it holds no statement."""
    return _indent(_stmts(lines), n)


def _loops(node: FlowNode) -> list[Loop]:
    out: list[Loop] = []
    if isinstance(node, Loop):
        out.append(node)
        out += _loops(node.body)
    elif isinstance(node, Seq):
        for i in node.items:
            out += _loops(i)
    elif isinstance(node, If):
        out += _loops(node.then) + _loops(node.otherwise)
    elif isinstance(node, Queue):
        for i in node.nodes:
            out += _loops(i)
    elif isinstance(node, Try):
        out += _loops(node.body)
        for _, f in node.catch:
            out += _loops(f)
        if node.catch_all is not None:
            out += _loops(node.catch_all)
    return out


def _has(node: FlowNode, kind: type) -> bool:
    if isinstance(node, kind):
        return True
    if isinstance(node, Seq):
        return any(_has(i, kind) for i in node.items)
    if isinstance(node, If):
        return _has(node.then, kind) or _has(node.otherwise, kind)
    if isinstance(node, Loop):
        return _has(node.body, kind)
    if isinstance(node, Try):
        return _has(node.body, kind) or any(_has(f, kind) for _, f in node.catch) or \
            (node.catch_all is not None and _has(node.catch_all, kind))
    return False
