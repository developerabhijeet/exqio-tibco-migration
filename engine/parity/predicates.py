"""Transition predicates: parsing, input-path resolution and solving (design spec 6.3).

A condition becomes a disjunction of conjunctions of :class:`Atom` (``number($x) >= 680``,
``string-length($x) = 0``, ``exists($x)``, ``$x = 'A'``, ``$x = ('a', 'b')``,
``matches($x, 're')``); :class:`Resolver` maps an atom's XPath path to a scenario input path,
also through mapper aliases (``$NormalizeApplicant/tns:ssn`` is the input ssn).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from engine.ir.models import Process
from engine.parity.schema import starter_prefixes

# --- predicate solving ---------------------------------------------------------------------

_NUM = r"-?\d+(?:\.\d+)?"
_PATH = r"\$[A-Za-z_][\w.-]*(?:/[\w.*:-]+(?:\[\d+\])?)*"
_OPS = {">": "gt", ">=": "ge", "<": "lt", "<=": "le", "=": "eq", "!=": "ne"}
_FLIP = {"gt": "le", "ge": "lt", "lt": "ge", "le": "gt", "eq": "ne", "ne": "eq"}
_MIRROR = {"gt": "lt", "ge": "le", "lt": "gt", "le": "ge", "eq": "eq", "ne": "ne"}


@dataclass(frozen=True)
class Atom:
    """One solvable comparison: ``measure(path) op value``.

    ``measure`` is ``num`` (``number()``/``xsd:integer()``/bare path vs a number), ``len``
    (``string-length``), ``str`` (string equality), ``exists``, ``in`` (membership in a set of
    strings) or ``matches`` (a regular expression).
    """

    measure: str
    path: str
    op: str
    value: Any = None

    def negate(self) -> Atom:
        if self.measure == "exists":
            return Atom("exists", self.path, "eq", not self.value)
        if self.measure in ("in", "matches"):
            return Atom(self.measure, self.path, _FLIP[self.op], self.value)
        return Atom(self.measure, self.path, _FLIP[self.op], self.value)


def _split_bool(expr: str, word: str) -> list[str]:
    parts, depth, quote, start, i = [], 0, "", 0, 0
    token = f" {word} "
    while i < len(expr):
        c = expr[i]
        if quote:
            quote = "" if c == quote else quote
        elif c in "'\"":
            quote = c
        elif c in "([":
            depth += 1
        elif c in ")]":
            depth -= 1
        elif depth == 0 and expr.startswith(token, i):
            parts.append(expr[start:i])
            start = i + len(token)
            i = start
            continue
        i += 1
    parts.append(expr[start:])
    return [p.strip() for p in parts]


def _strip_parens(expr: str) -> str:
    expr = expr.strip()
    while expr.startswith("(") and expr.endswith(")"):
        depth = 0
        for i, c in enumerate(expr):
            depth += c == "("
            depth -= c == ")"
            if depth == 0 and i < len(expr) - 1:
                return expr
        expr = expr[1:-1].strip()
    return expr


def parse_condition(expr: str) -> list[list[Atom | None]]:
    """Disjunctive form of a transition condition: ``[[atom and atom], or [...]]``.

    An atom the solver does not understand is ``None`` (the conjunction is then only partly
    solvable and the coverage loop has to confirm it).
    """
    out = []
    for disjunct in _split_bool(_strip_parens(expr), "or"):
        out.append([parse_atom(a) for a in _split_bool(_strip_parens(disjunct), "and")])
    return out


def parse_atom(text: str) -> Atom | None:
    t = _strip_parens(text)
    neg = re.fullmatch(r"not\s*\((.*)\)", t, re.DOTALL)
    if neg:
        inner = parse_atom(neg.group(1))
        return inner.negate() if inner else None
    m = re.fullmatch(rf"(exists|empty)\(\s*({_PATH})\s*\)", t)
    if m:
        return Atom("exists", m.group(2), "eq", m.group(1) == "exists")
    m = re.fullmatch(rf"matches\(\s*({_PATH})\s*,\s*'([^']*)'\s*\)", t)
    if m:
        return Atom("matches", m.group(1), "eq", m.group(2))
    m = re.fullmatch(rf"({_PATH})\s*(!?=)\s*\(((?:\s*'[^']*'\s*,?)+)\)", t)
    if m:
        values = tuple(re.findall(r"'([^']*)'", m.group(3)))
        return Atom("in", m.group(1), "eq" if m.group(2) == "=" else "ne", values)
    m = re.fullmatch(r"(.+?)\s*(>=|<=|!=|=|>|<)\s*(.+)", t)
    if not m:
        return None
    left, op, right = m.group(1).strip(), _OPS[m.group(2)], m.group(3).strip()
    if re.fullmatch(_NUM, left) or re.fullmatch(r"'[^']*'", left):
        left, right, op = right, left, _MIRROR[op]
    measure, path = _operand(left)
    if path is None:
        return None
    if re.fullmatch(_NUM, right):
        value: Any = Decimal(right)
        if measure == "str":
            measure = "num"
    elif re.fullmatch(r"'[^']*'|\"[^\"]*\"", right):
        value = right[1:-1]
        if measure != "str":
            return None
    else:
        return None
    return Atom(measure, path, op, value)


def _operand(text: str) -> tuple[str, str | None]:
    t = _strip_parens(text)
    m = re.fullmatch(rf"(?:number|xsd?:integer|xsd?:int|xsd?:decimal|xsd?:double)\(\s*({_PATH})\s*\)", t)
    if m:
        return "num", m.group(1)
    m = re.fullmatch(rf"string-length\(\s*({_PATH})\s*\)", t)
    if m:
        return "len", m.group(1)
    m = re.fullmatch(rf"(?:string|normalize-space)\(\s*({_PATH})\s*\)", t)
    if m:
        return "str", m.group(1)
    if re.fullmatch(_PATH, t):
        return "str", t
    return "?", None


_TRANSPARENT = re.compile(
    r"^(?:normalize-space|upper-case|lower-case|string|number|tib:trim|xsd?:\w+)\((.*)\)$", re.DOTALL)


def _local_path(path: str) -> tuple[str, tuple[str, ...]]:
    """``$X/tns:a/b[1]`` -> (``$X``, (``a``, ``b``))."""
    var, *steps = path.split("/")
    return var, tuple(re.sub(r"\[\d+\]$", "", s).split(":")[-1] for s in steps if s)


class Resolver:
    """Map a condition's XPath path to a scenario input path (through mapper aliases)."""

    def __init__(self, process: Process) -> None:
        self.prefixes = starter_prefixes(process)
        self.aliases: dict[tuple[str, tuple[str, ...]], tuple[str | None, tuple[str, ...]]] = {}
        names = {a.name: a for a in process.activities}
        var_of = (lambda a: "$" + a.name.replace(" ", "-")) if process.dialect == "bw5" else (lambda a: "$" + a.name)
        for act in process.activities:
            if act.type not in ("mapper", "assign") or act.name not in names:
                continue
            for b in act.input_bindings:
                expr = b.expression.strip()
                while True:
                    m = _TRANSPARENT.match(expr)
                    if not m or "," in m.group(1):
                        break
                    expr = m.group(1).strip()
                if not re.fullmatch(_PATH, expr):
                    continue
                target = self._starter(expr)
                if target is None:
                    continue
                # BW6: $Mapper is the output element; BW5: $Mapper wraps it (root in the path)
                steps = tuple(b.target.split("/")[0 if process.dialect == "bw5" else 1:])
                self.aliases[(var_of(act), steps)] = target

    def _starter(self, path: str) -> tuple[str | None, tuple[str, ...]] | None:
        for prefix in sorted(self.prefixes, key=len, reverse=True):
            if prefix.endswith("/*"):
                base = prefix[:-2]
                if path.startswith(base + "/"):
                    rest = path[len(base) + 1:].split("/", 1)
                    steps = _local_path("$x/" + rest[1])[1] if len(rest) > 1 else ()
                    return self.prefixes[prefix], steps
            elif path == prefix or path.startswith(prefix + "/"):
                return self.prefixes[prefix], _local_path("$x" + path[len(prefix):])[1]
        return None

    def resolve(self, path: str) -> tuple[str | None, tuple[str, ...]] | None:
        direct = self._starter(path)
        if direct is not None:
            return direct
        var, steps = _local_path(path)
        return self.aliases.get((var, steps))


