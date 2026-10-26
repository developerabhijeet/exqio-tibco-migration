"""JDBC ``?`` placeholders in the style each stack's driver binds (target-stacks spec section 2).

The TIBCO statement stays verbatim; only its placeholders change, in order:

==========  =================  ==========================================================
style       stack / driver     placeholders
==========  =================  ==========================================================
named_at    .NET / Npgsql      ``@p0, @p1, ...`` (``generator.to_named_parameters``)
jdbc        Java / PgJDBC      ``?`` unchanged: the statement is already JDBC
dollar      Go / pgx           ``$1, $2, ...``
pyformat    Python / psycopg   ``%s``; every literal ``%`` doubled, as psycopg requires
==========  =================  ==========================================================

Quoted literals (``E'...'`` too), quoted identifiers, ``--`` and nested ``/* */`` comments and
dollar-quoted strings are skipped, as are the jsonb operators ``?|`` and ``?&``. The PgJDBC escape
``??`` (a literal ``?`` operator) becomes ``?`` for the drivers that do not use ``?`` placeholders.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from engine.dotnet_gen.generator import _DOLLAR_TAG, _skip_quoted, to_named_parameters

PlaceholderStyle = Literal["named_at", "jdbc", "dollar", "pyformat"]


def _rewrite(sql: str, placeholder: Callable[[int], str], text: Callable[[str], str]) -> str:
    """``sql`` with the k-th placeholder (from 0) replaced by ``placeholder(k)`` and every other
    piece passed through ``text``. The scan is ``to_named_parameters``'s, byte for byte."""
    out: list[str] = []
    i, n, k = 0, len(sql), 0
    while i < n:
        c = sql[i]
        if c == "'":
            prev = sql[i - 1] if i else ""
            before = sql[i - 2] if i > 1 else ""
            escape = prev in "eE" and not (before.isalnum() or before == "_")
            j = _skip_quoted(sql, i, "'", escape)
        elif c == '"':
            j = _skip_quoted(sql, i, '"', False)
        elif sql.startswith("--", i):
            j = sql.find("\n", i)
            j = n if j == -1 else j
        elif sql.startswith("/*", i):
            depth, j = 1, i + 2
            while j < n and depth:
                if sql.startswith("/*", j):
                    depth, j = depth + 1, j + 2
                elif sql.startswith("*/", j):
                    depth, j = depth - 1, j + 2
                else:
                    j += 1
        elif c == "$" and (m := _DOLLAR_TAG.match(sql, i)) and (not i or not (sql[i - 1].isalnum() or sql[i - 1] == "_")):
            close = sql.find(m.group(0), m.end())
            j = n if close == -1 else close + len(m.group(0))
        elif c == "?":
            nxt = sql[i + 1:i + 3]
            if nxt[:1] == "?":  # PgJDBC escape: ?? is a literal ? operator, not a placeholder
                out.append(text("?"))
                i += 2
                continue
            if nxt[:1] in ("|", "&") and nxt[1:2] != nxt[:1]:
                out.append(text("?" + nxt[0]))
                i += 2
                continue
            out.append(placeholder(k))
            k += 1
            i += 1
            continue
        else:
            out.append(text(c))
            i += 1
            continue
        out.append(text(sql[i:j]))
        i = j
    return "".join(out)


def convert_placeholders(sql: str, style: PlaceholderStyle) -> str:
    """``sql`` with its JDBC placeholders in ``style`` (table above); ``ValueError`` for another style."""
    if style == "named_at":
        return to_named_parameters(sql)
    if style == "jdbc":
        return sql
    if style == "dollar":
        return _rewrite(sql, lambda k: f"${k + 1}", lambda s: s)
    if style == "pyformat":
        return _rewrite(sql, lambda k: "%s", lambda s: s.replace("%", "%%"))
    raise ValueError(f"unknown placeholder style {style!r}")
