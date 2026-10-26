"""``ServicePlan``: one process as the Java, Go and Python generators print it (target-stacks spec section 2).

Built by code from the IR and the stack-aware design record, with no target language in it:

* ``steps``: one ``PlanStep`` per activity, in the design's walk order (``design._walk_order``),
  each with its mapping row, its input bindings as ``Assign`` (the expression parsed by
  ``expr.parse_simple``, ``None`` where code cannot write it), its SQL verbatim, what it calls and
  what it throws;
* ``flow``: how the steps run, derived from the links exactly as the .NET generator derives its
  service body. Links forming sequences and if/else regions give ``Seq`` / ``If`` (the .NET
  ``_Structured`` rules, including the immediate post-dominator as the merge of an if/else); any
  other graph (parallel links, joins, error links, several conditional links on one step) gives a
  ``Queue`` of the nodes and links, which a printer runs like ``ProcessFlow``: a node runs once
  every live incoming link is decided. A group is a ``Loop`` around its members' own flow; fault
  handlers wrap the whole flow in a ``Try``;
* ``agent``: true on a step whose body is left to the Code Builder from the expressions' shape
  alone (its mapping has an expression ``is_simple_expression`` refuses, it is a pattern with no
  code template, a stored-procedure call, a SOAP starter or a JMS selector code cannot read). A
  condition whose shape code cannot write is an ``If`` (or a ``QueueLink``) whose ``cond`` is
  ``None``, with the TIBCO text kept.

The agent flags are a LOWER BOUND. ``agent=False`` and a non-``None`` ``cond`` say only that the
expression has a shape code can write; whether its paths resolve against the contracts and the
state is the generator's call, as it is in .NET (``ProcGen.map_origin``, ``translate_condition``).
A stack generator marks the step, its row and its file ``agent`` whenever its own path resolution
or condition translation fails, whatever the plan says; it never writes code for a step the plan
flags ``agent``. ``test_stacks_plan`` pins the bound: every plan flag is also a .NET agent body on
the whole sample estate.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from lxml import etree

from engine.dotnet_gen.design import _walk_order, is_simple_expression
from engine.dotnet_gen.models import ConfigMap, DesignRecord, MappingRow, MessagingEntity
from engine.ir.models import Activity, Application, Process, Transition
from engine.stacks.contracts import ContractModel, contract_model, plan_contracts
from engine.stacks.expr import Expr, parse_simple

# ------------------------------------------------------------------ the plan


@dataclass(frozen=True)
class Assign:
    target: str  # the TIBCO binding target, e.g. ``jdbcQueryActivityInput/dealId``
    source: Expr | None  # None: code cannot write it, the agent fills it
    tibco: str  # the TIBCO expression verbatim (kept as a comment)


@dataclass
class PlanStep:
    activity_id: str
    name: str
    pattern: str  # the mapping row's pattern (``patterns.PATTERNS`` key)
    row: MappingRow  # the primary row: file, class and method in the target stack
    inputs: list[Assign]
    sql: str | None  # JDBC steps: the statement verbatim, ``?`` placeholders (``sql.convert_placeholders``)
    # What the step calls: the target process id of a call.process, the resource of an invoke, the
    # destination of a JMS send or publish.
    call_target: str | None
    throws: str | None  # a throw: the TIBCO fault name
    agent: bool  # a lower bound (module docstring): the generator may still find it needs the agent


@dataclass(frozen=True)
class Seq:
    items: tuple[FlowNode, ...]


@dataclass(frozen=True)
class Run:
    step_id: str  # a ``PlanStep.activity_id``


@dataclass(frozen=True)
class If:
    cond: Expr | None  # None: the agent writes the condition from ``tibco``
    tibco: str
    then: FlowNode
    otherwise: FlowNode  # an empty ``Seq`` when the step has no otherwise link
    link_id: str = ""  # the conditional link
    otherwise_id: str | None = None  # its otherwise link, when there is one


@dataclass(frozen=True)
class QueueLink:
    id: str
    from_id: str
    to_id: str
    kind: Literal["success", "conditional", "otherwise", "error"]
    cond: Expr | None  # conditional links only; None there means the agent writes it
    tibco: str
    # One end lies outside the queue's nodes (a link into or out of a group). BW does not draw
    # these; kept, not dropped, so a printer can refuse them or hand them to the agent.
    crosses: bool = False


@dataclass(frozen=True)
class Queue:
    links: tuple[QueueLink, ...]
    starts: tuple[str, ...] = ()  # the nodes that run first
    nodes: tuple[FlowNode, ...] = ()  # ``Run`` per activity and ``Loop`` per group, in IR order


@dataclass(frozen=True)
class Try:
    body: FlowNode
    catch: tuple[tuple[str, FlowNode], ...]  # (TIBCO fault name, the handlers' flow), in IR order
    catch_all: FlowNode | None


@dataclass(frozen=True)
class Loop:
    """A group. ``over`` and the counters drive an ``iterate`` group, ``condition`` and ``style`` a
    ``loop``; the other kinds (transaction, critical, parallel, scope) only bound ``body``. Each
    expression is ``None`` when code cannot write it; its TIBCO text is in ``tibco``."""

    group_id: str
    over: Expr | None  # iterate: the list walked (BW6 ``variable_list``, BW5 ``over``)
    body: FlowNode
    kind: str = ""  # the IR group kind: loop, iterate, transaction, critical, parallel, scope
    style: str = ""  # forEach, while, repeatUntil (BW6 loop element); repeatUntil for a BW5 repeat
    counter: str | None = None  # the index variable (BW6 ``counter``, BW5 ``index_slot``)
    element: str | None = None  # the current element variable (BW6 ``current_element``, BW5 slot)
    start: Expr | None = None  # iterate without a list: the counter's first value
    final: Expr | None = None  # and its last
    condition: Expr | None = None  # loop: while / until
    parallel: bool = False  # iterate: iterations may run in parallel
    tibco: tuple[tuple[str, str], ...] = ()  # (setting, TIBCO text) of every expression setting


FlowNode = Seq | Run | If | Queue | Try | Loop


@dataclass(frozen=True)
class Starter:
    kind: Literal["rest", "http", "soap", "jms", "timer", "subprocess"]
    route: str | None = None  # rest/http: the path; soap: the operation
    verb: str | None = None  # rest/http: upper case
    destination: str | None = None  # jms
    selector: str | None = None  # jms, verbatim
    interval_ms: int | None = None  # timer


@dataclass
class ServicePlan:
    process_id: str
    process_name: str
    starter: Starter
    steps: list[PlanStep]
    flow: FlowNode
    contracts: dict[str, ContractModel]  # by the namespace the stack emits them in
    config: list[ConfigMap] = field(default_factory=list)
    messaging: list[MessagingEntity] = field(default_factory=list)


# ------------------------------------------------------------------- steps
_INVOKE = frozenset({"http.invoke", "rest.invoke", "soap.invoke"})
_CODE_ONLY = frozenset({"noop", "catch", "confirm", "throw", "reply", "end", "log", "mapper", "assign",
                        "call.process", "jdbc.query", "jdbc.update"})
_SELECTOR_ATOM = re.compile(r"^\s*([A-Za-z_][\w.]*)\s*=\s*'([^']*)'\s*$")  # the selectors code reads


def _destination(act: Activity) -> str:
    settings = act.config.get("settings") if isinstance(act.config.get("settings"), dict) else {}
    return str(act.config.get("destination") or settings.get("destination") or "")


def _assign(target: str, expression: str) -> Assign:
    source = parse_simple(expression) if is_simple_expression(expression) else None
    return Assign(target=target, source=source, tibco=expression)


def _starter_agent(process: Process) -> bool:
    st = process.starter
    if st is None:
        return False
    if st.kind == "soap":
        return True  # the SOAP endpoint is drafted by the agent
    selector = str(st.config.get("selector") or "").strip() if st.kind == "jms" else ""
    return bool(selector) and not all(_SELECTOR_ATOM.match(a) for a in re.split(r"(?i)\s+and\s+", selector))


def _step(process: Process, act: Activity, row: MappingRow) -> PlanStep:
    inputs = [_assign(b.target, b.expression) for b in act.input_bindings]
    t = act.type
    mapping_agent = any(a.source is None for a in inputs)
    call_target: str | None = None
    if t == "call.process":
        call_target = next((c.target_process for c in process.calls if c.activity_id == act.id), None) or \
            (str(act.config.get("sub_process") or act.config.get("subprocess") or "") or None)
    elif t in _INVOKE:
        call_target = str(act.config.get("resource_ref") or "") or None
    elif t in ("jms.send", "jms.publish", "jms.request_reply"):
        call_target = _destination(act) or None
    if process.starter and act.id == process.starter.activity_id:
        agent = _starter_agent(process)
    elif t in ("jms.send", "jms.publish"):
        agent = not act.input_bindings or not call_target or mapping_agent
    elif t in _INVOKE:
        agent = not (row.class_name and row.method) or mapping_agent
    elif t in _CODE_ONLY:
        agent = mapping_agent
    else:  # jdbc.call, generic and unknown patterns: no code template
        agent = True
    return PlanStep(
        activity_id=act.id, name=act.name or act.id, pattern=row.pattern, row=row, inputs=inputs,
        sql=(str(act.config.get("sql") or "") or None) if t.startswith("jdbc.") else None,
        call_target=call_target,
        throws=str(act.config.get("fault_name") or act.name) if t == "throw" else None,
        agent=agent)


# -------------------------------------------------------------------- flow
class _Graph:
    """The process links, and how a set of nodes (the main flow, a group, a handler) runs."""

    def __init__(self, process: Process) -> None:
        self.p = process
        self.acts = {a.id: a for a in process.activities}
        self.groups = {g.id: g for g in process.groups}
        # A group sits in another when that one lists it or it names it as ``parent_group``; a node
        # belongs to the innermost group that holds it.
        parent: dict[str, str] = {}
        for g in process.groups:
            for other in process.groups:
                if other.id != g.id and g.id in other.member_ids:
                    parent.setdefault(g.id, other.id)
            p = g.config.get("parent_group")
            if isinstance(p, str) and p in self.groups and p != g.id:
                parent.setdefault(g.id, p)

        def depth(gid: str) -> int:
            seen: set[str] = set()
            while gid in parent and gid not in seen:
                seen.add(gid)
                gid = parent[gid]
            return len(seen)

        member_of: dict[str, str] = dict(parent)
        for a in process.activities:
            holders = [g.id for g in process.groups if a.id in g.member_ids]
            if a.group_id in self.groups and a.group_id not in holders:
                holders.append(a.group_id)
            if holders:
                member_of[a.id] = max(holders, key=depth)  # max keeps the first of equals
        self.nested = {g.id: [n for n in [*self.acts, *self.groups] if member_of.get(n) == g.id]
                       for g in process.groups}
        self.member_of = member_of
        self.handled = {aid for h in process.fault_handlers for aid in h.activity_ids}

    def main_nodes(self) -> list[str]:
        return [n for n in [*self.acts, *self.groups] if n not in self.member_of and n not in self.handled]

    def node(self, node_id: str) -> FlowNode:
        if node_id not in self.groups:
            return Run(node_id)
        g = self.groups[node_id]
        cfg = g.config

        def text(*keys: str) -> str:
            return next((str(cfg[k]).strip() for k in keys if isinstance(cfg.get(k), str) and cfg[k].strip()), "")

        current = cfg.get("current_element") if isinstance(cfg.get("current_element"), dict) else {}
        settings = (("variable_list", text("variable_list", "over")), ("start_counter", text("start_counter")),
                    ("final_counter", text("final_counter")),
                    ("condition", text("condition", "while_condition", "repeat_condition")),
                    ("current_element", str(current.get("expression") or "").strip()))
        tib = dict(settings)

        def expr(key: str) -> Expr | None:
            return parse_simple(tib[key]) if tib[key] else None

        style = text("loop") or ("repeatUntil" if text("repeat_condition") else "while" if text("while_condition") else "")
        return Loop(node_id, expr("variable_list"), self.flow(self.nested[node_id], self._entries(node_id)),
                    kind=g.kind, style=style, counter=text("counter", "index_slot") or None,
                    element=str(current.get("variable") or "") or text("iteration_element_slot") or None,
                    start=expr("start_counter"), final=expr("final_counter"), condition=expr("condition"),
                    parallel=cfg.get("parallel") is True, tibco=tuple((k, v) for k, v in settings if v))

    def _entries(self, group_id: str) -> list[str]:
        e = self.groups[group_id].config.get("entry_ids")
        return [str(x) for x in e] if isinstance(e, list) and e else []

    def flow(self, nodes: list[str], entries: list[str]) -> FlowNode:
        if not nodes:
            return Seq(())
        built = _Structured(self, nodes, entries).build()
        return built if built is not None else self._queue(nodes, entries)

    def _queue(self, nodes: list[str], entries: list[str]) -> Queue:
        inside = set(nodes)
        links = tuple(QueueLink(t.id, t.from_id, t.to_id, t.kind,
                                parse_simple(t.expression or "") if t.kind == "conditional" else None,
                                t.expression or "", crosses=not (t.from_id in inside and t.to_id in inside))
                      for t in self.p.transitions if t.from_id in inside or t.to_id in inside)
        targets = {lk.to_id for lk in links if not lk.crosses}
        st = self.p.starter
        if st and st.activity_id in inside:
            starts = [st.activity_id]
        else:
            starts = [e for e in entries if e in inside] or [n for n in nodes if n not in targets] or nodes[:1]
        return Queue(links, tuple(starts), tuple(self.node(n) for n in nodes))


class _Structured:
    """Sequences and if/else regions, as ``generator._Structured`` builds them; ``build`` gives
    ``None`` on any graph that needs the queue form."""

    EXIT = "\0exit"

    def __init__(self, g: _Graph, nodes: list[str], entries: list[str]) -> None:
        self.g, self.main, self.entries = g, nodes, entries
        main = set(nodes)
        self.out: dict[str, list[Transition]] = {n: [] for n in nodes}
        self.preds: dict[str, list[str]] = {n: [] for n in nodes}
        self.ok = True
        for t in g.p.transitions:
            a, b = t.from_id in main, t.to_id in main
            if a and b:
                self.out[t.from_id].append(t)
                self.preds[t.to_id].append(t.from_id)
                if t.kind == "error":
                    self.ok = False
            elif a != b:
                self.ok = False
        self.emitted: set[str] = set()
        self.allowed: set[str] = set()
        self.ipdom = self._ipdoms() if self.ok else {}

    def _ipdoms(self) -> dict[str, str | None]:
        succ = {n: sorted({t.to_id for t in self.out[n]}) or [self.EXIT] for n in self.main}
        everything = set(self.main) | {self.EXIT}
        pdom: dict[str, set[str]] = {n: set(everything) for n in self.main}
        pdom[self.EXIT] = {self.EXIT}
        changed = True
        while changed:
            changed = False
            for n in self.main:
                new = {n} | set.intersection(*(pdom[s] for s in succ[n]))
                if new != pdom[n]:
                    pdom[n], changed = new, True
        out: dict[str, str | None] = {}
        for n in self.main:
            strict = pdom[n] - {n}
            d = next((x for x in sorted(strict) if pdom[x] == strict), None)
            out[n] = None if d in (None, self.EXIT) else d
        return out

    def build(self) -> FlowNode | None:
        if not self.ok:
            return None
        st = self.g.p.starter
        if st and st.activity_id in self.out:
            starts = [st.activity_id]
        else:
            starts = [n for n in self.main if not self.preds[n]]
        if len(starts) != 1:
            return None
        items = self.region(starts[0], None)
        if items is None or set(self.main) - self.emitted:
            return None
        return Seq(tuple(items))

    def _terminal(self, node: str) -> bool:
        act = self.g.acts.get(node)
        if act is None:
            return False
        return act.type == "throw" or (act.type in ("reply", "end") and not self.out[node])

    def region(self, node: str | None, stop: str | None) -> list[FlowNode] | None:
        items: list[FlowNode] = []
        while node is not None and node != stop:
            if node in self.emitted or (len(self.preds[node]) > 1 and node not in self.allowed):
                return None
            self.emitted.add(node)
            outs = self.out[node]
            items.append(self.g.node(node))
            if self._terminal(node):
                return None if outs else items
            succ = [t for t in outs if t.kind == "success"]
            cond = [t for t in outs if t.kind == "conditional"]
            other = [t for t in outs if t.kind == "otherwise"]
            if (succ and (cond or other)) or len(succ) > 1 or len(cond) > 1 or len(other) > 1:
                return None
            if succ:
                node = succ[0].to_id
                continue
            if not cond:
                node = other[0].to_id if other else None
                continue
            c, o = cond[0], (other[0] if other else None)
            merge = self.ipdom.get(node)
            then_r = self.region(c.to_id, merge) if c.to_id != merge else []
            else_r = (self.region(o.to_id, merge) if o.to_id != merge else []) if o else []
            if then_r is None or else_r is None:
                return None
            items.append(If(parse_simple(c.expression or ""), c.expression or "", Seq(tuple(then_r)),
                            Seq(tuple(else_r)), link_id=c.id, otherwise_id=o.id if o else None))
            if merge is None:  # both branches end the process
                return items
            if any(p not in self.emitted for p in self.preds[merge]):
                return None
            self.allowed.add(merge)
            node = merge
        return items


def _flow(process: Process) -> FlowNode:
    g = _Graph(process)
    body = g.flow(g.main_nodes(), [])
    if not process.fault_handlers:
        return body
    by_fault: dict[str, list[str]] = {}  # several handlers of one fault run as one catch
    catch_all: list[str] = []
    for h in process.fault_handlers:
        ids = [a for a in h.activity_ids if a in g.acts]
        if h.fault_name:
            by_fault.setdefault(h.fault_name, []).extend(i for i in ids if i not in by_fault[h.fault_name])
        else:
            catch_all += [i for i in ids if i not in catch_all]
    catch = tuple((fault, g.flow(ids, [])) for fault, ids in by_fault.items())
    return Try(body, catch, g.flow(catch_all, []) if catch_all else None)


# ----------------------------------------------------------------- starter
def _interval_ms(cfg: dict) -> int:
    """The timer period as the .NET job reads it (``generator._interval``): ``time_interval`` units
    of ``interval_unit``, a day when the unit is unknown."""
    unit = str(cfg.get("interval_unit") or "").lower().rstrip("s")
    try:
        n = int(str(cfg.get("time_interval") or "1"))
    except ValueError:
        n = 1
    return {"second": 1, "minute": 60, "hour": 3600, "day": 86400, "week": 604800}.get(unit, 86400) * n * 1000


def _starter(process: Process) -> Starter:
    st = process.starter
    if st is None:
        return Starter("subprocess")
    c = st.config
    if st.kind in ("rest", "http"):
        return Starter(st.kind, route=str(c.get("path") or c.get("resource_path") or f"/{process.name}"),
                       verb=str(c.get("verb") or "POST").upper())
    if st.kind == "soap":
        return Starter("soap", route=str(c.get("operation") or process.name))
    if st.kind == "jms":
        return Starter("jms", destination=str(c.get("destination") or "") or None,
                       selector=str(c.get("selector") or "").strip() or None)
    if st.kind == "timer":
        return Starter("timer", interval_ms=_interval_ms(c))
    return Starter(st.kind)


# --------------------------------------------------------------- contracts
def _contracts(app: Application, design: DesignRecord) -> dict[str, ContractModel]:
    """The design's contracts, one model per namespace the stack emits them in, with the record
    names its plan gives (unique within a namespace, so nothing is shadowed)."""
    from engine.stacks import get_stack

    naming = get_stack(design.stack).naming
    app_name = naming.type_name(app.id, "App")
    plans = plan_contracts(app_name, app.contracts, naming)
    by_id = {c.id: c for c in app.contracts}
    out: dict[str, ContractModel] = {}
    for cm in design.contracts:
        contract, plan = by_id.get(cm.contract_id), plans.get(cm.contract_id)
        if contract is None or plan is None:
            continue
        try:
            model = contract_model(contract, plan.reserved)
        except (etree.XMLSyntaxError, ValueError):
            continue  # an unparsable contract has no records (``contracts.record_names`` gives [])
        merged = out.setdefault(plan.namespace, ContractModel())
        clash = (set(model.records) | set(model.enums)) & (set(merged.records) | set(merged.enums))
        if clash:  # plan_contracts reserves names per namespace, so this is a planning bug
            raise ValueError(f"contract {cm.contract_id} redefines {sorted(clash)} in {plan.namespace}")
        merged.records.update(model.records)
        merged.enums.update(model.enums)
        merged.root_paths += [rp for rp in model.root_paths if rp not in merged.root_paths]
    return {ns: out[ns] for ns in sorted(out)}


# -------------------------------------------------------------------- build
def build_plan(process: Process, app: Application, design: DesignRecord) -> ServicePlan:
    """The plan of ``process`` for ``design`` (any stack's record; its ``stack`` names the contracts).

    ``KeyError`` when the design has no mapping row for an activity: the record is not this
    process's, or it predates a change to the IR.
    """
    primary: dict[str, MappingRow] = {}
    for r in design.mapping:
        if r.pattern != "input.mapping":
            primary.setdefault(r.activity_id, r)
    acts = {a.id: a for a in process.activities}
    steps: list[PlanStep] = []
    for node, _ in _walk_order(process):
        if node not in acts:
            continue
        if node not in primary:
            raise KeyError(f"the design has no mapping row for activity {node!r} of {process.id}")
        steps.append(_step(process, acts[node], primary[node]))
    return ServicePlan(
        process_id=process.id, process_name=process.name, starter=_starter(process), steps=steps,
        flow=_flow(process), contracts=_contracts(app, design), config=list(design.config),
        messaging=list(design.messaging))
