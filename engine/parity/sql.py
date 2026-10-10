"""Describe a BW JDBC write statement as the row it writes (side-effect payloads, lineage)."""
from __future__ import annotations

import re
from typing import Any

_INSERT_RE = re.compile(r"^\s*insert\s+into\s+([\w.\"]+)\s*\((.*?)\)\s*values\s*\((.*)\)\s*;?\s*$",
                        re.IGNORECASE | re.DOTALL)
_UPDATE_RE = re.compile(r"^\s*update\s+([\w.\"]+)\s+set\s+(.*?)(?:\s+where\s+(.*?))?\s*;?\s*$",
                        re.IGNORECASE | re.DOTALL)
_DELETE_RE = re.compile(r"^\s*delete\s+from\s+([\w.\"]+)(?:\s+where\s+(.*?))?\s*;?\s*$",
                        re.IGNORECASE | re.DOTALL)


def _split_sql(text: str) -> list[str]:
    out, depth, quote, start = [], 0, False, 0
    for i, c in enumerate(text):
        if c == "'":
            quote = not quote
        elif not quote and c == "(":
            depth += 1
        elif not quote and c == ")":
            depth -= 1
        elif not quote and depth == 0 and c == ",":
            out.append(text[start:i].strip())
            start = i + 1
    out.append(text[start:].strip())
    return out


def _fill_placeholders(exprs: list[str], values: list[Any], cursor: list[int]) -> list[Any]:
    out = []
    for e in exprs:
        if e == "?":
            out.append(values[cursor[0]] if cursor[0] < len(values) else None)
            cursor[0] += 1
        else:
            n = e.count("?")
            m = re.fullmatch(r"'((?:[^']|'')*)'", e)
            out.append(m.group(1).replace("''", "'") if m else e)
            cursor[0] += n
    return out


def sql_write_payload(sql: str, values: list[Any]) -> tuple[str, str, dict[str, Any], dict[str, str]]:
    """Describe a write statement: ``(verb, table, row, column -> parameter-or-expression)``.

    ``row`` maps each column written (and, for update/delete, each ``where`` column) to the
    bound value, or to the SQL expression text for non-parameter values (``current_timestamp``).
    """
    m = _INSERT_RE.match(sql)
    if m:
        cols = [c.strip().strip('"') for c in m.group(2).split(",")]
        exprs = _split_sql(m.group(3))
        vals = _fill_placeholders(exprs, values, [0])
        return "insert", _table(m.group(1)), dict(zip(cols, vals, strict=False)), dict(zip(cols, exprs, strict=False))
    m = _UPDATE_RE.match(sql)
    if m:
        cursor = [0]
        row: dict[str, Any] = {}
        sources: dict[str, str] = {}
        for assign in _split_sql(m.group(2)):
            col, _, expr = assign.partition("=")
            col, expr = col.strip().strip('"'), expr.strip()
            row[col] = _fill_placeholders([expr], values, cursor)[0]
            sources[col] = expr
        for col, expr in _where_pairs(m.group(3) or ""):
            row[f"where.{col}"] = _fill_placeholders([expr], values, cursor)[0]
            sources[f"where.{col}"] = expr
        return "update", _table(m.group(1)), row, sources
    m = _DELETE_RE.match(sql)
    if m:
        cursor = [0]
        row = {}
        sources = {}
        for col, expr in _where_pairs(m.group(2) or ""):
            row[f"where.{col}"] = _fill_placeholders([expr], values, cursor)[0]
            sources[f"where.{col}"] = expr
        return "delete", _table(m.group(1)), row, sources
    return "write", "", {f"param{i + 1}": v for i, v in enumerate(values)}, {}


def _where_pairs(where: str) -> list[tuple[str, str]]:
    return [(c, e.strip()) for c, e in re.findall(r"([\w.\"]+)\s*=\s*(\?|'[^']*'|[\w.]+)", where)]


def _table(name: str) -> str:
    return name.replace('"', "").split(".")[-1]
