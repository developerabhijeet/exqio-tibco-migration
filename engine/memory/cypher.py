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

# ------------------------------------------------------------------ versioned write path
# Current versions have no `valid_to`. Every write statement takes `$rows`, `$vids` or `$uids`
# batches of at most 1000 (MemoryGraph.apply_revision) and `$n`, the revision being applied.

# Current node versions owned by one of the delta scopes, or carrying one of the delta uids.
CURRENT_IN_SCOPE = (
    "MATCH (n:Mem {project_id: $project_id}) "
    "WHERE n.valid_to IS NULL AND (n.scope IN $scopes OR n.uid IN $uids) "
    "RETURN n.uid AS uid, n.scope AS scope, n.content_hash AS content_hash, "
    "n.text_hash AS text_hash, n.vid AS vid"
)


def _render_current_rels(rel_type: str) -> str:
    return (
        f"MATCH ()-[r:{rel_type}]->() WHERE r.project_id = $project_id AND r.valid_to IS NULL "
        "AND (r.scope IN $scopes OR r.uid IN $uids) "
        "RETURN r.uid AS uid, r.scope AS scope, r.content_hash AS content_hash"
    )


CURRENT_RELS: dict[str, str] = {t: _render_current_rels(t) for t in REL_TYPES}

REVISION_BY_IKEY = (
    "MATCH (r:Revision {project_id: $project_id, ikey: $ikey}) RETURN r.n AS n"
)
NEXT_REVISION = (
    "OPTIONAL MATCH (r:Revision {project_id: $project_id}) RETURN coalesce(max(r.n), 0) + 1 AS n"
)

CLOSE_NODES = (
    "UNWIND $vids AS vid MATCH (n:Mem {project_id: $project_id, vid: vid}) "
    "WHERE n.valid_to IS NULL SET n.valid_to = $n RETURN count(n) AS closed"
)


def _render_create_nodes(label: str) -> str:
    statement = (
        f"UNWIND $rows AS row CREATE (n:Mem:{label}) "
        "SET n = row.props, n.project_id = $project_id"
    )
    if label in EMBEDDED_LABELS:  # a preset or computed vector, else the previous version's
        statement += (
            " WITH n, row OPTIONAL MATCH (p:Mem {project_id: $project_id, vid: row.copy_from}) "
            "SET n.embedding = coalesce(row.embedding, p.embedding)"
        )
    return statement


CREATE_NODES: dict[str, str] = {label: _render_create_nodes(label) for label in NODE_LABELS}


def _render_reattach(rel_type: str) -> tuple[str, str]:
    """Moves each current relationship of a closed version (`$vids`) onto the current versions
    of both endpoints: closed at `$n`, re-created with the same uid and hash. Outgoing first,
    then incoming, so a relationship between two re-versioned nodes moves once."""
    tail = (
        "MATCH (a:Mem {project_id: $project_id, uid: src.uid}) WHERE a.valid_to IS NULL "
        "MATCH (b:Mem {project_id: $project_id, uid: dst.uid}) WHERE b.valid_to IS NULL "
        f"CREATE (a)-[moved:{rel_type}]->(b) "
        "SET moved = properties(r), moved.valid_from = $n, moved.phase = $phase "
        "SET r.valid_to = $n RETURN count(moved) AS moved"
    )
    return (
        "UNWIND $vids AS vid MATCH (src:Mem {project_id: $project_id, vid: vid})"
        f"-[r:{rel_type}]->(dst:Mem) WHERE r.valid_to IS NULL " + tail,
        "UNWIND $vids AS vid MATCH (src:Mem)"
        f"-[r:{rel_type}]->(dst:Mem {{project_id: $project_id, vid: vid}}) "
        "WHERE r.valid_to IS NULL " + tail,
    )


REATTACH: dict[str, tuple[str, str]] = {t: _render_reattach(t) for t in REL_TYPES}


def _render_close_rels_of_closed(rel_type: str) -> str:
    return (
        "UNWIND $vids AS vid MATCH (n:Mem {project_id: $project_id, vid: vid})"
        f"-[r:{rel_type}]-() WHERE r.valid_to IS NULL "
        "SET r.valid_to = $n RETURN count(DISTINCT r) AS closed"
    )


CLOSE_RELS_OF_CLOSED: dict[str, str] = {t: _render_close_rels_of_closed(t) for t in REL_TYPES}


def _render_create_rels(rel_type: str) -> str:
    """Creates each row between the current versions of its endpoints; a row whose endpoint
    has no current version returns nothing (dangling)."""
    return (
        "UNWIND $rows AS row "
        "MATCH (a:Mem {project_id: $project_id, uid: row.src}) WHERE a.valid_to IS NULL "
        "MATCH (b:Mem {project_id: $project_id, uid: row.dst}) WHERE b.valid_to IS NULL "
        f"CREATE (a)-[r:{rel_type}]->(b) SET r = row.props, r.project_id = $project_id "
        "RETURN r.uid AS uid"
    )


CREATE_RELS: dict[str, str] = {t: _render_create_rels(t) for t in REL_TYPES}


def _render_close_rels(rel_type: str) -> str:
    return (
        f"UNWIND $uids AS uid MATCH ()-[r:{rel_type} {{project_id: $project_id, uid: uid}}]->() "
        "WHERE r.valid_to IS NULL SET r.valid_to = $n RETURN count(r) AS closed"
    )


CLOSE_RELS: dict[str, str] = {t: _render_close_rels(t) for t in REL_TYPES}

CREATE_REVISION = (
    "CREATE (r:Revision {rid: $rid, ikey: $ikey, project_id: $project_id, n: $n, "
    "phase: $phase, scopes: $scopes, job_id: $job_id, ingest_job_id: $ingest_job_id, "
    "iteration: $iteration, at: $at, actor: $actor, added: $added, changed: $changed, "
    "removed: $removed})"
)

COUNTS = (
    "MATCH (n:Mem {project_id: $project_id}) WHERE n.valid_to IS NULL "
    "UNWIND labels(n) AS label WITH label WHERE label <> 'Mem' "
    "RETURN label, count(*) AS n"
)

# Install-wide (rulings R22): the embedding backend whose vectors the database holds.
META_GET = "MATCH (m:MemoryMeta {key: 'embedding_backend'}) RETURN m.value AS value"
META_SET = (
    "MERGE (m:MemoryMeta {key: 'embedding_backend'}) ON CREATE SET m.value = $value "
    "RETURN m.value AS value"
)
