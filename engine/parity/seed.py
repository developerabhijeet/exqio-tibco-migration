"""Rows of the estate's seed SQL and primary keys of its schema SQL (design spec 6.3).

Generated inputs reuse seeded ids so JDBC lookups hit; keys of tables a process inserts into
get fresh values shaped like the seeded ones.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from engine.ir.models import Application, Process

# --- seed data --------------------------------------------------------------------


_INSERT = re.compile(r"insert\s+into\s+([\w.]+)\s*\(([^)]*)\)\s*values\s*", re.IGNORECASE)


def _split_top(text: str, sep: str = ",") -> list[str]:
    out, depth, quote, start = [], 0, False, 0
    i = 0
    while i < len(text):
        c = text[i]
        if quote:
            if c == "'":
                if i + 1 < len(text) and text[i + 1] == "'":
                    i += 1
                else:
                    quote = False
        elif c == "'":
            quote = True
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        elif c == sep and depth == 0:
            out.append(text[start:i])
            start = i + 1
        i += 1
    out.append(text[start:])
    return [s.strip() for s in out]


def _sql_literal(text: str) -> Any:
    t = text.strip()
    if t.lower() == "null":
        return None
    m = re.fullmatch(r"(?:date\s+)?'((?:[^']|'')*)'", t, re.IGNORECASE)
    if m:
        return m.group(1).replace("''", "'")
    if re.fullmatch(r"-?\d+(\.\d+)?", t):
        return t
    return t  # an expression such as ``current_date + 20``


class SeedData:
    """Rows of the estate's seed SQL plus the primary keys of its schema SQL."""

    def __init__(self, rows: dict[str, list[dict[str, Any]]], primary_keys: dict[str, str] | None = None):
        self.rows = rows
        self.primary_keys = primary_keys or {}

    @classmethod
    def from_sql(cls, seed_sql: str, schema_sql: str = "") -> SeedData:
        rows: dict[str, list[dict[str, Any]]] = {}
        text = re.sub(r"--[^\n]*", "", seed_sql)
        for m in _INSERT.finditer(text):
            table = m.group(1).split(".")[-1]
            cols = [c.strip() for c in m.group(2).split(",")]
            rest = text[m.end():]
            end = _statement_end(rest)
            for tup in _split_top(rest[:end]):
                if tup.startswith("(") and tup.endswith(")"):
                    vals = [_sql_literal(v) for v in _split_top(tup[1:-1])]
                    rows.setdefault(table, []).append(dict(zip(cols, vals, strict=False)))
        keys: dict[str, str] = {}
        for m in re.finditer(r"create\s+table\s+([\w.]+)\s*\((.*?)\);", schema_sql, re.IGNORECASE | re.DOTALL):
            for line in m.group(2).splitlines():
                pk = re.match(r"\s*(\w+)\s+.*primary\s+key", line, re.IGNORECASE)
                if pk:
                    keys[m.group(1).split(".")[-1]] = pk.group(1)
        return cls(rows, keys)

    @classmethod
    def for_app(cls, app: Application, process: Process | None = None) -> SeedData | None:
        root = estate_root(app, process)
        if root is None:
            return None
        seed, schema = "", ""
        for rel in app.sql_files:
            text = (root / rel).read_text(encoding="utf-8")
            if re.search(r"create\s+table", text, re.IGNORECASE):
                schema += text + "\n"
            if re.search(r"insert\s+into", text, re.IGNORECASE):
                seed += text + "\n"
        return cls.from_sql(seed, schema)

    def fresh_value(self, table: str, column: str) -> str | None:
        """A value of the same shape as the seeded ones, past the largest (for new keys)."""
        values = [str(r[column]) for r in self.rows.get(table, []) if r.get(column) is not None]
        best = None
        for v in values:
            m = re.search(r"(\d+)(?!.*\d)", v)
            if m and (best is None or int(m.group(1)) > best[0]):
                best = (int(m.group(1)), v, m)
        if best is None:
            return None
        n, v, m = best
        digits = m.group(1)
        return v[:m.start(1)] + str(n + 1).zfill(len(digits)) + v[m.end(1):]


def _statement_end(text: str) -> int:
    quote = False
    for i, c in enumerate(text):
        if c == "'":
            quote = not quote
        elif c == ";" and not quote:
            return i
    return len(text)


def estate_root(app: Application, process: Process | None = None) -> Path | None:
    """The folder ``app.sql_files`` are relative to, found above a process or contract path."""
    if not app.sql_files:
        return None
    starts = [Path(p.source_path) for p in ([process] if process else []) + list(app.processes)]
    starts += [Path(c.path) for c in app.contracts]
    for start in starts:
        for parent in start.parents:
            if (parent / app.sql_files[0]).exists():
                return parent
    return None


def snake(name: str) -> str:
    return re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", name).lower()

