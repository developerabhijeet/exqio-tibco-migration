"""Every Cypher statement of the memory graph, as module constants.

Labels and relationship types cannot be Cypher parameters, so statements per label or type are
rendered once at import by the private ``_render_*`` functions, only from the closed sets in
``model``; no other code builds Cypher and request values are always parameters.
"""
from __future__ import annotations

from engine.memory.model import EMBEDDED_LABELS, NODE_LABELS, REL_TYPES

TOP_LEVEL_LABELS = ("Domain", "Application", "Solution")

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
        (f"CREATE INDEX {name}_closed IF NOT EXISTS FOR ()-[r:{rel_type}]-() "
         "ON (r.project_id, r.closed_in)"),
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
    # Closed versions only (`closed_in` is set when a version closes): "closed at revision X".
    "CREATE INDEX mem_closed IF NOT EXISTS FOR (n:Mem) ON (n.project_id, n.closed_in)",
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

# Install-wide maintenance run by ensure_schema (exempt from the project_id rule like SCHEMA,
# rulings R22): versions closed before the write path recorded `closed_in` get it, so the
# revision diff sees them. Idempotent; a no-op once every closed version has it.
BACKFILL_CLOSED_IN: tuple[str, ...] = (
    ("MATCH (n:Mem) WHERE n.valid_to < $open AND n.closed_in IS NULL "
    "CALL (n) { SET n.closed_in = n.valid_to } IN TRANSACTIONS OF 10000 ROWS"),
    ("MATCH ()-[r]->() WHERE r.project_id IS NOT NULL AND r.valid_to < $open "
    "AND r.closed_in IS NULL CALL (r) { SET r.closed_in = r.valid_to } "
    "IN TRANSACTIONS OF 10000 ROWS"),
)

SHOW_INDEX_NAMES = "SHOW INDEXES YIELD name RETURN name"
SHOW_CONSTRAINT_NAMES = "SHOW CONSTRAINTS YIELD name RETURN name"
PING = "RETURN 1 AS ok"
# Test-only (MemoryGraph.reset): deletes every node of the test database, keeps the schema.
RESET = "MATCH (n) CALL (n) { DETACH DELETE n } IN TRANSACTIONS OF 10000 ROWS"

# ------------------------------------------------------------------ versioned write path
# Current versions carry `valid_to = $open` (model.OPEN_VALID_TO, rulings R25), never null, so
# every lookup of current versions seeks an index. Closing a version sets `valid_to` and
# `closed_in` (the same n; only closed versions have it, so its index serves the revision diff
# without matching current versions). Every write statement takes `$rows`, `$vids`
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
    "USING INDEX SEEK n:Mem(vid) WHERE n.valid_to = $open SET n.valid_to = $n, n.closed_in = $n "
    "RETURN count(n) AS closed"
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
        "SET r.valid_to = $n, r.closed_in = $n RETURN count(moved) AS moved"
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
        "SET r.valid_to = $n, r.closed_in = $n RETURN count(DISTINCT r) AS closed"
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
        f"USING INDEX SEEK r:{rel_type}(project_id, uid) WHERE r.valid_to = $open "
        "SET r.valid_to = $n, r.closed_in = $n RETURN count(r) AS closed"
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

# ------------------------------------------------------------------ reads
# Every read is as of revision `$k`: a version is valid when `valid_from <= $k < valid_to`.
# Current reads pass `$k = OPEN_VALID_TO - 1`, which only open versions satisfy, so one
# predicate serves both. A relationship's validity lies within both endpoints' (the write path
# re-attaches it whenever an endpoint is re-versioned), so walking valid relationships from a
# valid node reaches valid nodes. Each statement starts from an index seek (uid, vid,
# target_uid, valid_from, closed_in, Revision n), except TOP_LEVEL (label scans of its three
# labels).


def _render_valid(var: str) -> str:
    return f"{var}.valid_from <= $k AND $k < {var}.valid_to"


def _render_node_out(var: str) -> str:
    """A node as `{label, p}`: its model label and stored properties without the vector."""
    return f"{{label: [l IN labels({var}) WHERE l <> 'Mem'][0], p: {var} {{.*, embedding: null}}}}"


def _render_edge_out(var: str) -> str:
    return (f"{{type: type({var}), src: startNode({var}).uid, dst: endNode({var}).uid, "
            f"p: properties({var})}}")


