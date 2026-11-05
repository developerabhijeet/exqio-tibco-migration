"""Shared helpers of the memory-graph tests (rulings R13): deltas, revision metadata and
read-only inspection queries of the test Neo4j.

The inspection statements are test-only constants with every value a parameter; product
Cypher lives in ``engine/memory/cypher.py``."""
from __future__ import annotations

from typing import Any

from engine.memory.model import OPEN_VALID_TO, MemNode, MemRel, RevisionMeta, ScopedDelta

P = "proj-memory-test"


def meta(ingest_key: str, phase: str = "discover", project: str = P, iteration: int = 1) -> RevisionMeta:
    return RevisionMeta(project_id=project, phase=phase, ingest_key=ingest_key, actor="tester",
                        iteration=iteration)


def estate(n_processes: int, changed: set[str] = frozenset(), vanished: set[str] = frozenset(),
           *, activities: int = 3, text_same: bool = False,
           added: set[str] = frozenset()) -> ScopedDelta:
    """Processes ``p0..p<n-1>`` (plus the ``p<i>`` keys in ``added``) each CONTAINS
    ``activities`` activities, in scope ``estate``.

    A key in ``changed`` changes that process's ``complexity_band`` and, unless ``text_same``,
    its text; a key in ``vanished`` leaves the process and its activities out."""
    nodes: list[MemNode] = []
    rels: list[MemRel] = []
    keys = [f"p{i}" for i in range(n_processes)] + sorted(added, key=lambda k: int(k[1:]))
    for key in keys:
        i = int(key[1:])
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


def build_delta(process_key: str = "p0", activities: int = 1) -> ScopedDelta:
    """Scope ``build:<process_key>``: for each activity ``a<j>`` a Method
    ``method:dotnet:<key>/Api/Flow.A<j>`` it is MIGRATED_TO (status generated) and a Test that
    TESTS the method."""
    nodes: list[MemNode] = []
    rels: list[MemRel] = []
    for j in range(activities):
        muid = f"method:dotnet:{process_key}/Api/Flow.A{j}"
        tuid = f"test:dotnet:{process_key}/A{j}_maps"
        nodes += [
            MemNode(label="Method", uid=muid, name=f"Flow.A{j}",
                    text=f"Flow.A{j} implements Activity {j}",
                    props={"pattern": "mapper", "stack": "dotnet"}),
            MemNode(label="Test", uid=tuid, name=f"A{j}_maps",
                    props={"kind": "unit", "outcome": "passed", "stack": "dotnet"}),
        ]
        rels += [
            MemRel(type="MIGRATED_TO", src=f"activity:{process_key}/a{j}", dst=muid,
                   props={"status": "generated", "pattern": "mapper"}),
            MemRel(type="TESTS", src=tuid, dst=muid),
        ]
    return ScopedDelta(scope=f"build:{process_key}", nodes=nodes, rels=rels)


def curation_delta(action: str, target: str, *, event_id: str = "ev1",
                   reason: str = "reviewed by the SME") -> ScopedDelta:
    """Scope ``curation:<event_id>``: a Curation ``{action, target_uid}`` ABOUT the target, which
    for a mapping action is the source node of the MIGRATED_TO uid ``target``."""
    about = target.split(":", 1)[1].split("->", 1)[0] if action.endswith("_mapping") else target
    uid = f"curation:{event_id}"
    return ScopedDelta(
        scope=uid,
        nodes=[MemNode(label="Curation", uid=uid, name=action, text=reason,
                       props={"action": action, "target_uid": target, "actor": "tester",
                              "reason": reason})],
        rels=[MemRel(type="ABOUT", src=uid, dst=about)],
    )


def memory_item(uid: str, text: str, about: str | None = None) -> ScopedDelta:
    """Scope ``decision:<uid>``: a Memory (kind decision), ABOUT ``about`` when given."""
    rels = [MemRel(type="ABOUT", src=uid, dst=about)] if about else []
    return ScopedDelta(scope=f"decision:{uid}", rels=rels, nodes=[
        MemNode(label="Memory", uid=uid, name="decision", text=text, props={"kind": "decision"})])


def chunk_uid(process_key: str, kind: str, i: int) -> str:
    """``chunk:<key>:summary`` for a process's first summary, else ``chunk:<key>:<kind>:<K><i>``
    (``chunk:p0:rule:R1``)."""
    if kind == "summary" and i == 1:
        return f"chunk:{process_key}:summary"
    return f"chunk:{process_key}:{kind}:{kind[0].upper()}{i}"


async def seed_chunks(graph, items: list[tuple[str, str]], *, key: str = "k1",
                      project: str = P, process_key: str = "p0"):
    """Applies the analysis Chunks ``(kind, text)`` of one process (scope
    ``analysis:<process_key>``, phase analysis) with their citation properties; a later call
    with another ``key`` replaces that scope's chunks."""
    seen: dict[str, int] = {}
    nodes = []
    for kind, text in items:
        seen[kind] = seen.get(kind, 0) + 1
        nodes.append(MemNode(
            label="Chunk", uid=chunk_uid(process_key, kind, seen[kind]), name=kind, text=text,
            props={"kind": kind, "artifact_type": "analysis",
                   "artifact_id": f"analysis-{process_key}", "process_id": f"proc-{process_key}"}))
    return await apply(graph, key, ScopedDelta(scope=f"analysis:{process_key}", nodes=nodes),
                       phase="analysis", project=project)


async def seed_memory(graph, uid: str, text: str, *, key: str | None = None, project: str = P):
    """Applies a decision Memory ``uid`` (scope ``decision:<uid>``, phase decision)."""
    delta = ScopedDelta(scope=f"decision:{uid}", nodes=[MemNode(
        label="Memory", uid=uid, name="decision", text=text,
        props={"kind": "decision", "artifact_type": "memory", "artifact_id": uid})])
    return await apply(graph, key or f"seed:{uid}", delta, phase="decision", project=project)


async def apply(graph, ingest_key: str, delta: ScopedDelta | list[ScopedDelta], *,
                phase: str = "discover", project: str = P):
    deltas = delta if isinstance(delta, list) else [delta]
    return await graph.apply_revision(meta(ingest_key, phase, project), deltas)


def names(subgraph) -> set[str]:
    """The node uids of ``subgraph`` without their label prefix (``process:p1`` -> ``p1``)."""
    return {n.uid.split(":", 1)[1] for n in subgraph.nodes}


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


_RAW_SIMILARITY = (
    "MATCH (n:Mem {project_id: $project_id}) WHERE n.valid_to = $open AND n.embedding IS NOT NULL "
    "RETURN max(2 * vector.similarity.cosine(n.embedding, $v) - 1) AS sim"
)


async def raw_similarity(graph, text: str, project: str = P) -> float:
    """The best raw cosine (``2s - 1`` of Neo4j's score) between ``text``'s vector and a current
    embedded node of ``project``."""
    from engine.knowledge import embeddings
    rows = await query(graph, _RAW_SIMILARITY, project_id=project, v=embeddings.embed([text])[0])
    return rows[0]["sim"]


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
