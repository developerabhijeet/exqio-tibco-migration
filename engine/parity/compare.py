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
   ``true``/``True``; an absent value equals ``None``).
3. ``intended``: different, and a finding decided ``fix`` cites an activity in the field's
   lineage (the activities whose bindings produced the field, see :func:`build_lineage`).
4. ``defect``: anything else.

``parity = matching / compared`` where compared excludes noise; ``unexplained`` counts defects.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from decimal import Decimal, InvalidOperation
from typing import Any

from lxml import etree
from pydantic import BaseModel

from engine.analysis.models import Finding
from engine.ir.models import Activity, Application, Process
from engine.parity.models import ExpectedResult, FieldDiff, ParityReport, SideEffect

DEFAULT_SIDE_EFFECT_KINDS = ("row_written", "message_published", "service_called")
_XSL = "{http://www.w3.org/1999/XSL/Transform}"


class NoiseRule(BaseModel):
    """A reviewed rule: fields whose path matches ``path_glob`` are noise (not compared).

    Only ``*`` (any run of characters, dots included) and ``?`` are wildcards; brackets are
    literal because field paths use them (``row_written[*].*_at``).
    """

    path_glob: str
    reason: str

    def matches(self, path: str) -> bool:
        pattern = re.escape(self.path_glob).replace(r"\*", ".*").replace(r"\?", ".")
        return re.fullmatch(pattern, path) is not None


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


def equal(a: Any, b: Any) -> bool:
    na, nb = normalise(a), normalise(b)
    if na in (None, "") and nb in (None, ""):
        return True
    return na == nb


# --- comparison -------------------------------------------------------------------------------


def _lineage_for(path: str, lineage: dict[str, list[str]]) -> list[str]:
    """Lineage of ``path`` or of its nearest ancestor path present in ``lineage``."""
    p = path
    while True:
        if p in lineage:
            return lineage[p]
        cut = max(p.rfind("."), p.rfind("["))
        if cut <= 0:
            return []
        p = p[:cut]


def _touches(finding: Finding, activities: list[str]) -> bool:
    for c in finding.citations:
        for a in activities:
            if a == c.activity_id or a.endswith("/" + c.activity_id):
                return True
    return False


def _classify(path: str, exp: Any, act: Any, noise_rules: list[NoiseRule], fixes: list[Finding],
              lineage: dict[str, list[str]]) -> FieldDiff:
    for rule in noise_rules:
        if rule.matches(path):
            return FieldDiff(path=path, expected=exp, actual=act, status="noise")
    if equal(exp, act):
        return FieldDiff(path=path, expected=exp, actual=act, status="match")
    acts = _lineage_for(path, lineage)
    for f in fixes:
        if _touches(f, acts):
            return FieldDiff(path=path, expected=exp, actual=act, status="intended", finding_id=f.id)
    return FieldDiff(path=path, expected=exp, actual=act, status="defect")


def _diff(expected: dict[str, Any], actual: dict[str, Any], noise_rules: list[NoiseRule],
          fixes: list[Finding], lineage: dict[str, list[str]]) -> list[FieldDiff]:
    paths = list(expected) + [p for p in actual if p not in expected]
    return [_classify(p, expected.get(p), actual.get(p), noise_rules, fixes, lineage) for p in paths]


def compare(
    expected: ExpectedResult,
    actual: ExpectedResult,
    noise_rules: list[NoiseRule],
    fix_findings: list[Finding],
    lineage: dict[str, list[str]],
    *,
    scenario_id: str = "",
    side_effect_kinds: Iterable[str] = DEFAULT_SIDE_EFFECT_KINDS,
) -> ParityReport:
    """Compare ``actual`` (the .NET service) with ``expected`` (the model) field by field.

    Only findings whose ``decision`` is ``fix`` explain a difference. ``subprocess_called``
    side effects are internal to the TIBCO model and are not compared by default.
    """
    kinds = tuple(side_effect_kinds)
    fixes = [f for f in fix_findings if f.decision == "fix"]
    exp_fields, exp_effects = flatten_result(expected, kinds)
    act_fields, act_effects = flatten_result(actual, kinds)
    fields = _diff(exp_fields, act_fields, noise_rules, fixes, lineage)
    effects = _diff(exp_effects, act_effects, noise_rules, fixes, lineage)
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
    def __init__(self, app: Application, ran: set[str]) -> None:
        self.app = app
        self.ran = ran
        self.processes = {p.id: p for p in app.processes}

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

    def closure(self, process: Process, act: Activity, prefix: str, refs: set[str] | None = None,
                seen: set[str] | None = None) -> set[str]:
        """Activities (trace ids) whose outputs flow into ``act`` (``refs`` narrows the first hop)."""
        seen = seen if seen is not None else set()
        me = f"{prefix}{act.id}"
        if me in seen:
            return set()
        seen.add(me)
        out = {me}
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
        names = self._var_names(process)
        for name in refs:
            src = names.get(name)
            if src is not None and f"{prefix}{src.id}" in self.ran and src.id != act.id:
                out |= self.closure(process, src, prefix, None, seen)
        return out

    def field(self, process: Process, act: Activity, prefix: str, steps: list[str]) -> set[str]:
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