def _render_at_uid(var: str) -> str:
    """The version of `$uid` valid at `$k` (index mem_uid)."""
    return (f"MATCH ({var}:Mem {{project_id: $project_id, uid: $uid}}) "
            f"USING INDEX SEEK {var}:Mem(project_id, uid) WHERE {_render_valid(var)} ")


def _render_view_filter(var: str) -> str:
    return (f"($labels IS NULL OR any(l IN labels({var}) WHERE l IN $labels)) "
            f"AND ($side IS NULL OR {var}.side = $side)")


def _render_node_at() -> str:
    return _render_at_uid("n") + f"RETURN {_render_node_out('n')} AS node"


def _render_neighbourhood(depth: int) -> str:
    """The node `$uid` first, then its neighbours within `depth` hops over valid relationships
    that pass the view filter, nearest first; `total` counts them all, `nodes` the first
    `$limit`."""
    return (
        _render_at_uid("c")
        + f"OPTIONAL MATCH path = (c)-[rels*1..{depth}]-(m:Mem) "
        f"WHERE m <> c AND {_render_valid('m')} AND all(r IN rels WHERE {_render_valid('r')}) "
        f"AND {_render_view_filter('m')} "
        "WITH c, m, min(length(path)) AS d ORDER BY d, m.uid "
        "WITH c, collect(m) AS found WITH [c] + found AS ms "
        f"RETURN size(ms) AS total, [m IN ms[0..$limit] | {_render_node_out('m')}] AS nodes"
    )


def _render_top_level() -> str:
    """The project's top-level nodes (a label scan per label: they are few in any project)."""
    return (
        "CALL () { "
        + " UNION ALL ".join(
            f"MATCH (m:{label}) USING SCAN m:{label} WHERE m.project_id = $project_id "
            f"AND {_render_valid('m')} AND {_render_view_filter('m')} RETURN m, {rank} AS rank"
            for rank, label in enumerate(TOP_LEVEL_LABELS))
        + " } WITH m ORDER BY rank, m.name, m.uid "
        "WITH collect(m) AS ms "
        f"RETURN size(ms) AS total, [m IN ms[0..$limit] | {_render_node_out('m')}] AS nodes"
    )


def _render_edges_among() -> str:
    """The valid relationships between the versions `$vids` (a subgraph's nodes)."""
    return (
        "UNWIND $vids AS vid MATCH (a:Mem {project_id: $project_id, vid: vid}) "
        "USING INDEX SEEK a:Mem(vid) "
        f"MATCH (a)-[r]->(b:Mem) WHERE b.vid IN $vids AND {_render_valid('r')} "
        f"RETURN {_render_edge_out('r')} AS edge ORDER BY edge.type, edge.p.uid"
    )


LINEAGE_HOPS = 4


def _render_lineage() -> str:
    """What valid MIGRATED_TO, TESTS and FAILED_AT relationships (either direction) and
    CONTAINS (only from a Process down to its children) reach from `$uid` in at most
    LINEAGE_HOPS hops. A breadth-first walk: each hop expands the distinct nodes first reached
    by the previous one, so the work is bounded by the relationships reached, not the paths
    (a Method shared by N activities would otherwise make O(N^2) paths). `hops[i]` holds the
    nodes first reached at hop i + 1; `edges` may repeat a relationship met from both ends."""
    lineage_types = "MIGRATED_TO|TESTS|FAILED_AT|CONTAINS"
    hop = (
        "CALL (frontier) { UNWIND frontier AS f "
        f"OPTIONAL MATCH (f)-[r:{lineage_types}]-(m:Mem) WHERE {_render_valid('r')} "
        "AND (type(r) <> 'CONTAINS' OR (startNode(r) = f AND f:Process)) "
        "RETURN collect(DISTINCT r) AS hop_rs, collect(DISTINCT m) AS hop_ms } "
        "WITH c, seen, hops, rs + hop_rs AS rs, [m IN hop_ms WHERE NOT m IN seen] AS frontier "
        "WITH c, seen + frontier AS seen, hops + [frontier] AS hops, rs, frontier "
    )
    return (
        _render_at_uid("c")
        + "WITH c, [c] AS seen, [c] AS frontier, [] AS hops, [] AS rs "
        + hop * LINEAGE_HOPS
        + f"RETURN {_render_node_out('c')} AS center, "
        f"[h IN hops | [x IN h | {_render_node_out('x')}]] AS hops, "
        f"[x IN rs | {_render_edge_out('x')}] AS edges"
    )


