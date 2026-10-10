"""Process complexity score, 0 to 10 (design spec section 4.4).

Each factor contributes weighted points; the factors dict holds those points,
so ``sum(factors.values())`` equals the score before clamping. Bands: low up
to 3, medium 4 to 6, high 7 and above (the continuous score is banded at the
half-point, so 3.5 and above is medium and 6.5 and above is high).

A branch is any transition that is not a plain success edge, so conditional,
otherwise and error transitions all count.

Weights are calibrated so that a process with 25 activities, 8 branches and an
XSLT transform scores exactly 7.0 on those three factors alone.
"""
import re

from .models import Complexity, Group, Process

# factor -> (weight in points, saturation value at which the factor is maxed)
_WEIGHTS: dict[str, float] = {
    "activities": 2.5,
    "branches": 2.5,
    "mapping_size": 1.0,
    "functions": 0.5,
    "xslt": 2.0,
    "java": 1.0,
    "subprocess_depth": 0.5,
    "group_nesting": 0.5,
    "external_systems": 0.5,
}
_SATURATION: dict[str, float] = {
    "activities": 25,
    "branches": 8,
    "mapping_size": 1500,  # characters of binding expression text
    "functions": 15,  # function calls in expressions
    "subprocess_depth": 3,
    "group_nesting": 3,
    "external_systems": 4,
}
_EXTERNAL_FAMILIES = {"rest", "http", "soap", "jms", "jdbc", "file"}
_FUNC_CALL = re.compile(r"(?<![\w$])([A-Za-z_][\w.-]*(?::[A-Za-z_][\w.-]*)?)\s*\(")
_NODE_TESTS = {"text", "node", "comment", "processing-instruction"}


def _frac(value: float, factor: str) -> float:
    return min(max(value, 0.0) / _SATURATION[factor], 1.0)


def _expressions(process: Process) -> list[str]:
    out = [b.expression for a in process.activities for b in a.input_bindings]
    out.extend(t.expression for t in process.transitions if t.expression)
    return out


def _group_depth(groups: list[Group]) -> int:
    """Deepest chain of groups containing groups (1 for a single flat group, 0 for none)."""
    if not groups:
        return 0
    ids = {g.id for g in groups}
    children = {g.id: [m for m in g.member_ids if m in ids and m != g.id] for g in groups}
    memo: dict[str, int] = {}

    def depth(gid: str, seen: set[str]) -> int:
        if gid in memo:
            return memo[gid]
        if gid in seen:  # cycle in malformed input; stop
            return 0
        sub = [depth(c, seen | {gid}) for c in children[gid]]
        memo[gid] = 1 + (max(sub) if sub else 0)
        return memo[gid]

    return max(depth(g.id, set()) for g in groups)


def _band(score: float) -> str:
    if score < 3.5:
        return "low"
    if score < 6.5:
        return "medium"
    return "high"


def score(process: Process, subprocess_depth: int | None = None) -> Complexity:
    """Score a process.

    ``subprocess_depth`` is the depth of the call chain below this process. It
    needs the whole application, so the caller may supply it; when omitted the
    number of distinct called processes is used as a local proxy.
    """
    types = [a.type for a in process.activities]
    exprs = _expressions(process)

    branches = sum(1 for t in process.transitions if t.kind != "success")
    mapping_chars = sum(len(e) for e in exprs)
    functions = sum(
        1 for e in exprs for m in _FUNC_CALL.finditer(e) if m.group(1).lower() not in _NODE_TESTS
    )
    has_xslt = "xslt.transform" in types or any(
        b.language == "xslt" for a in process.activities for b in a.input_bindings
    )
    has_java = "java.invoke" in types
    if subprocess_depth is None:
        subprocess_depth = len({c.target_process for c in process.calls})
    families = {t.split(".", 1)[0] for t in types if "." in t} & _EXTERNAL_FAMILIES
    if process.starter and process.starter.kind in _EXTERNAL_FAMILIES:
        families.add(process.starter.kind)

    raw = {
        "activities": _frac(len(process.activities), "activities"),
        "branches": _frac(branches, "branches"),
        "mapping_size": _frac(mapping_chars, "mapping_size"),
        "functions": _frac(functions, "functions"),
        "xslt": 1.0 if has_xslt else 0.0,
        "java": 1.0 if has_java else 0.0,
        "subprocess_depth": _frac(subprocess_depth, "subprocess_depth"),
        "group_nesting": _frac(_group_depth(process.groups), "group_nesting"),
        "external_systems": _frac(len(families), "external_systems"),
    }
    factors = {name: round(raw[name] * _WEIGHTS[name], 3) for name in _WEIGHTS}
    total = round(min(sum(factors.values()), 10.0), 2)
    return Complexity(score=total, band=_band(total), factors=factors)  # type: ignore[arg-type]