def _resolve(app: Application, process: Process, trace_id: str) -> tuple[Process, Activity, str] | None:
    """Trace path ``CallX/CallY/Act`` -> (process, activity, prefix)."""
    processes = {p.id: p for p in app.processes}
    *calls, last = trace_id.split("/")
    prefix = ""
    for call in calls:
        target = next((c.target_process for c in process.calls if c.activity_id == call), None)
        act = next((a for a in process.activities if a.id == call), None)
        target = target or (act.config.get("sub_process") if act else None)
        if target not in processes:
            return None
        process = processes[target]
        prefix += f"{call}/"
    act = next((a for a in process.activities if a.id == last), None)
    return (process, act, prefix) if act else None


def _param_steps(act: Activity, column: str) -> list[str] | None:
    """The binding element (JDBC parameter) a written column takes its value from."""
    from engine.parity.evaluator import sql_write_payload

    params = [p.get("name") for p in act.config.get("parameters") or []]
    sql = str(act.config.get("sql") or "")
    _, _, _, sources = sql_write_payload(sql, [None] * len(params))
    index = 0
    for col, expr in sources.items():
        if col == column:
            return [params[index]] if expr == "?" and index < len(params) else None
        index += expr.count("?")
    return None


def build_lineage(result: ExpectedResult, app: Application, process_id: str,
                  kinds: Iterable[str] = DEFAULT_SIDE_EFFECT_KINDS) -> dict[str, list[str]]:
    """Field path -> trace ids of the activities whose bindings produced it.

    Derived from the trace: the producing activity (reply, End, the JDBC/JMS/HTTP activity of a
    side effect), the variables its binding reads for that field (first hop, field level), then
    every activity those variables came from, transitively, among the activities that ran.
    """
    process = next(p for p in app.processes if p.id == process_id or p.name == process_id)
    ran = {s.activity_id for s in result.trace}
    lin = _Lineage(app, ran)
    out: dict[str, list[str]] = {}
    producer = None
    for step in result.trace:
        if "/" in step.activity_id:
            continue
        act = next((a for a in process.activities if a.id == step.activity_id), None)
        if act is not None and act.type == "reply" and producer is None:
            producer = act
    if producer is None:
        producer = next((a for a in process.activities if a.type == "end" and a.id in ran), None)
    if producer is not None and result.output:
        body_based = producer.type == "reply" and isinstance(result.output.get("body"), (dict, list))
        for path in flatten(result.output, "output"):
            steps = [re.sub(r"\[\d+\]$", "", s) for s in path.split(".")[1:]]
            if body_based:
                steps = steps[1:] if steps and steps[0] == "body" else []
            acts = lin.field(process, producer, "", steps) if steps else lin.closure(process, producer, "")
            out[path] = sorted(acts)
    if result.fault is not None:
        thrower = next((s.activity_id for s in reversed(result.trace)
                        if s.output.get("fault", {}).get("name") == result.fault.name), None)
        found = _resolve(app, process, thrower) if thrower else None
        if found:
            out["fault"] = sorted(lin.closure(found[0], found[1], found[2]))
    for prefix, se in side_effect_prefixes(result.side_effects, kinds):
        found = _resolve(app, process, se.activity_id) if se.activity_id else None
        if found is None:
            continue
        sub, act, pre = found
        out[prefix] = sorted(lin.closure(sub, act, pre))
        for key in se.payload:
            steps = _param_steps(act, key) if se.kind == "row_written" else [key]
            if steps:
                out[f"{prefix}.{key}"] = sorted(lin.field(sub, act, pre, steps))
    return out
