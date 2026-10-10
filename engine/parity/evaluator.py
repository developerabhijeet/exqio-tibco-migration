"""The parity evaluator: expected output derived from the TIBCO model (design spec 6.1, 6.2).

``Evaluator.run`` executes a process of the IR the way the BusinessWorks engine would:

1. The starter input (a scenario dict) becomes the starter variable, qualified by the contract
   (``$Start``, ``$post.item``, ``$get.parameters``, ``$ReceivePayment/Body/tns:Payment``,
   BW5 ``$HTTP-Receiver/ProcessStarterOutput/parameters``).
2. Activities run in link order. The walk is BPEL's: an activity runs once all its incoming
   links are decided and at least one is true; when none is true it is skipped and its outgoing
   links die (dead-path elimination), which is how a join after a choice (four tier mappers
   into ``RecordDecision``) runs exactly once. Among ready activities the most recently
   enabled runs first, so parallel branches run one after another, in document order. A graph
   that stops with live activities still waiting (a cycle) raises :class:`EvaluationError`.
3. Each activity's input binding stylesheet runs through :func:`transform` against the current
   variables; the activity executes by canonical type; its output becomes ``$<ActivityName>``.
4. Outgoing links: success links fire; every conditional link whose expression is true fires
   (BW evaluates them all; overlapping conditions run several branches); the otherwise link
   fires when no conditional did; error links fire only when the activity failed (and then
   nothing else fires). Conditions use XPath's effective boolean value (a non-empty string,
   even ``"false"``, is true).
5. A failure without an error link raises a :class:`Fault` that propagates through
   ``call.process`` to the caller and to the nearest matching fault handler (by fault name,
   else catch-all); an unhandled fault surfaces in ``ExpectedResult.fault``.

Canonical types executed: see ``SUPPORTED_TYPES`` and ``NOOP_TYPES``; anything else raises
:class:`UnsupportedActivity` (never a silent guess).

Prefixes in transition conditions are not declared in BW6 process files (Studio writes them
as they appear in the mapper), so condition steps ``pfx:name`` are matched by local name
(``*:name``); functions (``pfx:name(``) keep their prefix.

A ``group.transaction`` that exits by fault rolls back, and its ``row_written`` side effects
are dropped from the result (JMS and HTTP effects are not transactional and stay).

Fields computed in XPath double arithmetic (``number(a) div number(b)``) are marked in
``TraceStep.numeric_kinds`` and listed in ``ExpectedResult.numeric_fields`` with the scale they
should be compared at (contract ``fractionDigits``, the column scale for rows, else ``None`` =
15 significant digits).

ASSUMPTIONS (from BW6 documentation as recalled, not evidenced in the estate):

* Send HTTP Request faults are named ``HttpClientException`` (4xx), ``HttpServerException``
  (5xx) and ``HttpCommunicationException`` (timeout, connection).
* A catch-all handler sees ``$FaultDetails`` / ``$_error`` shaped
  ``<FaultDetails><Msg/><MsgCode/><ActivityName/></FaultDetails>``, where ``Msg`` is the
  thrown fault's ``message`` field (else its name) and ``MsgCode`` the fault name.
"""
from __future__ import annotations

import copy
import datetime as dt
import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import psycopg
from lxml import etree

from engine.ir.models import Activity, Application, Group, Process, Resource, Transition
from engine.parity.compare import side_effect_prefixes
from engine.parity.db import ScenarioDB
from engine.parity.models import (
    ExpectedResult,
    Fault,
    Scenario,
    SideEffect,
    StubResponse,
    TraceStep,
    dict_to_xml,
    xml_to_dict,
)
from engine.parity.schema import (
    SchemaIndex,
    editor_href,
    rest_body_decl,
    rest_parameters_decl,
    typed_json,
)
from engine.parity.sql import sql_write_payload
from engine.parity.stubs import StubRegistry
from engine.parity.xpath import XPathContext
from engine.parity.xslt import XSL_NS, transform

_XSL = f"{{{XSL_NS}}}"

STARTER_TYPES = {"rest.receive", "http.receive", "soap.receive", "jms.receive", "timer.start"}
NOOP_TYPES = {"noop", "catch", "log", "wait", "checkpoint", "confirm", "engine.command"}
SUPPORTED_TYPES = STARTER_TYPES | NOOP_TYPES | {
    "jdbc.query", "jdbc.update", "jdbc.call",
    "http.invoke", "rest.invoke", "soap.invoke",
    "jms.send", "jms.publish", "jms.reply",
    "call.process", "mapper", "assign", "xslt.transform",
    "json.render", "json.parse", "xml.render", "xml.parse",
    "throw", "reply", "end",
}
GROUP_KINDS = {"loop", "iterate", "transaction", "critical", "parallel", "scope"}
MAX_LOOP_ITERATIONS = 1000

# BW6 Send HTTP Request fault names (palette documentation).
HTTP_TIMEOUT_FAULT = "HttpCommunicationException"
HTTP_CLIENT_FAULT = "HttpClientException"
HTTP_SERVER_FAULT = "HttpServerException"
JDBC_FAULT = "JDBCSQLException"


class EvaluationError(RuntimeError):
    """The model cannot be evaluated (bad expression, unknown process, missing binding)."""


class UnsupportedActivity(EvaluationError):
    """An activity type the evaluator does not execute."""


class ActivityFault(Exception):
    """A BW fault raised by an activity (``throw``, a failed call, a SQL error)."""

    def __init__(self, fault: Fault, element: etree._Element | None = None, activity_id: str = ""):
        super().__init__(fault.name)
        self.fault = fault
        self.element = element
        self.activity_id = activity_id


class _Rollback(Exception):
    pass


# --- helpers ------------------------------------------------------------------------


def _safe_tag(name: str) -> str:
    tag = re.sub(r"[^\w.-]", "-", name)
    return tag if re.match(r"[A-Za-z_]", tag) else f"_{tag}"


def bw5_var(name: str) -> str:
    """BW5 variable name of an activity: spaces become dashes (``$Standardize-Address``)."""
    return name.replace(" ", "-")


