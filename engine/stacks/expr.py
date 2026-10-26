"""A language-neutral AST for the TIBCO XPath that code can write (target-stacks spec section 2).

``parse_simple`` reads the same subset the .NET generator translates: variable paths, string,
number and boolean literals, ``=  !=  <  <=  >  >=`` comparisons, ``and`` / ``or`` / ``not()``,
``count(path)`` and the functions in ``engine.dotnet_gen.xpath.FUNCTIONS`` with their argument
counts. Anything else (arithmetic, ``abs``, ``tib:*`` extensions, malformed text) gives ``None``:
the step keeps its TIBCO comment and goes to the Code Builder, in every stack.

Paths are read like the .NET generator reads them: namespace prefixes are dropped (a step
resolves by local name), ``[n]`` is a 1-based index, and an attribute may only be the last step.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from engine.dotnet_gen.xpath import FUNCTIONS
from engine.dotnet_gen.xpath import call as split_call

CompareOp = Literal["==", "!=", "<", "<=", ">", ">="]


@dataclass(frozen=True)
class Path:
    root: str  # the variable after ``$``: an activity name, ``Start``, ``get.parameters``
    steps: tuple[tuple[str, int | None], ...]  # (local name, 1-based index or None)
    attribute: bool  # the last step is an attribute (``@name``)


@dataclass(frozen=True)
class Lit:
    value: str | int | Decimal | bool


@dataclass(frozen=True)
class Compare:
    op: CompareOp
    left: Expr
    right: Expr


@dataclass(frozen=True)
class And:
    items: tuple[Expr, ...]


@dataclass(frozen=True)
class Or:
    items: tuple[Expr, ...]


@dataclass(frozen=True)
class Not:
    item: Expr


@dataclass(frozen=True)
class Count:
    path: Path


@dataclass(frozen=True)
class Call:
    name: str  # an XPath function from ``xpath.FUNCTIONS``, as TIBCO spells it (``upper-case``)
    args: tuple[Expr, ...]


Expr = Path | Lit | Compare | And | Or | Not | Count | Call

_PATH = re.compile(r"^\$([\w.\-]+)((?:/@?(?:[\w.\-]+:)?[\w.\-]+(?:\[\d+\])?)*)$")
_STEP = re.compile(r"^(@?)(?:[\w.\-]+:)?([\w.\-]+)(?:\[(\d+)\])?$")
_STRING = re.compile(r"""^(?:"([^"]*)"|'([^']*)')$""")
_INT = re.compile(r"^-?\d+$")
_DECIMAL = re.compile(r"^-?\d+\.\d+$")
_OPS: dict[str, CompareOp] = {"=": "==", "!=": "!=", "<": "<", "<=": "<=", ">": ">", ">=": ">="}


def _scan_top(expr: str):
    """Yield ``(index, char)`` for every character outside quotes and brackets."""
    depth, quote = 0, ""
    for i, c in enumerate(expr):
        if quote:
            if c == quote:
                quote = ""
            continue
        if c in "'\"":
            quote = c
        elif c in "([":
            depth += 1
        elif c in ")]":
            depth -= 1
        elif depth == 0:
            yield i, c


def _balanced(expr: str) -> bool:
    depth, quote = 0, ""
    for c in expr:
        if quote:
            quote = "" if c == quote else quote
        elif c in "'\"":
            quote = c
        elif c in "([":
            depth += 1
        elif c in ")]":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0 and not quote


def _split_word(expr: str, word: str) -> list[str]:
    """``expr`` split on `` word `` outside quotes and brackets."""
    token = f" {word} "
    parts, start = [], 0
    for i, _ in _scan_top(expr):
        if i >= start and expr.startswith(token, i):
            parts.append(expr[start:i])
            start = i + len(token)
    parts.append(expr[start:])
    return [p.strip() for p in parts]


def _compare_split(expr: str) -> tuple[str, str, str] | None:
    """The first top-level comparison: (left, operator, right)."""
    for i, c in _scan_top(expr):
        if c in "=!<>":
            op = expr[i:i + 2] if expr[i:i + 2] in ("!=", "<=", ">=") else c
            if op == "!":
                return None
            return expr[:i].strip(), op, expr[i + len(op):].strip()
    return None


def _path(expr: str) -> Path | None:
    m = _PATH.match(expr)
    if not m:
        return None
    steps: list[tuple[str, int | None]] = []
    attribute = False
    parts = [p for p in m.group(2).split("/") if p]
    for n, part in enumerate(parts):
        s = _STEP.match(part)
        if not s:
            return None
        if s.group(1):
            if n != len(parts) - 1:
                return None
            attribute = True
        steps.append((s.group(2), int(s.group(3)) if s.group(3) else None))
    return Path(m.group(1), tuple(steps), attribute)


def _operand(expr: str) -> Expr | None:
    """A value: literal, path, ``count(path)`` or a translatable function call."""
    e = expr.strip()
    if not e:
        return None
    if m := _STRING.match(e):
        return Lit(m.group(1) if m.group(1) is not None else m.group(2))
    if _INT.match(e):
        return Lit(int(e))
    if _DECIMAL.match(e):
        return Lit(Decimal(e))
    if e in ("true()", "false()"):
        return Lit(e == "true()")
    if e.startswith("$"):
        return _path(e)
    fn = split_call(e)
    if fn is None:
        return None
    name, args = fn
    if name == "count":
        inner = _path(args[0].strip()) if len(args) == 1 else None
        return Count(inner) if inner is not None else None
    counts = FUNCTIONS.get(name)
    if counts is None or len(args) not in counts:
        return None
    parsed = [_operand(a) for a in args]
    if any(p is None for p in parsed):
        return None
    return Call(name, tuple(parsed))  # type: ignore[arg-type]


def parse_simple(expr: str) -> Expr | None:
    """The AST of ``expr``, or ``None`` when code cannot write it (see the module docstring)."""
    e = " ".join(str(expr or "").split())
    if not e or not _balanced(e):
        return None
    for word, node in (("or", Or), ("and", And)):
        parts = _split_word(e, word)
        if len(parts) > 1:
            items = [parse_simple(p) for p in parts]
            return None if any(i is None for i in items) else node(tuple(items))  # type: ignore[arg-type]
    if e.startswith("(") and e.endswith(")") and _balanced(e[1:-1]):
        return parse_simple(e[1:-1])
    if e.startswith("not(") and e.endswith(")") and _balanced(e[4:-1]):
        inner = parse_simple(e[4:-1])
        return None if inner is None else Not(inner)
    cmp = _compare_split(e)
    if cmp is not None:
        left, op, right = cmp
        lhs, rhs = _operand(left), _operand(right)
        if lhs is None or rhs is None or op not in _OPS:
            return None
        return Compare(_OPS[op], lhs, rhs)
    return _operand(e)
