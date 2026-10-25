"""Every Cypher statement of the memory graph, as module constants.

Labels and relationship types cannot be Cypher parameters, so statements per label or type are
rendered once at import by the private ``_render_*`` functions, only from the closed sets in
``model``; no other code builds Cypher and request values are always parameters.
"""
from __future__ import annotations

from engine.memory.model import EMBEDDED_LABELS, NODE_LABELS, REL_TYPES

EMBEDDING_DIMENSIONS = 384


def _render_rel_indexes(rel_type: str) -> tuple[str, ...]:
    name = f"rel_{rel_type.lower()}"
    return (
        (f"CREATE INDEX {name}_from IF NOT EXISTS FOR ()-[r:{rel_type}]-() "
         "ON (r.project_id, r.valid_from)"),
        (f"CREATE INDEX {name}_to IF NOT EXISTS FOR ()-[r:{rel_type}]-() "
         "ON (r.project_id, r.valid_to)"),
        (f"CREATE INDEX {name}_uid IF NOT EXISTS FOR ()-[r:{rel_type}]-() "
         "ON (r.project_id, r.uid)"),
    )


def _render_vector_index(label: str) -> str:
    return (
        f"CREATE VECTOR INDEX vec_{label.lower()} IF NOT EXISTS FOR (n:{label}) ON n.embedding "
        f"OPTIONS {{indexConfig: {{`vector.dimensions`: {EMBEDDING_DIMENSIONS}, "
        "`vector.similarity_function`: 'cosine'}}"
    )


def _render_fulltext_index() -> str:
    return (
        f"CREATE FULLTEXT INDEX mem_text IF NOT EXISTS FOR (n:{'|'.join(NODE_LABELS)}) "
        "ON EACH [n.text] OPTIONS {indexConfig: {`fulltext.analyzer`: 'english'}}"
    )


SCHEMA: tuple[str, ...] = (
    "CREATE CONSTRAINT mem_vid IF NOT EXISTS FOR (n:Mem) REQUIRE n.vid IS UNIQUE",
    "CREATE CONSTRAINT rev_rid IF NOT EXISTS FOR (r:Revision) REQUIRE r.rid IS UNIQUE",
    "CREATE CONSTRAINT rev_ikey IF NOT EXISTS FOR (r:Revision) REQUIRE r.ikey IS UNIQUE",
    "CREATE CONSTRAINT meta_key IF NOT EXISTS FOR (m:MemoryMeta) REQUIRE m.key IS UNIQUE",
    "CREATE INDEX mem_uid IF NOT EXISTS FOR (n:Mem) ON (n.project_id, n.uid)",
    "CREATE INDEX mem_scope IF NOT EXISTS FOR (n:Mem) ON (n.project_id, n.scope, n.valid_to)",
    "CREATE INDEX mem_from IF NOT EXISTS FOR (n:Mem) ON (n.project_id, n.valid_from)",
    "CREATE INDEX mem_to IF NOT EXISTS FOR (n:Mem) ON (n.project_id, n.valid_to)",
    "CREATE INDEX mem_target IF NOT EXISTS FOR (n:Mem) ON (n.project_id, n.target_uid)",
    "CREATE INDEX rev_n IF NOT EXISTS FOR (r:Revision) ON (r.project_id, r.n)",
    *(stmt for t in REL_TYPES for stmt in _render_rel_indexes(t)),
    *(_render_vector_index(label) for label in EMBEDDED_LABELS),
    _render_fulltext_index(),
)

SHOW_INDEX_NAMES = "SHOW INDEXES YIELD name RETURN name"
SHOW_CONSTRAINT_NAMES = "SHOW CONSTRAINTS YIELD name RETURN name"
PING = "RETURN 1 AS ok"
# Test-only (MemoryGraph.reset): deletes every node of the test database, keeps the schema.
RESET = "MATCH (n) CALL (n) { DETACH DELETE n } IN TRANSACTIONS OF 10000 ROWS"
