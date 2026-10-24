"""The shape of a TIBCO mapping expression, read the same way by the design and by the generator.

The design decides whether a mapping goes to code or to the Code Builder before any C# exists, and the
generator later writes it. Both have to agree about what code can translate, so the structural facts
live here: how a function call splits into its arguments, and which functions have a single meaning
code can emit. The generator still has the last word — it marks a mapping ``agent`` when a path inside
one of these calls turns out not to resolve.
"""
from __future__ import annotations

import re

_CALL = re.compile(r"^([A-Za-z][\w\-]*)\s*\((.*)\)$", re.DOTALL)
_SIMPLE_PATH = re.compile(r"^\$[\w.\-]+(/@?[\w.\-]+(:[\w.\-]+)?(\[\d+\])?)*$")
_LITERAL = re.compile(r"""^("[^"]*"|'[^']*'|-?\d+(\.\d+)?)$""")

#: XPath functions the generator translates, each with the argument counts it accepts.
FUNCTIONS: dict[str, tuple[int, ...]] = {
    "concat": (2, 3, 4, 5, 6, 7, 8),
    "string": (1,),
    "number": (1,),
    "upper-case": (1,),
    "lower-case": (1,),
    "normalize-space": (1,),
    "string-length": (1,),
    "substring": (2, 3),
    "substring-before": (2,),
    "substring-after": (2,),
    "translate": (3,),
    "round": (1,),
    "floor": (1,),
    "ceiling": (1,),
    "current-dateTime": (0,),
    "current-date": (0,),
}


def _balanced(text: str) -> bool:
    """True when every bracket and quote in ``text`` closes inside it."""
    depth, quote = 0, ""
    for c in text:
        if quote:
            if c == quote:
                quote = ""
        elif c in "'\"":
            quote = c
        elif c in "([":
            depth += 1
        elif c in ")]":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0 and not quote


def call(expr: str) -> tuple[str, list[str]] | None:
    """``name(arg, arg)`` split into its name and top-level arguments, or ``None``.

    The outer parentheses have to be the call's own: ``concat(a, b)`` is a call, ``(a) + (b)`` is not.
    """
    m = _CALL.match((expr or "").strip())
    if not m or not _balanced(m.group(2)):
        return None
    inner = m.group(2).strip()
    if not inner:
        return m.group(1), []
    args: list[str] = []
    depth, quote, start = 0, "", 0
    for i, c in enumerate(inner):
        if quote:
            if c == quote:
                quote = ""
        elif c in "'\"":
            quote = c
        elif c in "([":
            depth += 1
        elif c in ")]":
            depth -= 1
        elif c == "," and depth == 0:
            args.append(inner[start:i].strip())
            start = i + 1
    args.append(inner[start:].strip())
    return m.group(1), args


def is_path_or_literal(expr: str) -> bool:
    """A plain variable path or a literal."""
    e = (expr or "").strip()
    return bool(_SIMPLE_PATH.match(e) or _LITERAL.match(e))


def is_translatable(expr: str) -> bool:
    """A path, a literal, or a call to a function in :data:`FUNCTIONS` over translatable arguments."""
    e = (expr or "").strip()
    if is_path_or_literal(e):
        return True
    fn = call(e)
    if fn is None:
        return False
    name, args = fn
    counts = FUNCTIONS.get(name)
    return counts is not None and len(args) in counts and all(is_translatable(a) for a in args)
