"""Field-by-field comparison of expected and actual results (design spec section 6.4).

Both results are flattened to field paths:

* ``output.<a>.<b>``, list items as ``output.<a>[0].<b>``
* ``fault.name``, ``fault.data.<x>``
* side effects grouped by kind and target, in order:
  ``row_written[payment][0].late_fee_applied``, ``message_published[gmf.funding.events][0].status``

Each field is classified, in this order:

1. ``noise``: the path matches a configured :class:`NoiseRule` glob (timestamps, generated
   ids, ordering). Noise is reported but never counted.
2. ``match``: equal after normalisation (numbers compare as decimals, so ``25.00 == 25``;
   ``true``/``True``; an absent value equals ``None``). A :class:`NumericRule` (explicit, or the
   default the evaluator records for XPath-double fields in ``ExpectedResult.numeric_fields``)
   compares numbers at a scale or tolerance; it decides match or defect, never noise.
3. ``intended``: different, a finding decided ``fix`` cites an activity in the field's lineage
   (data and control dependence, scoped by process), and, when a ``counterfactual`` result
   (the model with the fixes applied) is given, the counterfactual changes the field and the
   actual value equals it. A difference the counterfactual does not produce stays a defect.
4. ``defect``: anything else.

``parity = matching / compared`` where compared excludes noise; ``unexplained`` counts defects.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import Any

from lxml import etree
from pydantic import BaseModel

from engine.analysis.models import Finding
from engine.ir.models import Activity, Application, Process
from engine.parity.models import ExpectedResult, FieldDiff, ParityReport, SideEffect
from engine.parity.sql import sql_write_payload

DEFAULT_SIDE_EFFECT_KINDS = ("row_written", "message_published", "service_called")
DOUBLE_SIGNIFICANT_DIGITS = 15
_XSL = "{http://www.w3.org/1999/XSL/Transform}"


def _glob(pattern: str, path: str) -> bool:
    """Only ``*`` and ``?`` are wildcards; brackets are literal (field paths use them)."""
    rx = re.escape(pattern).replace(r"\*", ".*").replace(r"\?", ".")
    return re.fullmatch(rx, path) is not None


class NoiseRule(BaseModel):
    """A reviewed rule: fields whose path matches ``path_glob`` are noise (not compared)."""

    path_glob: str
    reason: str

    def matches(self, path: str) -> bool:
        return _glob(self.path_glob, path)


class NumericRule(BaseModel):
    """Compare numbers on matching paths at ``scale`` decimal places, within ``rel_tol``, or
    (both ``None``) at 15 significant digits. The result is match or defect, never noise."""

    path_glob: str
    scale: int | None = None
    rel_tol: float | None = None
    reason: str = ""

    def matches(self, path: str) -> bool:
        return _glob(self.path_glob, path)

    def equal(self, a: Any, b: Any) -> bool | None:
        """``None`` when either side is not a number (fall back to plain equality)."""
        da, db = _to_decimal(a), _to_decimal(b)
        if da is None or db is None:
            return None
        if self.rel_tol is not None:
            return abs(da - db) <= Decimal(str(self.rel_tol)) * max(abs(da), abs(db))
        if self.scale is not None:
            q = Decimal(1).scaleb(-self.scale)
            return da.quantize(q, ROUND_HALF_EVEN) == db.quantize(q, ROUND_HALF_EVEN)
        return _significant(da) == _significant(db)


class LineageEntry(BaseModel):
    """An activity a field depends on: its process, its id there, and its trace path."""

    process_id: str
    activity_id: str
    trace_id: str


def _to_decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value).strip())
    except InvalidOperation:
        return None


def _significant(d: Decimal, digits: int = DOUBLE_SIGNIFICANT_DIGITS) -> Decimal:
    if d == 0:
        return Decimal(0)
    exponent = d.adjusted() - digits + 1
    return d.quantize(Decimal(1).scaleb(exponent), ROUND_HALF_EVEN)


# --- flattening --------------------------------------------------------------------------------


def flatten(value: Any, prefix: str) -> dict[str, Any]:
    """Nested dicts/lists to ``{path: leaf}``; an empty dict or list is one ``None`` leaf."""
    out: dict[str, Any] = {}
    if isinstance(value, dict):
        if not value:
            out[prefix] = None
        for k, v in value.items():
            out.update(flatten(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(value, list):
        if not value:
            out[prefix] = None
        for i, v in enumerate(value):
            out.update(flatten(v, f"{prefix}[{i}]"))
    else:
        out[prefix] = value
    return out


def side_effect_prefixes(side_effects: Iterable[SideEffect],
                         kinds: Iterable[str] = DEFAULT_SIDE_EFFECT_KINDS) -> list[tuple[str, SideEffect]]:
    """``(path prefix, side effect)`` with an occurrence index per (kind, target)."""
    wanted = set(kinds)
    counts: dict[tuple[str, str], int] = {}
    out = []
    for se in side_effects:
        if se.kind not in wanted:
            continue
        n = counts.get((se.kind, se.target), 0)
        counts[(se.kind, se.target)] = n + 1
        out.append((f"{se.kind}[{se.target}][{n}]", se))
    return out


def flatten_result(result: ExpectedResult, kinds: Iterable[str] = DEFAULT_SIDE_EFFECT_KINDS
                   ) -> tuple[dict[str, Any], dict[str, Any]]:
    """``(fields, side-effect fields)`` of a result."""
    fields = flatten(result.output, "output") if result.output else {}
    if result.fault is not None:
        fields["fault.name"] = result.fault.name
        fields.update(flatten(result.fault.data, "fault.data") if result.fault.data else {})
    effects: dict[str, Any] = {}
    for prefix, se in side_effect_prefixes(result.side_effects, kinds):
        effects.update(flatten(se.payload, prefix) if se.payload else {prefix: None})
    return fields, effects


# --- normalisation -------------------------------------------------------------------------------


def normalise(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float, Decimal)):
        return _decimal(str(value))
    text = str(value).strip()
    if text.lower() in ("true", "false"):
        return text.lower()
    if re.fullmatch(r"[-+]?\d+(\.\d+)?([eE][-+]?\d+)?", text):
        return _decimal(text)
    return text


def _decimal(text: str) -> Any:
    try:
        d = Decimal(text)
    except InvalidOperation:
        return text
    return d.normalize() if d != 0 else Decimal(0)


def equal(a: Any, b: Any, rule: NumericRule | None = None) -> bool:
    if rule is not None:
        verdict = rule.equal(a, b)
        if verdict is not None:
            return verdict
    na, nb = normalise(a), normalise(b)
    if na in (None, "") and nb in (None, ""):
        return True
    return na == nb


# --- comparison -------------------------------------------------------------------------------


Lineage = Mapping[str, Iterable["LineageEntry | str"]]


def _lineage_for(path: str, lineage: Lineage) -> list[LineageEntry | str]:
    """Lineage of ``path`` or of its nearest ancestor path present in ``lineage``."""
    p = path
    while True:
        if p in lineage:
            return list(lineage[p])
        cut = max(p.rfind("."), p.rfind("["))
        if cut <= 0:
            return []
        p = p[:cut]


def _touches(finding: Finding, entries: list[LineageEntry | str], process_id: str | None) -> bool:
    """A citation names an activity of ``process_id`` (the finding's process) in the lineage.

    Plain-string entries (trace paths, unscoped) match on the last path segment.
    """
    for c in finding.citations:
        for e in entries:
            if isinstance(e, LineageEntry):
                if e.activity_id == c.activity_id and (process_id is None or e.process_id == process_id):
                    return True
            elif e == c.activity_id or e.endswith("/" + c.activity_id):
                return True
    return False


class _Classifier:
    def __init__(self, noise: list[NoiseRule], numeric: list[NumericRule], fixes: list[Finding],
                 lineage: Lineage, finding_process: str | Mapping[str, str] | None,
                 counterfactual: dict[str, Any] | None) -> None:
        self.noise, self.numeric, self.fixes = noise, numeric, fixes
        self.lineage, self.finding_process, self.counterfactual = lineage, finding_process, counterfactual

    def _process_of(self, finding: Finding) -> str | None:
        if isinstance(self.finding_process, Mapping):
            return self.finding_process.get(finding.id)
        return self.finding_process

    def rule(self, path: str) -> NumericRule | None:
        return next((r for r in self.numeric if r.matches(path)), None)

    def __call__(self, path: str, exp: Any, act: Any) -> FieldDiff:
        if any(r.matches(path) for r in self.noise):
            return FieldDiff(path=path, expected=exp, actual=act, status="noise")
        rule = self.rule(path)
        if equal(exp, act, rule):
            return FieldDiff(path=path, expected=exp, actual=act, status="match")
        entries = _lineage_for(path, self.lineage)
        for f in self.fixes:
            if not _touches(f, entries, self._process_of(f)):
                continue
            if self.counterfactual is not None:
                cf = self.counterfactual.get(path)
                if equal(exp, cf, rule) or not equal(act, cf, rule):
                    continue  # the fix does not change this field, or not to this value
            return FieldDiff(path=path, expected=exp, actual=act, status="intended", finding_id=f.id)
        return FieldDiff(path=path, expected=exp, actual=act, status="defect")


def _diff(expected: dict[str, Any], actual: dict[str, Any], classify: _Classifier) -> list[FieldDiff]:
    paths = list(expected) + [p for p in actual if p not in expected]
    return [classify(p, expected.get(p), actual.get(p)) for p in paths]


def default_numeric_rules(expected: ExpectedResult) -> list[NumericRule]:
    """Rules for the fields the evaluator marked as XPath-double derived."""
    return [NumericRule(path_glob=path, scale=scale,
                        reason="XPath double arithmetic; compared at "
                               + (f"scale {scale}" if scale is not None else "15 significant digits"))
            for path, scale in expected.numeric_fields.items()]


def compare(
    expected: ExpectedResult,
    actual: ExpectedResult,
    noise_rules: list[NoiseRule],
    fix_findings: list[Finding],
    lineage: Lineage,
    *,
    scenario_id: str = "",
    numeric_rules: list[NumericRule] | None = None,
    counterfactual: ExpectedResult | None = None,
    finding_process: str | Mapping[str, str] | None = None,
    side_effect_kinds: Iterable[str] = DEFAULT_SIDE_EFFECT_KINDS,
) -> ParityReport:
    """Compare ``actual`` (the .NET service) with ``expected`` (the model) field by field.

    * Only findings whose ``decision`` is ``fix`` explain a difference. ``finding_process`` is
      the process the findings belong to (one id for all, or finding id -> process id);
      ``Finding`` itself carries no process.
    * ``counterfactual``: the model re-evaluated with the fixes applied. When given, a field is
      ``intended`` only if the fix changes it and ``actual`` equals the counterfactual value.
    * ``numeric_rules`` take precedence over the defaults from ``expected.numeric_fields``.
    * ``subprocess_called`` side effects are internal to the TIBCO model and not compared.
    """
    kinds = tuple(side_effect_kinds)
    fixes = [f for f in fix_findings if f.decision == "fix"]
    cf: dict[str, Any] | None = None
    if counterfactual is not None:
        cf_fields, cf_effects = flatten_result(counterfactual, kinds)
        cf = {**cf_fields, **cf_effects}
    classify = _Classifier(noise_rules, list(numeric_rules or []) + default_numeric_rules(expected),
                           fixes, lineage, finding_process, cf)
    exp_fields, exp_effects = flatten_result(expected, kinds)
    act_fields, act_effects = flatten_result(actual, kinds)
    fields = _diff(exp_fields, act_fields, classify)
    effects = _diff(exp_effects, act_effects, classify)
    counted = [d for d in fields + effects if d.status != "noise"]
    matching = sum(1 for d in counted if d.status == "match")
    return ParityReport(
        scenario_id=scenario_id,
        fields=fields,
        side_effects=effects,
        parity=(matching / len(counted)) if counted else 1.0,
        unexplained=sum(1 for d in counted if d.status == "defect"),
    )


# --- lineage -----------------------------------------------------------------------------------


_VAR = re.compile(r"\$([A-Za-z_][\w.-]*)")


class _Lineage:
    """Data and control dependence over the activities that ran (from the trace)."""

    def __init__(self, app: Application, result: ExpectedResult, control: bool) -> None:
        self.app = app
        self.control = control
        self.processes = {p.id: p for p in app.processes}
        self.steps = {s.activity_id: s for s in result.trace}
        self.ran = set(self.steps)

    def fired(self, prefix: str, link_id: str, source: str) -> bool:
        step = self.steps.get(f"{prefix}{source}")
        return bool(step and step.chosen_transition and link_id in step.chosen_transition.split(","))

    @staticmethod
    def _sheet(act: Activity) -> etree._Element | None:
        raw = act.config.get("input_xslt")
        if isinstance(raw, list):
            raw = raw[0] if raw else None
        raw = raw or act.config.get("message_xslt")
        if not raw:
            return None
        try:
            return etree.fromstring(raw.encode("utf-8"))
        except etree.XMLSyntaxError:
            return None

    @staticmethod
    def _var_names(process: Process) -> dict[str, Activity]:
        names: dict[str, Activity] = {}
        for a in process.activities:
            names[a.name] = a
            names[a.name.replace(" ", "-")] = a
            if a.type == "throw" and a.config.get("fault_name"):
                names[a.config["fault_name"]] = a
        if process.starter is not None:
            starter = next((a for a in process.activities if a.id == process.starter.activity_id), None)
            if starter is not None:
                for suffix in (".item", ".parameters"):
                    names[starter.name + suffix] = starter
        return names

    def _refs(self, el: etree._Element, sheet: etree._Element) -> set[str]:
        """Variables read by ``el`` (and by the ``xsl:variable``s it uses)."""
        local_vars = {v.get("name"): v for v in sheet.iter(f"{_XSL}variable")}
        refs: set[str] = set()
        todo = [el]
        seen: set[int] = set()
        while todo:
            node = todo.pop()
            if id(node) in seen:
                continue
            seen.add(id(node))
            for sub in node.iter():
                if not isinstance(sub.tag, str):
                    continue
                for value in sub.attrib.values():
                    for name in _VAR.findall(value):
                        name = name.rstrip(".-")
                        if name in local_vars:
                            todo.append(local_vars[name])
                        else:
                            refs.add(name)
        return refs

    def _vars_closure(self, process: Process, names: Iterable[str], prefix: str,
                      seen: set[str]) -> set[tuple[str, str, str]]:
        out: set[tuple[str, str, str]] = set()
        by_name = self._var_names(process)
        for name in names:
            src = by_name.get(name)
            if src is not None and f"{prefix}{src.id}" in self.ran:
                out |= self.closure(process, src, prefix, None, seen)
        return out

    def _control(self, process: Process, node_id: str, prefix: str, seen: set[str],
                 visited: set[str]) -> set[tuple[str, str, str]]:
        """Decided conditional/otherwise links on the path to ``node_id``: their source
        activities and the variables their conditions read."""
        key = f"{prefix}{node_id}"
        if key in visited:
            return set()
        visited.add(key)
        out: set[tuple[str, str, str]] = set()
        incoming = [t for t in process.transitions if t.to_id == node_id]
        for t in incoming:
            if not self.fired(prefix, t.id, t.from_id):
                continue
            if t.kind in ("conditional", "otherwise"):
                src = next((a for a in process.activities if a.id == t.from_id), None)
                if src is not None:
                    out.add((process.id, src.id, f"{prefix}{src.id}"))
                siblings = [s for s in process.transitions if s.from_id == t.from_id and s.kind == "conditional"]
                exprs = [t.expression or ""] if t.kind == "conditional" else [s.expression or "" for s in siblings]
                names = {n.rstrip(".-") for e in exprs for n in _VAR.findall(e)}
                out |= self._vars_closure(process, names, prefix, seen)
            out |= self._control(process, t.from_id, prefix, seen, visited)
        act = next((a for a in process.activities if a.id == node_id), None)
        group_id = act.group_id if act else next(
            (g.config.get("parent_group") for g in process.groups if g.id == node_id), None)
        groups = {g.id: g for g in process.groups}
        if group_id in groups:
            out |= self._control(process, group_id, prefix, seen, visited)
        return out

    def closure(self, process: Process, act: Activity, prefix: str, refs: set[str] | None = None,
                seen: set[str] | None = None) -> set[tuple[str, str, str]]:
        """``(process, activity, trace id)`` of everything that flows into ``act``."""
        seen = seen if seen is not None else set()
        me = f"{prefix}{act.id}"
        if me in seen:
            return set()
        seen.add(me)
        out = {(process.id, act.id, me)}
        if act.type == "call.process":
            target = next((c.target_process for c in process.calls if c.activity_id == act.id), None)
            sub = self.processes.get(target or act.config.get("sub_process", ""))
            if sub is not None:
                for a in sub.activities:
                    if a.type == "end" and f"{me}/{a.id}" in self.ran:
                        out |= self.closure(sub, a, f"{me}/", None, seen)
        if refs is None:
            sheet = self._sheet(act)
            refs = self._refs(sheet, sheet) if sheet is not None else set()
        out |= self._vars_closure(process, {r for r in refs if r != act.name}, prefix, seen)
        if self.control:
            out |= self._control(process, act.id, prefix, seen, set())
        return out

    def field(self, process: Process, act: Activity, prefix: str, steps: list[str]) -> set[tuple[str, str, str]]:
        """First hop at field level: the element ``steps`` of ``act``'s binding result."""
        sheet = self._sheet(act)
        if sheet is None:
            return self.closure(process, act, prefix)
        template = next(sheet.iter(f"{_XSL}template"), None)
        node = _find_result(template, steps) if template is not None else None
        if node is None:
            return self.closure(process, act, prefix)
        return self.closure(process, act, prefix, self._refs(node, sheet))


def _result_children(el: etree._Element) -> Iterable[etree._Element]:
    """Literal result elements under ``el``, looking through xsl:if/choose/for-each."""
    for c in el:
        if not isinstance(c.tag, str):
            continue
        if c.tag.startswith(_XSL):
            if c.tag in (f"{_XSL}if", f"{_XSL}choose", f"{_XSL}when", f"{_XSL}otherwise",
                         f"{_XSL}for-each"):
                yield from _result_children(c)
            continue
        yield c


def _find_result(template: etree._Element, steps: list[str]) -> etree._Element | None:
    """Follow ``steps`` (local names) below the root result element, skipping wrappers."""
    level = list(_result_children(template))
    node = None
    for i, step in enumerate(steps):
        match = None
        frontier = level
        # the root result element (and message wrappers such as <item>) are not in the path
        for _ in range(4):
            match = next((e for e in frontier if etree.QName(e).localname == step), None)
            if match is not None or i > 0:
                break
            frontier = [g for e in frontier for g in _result_children(e)]
        if match is None:
            return node if i > 0 else None
        node = match
        level = list(_result_children(node))
    return node


def _resolve(app: Application, process: Process, trace_id: str
             ) -> tuple[Process, Activity, str, list[tuple[Process, Activity, str]]] | None:
    """Trace path ``CallX/CallY/Act`` -> (process, activity, prefix, calling chain)."""
    processes = {p.id: p for p in app.processes}
    *calls, last = trace_id.split("/")
    prefix = ""
    chain: list[tuple[Process, Activity, str]] = []
    for call in calls:
        act = next((a for a in process.activities if a.id == call), None)
        target = next((c.target_process for c in process.calls if c.activity_id == call), None)
        target = target or (act.config.get("sub_process") if act else None)
        if act is None or target not in processes:
            return None
        chain.append((process, act, prefix))
        process = processes[target]
        prefix += f"{call}/"
    act = next((a for a in process.activities if a.id == last), None)
    return (process, act, prefix, chain) if act else None


def _param_steps(act: Activity, column: str) -> list[str] | None:
    """The binding element (JDBC parameter) a written column takes its value from."""
    params = [p.get("name") for p in act.config.get("parameters") or []]
    sql = str(act.config.get("sql") or "")
    _, _, _, sources = sql_write_payload(sql, [None] * len(params))
    index = 0
    for col, expr in sources.items():
        if col == column:
            return [params[index]] if expr == "?" and index < len(params) else None
        index += expr.count("?")
    return None


def _entries(items: set[tuple[str, str, str]]) -> list[LineageEntry]:
    return [LineageEntry(process_id=p, activity_id=a, trace_id=t) for p, a, t in sorted(items, key=lambda x: x[2])]


def build_lineage(result: ExpectedResult, app: Application, process_id: str,
                  kinds: Iterable[str] = DEFAULT_SIDE_EFFECT_KINDS, *, control: bool = True
                  ) -> dict[str, list[LineageEntry]]:
    """Field path -> the activities (scoped by process) the field depends on.

    Data dependence: the producing activity (reply, End, the JDBC/JMS/HTTP activity of a side
    effect), the variables its binding reads for that field (first hop, field level), then every
    activity those variables came from, transitively, among the activities that ran. Control
    dependence (``control=True``): for every conditional or otherwise link decided on the path
    to an activity in the data lineage, the link's source activity and the variables its
    condition (for otherwise: its siblings' conditions) reads; for an activity inside a
    subprocess, also the path to the calling activity.
    """
    process = next(p for p in app.processes if p.id == process_id or p.name == process_id)
    lin = _Lineage(app, result, control)
    out: dict[str, list[LineageEntry]] = {}
    producer = None
    for step in result.trace:
        if "/" in step.activity_id:
            continue
        act = next((a for a in process.activities if a.id == step.activity_id), None)
        if act is not None and act.type == "reply" and producer is None:
            producer = act
    if producer is None:
        producer = next((a for a in process.activities if a.type == "end" and a.id in lin.ran), None)
    if producer is not None and result.output:
        body_based = producer.type == "reply" and isinstance(result.output.get("body"), (dict, list))
        for path in flatten(result.output, "output"):
            steps = [re.sub(r"\[\d+\]$", "", s) for s in path.split(".")[1:]]
            if body_based:
                steps = steps[1:] if steps and steps[0] == "body" else []
            acts = lin.field(process, producer, "", steps) if steps else lin.closure(process, producer, "")
            out[path] = _entries(acts)

    def with_callers(found: tuple[Process, Activity, str, list[tuple[Process, Activity, str]]],
                     items: set[tuple[str, str, str]]) -> set[tuple[str, str, str]]:
        for proc, call, pre in found[3]:
            items = items | {(proc.id, call.id, f"{pre}{call.id}")}
            if control:
                items |= lin._control(proc, call.id, pre, set(), set())
        return items

    if result.fault is not None:
        thrower = next((s.activity_id for s in reversed(result.trace)
                        if s.output.get("fault", {}).get("name") == result.fault.name), None)
        found = _resolve(app, process, thrower) if thrower else None
        if found:
            out["fault"] = _entries(with_callers(found, lin.closure(found[0], found[1], found[2])))
    for prefix, se in side_effect_prefixes(result.side_effects, kinds):
        found = _resolve(app, process, se.activity_id) if se.activity_id else None
        if found is None:
            continue
        sub, act, pre, _ = found
        out[prefix] = _entries(with_callers(found, lin.closure(sub, act, pre)))
        for key in se.payload:
            steps = _param_steps(act, key) if se.kind == "row_written" else [key]
            if steps:
                out[f"{prefix}.{key}"] = _entries(with_callers(found, lin.field(sub, act, pre, steps)))
    return out