_VAR_REF = re.compile(r"\$([A-Za-z_][\w.-]*)")


def _var_refs(text: str) -> set[str]:
    return {n.rstrip(".-") for n in _VAR_REF.findall(text)}


def _local_steps(expr: str) -> str:
    """``$X/tns:a/ns2:b`` -> ``$X/*:a/*:b``; prefixed function calls and strings untouched."""
    out, i, n = [], 0, len(expr)
    while i < n:
        c = expr[i]
        if c in "'\"":
            j = expr.find(c, i + 1)
            j = n if j == -1 else j + 1
            out.append(expr[i:j])
            i = j
            continue
        m = re.match(r"([A-Za-z_][\w.-]*):([A-Za-z_][\w.-]*)", expr[i:])
        if m and (i == 0 or not (expr[i - 1].isalnum() or expr[i - 1] in "_.-$:")):
            rest = expr[i + m.end():].lstrip()
            prev = expr[:i].rstrip()
            is_call = rest.startswith("(")
            is_axis = expr[i + m.end():i + m.end() + 1] == ":"
            if not is_call and not is_axis and (prev.endswith(("/", "[", "(", ",", " ")) or prev == ""):
                out.append(f"*:{m.group(2)}")
                i += m.end()
                continue
            out.append(m.group(0))
            i += m.end()
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _first_element(value: Any) -> etree._Element | None:
    if isinstance(value, etree._Element):
        return value
    if isinstance(value, list) and value and isinstance(value[0], etree._Element):
        return value[0]
    return None


def _children(el: etree._Element) -> list[etree._Element]:
    return [c for c in el if isinstance(c.tag, str)]


def _child(el: etree._Element | None, name: str) -> etree._Element | None:
    if el is None:
        return None
    return next((c for c in _children(el) if etree.QName(c).localname == name), None)


def _db_text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, dt.datetime):
        return value.isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


def _jdbc_value(text: str | None, sql_type: str) -> Any:
    """Convert a bound parameter (XML text) to the Python value for its JDBC type."""
    t = (sql_type or "").upper()
    if text is None:
        return None
    if t in ("VARCHAR", "CHAR", "NVARCHAR", "NCHAR", "LONGVARCHAR", "CLOB", ""):
        return text
    if text.strip() == "":
        return None
    s = text.strip()
    if t in ("INTEGER", "SMALLINT", "BIGINT", "TINYINT"):
        return int(Decimal(s))
    if t in ("NUMERIC", "DECIMAL", "DOUBLE", "FLOAT", "REAL"):
        return Decimal(s)
    if t in ("BOOLEAN", "BIT"):
        return s.lower() in ("true", "1")
    if t == "DATE":
        return dt.date.fromisoformat(s[:10])
    if t in ("TIMESTAMP", "TIMESTAMP_WITH_TIMEZONE"):
        return dt.datetime.fromisoformat(s)
    if t == "TIME":
        return dt.time.fromisoformat(s)
    return s


def _json_safe(value: Any) -> Any:
    if isinstance(value, (Decimal, dt.date, dt.datetime, dt.time)):
        return _db_text(value)
    return value


# --- stubs ----------------------------------------------------------------------------


class _StubChain:
    """Scenario-defined stub responses first, then the evaluator's registry.

    ``used`` records the response each call resolved to (also for timeouts), so a generated
    scenario can store the exact stub responses it ran with.
    """

    def __init__(self, overlay: StubRegistry | None, base: StubRegistry):
        self.overlay = overlay
        self.base = base
        self.used: dict[str, StubResponse] = {}

    def call(self, resource_id: str, path: str, body: Any, timeout_ms: int | None) -> StubResponse:
        reg = self.base
        if self.overlay is not None:
            try:
                self.overlay._lookup(resource_id, path)
                reg = self.overlay
            except LookupError:
                pass
        matcher = getattr(reg, "match", None)
        planned = matcher(resource_id, path, body) if matcher else reg._lookup(resource_id, path)
        self.used[f"{resource_id}{path}"] = planned
        return reg.call(resource_id, path, body, timeout_ms)


# --- execution state ---------------------------------------------------------------------


@dataclass
class _Frame:
    process: Process
    variables: dict[str, Any]
    path: str  # trace prefix: "" for the scenario's process, "CallX/" inside a subprocess
    reply: tuple[Activity, etree._Element] | None = None
    end: etree._Element | None = None
    # variable name -> {result path (local names below the root): "double"}
    numeric: dict[str, dict[tuple[str, ...], str]] = field(default_factory=dict)
    reply_kinds: dict[tuple[str, ...], str] = field(default_factory=dict)
    end_kinds: dict[tuple[str, ...], str] = field(default_factory=dict)

    @property
    def bw5(self) -> bool:
        return self.process.dialect == "bw5"


@dataclass
class _Run:
    result: ExpectedResult
    stubs: _StubChain
    depth: int = 0
    # row side effects whose columns are double-derived: (effect, column, column scale)
    numeric_rows: list[tuple[SideEffect, str, int | None]] = field(default_factory=list)