def _render_node_versions() -> str:
    """Every version of `$uid` opened by `$k`, newest first, with its revision's actor and time."""
    return (
        "MATCH (n:Mem {project_id: $project_id, uid: $uid}) USING INDEX SEEK n:Mem(project_id, uid) "
        "WHERE n.valid_from <= $k "
        "OPTIONAL MATCH (r:Revision {project_id: $project_id, n: n.valid_from}) "
        "USING INDEX SEEK r:Revision(project_id, n) "
        f"RETURN {_render_node_out('n')} AS node, r.actor AS actor, r.at AS at "
        "ORDER BY node.p.valid_from DESC"
    )


def _render_about_sources() -> str:
    return (
        _render_at_uid("t")
        + f"MATCH (s:Mem)-[r:ABOUT]->(t) WHERE {_render_valid('r')} AND {_render_valid('s')} "
        f"RETURN {_render_node_out('s')} AS node ORDER BY node.p.valid_from DESC, node.p.uid"
    )


def _render_lineage_edges() -> str:
    return (
        _render_at_uid("t")
        + "MATCH (t)-[r:MIGRATED_TO|TESTS|FAILED_AT]-(o:Mem) "
        f"WHERE {_render_valid('r')} AND {_render_valid('o')} "
        f"RETURN {_render_edge_out('r')} AS edge, {_render_node_out('o')} AS other, "
        "startNode(r) = t AS outgoing ORDER BY edge.type, edge.p.uid"
    )


def _render_curations_of() -> str:
    """Current-as-of-`$k` Curations whose `target_uid` is `$uid` (index mem_target)."""
    return (
        "MATCH (c:Mem {project_id: $project_id, target_uid: $uid}) "
        f"USING INDEX SEEK c:Mem(project_id, target_uid) WHERE c:Curation AND {_render_valid('c')} "
        f"RETURN {_render_node_out('c')} AS node ORDER BY node.p.valid_from DESC, node.p.uid"
    )


def _render_curation_targets() -> str:
    """The actions of the Curations valid at `$k` that target each of `$uids`."""
    return (
        "UNWIND $uids AS t MATCH (c:Mem {project_id: $project_id, target_uid: t}) "
        f"USING INDEX SEEK c:Mem(project_id, target_uid) WHERE c:Curation AND {_render_valid('c')} "
        "RETURN DISTINCT t AS target_uid, c.action AS action"
    )


def _render_curated() -> str:
    """Which of the versions `$vids` a Curation valid at `$k` is ABOUT."""
    return (
        "UNWIND $vids AS vid MATCH (n:Mem {project_id: $project_id, vid: vid}) "
        "USING INDEX SEEK n:Mem(vid) "
        "WHERE EXISTS { MATCH (c:Curation)-[r:ABOUT]->(n) "
        f"WHERE {_render_valid('r')} AND {_render_valid('c')} }} "
        "RETURN n.vid AS vid"
    )


def _render_nodes_at_revision(key: str) -> str:
    """Node versions opened (`valid_from`) or closed (`closed_in`) at `$n`."""
    return (
        f"MATCH (v:Mem {{project_id: $project_id, {key}: $n}}) "
        f"USING INDEX SEEK v:Mem(project_id, {key}) RETURN {_render_node_out('v')} AS node"
    )


def _render_rels_at_revision(key: str) -> str:
    """Relationship versions of every type opened (`valid_from`) or closed (`closed_in`) at
    `$n`, one index-seeking branch per type."""
    return " UNION ALL ".join(
        f"MATCH ()-[r:{t} {{project_id: $project_id, {key}: $n}}]->() "
        f"USING INDEX SEEK r:{t}(project_id, {key}) RETURN {_render_edge_out('r')} AS edge"
        for t in REL_TYPES
    )


