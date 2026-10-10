"""Scenario generation for the parity evaluator (design spec section 6.3).

* :func:`generate_scenarios` (static): a happy path from the request contract and seed rows,
  one scenario per conditional/otherwise transition by solving simple predicates (``fault``
  when it leads to a ``throw``), one timeout and one 5xx per HTTP call.
* :func:`generate_and_evaluate`: runs the evaluator, picks the happy seed row, fills
  ``expected`` and closes coverage gaps the static solver cannot reach (predicates over JDBC
  results, subprocess outputs) by trying other seed rows and single-field mutations.
* :func:`fill_expected`: runs scenarios and keeps only branch scenarios that really took
  their link.
"""
from __future__ import annotations

import copy
import datetime as dt
import itertools
import re
import warnings
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from engine.domains.discover import activity_sql, sql_tables
from engine.ir.models import Application, Contract, Process, Resource, Transition
from engine.parity.models import Scenario, StubResponse
from engine.parity.predicates import (
    REMOVE,
    Atom,
    Resolver,
    get_path,
    parse_condition,
    set_path,
    solve_path,
    to_decimal,
)
from engine.parity.schema import INT_TYPES, NUMBER_TYPES, SchemaIndex, XsdElement, starter_shape
from engine.parity.seed import SeedData, snake

if TYPE_CHECKING:  # pragma: no cover
    from engine.parity.evaluator import Evaluator


# --- generation ----------------------------------------------------------------------------


@dataclass
class _Ctx:
    process: Process
    app: Application
    schema: SchemaIndex
    seed: SeedData | None
    shape: dict[str | None, XsdElement | None]
    written: set[str]


def _written_tables(process: Process, app: Application, seen: set[str] | None = None) -> set[str]:
    seen = seen or set()
    if process.id in seen:
        return set()
    seen.add(process.id)
    tables: set[str] = set()
    for a in process.activities:
        sql = activity_sql(a)
        if sql:
            tables |= {t.name for t in sql_tables(sql) if t.operation in ("insert", "update")}
    by_id = {p.id: p for p in app.processes}
    for c in process.calls:
        if c.target_process in by_id:
            tables |= _written_tables(by_id[c.target_process], app, seen)
    return tables


def _match_column(table_cols: Iterable[str], name: str, parent: str | None) -> str | None:
    cols = list(table_cols)
    s = snake(name)
    if s in cols:
        return s
    if parent and f"{snake(parent)}_{s}" in cols:
        return f"{snake(parent)}_{s}"
    for c in cols:
        if c.startswith(f"{s}_") and c.endswith(("_code", "_id", "_number")):
            return c
    return None


def _default_value(decl: XsdElement) -> Any:
    if decl.enumerations:
        return decl.enumerations[0]
    t = decl.simple_type or "string"
    if t in INT_TYPES:
        return 1
    if t in NUMBER_TYPES:
        return "100.00"
    if t == "boolean":
        return False
    if t == "date":
        return dt.date.today().isoformat()
    if t == "dateTime":
        return dt.datetime.now().replace(microsecond=0).isoformat()
    if decl.pattern:
        return _sample_pattern(decl.pattern)
    return f"{decl.name.upper()}-1"


def _sample_pattern(pattern: str) -> str:
    """A string for the simple patterns XSD contracts use (``[0-9]{3}-[0-9]{2}``, ``[A-Z]{2}``)."""
    out = []
    for m in re.finditer(r"\[([^\]]+)\](\{(\d+)(?:,\d*)?\})?|\\d(\{(\d+)\})?|(\\?.)", pattern.strip("^$")):
        if m.group(1):
            cls, n = m.group(1), int(m.group(3) or 1)
            ch = "0" if "0" in cls else ("A" if "A" in cls else cls[0])
            out.append(ch * n)
        elif m.group(0).startswith("\\d"):
            out.append("0" * int(m.group(5) or 1))
        elif m.group(6):
            out.append(m.group(6).lstrip("\\"))
    return "".join(out)