class Evaluator:
    """Interpreter over the IR (``ExpectedOutputSource`` for the model-derived variant).

    ``isolate`` (default) runs each scenario inside one database transaction that is rolled
    back afterwards, so scenarios do not see each other's writes; seed rows from
    ``Scenario.seed_state`` are inserted inside it. ``now`` fixes ``current-dateTime()`` in
    transition conditions (bindings run in Saxon, whose clock is the wall clock).
    """

    source = "model"

    def __init__(
        self,
        app: Application,
        db: ScenarioDB,
        stubs: StubRegistry,
        *,
        isolate: bool = True,
        now: str | dt.datetime | None = None,
        schema: SchemaIndex | None = None,
    ) -> None:
        self.app = app
        self.db = db
        self.stubs = stubs
        self.isolate = isolate
        self.now = now
        self.schema = schema or SchemaIndex(app.contracts)
        self._processes = {p.id: p for p in app.processes}
        self._by_name = {p.name: p for p in app.processes}
        self._resources = {r.id: r for r in app.resources}
        self._sheets: dict[tuple[str, str, int], str] = {}
        self._columns: dict[str, dict[str, dict[str, Any]]] = {}
        self.last_stub_responses: dict[str, StubResponse] = {}

    # public API
    def process(self, process_id: str) -> Process:
        found = self._processes.get(process_id) or self._by_name.get(process_id)
        if found is None:
            raise EvaluationError(f"unknown process {process_id!r}")
        return found

    async def run(self, process_id: str, scenario: Scenario) -> ExpectedResult:
        process = self.process(process_id)
        overlay = (StubRegistry.from_stub_responses(scenario.stub_responses, self.stubs.timeout_ms)
                   if scenario.stub_responses else None)
        run = _Run(ExpectedResult(source="model"), _StubChain(overlay, self.stubs))
        with self._isolation():
            for seed in scenario.seed_state:
                self._insert_seed(seed.table, seed.row)
            frame = self._starter_frame(process, scenario.input, path="")
            try:
                await self._exec_process(run, frame)
            except ActivityFault as exc:
                run.result.fault = exc.fault
            run.result.output = self._output(frame)
            run.result.numeric_fields = self._numeric_fields(run, frame)
        self.last_stub_responses = dict(run.stubs.used)
        return run.result

    # isolation
    @contextmanager
    def _isolation(self) -> Iterator[None]:
        if not self.isolate:
            yield
            return
        try:
            with self.db.transaction():
                yield
                raise _Rollback
        except _Rollback:
            pass

    def _insert_seed(self, table: str, row: dict[str, Any]) -> None:
        def ident(name: str) -> str:
            return ".".join('"' + p.replace('"', '""') + '"' for p in name.split("."))

        cols = list(row)
        stmt = (f"insert into {ident(table)} ({', '.join(ident(c) for c in cols)}) "
                f"values ({', '.join('?' for _ in cols)})")
        self.db.execute(stmt, [json.dumps(v) if isinstance(v, (dict, list)) else v for v in row.values()])

    # substitution variables
    def substvars(self, process: Process) -> dict[str, str]:
        plain: dict[str, str] = {}
        scoped: dict[str, str] = {}
        prefix = f"//{process.module}//" if process.module else None
        for sv in self.app.substvars:
            if prefix and sv.name.startswith(prefix):
                scoped[sv.name[len(prefix):]] = sv.value
            elif not sv.name.startswith("//"):
                plain.setdefault(sv.name, sv.value)
        return {**plain, **scoped}

    # starter
    def _starter_frame(self, process: Process, data: dict[str, Any], path: str) -> _Frame:
        frame = _Frame(process, {}, path)
        starter = process.starter
        if starter is None:
            frame.variables["Start"] = self._start_element(process, data)
            return frame
        act = self._activity(process, starter.activity_id)
        if process.dialect == "bw5":
            frame.variables[bw5_var(act.name)] = self._bw5_starter(act, starter.kind, data)
            return frame
        if starter.kind == "subprocess":
            frame.variables["Start"] = self._start_element(process, data)
        elif starter.kind in ("rest", "http", "soap"):
            self._rest_starter(frame, act, data)
        elif starter.kind == "jms":
            href = editor_href(act, "bodyEditorElement")
            decl = self.schema.from_href(href) if href else None
            body = self.schema.build(decl, data, root=None if decl else "Body")
            out = etree.Element("ActivityOutput")
            holder = etree.SubElement(out, "Body")
            holder.append(body)
            frame.variables[act.name] = out
        else:  # timer and anything else: the scenario input is the event
            frame.variables[act.name] = dict_to_xml(data, "TimerOutput")
        return frame

    def _start_element(self, process: Process, data: dict[str, Any]) -> etree._Element:
        var = next((v for v in process.variables if v.name == "Start"), None)
        decl = self.schema.from_qname(var.schema if var else None)
        return self.schema.build(decl, data, root=None if decl else "Start")

    def _rest_starter(self, frame: _Frame, act: Activity, data: dict[str, Any]) -> None:
        verb = str(act.config.get("verb", "POST")).upper()
        if set(data) <= {"body", "parameters"} and data:
            body, params = data.get("body"), data.get("parameters")
        elif verb in ("GET", "DELETE", "HEAD"):
            body, params = None, data
        else:
            body, params = data, None
        wrapper = etree.Element(_safe_tag(f"{act.name}Request"))
        if body is not None:
            decl = rest_body_decl(act, self.app, self.schema)
            item = self.schema.build(decl, body, root=None if decl else "item")
            etree.SubElement(wrapper, "item").append(copy.deepcopy(item))
            frame.variables[f"{act.name}.item"] = item
        if params is not None:
            decl = rest_parameters_decl(act, frame.process, self.schema)
            el = self.schema.build(decl, params, root=None if decl else "parameters")
            etree.SubElement(wrapper, "parameters").append(copy.deepcopy(el))
            frame.variables[f"{act.name}.parameters"] = el
        frame.variables[act.name] = wrapper

    def _bw5_starter(self, act: Activity, kind: str, data: dict[str, Any]) -> etree._Element:
        wrapper = etree.Element(bw5_var(act.name))
        if kind == "http":
            wrapper.append(dict_to_xml({"parameters": data}, "ProcessStarterOutput"))
        else:
            wrapper.append(dict_to_xml(data, "ProcessStarterOutput"))
        return wrapper

    @staticmethod
    def _activity(process: Process, activity_id: str) -> Activity:
        act = next((a for a in process.activities if a.id == activity_id), None)
        if act is None:
            raise EvaluationError(f"{process.id}: no activity {activity_id!r}")
        return act

    # process execution
    async def _exec_process(self, run: _Run, frame: _Frame) -> None:
        process = frame.process
        handler_ids = {a for h in process.fault_handlers for a in h.activity_ids}
        nodes = self._container_nodes(process, None, exclude=handler_ids)
        try:
            await self._run_graph(run, frame, nodes)
        except ActivityFault as exc:
            handler = next((h for h in process.fault_handlers if h.fault_name == exc.fault.name), None)
            handler = handler or next((h for h in process.fault_handlers if h.fault_name is None), None)
            if handler is None:
                raise
            self._bind_fault(frame, exc)
            run.result.trace.append(TraceStep(activity_id=f"{frame.path}{handler.id}",
                                              input={"fault": exc.fault.model_dump()},
                                              chosen_transition=None))
            await self._run_graph(run, frame, [self._node(process, a) for a in handler.activity_ids])

    def _bind_fault(self, frame: _Frame, exc: ActivityFault) -> None:
        el = exc.element if exc.element is not None else dict_to_xml(exc.fault.data, exc.fault.name)
        frame.variables[exc.fault.name] = el
        details = etree.Element("FaultDetails")
        msg = exc.fault.data.get("message") if isinstance(exc.fault.data, dict) else None
        etree.SubElement(details, "Msg").text = str(msg or exc.fault.name)
        etree.SubElement(details, "MsgCode").text = exc.fault.name
        etree.SubElement(details, "ActivityName").text = exc.activity_id
        frame.variables["FaultDetails"] = details
        frame.variables["_error"] = details

    # graph
    def _container_of(self, process: Process, node_id: str) -> str | None:
        groups = {g.id: g for g in process.groups}
        if node_id in groups:
            parent = groups[node_id].config.get("parent_group")
        else:
            act = next((a for a in process.activities if a.id == node_id), None)
            parent = act.group_id if act else None
        while parent in groups and groups[parent].config.get("synthesised"):
            parent = groups[parent].config.get("parent_group")
        return parent

    def _container_nodes(self, process: Process, container: str | None,
                         exclude: set[str] = frozenset()) -> list[Activity | Group]:
        nodes: list[Activity | Group] = []
        for a in process.activities:
            if a.id not in exclude and self._container_of(process, a.id) == container:
                nodes.append(a)
        for g in process.groups:
            if g.config.get("synthesised"):
                continue
            if self._container_of(process, g.id) == container:
                nodes.append(g)
        order = self._doc_order(process)
        nodes.sort(key=lambda n: order.get(n.id, 1 << 30))
        return nodes

    @staticmethod
    def _doc_order(process: Process) -> dict[str, int]:
        order = {a.id: i for i, a in enumerate(process.activities)}
        for g in process.groups:
            positions = [order[m] for m in g.member_ids if m in order]
            order[g.id] = min(positions) - 0.5 if positions else len(order)
        return order

    @staticmethod
    def _node(process: Process, node_id: str) -> Activity | Group:
        for a in process.activities:
            if a.id == node_id:
                return a
        for g in process.groups:
            if g.id == node_id:
                return g
        raise EvaluationError(f"{process.id}: no node {node_id!r}")

    async def _run_graph(self, run: _Run, frame: _Frame, nodes: list[Activity | Group]) -> None:
        process = frame.process
        ids = {n.id for n in nodes}
        index = {n.id: i for i, n in enumerate(nodes)}
        incoming: dict[str, list[Transition]] = {n.id: [] for n in nodes}
        outgoing: dict[str, list[Transition]] = {n.id: [] for n in nodes}
        for t in process.transitions:
            if t.from_id in ids and t.to_id in ids:
                incoming[t.to_id].append(t)
                outgoing[t.from_id].append(t)
        links: dict[str, bool] = {}
        enabled = {n.id: 0 for n in nodes if not incoming[n.id]}
        seq = 0
        pending = set(ids)
        while pending:
            ready = [n for n in pending if n in enabled]
            if not ready:
                self._check_dead(frame, pending, incoming, links)
                break
            node_id = max(ready, key=lambda n: (enabled[n], -index[n]))
            pending.discard(node_id)
            node = nodes[index[node_id]]
            alive = not incoming[node_id] or any(links.get(t.id) for t in incoming[node_id])
            if alive:
                fired = await self._run_node(run, frame, node, outgoing[node_id])
            else:
                fired = set()
            seq += 1  # successors enabled by one completion share a rank: document order decides
            for t in outgoing[node_id]:
                links[t.id] = t.id in fired
                if all(x.id in links for x in incoming[t.to_id]) and t.to_id not in enabled:
                    enabled[t.to_id] = seq

    @staticmethod
    def _check_dead(frame: _Frame, pending: set[str], incoming: dict[str, list[Transition]],
                    links: dict[str, bool]) -> None:
        """Waiting activities are fine only if none can ever run (no true link reaches them)."""
        dead = {n for n in pending if not any(links.get(t.id) for t in incoming[n])}
        changed = True
        while changed:
            changed = False
            for n in list(dead):
                if any(t.id not in links and t.from_id not in dead for t in incoming[n]):
                    dead.discard(n)
                    changed = True
        if dead != pending or not links:
            stuck = sorted(pending - dead) or sorted(pending)
            raise EvaluationError(f"{frame.process.id}: activities {stuck} wait on links that never "
                                  "resolve (a cycle in the process graph)")

    async def _run_node(self, run: _Run, frame: _Frame, node: Activity | Group,
                        outgoing: list[Transition]) -> set[str]:
        step = TraceStep(activity_id=f"{frame.path}{node.id}")
        run.result.trace.append(step)
        try:
            if isinstance(node, Group):
                await self._run_group(run, frame, node, step)
            else:
                await self._run_activity(run, frame, node, step)
        except ActivityFault as exc:
            errors = [t for t in outgoing if t.kind == "error"]
            if not errors:
                step.output = {"fault": exc.fault.model_dump()}
                raise
            self._bind_fault(frame, exc)
            frame.variables[f"_error_{node.id}"] = frame.variables["_error"]
            step.output = {"fault": exc.fault.model_dump()}
            step.chosen_transition = ",".join(t.id for t in errors)
            return {t.id for t in errors}
        fired = self._choose(frame, outgoing)
        step.chosen_transition = ",".join(t.id for t in outgoing if t.id in fired) or None
        return fired

    def _choose(self, frame: _Frame, outgoing: list[Transition]) -> set[str]:
        fired: set[str] = set()
        conditional_true = False
        for t in outgoing:
            if t.kind == "conditional" and t.expression and self._condition(frame, t.expression):
                fired.add(t.id)
                conditional_true = True
        for t in outgoing:
            if t.kind == "success" or t.kind == "otherwise" and not conditional_true:
                fired.add(t.id)
        return fired

    def _context(self, frame: _Frame) -> XPathContext:
        return XPathContext(frame.variables, self.substvars(frame.process), now=self.now)

    def _condition(self, frame: _Frame, expression: str) -> bool:
        ctx = self._context(frame)
        try:  # effective boolean value, as BW does: boolean() of the expression
            value = ctx.evaluate(f"boolean({_local_steps(expression)})")
        except Exception as exc:
            raise EvaluationError(f"{frame.process.id}: cannot evaluate {expression!r}: {exc}") from exc
        return value is True

    # groups
    async def _run_group(self, run: _Run, frame: _Frame, group: Group, step: TraceStep) -> None:
        members = self._container_nodes(frame.process, group.id)
        if group.kind == "transaction":
            mark = len(run.result.side_effects)
            try:
                with self.db.transaction():
                    await self._run_graph(run, frame, members)
            except (ActivityFault, EvaluationError):
                # rolled back: the rows were never written; messages and calls did happen
                kept = run.result.side_effects[mark:]
                run.result.side_effects[mark:] = [e for e in kept if e.kind != "row_written"]
                raise
        elif group.kind == "iterate":
            ctx = self._context(frame)
            expr = group.config.get("variable_list")
            items: list[Any]
            if expr:
                raw = ctx.evaluate_items(_local_steps(expr))
                items = raw if isinstance(raw, list) else [raw]
            else:
                start = int(ctx.evaluate(group.config.get("start_counter") or "1"))
                final = int(ctx.evaluate(group.config.get("final_counter") or "0"))
                items = list(range(start, final + 1))
            counter = group.config.get("counter") or "index"
            current = (group.config.get("current_element") or {}).get("variable")
            step.input = {"items": len(items)}
            for i, item in enumerate(items, start=1):
                frame.variables[counter] = i
                if current:
                    frame.variables[current] = item
                await self._run_graph(run, frame, members)
        elif group.kind == "loop":
            cond = group.config.get("condition")
            style = str(group.config.get("loop", "while"))
            counter = group.config.get("counter")
            for i in range(1, MAX_LOOP_ITERATIONS + 1):
                if counter:
                    frame.variables[counter] = i
                if style != "repeatUntil" and cond and not self._condition(frame, cond):
                    break
                await self._run_graph(run, frame, members)
                if style == "repeatUntil" and (not cond or self._condition(frame, cond)):
                    break
                if not cond:
                    break
            else:
                raise EvaluationError(f"{group.id}: loop exceeded {MAX_LOOP_ITERATIONS} iterations")
        else:  # parallel (flow), critical, scope: members in link order
            await self._run_graph(run, frame, members)

    # activities
    def _stylesheet(self, frame: _Frame, act: Activity, key: str = "input_xslt") -> str | None:
        raw = act.config.get(key)
        if isinstance(raw, list):
            raw = raw[0] if raw else None
        if not raw:
            return None
        if not frame.bw5:
            return raw
        cache_key = (frame.process.id, act.id, hash(raw))
        if cache_key not in self._sheets:
            self._sheets[cache_key] = _declare_params(raw)
        return self._sheets[cache_key]

    def _bind_input(self, frame: _Frame, act: Activity, key: str = "input_xslt") -> etree._Element | None:
        sheet = self._stylesheet(frame, act, key)
        if sheet is None:
            return None
        try:
            return transform(sheet, self._context(frame))
        except Exception as exc:
            raise EvaluationError(f"{frame.process.id}/{act.id}: input binding failed: {exc}") from exc

    def _bind_output(self, frame: _Frame, act: Activity, output: etree._Element | None) -> None:
        if output is None:
            output = etree.Element(_safe_tag(f"{act.name}Output"))
        if frame.bw5:
            wrapper = etree.Element(bw5_var(act.name))
            wrapper.append(output)
            frame.variables[bw5_var(act.name)] = wrapper
        else:
            frame.variables[act.name] = output

    async def _run_activity(self, run: _Run, frame: _Frame, act: Activity, step: TraceStep) -> None:
        kind = act.type
        if kind not in SUPPORTED_TYPES:
            raise UnsupportedActivity(f"{frame.process.id}/{act.id}: activity type {kind!r} "
                                      f"({act.native_type}) is not supported by the evaluator")
        if kind in STARTER_TYPES:
            var = bw5_var(act.name) if frame.bw5 else act.name
            el = frame.variables.get(var)
            step.output = _as_dict(el)
            return
        inp = None if kind == "reply" and not act.config.get("input_xslt") else self._bind_input(frame, act)
        step.input = _as_dict(inp)
        kinds = self._numeric_kinds(frame, act) if inp is not None else {}
        step.numeric_kinds = {"/".join(k): v for k, v in kinds.items()}
        output: etree._Element | None = None
        if kind in NOOP_TYPES:
            output = None
        elif kind in ("mapper", "assign", "xslt.transform"):
            output = inp
            if kind == "assign" and act.config.get("variable") and inp is not None:
                frame.variables[act.config["variable"]] = inp
        elif kind.startswith("jdbc."):
            output = self._jdbc(run, frame, act, inp, kinds)
        elif kind in ("http.invoke", "rest.invoke", "soap.invoke"):
            output = self._http(run, frame, act, inp)
        elif kind in ("jms.send", "jms.publish", "jms.reply"):
            self._jms(run, frame, act, inp)
        elif kind == "json.render":
            output = self._json_render(inp)
        elif kind == "json.parse":
            output = self._json_parse(act, inp)
        elif kind == "xml.render":
            output = etree.Element("ActivityOutput")
            src = _children(inp)[0] if inp is not None and _children(inp) else inp
            etree.SubElement(output, "xmlString").text = (
                etree.tostring(src, encoding="unicode") if src is not None else "")
        elif kind == "xml.parse":
            text = _child(inp, "xmlString")
            output = etree.fromstring((text.text or "").encode()) if text is not None else None
        elif kind == "call.process":
            output, kinds = await self._call(run, frame, act, inp)
        elif kind == "throw":
            data = xml_to_dict(inp) if inp is not None else {}
            name = act.config.get("fault_name") or (etree.QName(inp).localname if inp is not None else "Fault")
            step.output = {"fault": {"name": name, "data": data}}
            raise ActivityFault(Fault(name=name, data=data), inp, act.id)
        elif kind == "reply":
            if inp is None:
                inp = self._bind_input(frame, act, "message_xslt")
                step.input = _as_dict(inp)
                kinds = self._numeric_kinds(frame, act, "message_xslt") if inp is not None else {}
                step.numeric_kinds = {"/".join(k): v for k, v in kinds.items()}
            if frame.reply is None:
                frame.reply = (act, inp if inp is not None else etree.Element("empty"))
                frame.reply_kinds = kinds
                step.output = self._reply_output(frame, act, frame.reply[1])
        elif kind == "end":
            frame.end = inp
            frame.end_kinds = kinds
            output = inp
        if kind not in NOOP_TYPES:  # a no-op (BW6 StartEvent, Empty, Log) has no output variable
            self._bind_output(frame, act, output)
            if kind in ("mapper", "assign", "xslt.transform", "end", "call.process") and kinds:
                frame.numeric[bw5_var(act.name) if frame.bw5 else act.name] = kinds
        if not step.output:
            step.output = _as_dict(output)

    # JDBC
    def _jdbc(self, run: _Run, frame: _Frame, act: Activity, inp: etree._Element | None,
              kinds: dict[tuple[str, ...], str] | None = None) -> etree._Element:
        sql = act.config.get("sql") or (act.config.get("settings") or {}).get("sqlStatement")
        if not sql:
            raise EvaluationError(f"{frame.process.id}/{act.id}: no SQL statement")
        params = act.config.get("parameters") or []
        values = []
        for p in params:
            el = _child(inp, p.get("name", ""))
            values.append(_jdbc_value(None if el is None else (el.text or ""), p.get("type", "")))
        try:
            with self.db.transaction():
                result = self.db.execute(sql, values if params else None)
        except psycopg.Error as exc:
            raise ActivityFault(Fault(name=JDBC_FAULT, data={"message": str(exc).strip(),
                                                             "sqlState": exc.sqlstate or ""}),
                                activity_id=act.id) from exc
        if act.type == "jdbc.update":
            _, table, row, sources = sql_write_payload(sql, values)
            row = self.as_stored(table, row)
            effect = SideEffect(kind="row_written", target=table,
                                payload={k: _json_safe(v) for k, v in row.items()},
                                activity_id=f"{frame.path}{act.id}")
            run.result.side_effects.append(effect)
            index = 0
            for col, expr in sources.items():  # double-derived parameters -> column scale
                if expr == "?" and index < len(params) and (kinds or {}).get((params[index].get("name"),)):
                    meta = self._columns.get(table, {}).get(col.removeprefix("where.")) or {}
                    scale = meta.get("numeric_scale")
                    run.numeric_rows.append((effect, col, int(scale) if scale is not None else None))
                index += expr.count("?")
            out = etree.Element("jdbcUpdateActivityOutput")
            etree.SubElement(out, "noOfUpdates").text = str(result if isinstance(result, int) else 0)
            return out
        rows = result if isinstance(result, list) else []
        max_rows = int(str(act.config.get("max_rows") or (act.config.get("settings") or {}).get("maxRows") or 0))
        if max_rows > 0:
            rows = rows[:max_rows]
        out = etree.Element("resultSet")
        for r in rows:
            rec = etree.SubElement(out, "Record")
            for col, val in r.items():
                if val is not None:
                    etree.SubElement(rec, col).text = _db_text(val)
        return out

    def as_stored(self, table: str, row: dict[str, Any]) -> dict[str, Any]:
        """Round values the way the column stores them (``numeric(8,4)`` -> 4 places).

        Public so the parity runner can quantise rows read back from the .NET side the same way.
        """
        if table not in self._columns:
            try:
                info = self.db.execute(
                    "select column_name, data_type, numeric_scale from information_schema.columns "
                    "where table_name = ? and table_schema = any(current_schemas(false))", [table])
            except psycopg.Error:
                info = []
            self._columns[table] = {r["column_name"]: r for r in info} if isinstance(info, list) else {}
        cols = self._columns[table]
        out = {}
        for key, value in row.items():
            meta = cols.get(key.removeprefix("where."))
            if meta and isinstance(value, Decimal) and meta.get("numeric_scale") is not None:
                value = value.quantize(Decimal(1).scaleb(-int(meta["numeric_scale"])))
            elif meta and isinstance(value, str) and meta.get("data_type") == "numeric" \
                    and meta.get("numeric_scale") is not None and re.fullmatch(r"-?\d+(\.\d+)?", value):
                value = Decimal(value).quantize(Decimal(1).scaleb(-int(meta["numeric_scale"])))
            out[key] = value
        return out

    # HTTP
    def _http(self, run: _Run, frame: _Frame, act: Activity, inp: etree._Element | None) -> etree._Element:
        resource = self._resource(act)
        method = (_text_of(inp, "Method") or act.config.get("verb") or "POST").upper()
        path = _text_of(inp, "RequestURI") or act.config.get("path") or "/"
        query = _text_of(inp, "QueryString")
        raw_body = _text_of(inp, "PostData")
        body: Any
        if raw_body is not None:
            try:
                body = json.loads(raw_body)
            except ValueError:
                body = raw_body
        else:
            body = xml_to_dict(inp) if inp is not None else None
        timeout = (resource.settings.get("timeout_ms") if resource else None) or self.stubs.timeout_ms
        resource_id = resource.id if resource else act.config.get("resource_ref", act.id)
        target = f"{resource.name if resource else resource_id} {method} {path}"
        payload: dict[str, Any] = {"request": body}
        if query:
            payload["query"] = query
        try:
            response = run.stubs.call(resource_id, path, body, int(timeout))
        except TimeoutError as exc:
            payload["outcome"] = "timeout"
            run.result.side_effects.append(SideEffect(kind="service_called", target=target, payload=payload,
                                                      activity_id=f"{frame.path}{act.id}"))
            raise ActivityFault(Fault(name=HTTP_TIMEOUT_FAULT, data={"message": str(exc)}),
                                activity_id=act.id) from exc
        except LookupError as exc:
            raise EvaluationError(f"{frame.process.id}/{act.id}: {exc}") from exc
        payload["status"] = response.status
        run.result.side_effects.append(SideEffect(kind="service_called", target=target, payload=payload,
                                                  activity_id=f"{frame.path}{act.id}"))
        text = json.dumps(response.body) if response.body is not None else ""
        if response.status >= 400:
            name = HTTP_SERVER_FAULT if response.status >= 500 else HTTP_CLIENT_FAULT
            raise ActivityFault(Fault(name=name, data={"statusCode": response.status, "message": text}),
                                activity_id=act.id)
        out = etree.Element("RequestActivityOutput")
        status_line = etree.SubElement(out, "statusLine")
        etree.SubElement(status_line, "statusCode").text = str(response.status)
        etree.SubElement(out, "asciiContent").text = text
        if act.type != "http.invoke" and isinstance(response.body, dict):
            out.append(dict_to_xml(response.body, "body"))
        return out

    def _resource(self, act: Activity) -> Resource | None:
        ref = act.config.get("resource_ref")
        return self._resources.get(ref) if ref else None

    # JMS
    def _jms(self, run: _Run, frame: _Frame, act: Activity, inp: etree._Element | None) -> None:
        destination = act.config.get("destination") or (act.config.get("settings") or {}).get("destination", "")
        body = _child(inp, "Body")
        content = _children(body)[0] if body is not None and _children(body) else body
        payload = xml_to_dict(content) if content is not None else {}
        if content is not None and not _children(content):
            payload = {"text": (content.text or "")}
        run.result.side_effects.append(SideEffect(
            kind="message_published", target=destination, payload=payload,
            activity_id=f"{frame.path}{act.id}"))

    # JSON
    def _json_render(self, inp: etree._Element | None) -> etree._Element:
        out = etree.Element("ActivityOutputClass")
        if inp is None:
            etree.SubElement(out, "jsonString").text = "{}"
            return out
        q = etree.QName(inp)
        decl = self.schema.element(q.namespace, q.localname)
        etree.SubElement(out, "jsonString").text = json.dumps(typed_json(inp, decl), separators=(",", ":"))
        return out

    def _json_parse(self, act: Activity, inp: etree._Element | None) -> etree._Element:
        text = _text_of(inp, "jsonString") or "{}"
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise ActivityFault(Fault(name="JSONParserException", data={"message": str(exc)}),
                                activity_id=act.id) from exc
        href = editor_href(act, "outputEditorElement")
        decl = self.schema.from_href(href) if href else None
        return self.schema.build(decl, data if isinstance(data, dict) else {"value": data},
                                 root=None if decl else "Result")

    # subprocess
    async def _call(self, run: _Run, frame: _Frame, act: Activity, inp: etree._Element | None
                    ) -> tuple[etree._Element, dict[tuple[str, ...], str]]:
        target = next((c.target_process for c in frame.process.calls if c.activity_id == act.id), None)
        target = target or act.config.get("sub_process") or act.config.get("subprocess")
        if not target:
            raise EvaluationError(f"{frame.process.id}/{act.id}: call.process without a target")
        sub = self.process(target)
        if run.depth > 32:
            raise EvaluationError(f"{act.id}: subprocess recursion deeper than 32")
        child = _Frame(sub, {}, f"{frame.path}{act.id}/")
        start = inp if inp is not None else etree.Element("Start")
        child.variables["Start"] = start
        run.result.side_effects.append(SideEffect(
            kind="subprocess_called", target=sub.id, payload=xml_to_dict(start),
            activity_id=f"{frame.path}{act.id}"))
        run.depth += 1
        try:
            await self._exec_process(run, child)
        finally:
            run.depth -= 1
        return (child.end if child.end is not None else etree.Element("End")), child.end_kinds

    # numeric kinds
    def _numeric_kinds(self, frame: _Frame, act: Activity, key: str = "input_xslt"
                       ) -> dict[tuple[str, ...], str]:
        """Result fields of ``act``'s binding computed in XPath double arithmetic."""
        sheet = self._stylesheet(frame, act, key)
        if not sheet:
            return {}
        _, leaves = binding_leaves(sheet)
        out: dict[tuple[str, ...], str] = {}
        for steps, expr in leaves:
            if is_double_expression(expr):
                out[steps] = "double"
                continue
            m = re.fullmatch(r"\s*\$([A-Za-z_][\w.-]*)((?:/[\w.*:-]+)*)\s*", expr)
            if m:
                path = tuple(x.split(":")[-1] for x in m.group(2).split("/") if x)
                if frame.numeric.get(m.group(1), {}).get(path):
                    out[steps] = "double"
        return out

    def _numeric_fields(self, run: _Run, frame: _Frame) -> dict[str, int | None]:
        fields: dict[str, int | None] = {}
        producer: Activity | None
        if frame.reply is not None:
            producer, kinds = frame.reply[0], frame.reply_kinds
            op = str(producer.config.get("operation") or "")
            wrappers = {f"{op}Response", f"{op}4XXFaultMessage", f"{op}5XXFaultMessage", "item",
                        "clientError", "serverError"}
            prefix = "output.body"
        else:
            kinds, wrappers, prefix = frame.end_kinds, set(), "output"
            producer = next((a for a in frame.process.activities if a.type == "end"), None)
        sheet = self._stylesheet(frame, producer, "input_xslt") if producer is not None else None
        root = binding_leaves(sheet)[0] if sheet else None
        decl = self.schema.element(*root) if root else None
        for steps in kinds:
            body = list(steps)
            while body and body[0] in wrappers:
                body.pop(0)
            if not body:
                continue
            leaf = decl.at(body) if decl is not None else None
            fields[prefix + "." + ".".join(body)] = leaf.fraction_digits if leaf is not None else None
        for path, effect in side_effect_prefixes(run.result.side_effects):
            for e, col, scale in run.numeric_rows:
                if e is effect:
                    fields[f"{path}.{col}"] = scale
        return fields

    # output
    def _output(self, frame: _Frame) -> dict[str, Any]:
        if frame.reply is not None:
            act, el = frame.reply
            return self._reply_output(frame, act, el)
        if frame.end is not None:
            return xml_to_dict(frame.end)
        return {}

    @staticmethod
    def _reply_output(frame: _Frame, act: Activity, el: etree._Element) -> dict[str, Any]:
        if frame.bw5 or _child(el, "asciiContent") is not None:
            status = 200
            line = _text_of(_child(el, "Headers"), "StatusLine") or ""
            m = re.search(r"\s(\d{3})\b", line)
            if m:
                status = int(m.group(1))
            content = _text_of(el, "asciiContent") or ""
            try:
                body: Any = xml_to_dict(etree.fromstring(content.encode("utf-8")))
            except etree.XMLSyntaxError:
                try:
                    body = json.loads(content)
                except ValueError:
                    body = content
            return {"status": status, "body": body}
        content = el
        # a full-message reply (message_xslt) wraps the part: <opResponse><item><X/></item>
        op = str(act.config.get("operation") or "")
        wrappers = {f"{op}Response", f"{op}4XXFaultMessage", f"{op}5XXFaultMessage", "item",
                    "clientError", "serverError"}
        while etree.QName(content).localname in wrappers and len(_children(content)) == 1:
            content = _children(content)[0]
        body = xml_to_dict(content)
        status = 200
        if act.config.get("fault_name"):
            code = body.get("statusCode") if isinstance(body, dict) else None
            status = int(code) if code and str(code).isdigit() else (
                500 if act.config["fault_name"] == "serverFault" else 400)
        return {"status": status, "body": body}


