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
        (f"CREATE INDEX {name}_uid IF NOT EXISTS FOR ()-[r:{rel_type}]-() "
         "ON (r.project_id, r.uid)"),
        (f"CREATE INDEX {name}_scope IF NOT EXISTS FOR ()-[r:{rel_type}]-() "
         "ON (r.project_id, r.scope)"),
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


def _render_drop_rel_to_index(rel_type: str) -> str:
    return f"DROP INDEX rel_{rel_type.lower()}_to IF EXISTS"


SCHEMA: tuple[str, ...] = (
    "CREATE CONSTRAINT mem_vid IF NOT EXISTS FOR (n:Mem) REQUIRE n.vid IS UNIQUE",
    "CREATE CONSTRAINT rev_rid IF NOT EXISTS FOR (r:Revision) REQUIRE r.rid IS UNIQUE",
    "CREATE CONSTRAINT rev_ikey IF NOT EXISTS FOR (r:Revision) REQUIRE r.ikey IS UNIQUE",
    "CREATE CONSTRAINT meta_key IF NOT EXISTS FOR (m:MemoryMeta) REQUIRE m.key IS UNIQUE",
    "CREATE INDEX mem_uid IF NOT EXISTS FOR (n:Mem) ON (n.project_id, n.uid)",
    "CREATE INDEX mem_scope IF NOT EXISTS FOR (n:Mem) ON (n.project_id, n.scope, n.valid_to)",
    "CREATE INDEX mem_from IF NOT EXISTS FOR (n:Mem) ON (n.project_id, n.valid_from)",
    "CREATE INDEX mem_target IF NOT EXISTS FOR (n:Mem) ON (n.project_id, n.target_uid)",
    "CREATE INDEX rev_n IF NOT EXISTS FOR (r:Revision) ON (r.project_id, r.n)",
    *(stmt for t in REL_TYPES for stmt in _render_rel_indexes(t)),
    *(_render_vector_index(label) for label in EMBEDDED_LABELS),
    _render_fulltext_index(),
    # (project_id, valid_to) indexes of the first schema: with `valid_to = OPEN` on every current
    # version (rulings R25) they match a whole project and the planner preferred them to the
    # uid index; dropped where an earlier ensure_schema created them.
    "DROP INDEX mem_to IF EXISTS",
    *(_render_drop_rel_to_index(t) for t in REL_TYPES),
)

SHOW_INDEX_NAMES = "SHOW INDEXES YIELD name RETURN name"
SHOW_CONSTRAINT_NAMES = "SHOW CONSTRAINTS YIELD name RETURN name"
PING = "RETURN 1 AS ok"
# Test-only (MemoryGraph.reset): deletes every node of the test database, keeps the schema.
RESET = "MATCH (n) CALL (n) { DETACH DELETE n } IN TRANSACTIONS OF 10000 ROWS"

# ------------------------------------------------------------------ versioned write path
# Current versions carry `valid_to = $open` (model.OPEN_VALID_TO, rulings R25), never null, so
# every lookup of current versions seeks an index. Every write statement takes `$rows`, `$vids`
# or `$uids` batches of at most 1000 (MemoryGraph.apply_revision) and `$n`, the revision.

# Current node versions owned by one of the delta scopes (index mem_scope).
CURRENT_IN_SCOPE = (
    "UNWIND $scopes AS scope MATCH (n:Mem {project_id: $project_id, scope: scope}) "
    "USING INDEX SEEK n:Mem(project_id, scope, valid_to) WHERE n.valid_to = $open "
    "RETURN n.uid AS uid, n.scope AS scope, n.content_hash AS content_hash, "
    "n.text_hash AS text_hash, n.vid AS vid"
)
# Current node versions of the delta uids, whatever their scope (index mem_uid).
CURRENT_BY_UID = (
    "UNWIND $uids AS uid MATCH (n:Mem {project_id: $project_id, uid: uid}) "
    "USING INDEX SEEK n:Mem(project_id, uid) WHERE n.valid_to = $open "
    "RETURN n.uid AS uid, n.scope AS scope, n.content_hash AS content_hash, "
    "n.text_hash AS text_hash, n.vid AS vid"
)


def _render_current_rels(rel_type: str) -> str:
    return (
        f"UNWIND $scopes AS scope MATCH ()-[r:{rel_type} "
        f"{{project_id: $project_id, scope: scope}}]->() USING INDEX SEEK r:{rel_type}(project_id, scope) "
        "WHERE r.valid_to = $open "
        "RETURN r.uid AS uid, r.scope AS scope, r.content_hash AS content_hash"
    )


def _render_current_rels_by_uid(rel_type: str) -> str:
    return (
        f"UNWIND $uids AS uid MATCH ()-[r:{rel_type} "
        f"{{project_id: $project_id, uid: uid}}]->() USING INDEX SEEK r:{rel_type}(project_id, uid) "
        "WHERE r.valid_to = $open "
        "RETURN r.uid AS uid, r.scope AS scope, r.content_hash AS content_hash"
    )


CURRENT_RELS: dict[str, str] = {t: _render_current_rels(t) for t in REL_TYPES}
CURRENT_RELS_BY_UID: dict[str, str] = {t: _render_current_rels_by_uid(t) for t in REL_TYPES}

REVISION_BY_IKEY = (
    "MATCH (r:Revision {project_id: $project_id, ikey: $ikey}) RETURN r.n AS n"
)
NEXT_REVISION = (
    "OPTIONAL MATCH (r:Revision {project_id: $project_id}) RETURN coalesce(max(r.n), 0) + 1 AS n"
)