NODE_AT = _render_node_at()
NEIGHBOURHOOD: dict[int, str] = {d: _render_neighbourhood(d) for d in (1, 2)}
TOP_LEVEL = _render_top_level()
EDGES_AMONG = _render_edges_among()
LINEAGE = _render_lineage()
NODE_VERSIONS = _render_node_versions()
ABOUT_SOURCES = _render_about_sources()
LINEAGE_EDGES = _render_lineage_edges()
CURATIONS_OF = _render_curations_of()
CURATION_TARGETS = _render_curation_targets()
CURATED = _render_curated()
NODES_OPENED_AT = _render_nodes_at_revision("valid_from")
NODES_CLOSED_AT = _render_nodes_at_revision("closed_in")
RELS_OPENED_AT = _render_rels_at_revision("valid_from")
RELS_CLOSED_AT = _render_rels_at_revision("closed_in")
REVISION_AT = "MATCH (r:Revision {project_id: $project_id, n: $n}) RETURN r {.*} AS revision"
REVISIONS_BEFORE = (
    "MATCH (r:Revision {project_id: $project_id}) USING INDEX SEEK r:Revision(project_id, n) "
    "WHERE r.n < $before AND ($iteration IS NULL OR r.iteration = $iteration) "
    "RETURN r {.*} AS revision ORDER BY r.n DESC LIMIT $limit"
)

# ------------------------------------------------------------------ hybrid search
# As of `$k` like the reads. Each half keeps the nodes of `$project_id` valid at `$k` whose
# labels meet `$labels`, filtered by `$side` and `$phase` (version properties) and, unless
# `$include_obsolete`, without the Memory targeted by an obsolete Curation valid at `$k`, and
# returns its best `$n` as `{label, p}` with its score. Neo4j's cosine score `s` is
# `(1 + cos) / 2`; the vector half returns raw cosine `2s - 1`, compared with `$floor`.


def _render_search_filter(var: str) -> str:
    return (
        f"{var}.project_id = $project_id AND {_render_valid(var)} "
        f"AND any(l IN labels({var}) WHERE l IN $labels) "
        f"AND ($side IS NULL OR {var}.side = $side) AND ($phase IS NULL OR {var}.phase = $phase) "
        f"AND ($include_obsolete OR NOT ({var}:Memory AND EXISTS {{ "
        f"MATCH (c:Mem {{project_id: $project_id, target_uid: {var}.uid}}) "
        "USING INDEX SEEK c:Mem(project_id, target_uid) "
        f"WHERE c:Curation AND c.action = 'obsolete' AND {_render_valid('c')} }}))"
    )


def _render_search_keyword() -> str:
    """One full-text query per term of `$terms` (only `[a-z0-9_]`, so no Lucene syntax); a node
    matching at least `$need` terms ranks by the sum of its term scores. Each term fetches at
    most `$ft_limit` matches across the database, except the terms in `$unbounded` (all of
    them). `raws` holds what each term fetched before the project filter, so the caller re-runs
    a term that hit the limit unbounded; `hits` holds the best `$n` nodes."""
    return (
        "UNWIND $terms AS t CALL (t) { "
        "WITH t WHERE t IN $unbounded "
        "CALL db.index.fulltext.queryNodes('mem_text', t) YIELD node, score RETURN node, score "
        "UNION ALL WITH t WHERE NOT t IN $unbounded "
        "CALL db.index.fulltext.queryNodes('mem_text', t, {limit: $ft_limit}) YIELD node, score "
        "RETURN node, score } "
        "WITH t, collect({node: node, score: score}) AS found "
        "WITH collect({t: t, raw: size(found)}) AS raws, collect(found) AS founds "
        "CALL (founds) { UNWIND founds AS fs UNWIND fs AS f "
        f"WITH f.node AS node, f.score AS score WHERE {_render_search_filter('node')} "
        "WITH node, count(*) AS matched, sum(score) AS rank WHERE matched >= $need "
        "WITH node, rank ORDER BY rank DESC, node.uid LIMIT $n "
        f"RETURN collect({{node: {_render_node_out('node')}, score: rank}}) AS hits }} "
        "RETURN raws, hits"
    )