def get_path(data: dict, key: str | None, steps: tuple[str, ...]) -> Any:
    cur: Any = data.get(key) if key else data
    for s in steps:
        if not isinstance(cur, dict) or s not in cur:
            return None
        cur = cur[s]
    return cur


def set_path(data: dict, key: str | None, steps: tuple[str, ...], value: Any, *, remove: bool = False) -> None:
    cur = data.setdefault(key, {}) if key else data
    for s in steps[:-1]:
        cur = cur.setdefault(s, {})
    if remove:
        cur.pop(steps[-1], None)
    else:
        cur[steps[-1]] = value


def to_decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None


def solve_path(atoms: list[Atom], current: Any, integer: bool) -> tuple[bool, Any]:
    """A value satisfying every atom on one path (``(False, None)`` when contradictory).

    Returns ``(True, REMOVE)`` when the field should be absent.
    """
    if all(_holds(a, current) for a in atoms):
        return True, current
    candidates: list[Any] = []
    step = Decimal(1) if integer else Decimal("0.01")
    for a in atoms:
        if a.measure == "num" and isinstance(a.value, Decimal):
            v = a.value
            candidates += {"gt": [v + step], "ge": [v], "lt": [v - step], "le": [v],
                           "eq": [v], "ne": [v + step, v - step]}[a.op]
        elif a.measure == "len":
            n = int(a.value)
            want = {"gt": n + 1, "ge": n, "lt": n - 1, "le": n, "eq": n, "ne": n + 1}[a.op]
            if want <= 0:
                candidates += [REMOVE, ""]
            else:
                base = str(current or "")
                candidates.append((base + "X" * want)[:want])
        elif a.measure == "str":
            candidates.append(a.value if a.op == "eq" else f"{a.value}X")
        elif a.measure == "in":
            candidates.append(a.value[0] if a.op == "eq" else "ZZ")
        elif a.measure == "exists":
            candidates.append(current if a.value else REMOVE)
        elif a.measure == "matches" and a.op == "ne":
            candidates.append("INVALID")
    for c in candidates:
        if c is not REMOVE and isinstance(c, Decimal):
            c = int(c) if integer else c
        if all(_holds(a, c) for a in atoms):
            return True, c
    return False, None


class _Remove:
    def __repr__(self) -> str:
        return "<remove>"


REMOVE = _Remove()


def _holds(atom: Atom, value: Any) -> bool:
    absent = value is None or value is REMOVE
    if atom.measure == "exists":
        return (not absent) == bool(atom.value)
    if atom.measure == "len":
        n = 0 if absent else len(str(value))
        return _cmp(Decimal(n), atom.op, atom.value)
    if absent:
        return atom.op == "ne" and atom.measure in ("str", "in")
    if atom.measure == "num":
        v = to_decimal(value)
        return v is not None and _cmp(v, atom.op, atom.value)
    if atom.measure == "str":
        return (str(value) == atom.value) == (atom.op == "eq")
    if atom.measure == "in":
        return (str(value) in atom.value) == (atom.op == "eq")
    if atom.measure == "matches":
        return bool(re.search(atom.value, str(value))) == (atom.op == "eq")
    return False


def _cmp(v: Decimal, op: str, ref: Decimal) -> bool:
    return {"gt": v > ref, "ge": v >= ref, "lt": v < ref, "le": v <= ref, "eq": v == ref, "ne": v != ref}[op]

