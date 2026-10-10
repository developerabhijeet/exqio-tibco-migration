"""Coverage matrix: which parts of a process are explained by an analyst-visible rule.

Items:

* every activity except ``noop``/``end`` markers (ref = activity id);
* every ``conditional``, ``otherwise`` and ``error`` transition (ref = transition id);
* every input binding, i.e. mapping (ref = ``<activity id>:<binding target>``).

``covered_by`` lists the rule ids that reference the item; ``technical_reason`` comes
from the analyst's ``technical`` declarations. An item counts as covered when it has
either. Rules that are ``rejected`` or ``unverified`` never count as cover.
"""
from __future__ import annotations

from engine.analysis.evidence import normalize
from engine.analysis.models import CoverageItem, CoverageResult, Rule
from engine.ir.models import Process

_MARKERS = {"noop", "end"}
_COVERING = {"draft", "approved", "edited"}


def coverage_matrix(process: Process, rules: list[Rule], technical: dict[str, str]) -> CoverageResult:
    live = [r for r in rules if r.status in _COVERING]
    # one pass over citations, normalised once
    cites = [(r.id, c.activity_id, c.kind, normalize(c.text)) for r in live for c in r.citations]

    def rules_where(pred) -> list[str]:
        out: list[str] = []
        for rid, aid, kind, text in cites:
            if rid not in out and pred(aid, kind, text):
                out.append(rid)
        return out

    items: list[CoverageItem] = []
    seen: set[tuple[str, str]] = set()

    def add(kind: str, ref: str, covered_by: list[str]) -> None:
        if (kind, ref) in seen:
            return
        seen.add((kind, ref))
        items.append(CoverageItem(kind=kind, ref=ref, covered_by=covered_by, technical_reason=technical.get(ref)))

    for act in process.activities:
        if act.type in _MARKERS:
            continue
        add("activity", act.id, rules_where(lambda aid, _k, _t, a=act.id: aid == a))

    for tr in process.transitions:
        if tr.kind == "success":
            continue
        expr = normalize(tr.expression) if tr.expression else ""
        if expr:
            by = rules_where(lambda aid, k, t, f=tr.from_id, e=expr: aid == f and k == "condition" and t and t in e)
        else:
            # an otherwise (or bare error) edge is the complement of its source's conditions:
            # a rule that cites any condition on the source activity explains it
            by = rules_where(lambda aid, k, _t, f=tr.from_id: aid == f and k == "condition")
        add("transition", tr.id, by)

    for act in process.activities:
        for b in act.input_bindings:
            expr = normalize(b.expression)
            by = rules_where(lambda aid, k, t, a=act.id, e=expr: aid == a and k == "expression" and t and t in e)
            add("mapping", f"{act.id}:{b.target}", by)

    covered = sum(1 for i in items if i.covered_by or i.technical_reason)
    return CoverageResult(covered=covered, total=len(items), items=items)