def _coerce(value: Any, decl: XsdElement) -> Any:
    if value is None:
        return None
    t = decl.simple_type
    if t in INT_TYPES:
        n = to_decimal(value)
        return int(n) if n is not None else value
    if t == "boolean":
        return str(value).lower() in ("true", "t", "1")
    if t == "date":
        return str(value)[:10]
    return str(value)


def _happy_value(ctx: _Ctx, decl: XsdElement, k: int, table: str | None = None,
                 parent: str | None = None) -> Any:
    if not decl.complex:
        return _default_value(decl)
    rows = ctx.seed.rows if ctx.seed else {}
    leaves = [c for c in decl.children if not c.complex]

    def score(tbl: str) -> int:
        cols = rows[tbl][0].keys() if rows.get(tbl) else []
        return sum(1 for c in leaves if _match_column(cols, c.name, decl.name))

    def pk_hits(tbl: str) -> int:
        pk = ctx.seed.primary_keys.get(tbl) if ctx.seed else None
        return sum(1 for c in leaves if pk and _match_column([pk], c.name, decl.name))

    if table is None or score(table) == 0:
        # most matching columns; then the table the ids are the primary key of; then the larger
        scored = sorted(rows, key=lambda t: (-score(t), -pk_hits(t), -len(rows[t]), t))
        table = scored[0] if scored and score(scored[0]) > 0 else None
    out: dict[str, Any] = {}
    for c in decl.children:
        if c.complex:
            out[c.name] = _happy_value(ctx, c, k, table, decl.name)
            continue
        value: Any = None
        found = False
        for tbl in ([table] if table else []) + sorted(rows):
            data = rows.get(tbl) or []
            col = _match_column(data[0].keys(), c.name, decl.name) if data else None
            if col is None:
                continue
            if ctx.seed and tbl in ctx.written and ctx.seed.primary_keys.get(tbl) == col:
                value = ctx.seed.fresh_value(tbl, col)
            else:
                value = data[k % len(data)].get(col)
                if isinstance(value, str) and re.search(r"current_|interval|\+|\(", value):
                    continue  # an SQL expression, not a usable literal
            found = value is not None
            if found:
                break
        if not found and table and rows.get(table):
            # last resort, same table only: a column sharing the field's last word
            # (fundingAmount ~ approved_amount)
            last = snake(c.name).split("_")[-1]
            col = next((k for k in rows[table][0] if k.split("_")[-1] == last), None)
            if col is not None:
                value = rows[table][k % len(rows[table])].get(col)
                found = value is not None and not (isinstance(value, str) and re.search(r"current_|interval", value))
        out[c.name] = _coerce(value, c) if found else _default_value(c)
    return out


def happy_input(ctx: _Ctx, k: int = 0) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for key, decl in ctx.shape.items():
        value = _happy_value(ctx, decl, k) if decl is not None else {}
        if key is None:
            data.update(value if isinstance(value, dict) else {})
        else:
            data[key] = value
    return data


def _leaf_decl(ctx: _Ctx, key: str | None, steps: tuple[str, ...]) -> XsdElement | None:
    decl = ctx.shape.get(key)
    for s in steps:
        decl = decl.child(s) if decl else None
    return decl


def _solve(ctx: _Ctx, resolver: Resolver, base: dict[str, Any], atoms: list[Atom]) -> dict[str, Any] | None:
    by_path: dict[tuple[str | None, tuple[str, ...]], list[Atom]] = {}
    for a in atoms:
        target = resolver.resolve(a.path)
        if target is None:
            continue  # not an input field: keep the base value (the coverage loop confirms)
        by_path.setdefault(target, []).append(a)
    if not by_path:
        return None
    data = copy.deepcopy(base)
    for (key, steps), path_atoms in by_path.items():
        if not steps:
            return None
        decl = _leaf_decl(ctx, key, steps)
        integer = bool(decl and decl.simple_type in INT_TYPES)
        ok, value = solve_path(path_atoms, get_path(data, key, steps), integer)
        if not ok:
            return None
        if value is REMOVE:
            if decl is not None and decl.min_occurs > 0:
                value = ""
            else:
                set_path(data, key, steps, None, remove=True)
                continue
        if isinstance(value, Decimal):
            value = str(value)
        set_path(data, key, steps, value)
    return data