def _text_of(el: etree._Element | None, name: str) -> str | None:
    c = _child(el, name)
    return None if c is None else (c.text or "")


def _as_dict(el: Any) -> dict[str, Any]:
    el = _first_element(el)
    if el is None:
        return {}
    data = xml_to_dict(el)
    if not data and (el.text or "").strip():
        return {"#text": el.text.strip()}
    return data if isinstance(data, dict) else {"value": data}


def _declare_params(stylesheet: str) -> str:
    """BW5 binding stylesheets read ``$Var`` without declaring it: add the params (XSLT 2.0)."""
    sheet = etree.fromstring(stylesheet.encode("utf-8"))
    declared = {el.get("name") for el in sheet.iter(f"{_XSL}param", f"{_XSL}variable")}
    refs: set[str] = set()
    for el in sheet.iter():
        if isinstance(el.tag, str):
            for value in el.attrib.values():
                refs |= _var_refs(value)
    first = next((i for i, c in enumerate(sheet) if isinstance(c.tag, str)), 0)
    for name in sorted(refs - declared):
        param = etree.Element(f"{_XSL}param", name=name)
        sheet.insert(first, param)
    sheet.set("version", "2.0")
    return etree.tostring(sheet, encoding="unicode")


def binding_leaves(stylesheet: str) -> tuple[tuple[str | None, str] | None, list[tuple[tuple[str, ...], str]]]:
    """``(root (ns, local), [(path below the root, select)])`` of a binding stylesheet.

    ``xsl:variable`` references are expanded (three levels), so ``$feeApplied`` shows the
    arithmetic it stands for.
    """
    try:
        sheet = etree.fromstring(stylesheet.encode("utf-8"))
    except etree.XMLSyntaxError:
        return None, []
    variables = {v.get("name"): v.get("select") or "" for v in sheet.iter(f"{_XSL}variable")}

    def expand(expr: str) -> str:
        for _ in range(3):
            expr = re.sub(r"\$([A-Za-z_][\w.-]*)",
                          lambda m: f"({variables[m.group(1)]})" if m.group(1) in variables else m.group(0),
                          expr)
        return expr

    template = next(sheet.iter(f"{_XSL}template"), None)
    if template is None:
        return None, []
    roots = list(_literal_children(template))
    if not roots:
        return None, []
    root = roots[0]
    q = etree.QName(root)
    leaves: list[tuple[tuple[str, ...], str]] = []

    def walk(el: etree._Element, path: tuple[str, ...]) -> None:
        selects = [v.get("select") for v in el.iter(f"{_XSL}value-of")
                   if _owner(v) is el and v.get("select")]
        if selects:
            leaves.append((path, expand(selects[0])))
        for child in _literal_children(el):
            walk(child, (*path, etree.QName(child).localname))

    for child in _literal_children(root):
        walk(child, (etree.QName(child).localname,))
    return (q.namespace, q.localname), leaves


def _literal_children(el: etree._Element):
    for c in el:
        if not isinstance(c.tag, str):
            continue
        if c.tag.startswith(_XSL):
            if etree.QName(c).localname in ("if", "choose", "when", "otherwise", "for-each"):
                yield from _literal_children(c)
            continue
        yield c


def _owner(node: etree._Element) -> etree._Element | None:
    """Nearest literal (non-XSL) ancestor of an XSL instruction."""
    cur = node.getparent()
    while cur is not None and isinstance(cur.tag, str) and cur.tag.startswith(_XSL):
        cur = cur.getparent()
    return cur


_STRINGS = re.compile(r"'[^']*'|\"[^\"]*\"")


def is_double_expression(expr: str) -> bool:
    """XPath arithmetic on ``number()`` / ``xs:double`` (IEEE double, not xs:decimal)."""
    text = _STRINGS.sub("''", expr)
    if not re.search(r"\bnumber\(|\bxsd?:(?:double|float)\(", text):
        return False
    return bool(re.search(r"\bdiv\b|\bmod\b|[*+]|\s-\s|\)\s*-", text))
