"""Scenario database: the seeded test Postgres the evaluator runs BW ``jdbc.*`` SQL against.

Synchronous psycopg (wrap in ``asyncio.to_thread`` from async callers). One lazily opened
autocommit connection with ``search_path = gmf, public``; ``transaction()`` wraps a
transaction (a savepoint when nested) that rolls back when the block raises.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from engine.parity.models import SeedRow

SCHEMA = "gmf"


def qmark_to_format(statement: str) -> str:
    """Convert BW JDBC ``?`` placeholders to psycopg ``%s``.

    ``?`` inside single-quoted literals, double-quoted identifiers and ``--`` comments is left
    alone, and literal ``%`` becomes ``%%`` so psycopg does not read it as a placeholder.
    """
    out: list[str] = []
    i, n = 0, len(statement)
    while i < n:
        c = statement[i]
        if c in ("'", '"'):
            j = i + 1
            while j < n:
                if statement[j] == c:
                    if j + 1 < n and statement[j + 1] == c:
                        j += 2
                        continue
                    break
                j += 1
            out.append(statement[i:j + 1].replace("%", "%%"))
            i = j + 1
        elif c == "-" and statement.startswith("--", i):
            j = statement.find("\n", i)
            j = n if j == -1 else j
            out.append(statement[i:j].replace("%", "%%"))
            i = j
        elif c == "?":
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
    ) -> None:
        """Drop and recreate schema ``gmf``, run the schema and seed SQL, add extra rows.

        ``schema_sql`` and ``seed_sql`` are SQL text, or ``Path`` objects to SQL files. The
        whole reset is one transaction, so a failing seed leaves no half-built schema.
        """
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