MAX_NEGATION_COMBINATIONS = 256


def _branch_atoms(target: Transition, siblings: list[Transition]) -> Iterator[list[Atom]]:
    """Alternative atom sets under which ``target`` is the only link BW takes from its source.

    BW fires every true conditional link, so every OTHER conditional sibling must be false:
    ``not(D1 or D2) = not D1 and not D2`` and ``not(a and b) = not a or not b``, i.e. one negated
    atom per sibling disjunct. The choices are enumerated (solvable atoms first) so the caller
    can pick the first combination that is consistent with the target's own condition.
    """
    others = [t for t in siblings if t.kind == "conditional" and t.expression and t.id != target.id]
    choices: list[list[Atom | None]] = []
    for t in others:
        for disjunct in parse_condition(t.expression or ""):
            negated: list[Atom | None] = [a.negate() for a in disjunct if a is not None]
            choices.append(negated or [None])
    positives: list[list[Atom]] = [[]] if target.kind == "otherwise" else [
        [a for a in disjunct if a is not None]
        for disjunct in parse_condition(target.expression or "")
        if any(a is not None for a in disjunct)]
    for positive in positives:
        for combo in itertools.islice(itertools.product(*choices), MAX_NEGATION_COMBINATIONS):
            yield positive + [a for a in combo if a is not None]


def _scenario_id(process: Process, *parts: str) -> str:
    return ":".join([process.name, *parts])


def _http_targets(process: Process, app: Application, seen: set[str] | None = None) -> list[tuple[str, str, Resource | None, str]]:
    """(resource id, path, resource, activity name) of every HTTP call reachable from ``process``."""
    seen = seen or set()
    if process.id in seen:
        return []
    seen.add(process.id)
    resources = {r.id: r for r in app.resources}
    out = []
    for a in process.activities:
        if a.type not in ("http.invoke", "rest.invoke", "soap.invoke"):
            continue
        path = next((b.expression.strip().strip("\"'") for b in a.input_bindings
                     if b.target.endswith("RequestURI") and re.fullmatch(r"\"[^\"]*\"|'[^']*'", b.expression.strip())),
                    str(a.config.get("path") or "/"))
        rid = str(a.config.get("resource_ref") or a.id)
        out.append((rid, path, resources.get(rid), a.name))
    by_id = {p.id: p for p in app.processes}
    for c in process.calls:
        if c.target_process in by_id:
            out += _http_targets(by_id[c.target_process], app, seen)
    return out


