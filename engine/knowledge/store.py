"""Postgres + pgvector knowledge store (synchronous psycopg, schema ``knowledge``).

Mirrors ``engine.parity.db``: one lazily opened autocommit connection; wrap calls in
``asyncio.to_thread`` from async code. Holds the estate graph, the analysis chunks and the
migration-memory items. ``ensure_schema()`` is idempotent.
"""
from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from engine.domains.models import Edge, Graph, Node

from . import embeddings

SCHEMA = "knowledge"
SCHEMA_SQL = Path(__file__).with_name("schema.sql")
CHUNK_KINDS = ("summary", "rule", "finding", "decision", "design")
IVFFLAT_LISTS = 10

# Raw TIBCO process markup is never embedded or stored as knowledge: summaries and rules are
# written in prose, and the XML stays in the parsed model.
_RAW_XML = re.compile(r"<\s*/?\s*(?:bpws|pd|tibex)\s*:", re.IGNORECASE)


class KnowledgeError(Exception):
    """A write was refused (raw XML, unknown kind) or the store is inconsistent."""


def guard_text(text: str) -> None:
    if not text or not text.strip():
        raise KnowledgeError("empty text cannot be stored")
    if _RAW_XML.search(text):
        raise KnowledgeError(
            "raw TIBCO process XML (bpws:/pd:/tibex: markup) is never embedded; "
            "store a prose summary instead"
        )


def vector_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.7g}" for x in vec) + "]"


def chunk_id(project_id: str, process_id: str, kind: str, text: str) -> str:
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]
    return f"chunk:{project_id}:{process_id}:{kind}:{digest}"


