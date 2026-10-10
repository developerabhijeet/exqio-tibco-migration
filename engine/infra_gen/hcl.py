"""Tiny HCL emitter that writes the canonical layout ``terraform fmt`` and ``terragrunt hcl fmt`` keep.

Canonical rules honoured here: two-space indent, one space around ``=``, consecutive single-line
attributes aligned on ``=``, and a multi-line value (object or list) standing alone, which ends the
alignment group around it. ``hclfmt`` is the final judge: the validators run it over the output.
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
INDENT = "  "


class Expr(str):
    """A raw HCL expression (a reference or a function call), emitted without quotes."""


def quote(text: str) -> str:
    """A double-quoted HCL string. ``${`` and ``%{`` are escaped so the text stays literal."""
    s = text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t")
    return '"' + s.replace("${", "$${").replace("%{", "%%{") + '"'


def interp(text: str) -> Expr:
    """A quoted string that keeps its ``${...}`` interpolations; the caller guarantees safe text."""
    return Expr('"' + text + '"')


def _key(k: str) -> str:
    return k if _IDENT.match(k) else quote(k)


def _is_multiline(value: Any) -> bool:
    if isinstance(value, Mapping):
        return bool(value)
    if isinstance(value, Sequence) and not isinstance(value, str | Expr):
        return any(isinstance(v, Mapping | list | tuple) for v in value) or len(value) > 3
    return False


def scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, Expr):
        return str(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return repr(value)
    if isinstance(value, str):
        return quote(value)
    raise TypeError(f"not a scalar: {value!r}")


def value(v: Any, level: int) -> str:
    """Render ``v`` for an attribute value that starts at indent ``level``."""
    if isinstance(v, Mapping):
        if not v:
            return "{}"
        return "{\n" + attrs(v, level + 1) + INDENT * level + "}"
    if isinstance(v, Sequence) and not isinstance(v, str | Expr):
        if not v:
            return "[]"
        if not _is_multiline(v):
            return "[" + ", ".join(scalar(x) for x in v) + "]"
        pad = INDENT * (level + 1)
        lines = [pad + value(x, level + 1) + "," for x in v]
        return "[\n" + "\n".join(lines) + "\n" + INDENT * level + "]"
    return scalar(v)


def attrs(items: Mapping[str, Any], level: int = 0, spaced: bool = False) -> str:
    """Attributes of one body, aligned per ``hclwrite``: single-line runs align, multi-line stand alone.

    With ``spaced`` a blank line separates each group (a run of single-line attributes, or one
    multi-line attribute), which reads better in long ``inputs`` blocks and does not change alignment.
    """
    out: list[str] = []
    run: list[tuple[str, str]] = []
    pad = INDENT * level

    def gap() -> None:
        if spaced and out:
            out.append("\n")

    def flush() -> None:
        if not run:
            return
        gap()
        width = max(len(k) for k, _ in run)
        out.extend(f"{pad}{k.ljust(width)} = {v}\n" for k, v in run)
        run.clear()

    for k, v in items.items():
        rendered = value(v, level)
        if "\n" in rendered:
            flush()
            gap()
            out.append(f"{pad}{_key(k)} = {rendered}\n")
        else:
            run.append((_key(k), rendered))
    flush()
    return "".join(out)
