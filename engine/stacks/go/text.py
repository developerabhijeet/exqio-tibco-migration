"""Go source text helpers: literals, comments, raw strings and import blocks."""
from __future__ import annotations

import re

PLACEHOLDER = 'panic("agent fills: {what}")'


def one_line(text: str) -> str:
    return " ".join(str(text or "").split())


def go_string(text: str) -> str:
    """A Go interpreted string literal (``"a\\"b"``); non-ASCII characters are kept (the source is UTF-8)."""
    out = []
    for c in str(text):
        if c == "\\":
            out.append("\\\\")
        elif c == '"':
            out.append('\\"')
        elif c == "\n":
            out.append("\\n")
        elif c == "\r":
            out.append("\\r")
        elif c == "\t":
            out.append("\\t")
        elif ord(c) < 0x20 or ord(c) == 0x7F:
            out.append(f"\\x{ord(c):02x}")
        elif c in "  ﻿":
            out.append(f"\\u{ord(c):04x}")
        else:
            out.append(c)
    return '"' + "".join(out) + '"'


def comment(text: str) -> str:
    """Text safe inside a ``//`` comment: one line."""
    return one_line(text).replace("﻿", "")


def doc(text: str) -> str:
    """Text for a doc comment: one line."""
    return comment(text)


def tibco(target: str, expression: str, pad: str) -> str:
    """The ``// TIBCO: <target> = <expression>`` line a mapping keeps for every binding."""
    return f"{pad}// TIBCO: {comment(target)} = {comment(expression)}"


def raw_string(text: str) -> str:
    """A Go expression holding ``text`` exactly: a raw string (backticks), with each backtick
    of the text concatenated as ``"`"`` (a raw string cannot hold one) and CRs normalised."""
    body = str(text).replace("\r\n", "\n").replace("\r", "\n")
    parts = body.split("`")
    return ' + "`" + '.join(f"`{p}`" for p in parts)


_SELECTOR_ATOM = re.compile(r"^\s*([A-Za-z_][\w.]*)\s*=\s*'([^']*)'\s*$")


def selector_properties(selector: str | None) -> list[tuple[str, str]] | None:
    """The ``(property, value)`` pairs of a JMS selector made only of ``name = 'value'`` atoms joined
    with ``AND``; ``[]`` for no selector, ``None`` for anything else (the agent writes the match)."""
    if not selector:
        return []
    atoms = [_SELECTOR_ATOM.match(a) for a in re.split(r"(?i)\s+and\s+", selector)]
    if not all(atoms):
        return None
    return [(m.group(1), m.group(2)) for m in atoms if m]


def import_block(imports: set[str] | list[str], local_prefix: str) -> list[str]:
    """``import (...)`` lines as gofmt keeps them: the standard library first, then the module's
    own and third-party packages, each group sorted; one import is a single line."""
    items = sorted(set(imports))
    if not items:
        return []
    std = [i for i in items if "." not in i.split("/")[0] and not i.startswith(local_prefix + "/")]
    other = [i for i in items if i not in std]
    if len(items) == 1:
        return [f"import {go_string(items[0])}"]
    lines = ["import ("]
    lines += [f"\t{go_string(i)}" for i in std]
    if std and other:
        lines.append("")
    lines += [f"\t{go_string(i)}" for i in other]
    lines.append(")")
    return lines


def package_name(import_path: str) -> str:
    """The package name an import path is referred to by: its last element without a ``/vN``."""
    parts = import_path.split("/")
    last = parts[-1]
    if re.fullmatch(r"v\d+", last) and len(parts) > 1:
        last = parts[-2]
    return re.sub(r"[^A-Za-z0-9_]", "", last)