def generate_scenarios(process: Process, contracts: list[Contract], app: Application,
                       seed: SeedData | None = None, *, variant: int = 0) -> list[Scenario]:
    """Static scenario set of one process (``expected`` is left empty, see ``fill_expected``).

    * ``happy``: values from the request contract and seed row ``variant`` (ids reused so
      lookups hit; keys of tables the process inserts into are fresh).
    * ``branch`` / ``fault``: one per conditional or otherwise transition whose predicate is
      solvable on the input (``fault`` when the transition leads to a ``throw``).
    * ``external_failure``: one timeout and one 5xx per HTTP call.
    """
    schema = SchemaIndex(contracts)
    seed = seed if seed is not None else SeedData.for_app(app, process)
    ctx = _Ctx(process, app, schema, seed, starter_shape(process, app, schema), _written_tables(process, app))
    base = happy_input(ctx, variant)
    scenarios = [Scenario(id=_scenario_id(process, "happy"), process_id=process.id,
                          name="happy path", kind="happy", input=base)]
    resolver = Resolver(process)
    throws = {a.id for a in process.activities if a.type == "throw"}
    seen_inputs: dict[str, list[dict[str, Any]]] = {"happy": [base], "branch": [], "fault": []}
    by_source: dict[str, list[Transition]] = {}
    for t in process.transitions:
        by_source.setdefault(t.from_id, []).append(t)
    for source, outs in by_source.items():
        if not any(t.kind == "conditional" for t in outs):
            continue
        branches = list(outs)
        if not any(t.kind == "otherwise" for t in outs):
            # no otherwise link: when no condition holds BW takes no transition (the job ends)
            branches.append(Transition(id=f"{source}:no-transition", from_id=source, to_id="",
                                       kind="otherwise"))
        for t in branches:
            if t.kind not in ("conditional", "otherwise"):
                continue
            data = None
            for atoms in _branch_atoms(t, branches):
                data = _solve(ctx, resolver, base, atoms)
                if data is not None:
                    break
            kind = "fault" if t.to_id in throws else "branch"
            if data is None or data in seen_inputs[kind]:
                continue  # de-duplicated by content within a kind only
            if t.kind == "otherwise" and any(data in v for v in seen_inputs.values()):
                continue  # an otherwise path another scenario already takes (usually the happy one)
            seen_inputs[kind].append(data)
            name = f"{t.from_id} -> {t.to_id}" if t.to_id else f"{t.from_id}: no condition holds"
            scenarios.append(Scenario(id=_scenario_id(process, kind, t.id), process_id=process.id,
                                      name=name, kind=kind, input=data))
    for rid, path, resource, name in _http_targets(process, app):
        timeout = int((resource.settings.get("timeout_ms") if resource else None) or 30_000)
        key = f"{rid}{path}"
        label = resource.name if resource else name
        scenarios.append(Scenario(
            id=_scenario_id(process, "timeout", label), process_id=process.id,
            name=f"{label} times out", kind="external_failure", input=copy.deepcopy(base),
            stub_responses={key: StubResponse(status=200, body=None, delay_ms=timeout + 1)}))
        scenarios.append(Scenario(
            id=_scenario_id(process, "5xx", label), process_id=process.id,
            name=f"{label} returns 503", kind="external_failure", input=copy.deepcopy(base),
            stub_responses={key: StubResponse(status=503, body={"error": "SERVICE_UNAVAILABLE"})}))
    return scenarios


def _covered(scenario: Scenario) -> set[str]:
    out: set[str] = set()
    for step in scenario.expected.trace if scenario.expected else []:
        if "/" in step.activity_id:
            continue
        out.add(step.activity_id)
        out.update((step.chosen_transition or "").split(",") if step.chosen_transition else [])
    return out


def _quality(result: Any) -> tuple[int, int, int]:
    """Happy-path ranking: no fault or error reply, then no error link, then the longer path."""
    status = result.output.get("status") if isinstance(result.output, dict) else None
    ok = result.fault is None and not (isinstance(status, int) and status >= 400)
    errors = sum(1 for s in result.trace if s.output.get("fault"))
    return (1 if ok else 0, -errors, len(result.trace))


def _mutations(data: dict[str, Any], prefix: tuple[str, ...] = ()) -> Iterable[tuple[tuple[str, ...], Any]]:
    for key, value in data.items():
        path = (*prefix, key)
        if isinstance(value, dict):
            yield from _mutations(value, path)
        elif isinstance(value, str) and value:
            number = to_decimal(value)
            if number is not None:
                yield path, str(number * 2)
                yield path, "0"
            else:
                yield path, value[:-1] + ("Y" if value.endswith("X") else "X")
            yield path, ""
        elif isinstance(value, bool):
            yield path, not value
        elif isinstance(value, int):
            yield path, value * 2
            yield path, 0


def took_link(scenario: Scenario) -> bool:
    """Whether a ``branch:<link>`` / ``fault:<link>`` scenario really took its link."""
    kind, _, link = scenario.id.split(":", 1)[-1].partition(":")
    if kind not in ("branch", "fault") or not link or scenario.expected is None:
        return True
    top = [s for s in scenario.expected.trace if "/" not in s.activity_id]
    if link.endswith(":no-transition"):
        source = link.removesuffix(":no-transition")
        return any(s.activity_id == source and not s.chosen_transition for s in top)
    return any(link in (s.chosen_transition or "").split(",") for s in top)