class KnowledgeStore:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self._conn: psycopg.Connection | None = None
        self._backend_checked = False

    # ------------------------------------------------------------ connection
    def connection(self) -> psycopg.Connection:
        if self._conn is None or self._conn.closed:
            self._conn = psycopg.connect(
                self.dsn, autocommit=True, row_factory=dict_row, connect_timeout=5,
                options=f"-c search_path={SCHEMA},public",
            )
            # Exact recall on small tables: probe every list of the ivfflat indexes.
            self._conn.execute(f"SET ivfflat.probes = {IVFFLAT_LISTS}")
        return self._conn

    def close(self) -> None:
        if self._conn is not None and not self._conn.closed:
            self._conn.close()
        self._conn = None

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with self.connection().transaction():
            yield

    # ------------------------------------------------------------ schema
    def ensure_schema(self) -> None:
        """Create the extension, schema, tables and indexes if missing, and record which
        embedding backend the vectors in this database come from."""
        conn = self.connection()
        with conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(hashtext('exiqo.knowledge.schema'))")
            conn.execute("CREATE EXTENSION IF NOT EXISTS vector SCHEMA public")
            conn.execute(SCHEMA_SQL.read_text(encoding="utf-8"))
            conn.execute(
                "INSERT INTO knowledge.meta (key, value) VALUES ('embedding_backend', %s) "
                "ON CONFLICT (key) DO NOTHING",
                [embeddings.backend_name()],
            )
        self._backend_checked = False

    def reset(self, *, allow_destructive: bool = False) -> None:
        """Drop and recreate schema ``knowledge``. Refuses unless the database name ends with
        ``_test`` or ``allow_destructive=True``."""
        dbname = conninfo_to_dict(self.dsn).get("dbname") or ""
        if not dbname.endswith("_test") and not allow_destructive:
            raise ValueError(
                f"refusing to reset database {dbname!r}: name does not end with '_test' "
                "(pass allow_destructive=True to override)"
            )
        self.connection().execute(
            sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(SCHEMA))
        )
        self.ensure_schema()

    def reindex_vectors(self) -> None:
        """Rebuild the ivfflat indexes. Call after a bulk load so the 10 lists are trained on
        real data (they are created empty by ``ensure_schema``)."""
        conn = self.connection()
        conn.execute("REINDEX INDEX knowledge.chunks_embedding_idx")
        conn.execute("REINDEX INDEX knowledge.memory_items_embedding_idx")

    def check_embedding_backend(self) -> None:
        """Refuse to mix vectors from two embedding backends in one database."""
        if self._backend_checked:
            return
        row = self.connection().execute(
            "SELECT value FROM knowledge.meta WHERE key = 'embedding_backend'"
        ).fetchone()
        active = embeddings.backend_name()
        if row is not None and row["value"] != active:
            raise KnowledgeError(
                f"this knowledge store holds vectors from {row['value']!r} but the active "
                f"embedding backend is {active!r}; re-embed or reset the store"
            )
        self._backend_checked = True

    # ------------------------------------------------------------ graph
    def load_graph(self, project_id: str, graph: Graph) -> None:
        """Replace the stored estate graph of ``project_id`` with ``graph`` (one transaction)."""
        conn = self.connection()
        with conn.transaction():
            conn.execute("DELETE FROM knowledge.graph_edges WHERE project_id = %s", [project_id])
            conn.execute("DELETE FROM knowledge.graph_nodes WHERE project_id = %s", [project_id])
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO knowledge.graph_nodes (project_id, id, kind, name, attrs) "
                    "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (project_id, id) DO UPDATE "
                    "SET kind = EXCLUDED.kind, name = EXCLUDED.name, attrs = EXCLUDED.attrs",
                    [(project_id, n.id, n.kind, n.name, Jsonb(n.attrs)) for n in graph.nodes],
                )
                cur.executemany(
                    "INSERT INTO knowledge.graph_edges (project_id, src, dst, kind, attrs) "
                    "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (project_id, src, dst, kind) "
                    "DO UPDATE SET attrs = EXCLUDED.attrs",
                    [(project_id, e.src, e.dst, e.kind, Jsonb(e.attrs)) for e in graph.edges],
                )

    def get_graph(self, project_id: str) -> Graph:
        conn = self.connection()
        nodes = conn.execute(
            "SELECT id, kind, name, attrs FROM knowledge.graph_nodes WHERE project_id = %s "
            "ORDER BY id", [project_id],
        ).fetchall()
        edges = conn.execute(
            "SELECT src, dst, kind, attrs FROM knowledge.graph_edges WHERE project_id = %s "
            "ORDER BY src, dst, kind", [project_id],
        ).fetchall()
        return Graph(nodes=[Node(**r) for r in nodes], edges=[Edge(**r) for r in edges])

    def find_dependents(self, project_id: str, node_id: str, max_depth: int = 6) -> list[Node]:
        """Nodes that depend on ``node_id``: everything with a path of ``calls``, ``reads``,
        ``writes``, ``publishes``, ``consumes``, ``exposes`` or ``uses`` edges leading to it
        (so the dependents of a table are the processes or activities that read or write it,
        and of a process the processes that call it, transitively). ``contains`` edges are not
        followed upward, except that an activity found this way brings its owning process, so
        activity-level graphs answer at process level too. ``node_id`` itself is excluded."""
        rows = self.connection().execute(
            """
            WITH RECURSIVE dep(id, depth) AS (
                SELECT e.src, 1 FROM knowledge.graph_edges e
                 WHERE e.project_id = %(p)s AND e.dst = %(n)s AND e.kind <> 'contains'
                UNION
                SELECT e.src, d.depth + 1
                  FROM dep d
                  JOIN knowledge.graph_nodes dn ON dn.project_id = %(p)s AND dn.id = d.id
                  JOIN knowledge.graph_edges e ON e.project_id = %(p)s AND e.dst = d.id
                 WHERE d.depth < %(max)s AND (e.kind <> 'contains' OR dn.kind = 'activity')
            )
            SELECT DISTINCT n.id, n.kind, n.name, n.attrs
              FROM knowledge.graph_nodes n JOIN dep ON dep.id = n.id
             WHERE n.project_id = %(p)s AND n.id <> %(n)s
             ORDER BY n.id
            """,
            {"p": project_id, "n": node_id, "max": max_depth},
        ).fetchall()
        return [Node(**r) for r in rows]

    def neighbors(self, project_id: str, node_id: str, direction: str = "both") -> list[Node]:
        """Nodes one edge away: ``out`` (node is the source), ``in`` or ``both``."""
        if direction not in ("out", "in", "both"):
            raise ValueError("direction must be 'out', 'in' or 'both'")
        parts = []
        if direction in ("out", "both"):
            parts.append("SELECT dst AS id FROM knowledge.graph_edges "
                         "WHERE project_id = %(p)s AND src = %(n)s")
        if direction in ("in", "both"):
            parts.append("SELECT src AS id FROM knowledge.graph_edges "
                         "WHERE project_id = %(p)s AND dst = %(n)s")
        rows = self.connection().execute(
            "SELECT n.id, n.kind, n.name, n.attrs FROM knowledge.graph_nodes n "
            f"WHERE n.project_id = %(p)s AND n.id IN ({' UNION '.join(parts)}) ORDER BY n.id",
            {"p": project_id, "n": node_id},
        ).fetchall()
        return [Node(**r) for r in rows]

    # ------------------------------------------------------------ chunks
    def upsert_chunks(
        self,
        project_id: str,
        process_id: str,
        items: list[tuple[str, str]],
        *,
        replace: bool = False,
    ) -> list[str]:
        """Embed and store ``(kind, text)`` chunks for one process; returns the chunk ids.

        The id is derived from the content, so re-sending the same text is a no-op update.
        ``replace=True`` first deletes the process's existing chunks of the kinds being written.
        Text containing raw TIBCO XML is refused with ``KnowledgeError`` before anything is
        written."""
        for kind, text in items:
            if kind not in CHUNK_KINDS:
                raise KnowledgeError(f"unknown chunk kind {kind!r}")
            guard_text(text)
        if not items:
            return []
        self.check_embedding_backend()
        vectors = embeddings.embed([t for _, t in items])
        ids = [chunk_id(project_id, process_id, k, t) for k, t in items]
        conn = self.connection()
        with conn.transaction():
            if replace:
                conn.execute(
                    "DELETE FROM knowledge.chunks WHERE project_id = %s AND process_id = %s "
                    "AND kind = ANY(%s)", [project_id, process_id, sorted({k for k, _ in items})],
                )
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO knowledge.chunks (id, project_id, process_id, kind, text, "
                    "embedding) VALUES (%s, %s, %s, %s, %s, %s::vector) ON CONFLICT (id) DO "
                    "UPDATE SET text = EXCLUDED.text, embedding = EXCLUDED.embedding",
                    [
                        (cid, project_id, process_id, k, t, vector_literal(v))
                        for cid, (k, t), v in zip(ids, items, vectors, strict=True)
                    ],
                )
        return ids

    def count(self, table: str, project_id: str) -> int:
        if table not in ("chunks", "memory_items", "graph_nodes", "graph_edges"):
            raise ValueError(f"unknown table {table!r}")
        row = self.connection().execute(
            f"SELECT count(*) AS n FROM knowledge.{table} WHERE project_id = %s", [project_id]
        ).fetchone()
        return int(row["n"]) if row else 0