def _render_search_vector_exact() -> str:
    """Exact cosine over the project's versions (a range seek of index mem_uid on project_id:
    never another project's nodes)."""
    return (
        "MATCH (n:Mem {project_id: $project_id}) USING INDEX SEEK n:Mem(project_id, uid) "
        f"WHERE n.uid IS NOT NULL AND n.embedding IS NOT NULL AND {_render_search_filter('n')} "
        "WITH n, 2 * vector.similarity.cosine(n.embedding, $v) - 1 AS sim WHERE sim >= $floor "
        f"RETURN {_render_node_out('n')} AS node, sim AS score "
        "ORDER BY score DESC, node.p.uid LIMIT $n"
    )


def _render_search_vector_ann(label: str) -> str:
    """The `$candidates` nearest versions of `label` across the database (ANN, index
    vec_<label>), then post-filtered by project and validity. `raw` counts the candidates the
    index returned and `lowest` is the raw cosine of the last, so the caller can tell whether
    more candidates could add hits."""
    return (
        f"CALL db.index.vector.queryNodes('vec_{label.lower()}', $candidates, $v) "
        "YIELD node, score WITH collect({n: node, score: score}) AS found "
        "CALL (found) { UNWIND found AS f WITH f.n AS n, f.score AS score "
        f"WHERE {_render_search_filter('n')} "
        "WITH n, 2 * score - 1 AS sim WHERE sim >= $floor "
        "WITH n, sim ORDER BY sim DESC, n.uid LIMIT $n "
        f"RETURN collect({{node: {_render_node_out('n')}, score: sim}}) AS hits }} "
        "RETURN size(found) AS raw, 2 * coalesce(last(found).score, 1.0) - 1 AS lowest, hits"
    )


def _render_search_embedded_counts() -> str:
    """Current embedded versions per label of `$labels` (index mem_uid on project_id)."""
    return (
        "MATCH (n:Mem {project_id: $project_id}) USING INDEX SEEK n:Mem(project_id, uid) "
        "WHERE n.uid IS NOT NULL AND n.valid_to = $open "
        "AND any(l IN labels(n) WHERE l IN $labels) AND n.embedding IS NOT NULL "
        "UNWIND labels(n) AS label WITH label WHERE label IN $labels "
        "RETURN label, count(*) AS n"
    )


SEARCH_KEYWORD = _render_search_keyword()
SEARCH_VECTOR_EXACT = _render_search_vector_exact()
SEARCH_VECTOR_ANN: dict[str, str] = {
    label: _render_search_vector_ann(label) for label in EMBEDDED_LABELS
}
SEARCH_EMBEDDED_COUNTS = _render_search_embedded_counts()

# Install-wide (rulings R22): the embedding backend whose vectors the database holds.
META_GET = "MATCH (m:MemoryMeta {key: 'embedding_backend'}) RETURN m.value AS value"
META_SET = (
    "MERGE (m:MemoryMeta {key: 'embedding_backend'}) ON CREATE SET m.value = $value "
    "RETURN m.value AS value"
)
# Replaces the recorded backend once every project is re-embedded (MemoryGraph.record_backend;
# install-wide, R22).
META_REPLACE = (
    "MERGE (m:MemoryMeta {key: 'embedding_backend'}) SET m.value = $value RETURN m.value AS value"
)

# ------------------------------------------------------------------ maintenance
# The current versions of one label with their text (the migration's verification).


def _render_current_texts(label: str) -> str:
    return (
        f"MATCH (n:Mem:{label} {{project_id: $project_id}}) USING INDEX SEEK n:Mem(project_id, uid) "
        "WHERE n.uid IS NOT NULL AND n.valid_to = $open RETURN n.uid AS uid, n.text AS text"
    )


CURRENT_TEXTS: dict[str, str] = {label: _render_current_texts(label) for label in NODE_LABELS}
# The current versions of the embedded labels that carry text (MemoryGraph.reembed).
CURRENT_EMBEDDED = (
    "MATCH (n:Mem {project_id: $project_id}) USING INDEX SEEK n:Mem(project_id, uid) "
    "WHERE n.uid IS NOT NULL AND n.valid_to = $open AND n.text <> '' "
    "AND any(label IN labels(n) WHERE label IN $labels) RETURN n.vid AS vid, n.text AS text"
)
SET_EMBEDDINGS = (
    "UNWIND $rows AS row MATCH (n:Mem {project_id: $project_id, vid: row.vid}) "
    "USING INDEX SEEK n:Mem(vid) SET n.embedding = row.embedding RETURN count(n) AS set"
)
