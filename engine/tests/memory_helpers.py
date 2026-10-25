"""Shared helpers of the memory-graph tests (rulings R13): deltas, revision metadata and
read-only inspection queries of the test Neo4j.

The inspection statements are test-only constants with every value a parameter; product
Cypher lives in ``engine/memory/cypher.py``."""
from __future__ import annotations

from typing import Any

from engine.memory.model import OPEN_VALID_TO, MemNode, MemRel, RevisionMeta, ScopedDelta

P = "proj-memory-test"


def meta(ingest_key: str, phase: str = "discover", project: str = P) -> RevisionMeta:
    return RevisionMeta(project_id=project, phase=phase, ingest_key=ingest_key, actor="tester")


def estate(n_processes: int, changed: set[str] = frozenset(), vanished: set[str] = frozenset(),
           *, activities: int = 3, text_same: bool = False) -> ScopedDelta:
    """Processes ``p0..p<n-1>`` each CONTAINS ``activities`` activities, in scope ``estate``.

    A key in ``changed`` changes that process's ``complexity_band`` and, unless ``text_same``,
    its text; a key in ``vanished`` leaves the process and its activities out."""
    nodes: list[MemNode] = []
    rels: list[MemRel] = []
    for i in range(n_processes):
        key = f"p{i}"
        if key in vanished:
            continue
        is_changed = key in changed
        text = f"Process {i} (app/orders), rest starter: receive, validate, reply"
        if is_changed and not text_same:
            text += ", audit"
        puid = f"process:{key}"
        nodes.append(MemNode(label="Process", uid=puid, name=f"Process {i}", text=text,
                             props={"module": "orders",
                                    "complexity_band": "high" if is_changed else "low"}))
        for j in range(activities):
            auid = f"activity:{key}/a{j}"
            nodes.append(MemNode(label="Activity", uid=auid, name=f"Activity {j}",
                                 text=f"Activity {j} (mapper) in Process {i}",
                                 props={"type": "mapper", "starter": j == 0}))
            rels.append(MemRel(type="CONTAINS", src=puid, dst=auid))
    return ScopedDelta(scope="estate", nodes=nodes, rels=rels)


def about_chunk(process_key: str, text: str | None = None) -> ScopedDelta:
    """An analysis summary Chunk ABOUT ``process:<process_key>``."""
    uid = f"chunk:{process_key}:summary"
    return ScopedDelta(
        scope=f"analysis:{process_key}",
        nodes=[MemNode(label="Chunk", uid=uid, name="summary",
                       text=text or f"Summary of {process_key}: validates and replies",
                       props={"kind": "summary"})],
        rels=[MemRel(type="ABOUT", src=uid, dst=f"process:{process_key}")],
    )


def chunk_with(text: str, process_key: str = "p0") -> ScopedDelta:
    return about_chunk(process_key, text)


def build_methods(process_key: str = "p0", activity_index: int = 0) -> ScopedDelta:
    """A build-scope Method that the activity ``a<activity_index>`` is MIGRATED_TO."""
    muid = f"method:dotnet:{process_key}/Api/Flow.Step{activity_index}"
    return ScopedDelta(
        scope=f"build:{process_key}",
        nodes=[MemNode(label="Method", uid=muid, name=f"Flow.Step{activity_index}",
                       text=f"Flow.Step{activity_index} implements Activity {activity_index}",
                       props={"pattern": "mapper", "stack": "dotnet"})],
        rels=[MemRel(type="MIGRATED_TO", src=f"activity:{process_key}/a{activity_index}",
                     dst=muid, props={"status": "generated"})],
    )


_REVISION_COUNT = "MATCH (r:Revision {project_id: $project_id}) RETURN count(r) AS n"
_NODE_VERSIONS = (
    "MATCH (n:Mem {project_id: $project_id, uid: $uid}) "
    "RETURN n.valid_from AS valid_from, n.valid_to AS valid_to, n.vid AS vid, "
    "n.embedding AS embedding, n.phase AS phase, n.scope AS scope ORDER BY n.valid_from"
)
_CURRENT_REL_COUNT = (
    "MATCH (a:Mem {project_id: $project_id})-[r]->(b:Mem {project_id: $project_id}) "
    "WHERE type(r) = $type AND r.valid_to = $open AND a.valid_to = $open "
    "AND b.valid_to = $open AND ($src IS NULL OR a.uid = $src) AND ($dst IS NULL OR b.uid = $dst) "
    "RETURN count(r) AS n"
)
_REVISIONS = "MATCH (r:Revision {project_id: $project_id}) RETURN r ORDER BY r.n"
_SET_BACKEND = "MATCH (m:MemoryMeta {key: 'embedding_backend'}) SET m.value = $value"
_STORED_TEXT = (
    "MATCH (n:Mem {project_id: $project_id, uid: $uid}) WHERE n.valid_to = $open "
    "RETURN n.text AS text"
)


async def query(graph, statement: str, **params: Any) -> list[dict[str, Any]]:
    async with graph._driver.session(database=graph.database) as session:
        result = await session.run(statement, {"open": OPEN_VALID_TO, **params})
        return [r.data() async for r in result]


async def revision_count(graph, project_id: str) -> int:
    return (await query(graph, _REVISION_COUNT, project_id=project_id))[0]["n"]


async def node_versions(graph, project_id: str, uid: str) -> list[dict[str, Any]]:
    """Every version of ``uid``, oldest first; a current version's ``valid_to`` reads None."""
    rows = await query(graph, _NODE_VERSIONS, project_id=project_id, uid=uid)
    for row in rows:
        if row["valid_to"] == OPEN_VALID_TO:
            row["valid_to"] = None
    return rows


async def current_rel_count(graph, project_id: str, rel_type: str, *, src: str | None = None,
                            dst: str | None = None) -> int:
    rows = await query(graph, _CURRENT_REL_COUNT, project_id=project_id, type=rel_type, src=src,
                       dst=dst)
    return rows[0]["n"]


async def stored_text(graph, project_id: str, uid: str) -> str:
    return (await query(graph, _STORED_TEXT, project_id=project_id, uid=uid))[0]["text"]


async def revisions(graph, project_id: str) -> list[dict[str, Any]]:
    return [row["r"] for row in await query(graph, _REVISIONS, project_id=project_id)]


async def set_embedding_backend(graph, value: str) -> None:
    await query(graph, _SET_BACKEND, value=value)


async def explain(graph, statement: str, **params: Any) -> str:
    """The operator tree of ``statement``'s plan (EXPLAIN runs nothing)."""
    async with graph._driver.session(database=graph.database) as session:
        result = await session.run("EXPLAIN " + statement, {"open": OPEN_VALID_TO, **params})
        summary = await result.consume()

    def ops(plan: dict[str, Any], depth: int = 0) -> list[str]:
        line = "  " * depth + plan["operatorType"] + " " + plan.get("args", {}).get("Details", "")
        return [line] + [x for child in plan.get("children", []) for x in ops(child, depth + 1)]

    return "\n".join(ops(summary.plan))
