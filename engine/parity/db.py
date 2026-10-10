"""Scenario database: the seeded test Postgres the evaluator runs BW ``jdbc.*`` SQL against.

Synchronous psycopg (wrap in ``asyncio.to_thread`` from async callers). One lazily opened
autocommit connection with ``search_path = gmf, public``; ``transaction()`` wraps a
transaction (a savepoint when nested) that rolls back when the block raises.
"""
from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from engine.parity.models import SeedRow

SCHEMA = "gmf"


_DOLLAR_TAG = re.compile(r"\$(?:[A-Za-z_\u0080-\uffff][\w\u0080-\uffff]*)?\$")


def _skip_quoted(statement: str, i: int, quote: str, backslash: bool) -> int:
    """Index just past the quoted literal starting at ``i`` (``statement[i] == quote``)."""
    j, n = i + 1, len(statement)
    while j < n:
        c = statement[j]
        if backslash and c == "\\":
            j += 2
            continue
        if c == quote:
            if j + 1 < n and statement[j + 1] == quote:
                j += 2
                continue
            return j + 1
        j += 1
    return n


def qmark_to_format(statement: str) -> str:
    """Convert BW JDBC ``?`` placeholders to psycopg ``%s``.

    Left alone (``?`` untouched, ``%`` doubled so psycopg does not read it as a placeholder):
    single-quoted literals (including ``E'..'`` backslash escapes), double-quoted identifiers,
    ``--`` and (nested) ``/* */`` comments, ``$tag$..$tag$`` dollar-quoted strings, and the
    jsonb operators ``?|`` and ``?&`` (but ``?||`` and ``?&&`` stay a placeholder followed by
    an operator).
    """
    out: list[str] = []
    i, n = 0, len(statement)

    def literal(text: str) -> None:
        out.append(text.replace("%", "%%"))

    while i < n:
        c = statement[i]
        if c == "'":
            prev = statement[i - 1] if i else ""
            before = statement[i - 2] if i > 1 else ""
            escape = prev in "eE" and not (before.isalnum() or before == "_")
            j = _skip_quoted(statement, i, "'", escape)
            literal(statement[i:j])
            i = j
        elif c == '"':
            j = _skip_quoted(statement, i, '"', False)
            literal(statement[i:j])
            i = j
        elif statement.startswith("--", i):
            j = statement.find("\n", i)
            j = n if j == -1 else j
            literal(statement[i:j])
            i = j
        elif statement.startswith("/*", i):
            depth, j = 1, i + 2
            while j < n and depth:
                if statement.startswith("/*", j):
                    depth, j = depth + 1, j + 2
                elif statement.startswith("*/", j):
                    depth, j = depth - 1, j + 2
                else:
                    j += 1
            literal(statement[i:j])
            i = j
        elif c == "$" and (m := _DOLLAR_TAG.match(statement, i)) and (
            not i or not (statement[i - 1].isalnum() or statement[i - 1] == "_")
        ):
            tag = m.group(0)
            close = statement.find(tag, m.end())
            j = n if close == -1 else close + len(tag)
            literal(statement[i:j])
            i = j
        elif c == "?":
            nxt = statement[i + 1:i + 3]
            if nxt[:1] in ("|", "&") and nxt[1:2] != nxt[:1]:
                out.append("?" + nxt[0])
                i += 2
            else:
                out.append("%s")
                i += 1
        elif c == "%":
            out.append("%%")
            i += 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _sql_text(source: str | Path) -> str:
    return source.read_text(encoding="utf-8") if isinstance(source, Path) else source


def _adapt(value: Any) -> Any:
    return Jsonb(value) if isinstance(value, (dict, list)) else value


class ScenarioDB:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self._conn: psycopg.Connection | None = None

    def _connection(self) -> psycopg.Connection:
        if self._conn is None or self._conn.closed:
            self._conn = psycopg.connect(
                self.dsn, autocommit=True, row_factory=dict_row, connect_timeout=5,
                options=f"-c search_path={SCHEMA},public",
            )
        return self._conn

    def close(self) -> None:
        if self._conn is not None and not self._conn.closed:
            self._conn.close()
        self._conn = None

    def reset(
        self,
        schema_sql: str | Path,
        seed_sql: str | Path,
        seed_rows: list[SeedRow] | None = None,
        *,
        allow_destructive: bool = False,
    ) -> None:
        """Drop and recreate schema ``gmf``, run the schema and seed SQL, add extra rows.

        ``schema_sql`` and ``seed_sql`` are SQL text, or ``Path`` objects to SQL files. The
        whole reset is one transaction, so a failing seed leaves no half-built schema.

        Refuses (``ValueError``, before connecting) unless the database name ends with
        ``_test`` or ``allow_destructive=True``, because it drops schema ``gmf``.
        """
        dbname = conninfo_to_dict(self.dsn).get("dbname") or ""
        if not dbname.endswith("_test") and not allow_destructive:
            raise ValueError(
                f"refusing to reset database {dbname!r}: name does not end with '_test' "
                "(pass allow_destructive=True to override)"
            )
        conn = self._connection()
        with conn.transaction():
            conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(SCHEMA)))
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(SCHEMA)))
            conn.execute(_sql_text(schema_sql))
            conn.execute(_sql_text(seed_sql))
            for seed in seed_rows or []:
                self._insert(conn, seed)

    @staticmethod
    def _insert(conn: psycopg.Connection, seed: SeedRow) -> None:
        schema, _, table = seed.table.rpartition(".")
        target = sql.Identifier(schema or SCHEMA, table)
        cols = list(seed.row)
        stmt = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
            target,
            sql.SQL(", ").join(sql.Identifier(c) for c in cols),
            sql.SQL(", ").join(sql.Placeholder() for _ in cols),
        )
        conn.execute(stmt, [_adapt(seed.row[c]) for c in cols])

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Commit on normal exit, roll back when the block raises (nested: savepoint)."""
        with self._connection().transaction():
            yield

    def execute(self, statement: str, params: list[Any] | tuple[Any, ...] | None = None
                ) -> list[dict[str, Any]] | int:
        """Run one statement with BW ``?`` placeholders; rows for queries, else the rowcount."""
        conn = self._connection()
        # psycopg only interprets placeholders (and %%) when parameters are supplied.
        values = [_adapt(p) for p in params] if params else None
        text = qmark_to_format(statement) if values is not None else statement
        cur = conn.execute(text, values)
        if cur.description is not None:
            return list(cur.fetchall())
        return cur.rowcount