CLOSE_NODES = (
    "UNWIND $vids AS vid MATCH (n:Mem {project_id: $project_id, vid: vid}) "
    "USING INDEX SEEK n:Mem(vid) WHERE n.valid_to = $open SET n.valid_to = $n RETURN count(n) AS closed"
)


def _render_create_nodes(label: str) -> str:
    statement = (
        f"UNWIND $rows AS row CREATE (n:Mem:{label}) "
        "SET n = row.props, n.project_id = $project_id"
    )
    if label in EMBEDDED_LABELS:  # a preset or computed vector, else the previous version's
        statement += (
            " WITH n, row OPTIONAL MATCH (p:Mem {project_id: $project_id, vid: row.copy_from}) "
            "USING INDEX SEEK p:Mem(vid) SET n.embedding = coalesce(row.embedding, p.embedding)"
        )
    return statement


CREATE_NODES: dict[str, str] = {label: _render_create_nodes(label) for label in NODE_LABELS}


def _render_reattach(rel_type: str) -> tuple[str, str]:
    """Moves each current relationship of a closed version (`$vids`) onto the current versions
    of both endpoints: closed at `$n`, re-created with the same uid and hash. Outgoing first,
    then incoming, so a relationship between two re-versioned nodes moves once."""
    tail = (
        "MATCH (a:Mem {project_id: $project_id, uid: src.uid}) USING INDEX SEEK a:Mem(project_id, uid) "
        "WHERE a.valid_to = $open "
        "MATCH (b:Mem {project_id: $project_id, uid: dst.uid}) USING INDEX SEEK b:Mem(project_id, uid) "
        "WHERE b.valid_to = $open "
        f"CREATE (a)-[moved:{rel_type}]->(b) "
        "SET moved = properties(r), moved.valid_from = $n, moved.phase = $phase "
        "SET r.valid_to = $n RETURN count(moved) AS moved"
    )
    return (
        "UNWIND $vids AS vid MATCH (src:Mem {project_id: $project_id, vid: vid})"
        f"-[r:{rel_type}]->(dst:Mem) USING INDEX SEEK src:Mem(vid) WHERE r.valid_to = $open "
        + tail,
        "UNWIND $vids AS vid MATCH (src:Mem)"
        f"-[r:{rel_type}]->(dst:Mem {{project_id: $project_id, vid: vid}}) "
        "USING INDEX SEEK dst:Mem(vid) WHERE r.valid_to = $open " + tail,
    )


REATTACH: dict[str, tuple[str, str]] = {t: _render_reattach(t) for t in REL_TYPES}


def _render_close_rels_of_closed(rel_type: str) -> str:
    return (
        "UNWIND $vids AS vid MATCH (n:Mem {project_id: $project_id, vid: vid})"
        f"-[r:{rel_type}]-() USING INDEX SEEK n:Mem(vid) WHERE r.valid_to = $open "
        "SET r.valid_to = $n RETURN count(DISTINCT r) AS closed"
    )


CLOSE_RELS_OF_CLOSED: dict[str, str] = {t: _render_close_rels_of_closed(t) for t in REL_TYPES}


def _render_create_rels(rel_type: str) -> str:
    """Creates each row between the current versions of its endpoints; a row whose endpoint
    has no current version returns nothing (dangling)."""
    return (
        "UNWIND $rows AS row "
        "MATCH (a:Mem {project_id: $project_id, uid: row.src}) USING INDEX SEEK a:Mem(project_id, uid) "
        "WHERE a.valid_to = $open "
        "MATCH (b:Mem {project_id: $project_id, uid: row.dst}) USING INDEX SEEK b:Mem(project_id, uid) "
        "WHERE b.valid_to = $open "
        f"CREATE (a)-[r:{rel_type}]->(b) SET r = row.props, r.project_id = $project_id "
        "RETURN r.uid AS uid"
    )


CREATE_RELS: dict[str, str] = {t: _render_create_rels(t) for t in REL_TYPES}


def _render_close_rels(rel_type: str) -> str:
    return (
        f"UNWIND $uids AS uid MATCH ()-[r:{rel_type} {{project_id: $project_id, uid: uid}}]->() "
        f"USING INDEX SEEK r:{rel_type}(project_id, uid) WHERE r.valid_to = $open SET r.valid_to = $n RETURN count(r) AS closed"
    )


CLOSE_RELS: dict[str, str] = {t: _render_close_rels(t) for t in REL_TYPES}

CREATE_REVISION = (
    "CREATE (r:Revision {rid: $rid, ikey: $ikey, project_id: $project_id, n: $n, "
    "phase: $phase, scopes: $scopes, job_id: $job_id, ingest_job_id: $ingest_job_id, "
    "iteration: $iteration, at: $at, actor: $actor, added: $added, changed: $changed, "
    "removed: $removed})"
)

COUNTS = (
    "MATCH (n:Mem {project_id: $project_id}) USING INDEX SEEK n:Mem(project_id, uid) "
    "WHERE n.uid IS NOT NULL AND n.valid_to = $open "
    "UNWIND labels(n) AS label WITH label WHERE label <> 'Mem' "
    "RETURN label, count(*) AS n"
)

# Install-wide (rulings R22): the embedding backend whose vectors the database holds.
META_GET = "MATCH (m:MemoryMeta {key: 'embedding_backend'}) RETURN m.value AS value"
META_SET = (
    "MERGE (m:MemoryMeta {key: 'embedding_backend'}) ON CREATE SET m.value = $value "
    "RETURN m.value AS value"
)