async def fill_expected(scenarios: list[Scenario], evaluator: Evaluator) -> list[Scenario]:
    """Run every scenario; store the expected result and the stub responses it used.

    A ``branch``/``fault`` scenario whose run did not take its link (the solver's guess was
    wrong, e.g. an unsolvable sibling condition held) is dropped, with a warning.
    """
    kept = []
    for sc in scenarios:
        sc.expected = await evaluator.run(sc.process_id, sc)
        for key, response in evaluator.last_stub_responses.items():
            sc.stub_responses.setdefault(key, response)
        if took_link(sc):
            kept.append(sc)
        else:
            warnings.warn(f"scenario {sc.id} did not take its link; dropped", stacklevel=2)
    return kept


async def generate_and_evaluate(process: Process, contracts: list[Contract], app: Application,
                                evaluator: Evaluator, seed: SeedData | None = None,
                                *, max_variants: int = 10) -> list[Scenario]:
    """Static scenarios plus coverage closure, all with ``expected`` filled.

    The happy path is the first seed row whose run ends without a fault, an error reply or an
    error transition. Transitions (and ``throw`` activities) still uncovered afterwards, such as
    predicates over JDBC results, are searched for among the other seed rows and single-field
    mutations of the happy input; each one found becomes a ``branch`` or ``fault`` scenario.
    """
    seed = seed if seed is not None else SeedData.for_app(app, process)
    schema = SchemaIndex(contracts)
    ctx = _Ctx(process, app, schema, seed, starter_shape(process, app, schema), _written_tables(process, app))
    n_rows = max((len(r) for r in (seed.rows.values() if seed else [])), default=1)
    probes: list[tuple[int, dict[str, Any], Any]] = []
    best = 0
    best_quality: tuple[int, int, int] | None = None
    for k in range(min(n_rows, max_variants)):
        data = happy_input(ctx, k)
        if any(p[1] == data for p in probes):
            continue
        result = await evaluator.run(process.id, Scenario(id="probe", process_id=process.id, name="probe",
                                                          kind="happy", input=data))
        probes.append((k, data, result))
        q = _quality(result)
        if best_quality is None or q > best_quality:
            best, best_quality = k, q
    scenarios = generate_scenarios(process, contracts, app, seed, variant=best)
    scenarios = await fill_expected(scenarios, evaluator)
    covered: set[str] = set().union(*(_covered(s) for s in scenarios))
    targets = [t.id for t in process.transitions if t.kind in ("conditional", "otherwise")]
    targets += [a.id for a in process.activities if a.type == "throw"]
    throws = {a.id for a in process.activities if a.type == "throw"}
    leads_to = {t.id: t.to_id for t in process.transitions}
    missing = [t for t in targets if t not in covered]
    if not missing:
        return scenarios
    base = scenarios[0].input
    candidates: list[tuple[str, dict[str, Any]]] = [(f"seed row {k + 1}", d) for k, d, _ in probes if k != best]
    candidates += [(f"seed row {k + 1}", happy_input(ctx, k)) for k in range(min(n_rows, max_variants))
                   if all(p[0] != k for p in probes)]
    for path, value in _mutations(base):
        data = copy.deepcopy(base)
        set_path(data, None, path, value)
        candidates.append((f"{'.'.join(path)} = {value!r}", data))
    tried: list[dict[str, Any]] = [s.input for s in scenarios]
    for label, data in candidates:
        if not missing:
            break
        if data in tried:
            continue
        tried.append(data)
        probe = Scenario(id="probe", process_id=process.id, name=label, kind="branch", input=data)
        await fill_expected([probe], evaluator)
        hit = [t for t in missing if t in _covered(probe)]
        if not hit:
            continue
        first = hit[0]
        kind = "fault" if first in throws or leads_to.get(first) in throws else "branch"
        probe.id = _scenario_id(process, kind, first)
        probe.kind = kind
        probe.name = f"{first} ({label})"
        scenarios.append(probe)
        missing = [t for t in missing if t not in hit]
    return scenarios
