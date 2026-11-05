"""Python source text helpers: literals, comments, docstrings and import order."""
from __future__ import annotations

import json
import re

PLACEHOLDER = 'raise NotImplementedError("agent fills: {what}")'
_SELECTOR_ATOM = re.compile(r"^\s*([A-Za-z_][\w.]*)\s*=\s*'([^']*)'\s*$")


def one_line(text: str) -> str:
    return " ".join(str(text or "").split())


def py_string(text: str) -> str:
    """A double-quoted Python string literal (JSON escapes are Python escapes); non-ASCII kept."""
    return json.dumps(str(text), ensure_ascii=False)


def comment(text: str) -> str:
    """Text safe inside a ``#`` comment: one line."""
    return one_line(text)


def doc(text: str) -> str:
    """Text safe inside a one-line ``\"\"\"docstring\"\"\"``: no backslash escapes, no closing quotes."""
    t = one_line(text).replace("\\", "\\\\").replace('"', "'")
    return t


def tibco(target: str, expression: str, pad: str) -> str:
    """The ``# TIBCO: <target> = <expression>`` line a mapping keeps for every binding."""
    return f"{pad}# TIBCO: {comment(target)} = {comment(expression)}"


def placeholder(what: str, pad: str) -> str:
    """``raise NotImplementedError("agent fills: <what>")`` at ``pad``."""
    return pad + PLACEHOLDER.format(what=comment(what).replace("\\", "\\\\").replace('"', '\\"'))


def sql_literal(text: str) -> str:
    """A Python string holding ``text`` exactly: a triple-quoted raw-free literal when it spans
    lines (backslashes and quotes escaped), else one double-quoted line."""
    body = str(text).replace("\r\n", "\n")
    if "\n" not in body:
        return py_string(body)
    escaped = body.replace("\\", "\\\\").replace('"""', '\\"\\"\\"')
    if escaped.endswith('"'):
        escaped = escaped[:-1] + '\\"'
    return f'"""{escaped}"""'


def selector_properties(selector: str | None) -> list[tuple[str, str]] | None:
    """The ``(property, value)`` pairs of a JMS selector made only of ``name = 'value'`` atoms joined
    with ``AND``; ``[]`` for no selector, ``None`` for anything else (the agent writes the match)."""
    if not selector:
        return []
    atoms = [_SELECTOR_ATOM.match(a) for a in re.split(r"(?i)\s+and\s+", selector)]
    if not all(atoms):
        return None
    return [(m.group(1), m.group(2)) for m in atoms if m]


def imports(lines: set[str]) -> list[str]:
    """Import lines in isort order: ``__future__``, standard library, third party, the package."""
    def rank(line: str) -> tuple[int, int, str, str]:
        mod = line.split()[1]
        top = mod.split(".")[0]
        plain = 0 if line.startswith("import ") else 1  # isort: "import x" before "from x import y"
        if top == "__future__":
            return 0, plain, mod, line
        if top in _STDLIB:
            return 1, plain, mod, line
        if top in _THIRD_PARTY:
            return 2, plain, mod, line
        return 3, plain, mod, line

    out: list[str] = []
    last = None
    for ln in sorted(lines, key=rank):
        group = rank(ln)[0]
        if last is not None and group != last:
            out.append("")
        out.append(ln)
        last = group
    return out


_STDLIB = frozenset({"asyncio", "contextlib", "dataclasses", "datetime", "decimal", "json", "logging", "os", "re",
                     "time", "typing", "uuid", "collections", "functools", "pathlib", "threading"})
_THIRD_PARTY = frozenset({"fastapi", "starlette", "pydantic", "psycopg", "jwt", "azure", "httpx", "uvicorn",
                          "pytest"})
