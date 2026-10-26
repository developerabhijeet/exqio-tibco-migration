"""``MemoryGraph``: the async Neo4j connection of the memory, its schema, the versioned write
path (``apply_revision``), the as-of reads and test reset."""
from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from neo4j import AsyncDriver, AsyncGraphDatabase, AsyncManagedTransaction

from engine.knowledge import embeddings
from engine.memory import cypher
from engine.memory.diff import VersionRef, changed_keys, classify
from engine.memory.model import (
    EMBEDDED_LABELS,
    NODE_LABELS,
    OPEN_VALID_TO,
    REJECTING_ACTIONS,
    REL_TYPES,
    RESERVED_NODE_KEYS,
    RESERVED_REL_KEYS,
    SIDES,
    ApplyResult,
    EdgeOut,
    LineageOut,
    MemNode,
    MemRel,
    NodeDetail,
    NodeOut,
    RevisionDiff,
    RevisionMeta,
    RevisionOut,
    ScopedDelta,
    Subgraph,
    VersionOut,
    backend_mismatch,
    mask_node,
    mask_rel,
    node_hash,
    outward_valid_to,
    public_node_props,
    public_rel_props,
    rel_hash,
    side_of,
    text_hash,
)
from engine.memory.search import hybrid

log = logging.getLogger(__name__)

WRITE_BATCH = 1000  # rows per UNWIND statement
EMBED_BATCH = 256  # texts per embedding call
GRAPH_LIMIT = 400  # nodes per subgraph at most
DEPTHS = (1, 2)
_SCALARS = (str, int, float, bool)


class MemoryUnavailable(Exception):
    """Neo4j cannot be reached or refused the request; the message is the exception class name."""


class MemoryGraph:
    def __init__(self, driver: AsyncDriver, uri: str, database: str, exact_scan_max: int) -> None:
        self._driver = driver
        self.uri = uri
        self.database = database
        self.exact_scan_max = exact_scan_max
        # False until ensure_schema succeeds: a start with Neo4j down leaves it False, and the
        # first ingest or read retries it (ensure_ready)
        self.schema_ok = False
        self._schema_lock = asyncio.Lock()

    @classmethod
    def connect(cls, uri: str, user: str, password: str, database: str = "neo4j", *,
                exact_scan_max: int = 50000) -> MemoryGraph:
        driver = AsyncGraphDatabase.driver(uri, auth=(user, password), connection_timeout=5)
        return cls(driver, uri, database, exact_scan_max)

    async def close(self) -> None:
        await self._driver.close()

    async def ping(self) -> None:
        try:
            async with self._driver.session(database=self.database) as session:
                await (await session.run(cypher.PING)).consume()
        except Exception as exc:  # callers only need "unavailable" and the class
            raise MemoryUnavailable(f"{type(exc).__name__}") from exc

    async def ensure_schema(self) -> None:
        async with self._driver.session(database=self.database) as session:
            for statement in cypher.SCHEMA:
                await (await session.run(statement)).consume()
            for statement in cypher.BACKFILL_CLOSED_IN:
                await (await session.run(statement, open=OPEN_VALID_TO)).consume()
        self.schema_ok = True

    async def ensure_ready(self) -> None:
        """``ensure_schema`` unless it already succeeded in this process; its driver errors
        propagate, so callers treat them as Neo4j being unavailable."""
        if self.schema_ok:
            return
        async with self._schema_lock:
            if not self.schema_ok:
                await self.ensure_schema()

    async def reset(self) -> None:
        if not self.uri or self.uri != os.environ.get("NEO4J_TEST_URI"):
            raise ValueError("refusing to reset a Neo4j that is not NEO4J_TEST_URI")
        async with self._driver.session(database=self.database) as session:
            await (await session.run(cypher.RESET)).consume()

    async def counts(self, project_id: str) -> dict[str, int]:
        """Current node versions per label (every label, zero when absent)."""
        await self.ensure_ready()
        async with self._driver.session(database=self.database) as session:
            result = await session.run(cypher.COUNTS, project_id=project_id, open=OPEN_VALID_TO)
            found = {r["label"]: r["n"] async for r in result}
        return {label: found.get(label, 0) for label in NODE_LABELS}

    async def apply_revision(self, meta: RevisionMeta, deltas: list[ScopedDelta]) -> ApplyResult:
        """Apply the projected ``deltas`` as revision n of ``meta.project_id`` in one write
        transaction; no revision when nothing changed, the existing one when ``ingest_key``
        was already applied. A start with Neo4j down left the schema to the first writer
        (``ensure_ready``); its driver errors propagate as Neo4j being unavailable."""
        plan = _Prepared.of(meta.project_id, deltas)
        await self.ensure_ready()
        backend = embeddings.backend_name()
        async with self._driver.session(database=self.database) as session:
            current, recorded = await session.execute_read(_read_current, plan)
        if recorded is not None and recorded != backend:
            raise backend_mismatch(recorded, backend)
        vectors: dict[str, list[float]] = {}
        await _embed_missing(_classify(plan, current).nodes_to_embed(), vectors)
        async with self._driver.session(database=self.database) as session:
            try:
                return await session.execute_write(_write, plan, meta, backend, vectors)
            except _NoChange as nothing:
                return nothing.result

    # -------------------------------------------------------------- maintenance
    async def current_texts(self, project_id: str, label: str) -> dict[str, str]:
        """uid -> text of the current versions of ``label`` in the project."""
        if label not in NODE_LABELS:
            raise ValueError(f"unknown node label {label!r}")
        rows = await self._read(_rows, cypher.CURRENT_TEXTS[label],
                                {"project_id": project_id, "open": OPEN_VALID_TO})
        return {r["uid"]: r["text"] or "" for r in rows}

    async def reembed(self, project_id: str) -> int:
        """Re-embed every current version of the embedded labels of the project with the active
        backend, and record that backend; returns how many vectors were written. Run it for each
        project after the embedding backend changed (``memory_admin reembed``)."""
        backend = embeddings.backend_name()
        rows = await self._read(_rows, cypher.CURRENT_EMBEDDED, {
            "project_id": project_id, "open": OPEN_VALID_TO, "labels": list(EMBEDDED_LABELS)})
        texts = [r["text"] for r in rows]
        vectors: list[list[float]] = []
        for i in range(0, len(texts), EMBED_BATCH):
            vectors += await asyncio.to_thread(embeddings.embed, texts[i:i + EMBED_BATCH])
        out = [{"vid": r["vid"], "embedding": v} for r, v in zip(rows, vectors, strict=True)]

        async def write(tx: AsyncManagedTransaction) -> int:
            written = await _total(tx, cypher.SET_EMBEDDINGS, "rows", out, "set",
                                   project_id=project_id)
            await (await tx.run(cypher.META_REPLACE, value=backend)).consume()
            return written

        async with self._driver.session(database=self.database) as session:
            return await session.execute_write(write)

    # MemoryRetriever: hybrid search (engine.memory.search) as a method.
    retrieve = hybrid

    # -------------------------------------------------------------- reads (as of a revision)
    async def _read(self, work: Any, *args: Any) -> Any:
        await self.ensure_ready()
        async with self._driver.session(database=self.database) as session:
            return await session.execute_read(work, *args)

    async def subgraph(self, project_id: str, *, uid: str | None = None, depth: int = 1,
                       labels: Sequence[str] | None = None, side: str | None = None,
                       as_of: int | None = None, limit: int = GRAPH_LIMIT) -> Subgraph:
        """``uid`` and its neighbours within ``depth`` hops (nearest first), or without
        ``uid`` the project's Domains, Applications and Solutions; ``labels`` and ``side``
        filter all but ``uid`` itself. ``total`` counts before ``limit``."""
        if depth not in DEPTHS:
            raise ValueError(f"depth must be one of {DEPTHS}, got {depth}")
        if not 1 <= limit <= GRAPH_LIMIT:
            raise ValueError(f"limit must be 1..{GRAPH_LIMIT}, got {limit}")
        unknown = set(labels or ()) - set(NODE_LABELS)
        if unknown:
            raise ValueError(f"unknown node labels {sorted(unknown)}")
        if side is not None and side not in SIDES:
            raise ValueError(f"side must be one of {SIDES}, got {side!r}")
        p = {"project_id": project_id, "k": _moment(as_of), "uid": uid, "limit": limit,
             "labels": list(labels) if labels else None, "side": side}
        statement = cypher.TOP_LEVEL if uid is None else cypher.NEIGHBOURHOOD[depth]
        return await self._read(_subgraph, statement, p)

    async def lineage_chain(self, project_id: str, uid: str, as_of: int | None = None, *,
                            limit: int = GRAPH_LIMIT) -> Subgraph:
        """``uid`` and what MIGRATED_TO, TESTS and FAILED_AT (either direction) and CONTAINS
        (down from a Process) reach from it in at most 4 hops, nearest first (then by uid);
        ``total`` counts before ``limit``."""
        if not 1 <= limit <= GRAPH_LIMIT:
            raise ValueError(f"limit must be 1..{GRAPH_LIMIT}, got {limit}")
        return await self._read(_lineage_chain, {"project_id": project_id, "uid": uid,
                                                 "k": _moment(as_of), "limit": limit})

    async def node_detail(self, project_id: str, uid: str,
                          as_of: int | None = None) -> NodeDetail | None:
        return await self._read(_node_detail, {"project_id": project_id, "uid": uid,
                                               "k": _moment(as_of)})

    async def current_node(self, project_id: str, uid: str) -> NodeOut | None:
        return await self._read(_current_node, {"project_id": project_id, "uid": uid,
                                                "k": _moment(None)})

    async def revisions(self, project_id: str, *, before: int | None = None,
                        limit: int = 50) -> list[RevisionOut]:
        """Revisions below ``before`` (all when None), newest first."""
        return await self._read(_revisions, {
            "project_id": project_id, "limit": limit,
            "before": OPEN_VALID_TO if before is None else before})

    async def latest_revision(self, project_id: str) -> RevisionOut | None:
        found = await self.revisions(project_id, limit=1)
        return found[0] if found else None

    async def diff(self, project_id: str, n: int) -> RevisionDiff | None:
        """What revision ``n`` added, changed and removed (re-attachments left out)."""
        return await self._read(_diff, {"project_id": project_id, "n": n})


# ------------------------------------------------------------------ read internals
def _moment(as_of: int | None) -> int:
    """The `$k` of a read: ``as_of``, or the one value only open versions are valid at."""
    return OPEN_VALID_TO - 1 if as_of is None else as_of


async def _rows(tx: AsyncManagedTransaction, statement: str, p: dict[str, Any],
                **extra: Any) -> list[dict[str, Any]]:
    return await (await tx.run(statement, {**p, **extra})).data()


async def _annotate(tx: AsyncManagedTransaction, p: dict[str, Any], nodes: list[dict[str, Any]],
                    edges: list[dict[str, Any]]) -> tuple[list[NodeOut], list[EdgeOut]]:
    """Builds the read models of ``nodes`` (``{label, p}`` rows) and ``edges`` (``{type, src,
    dst, p}`` rows), applying the curations valid at ``p["k"]``: curated (ABOUT-target of a
    Curation), obsolete Memory, rejected MIGRATED_TO."""
    targeted = [n["p"]["uid"] for n in nodes if n["label"] == "Memory"]
    targeted += [e["p"]["uid"] for e in edges if e["type"] == "MIGRATED_TO"]
    actions: dict[str, set[str]] = {}
    if targeted:
        for r in await _rows(tx, cypher.CURATION_TARGETS, p, uids=targeted):
            actions.setdefault(r["target_uid"], set()).add(r["action"])
    curated: set[str] = set()
    if nodes:
        vids = [n["p"]["vid"] for n in nodes]
        curated = {r["vid"] for r in await _rows(tx, cypher.CURATED, p, vids=vids)}
    node_out = [
        NodeOut.of(n["label"], n["p"], curated=n["p"]["vid"] in curated,
                   obsolete=n["label"] == "Memory" and "obsolete" in actions.get(n["p"]["uid"], ()))
        for n in nodes
    ]
    edge_out = [
        EdgeOut.of(e["type"], e["src"], e["dst"], e["p"],
                   rejected=bool(actions.get(e["p"]["uid"], set()) & set(REJECTING_ACTIONS)))
        for e in edges
    ]
    return node_out, edge_out


_EMPTY = Subgraph(nodes=[], edges=[], truncated=False, total=0)


async def _subgraph(tx: AsyncManagedTransaction, statement: str, p: dict[str, Any]) -> Subgraph:
    row = await (await tx.run(statement, p)).single()
    if row is None or not row["nodes"]:
        return _EMPTY
    vids = [n["p"]["vid"] for n in row["nodes"]]
    edges = [r["edge"] for r in await _rows(tx, cypher.EDGES_AMONG, p, vids=vids)]
    nodes, edge_out = await _annotate(tx, p, row["nodes"], edges)
    return Subgraph(nodes=nodes, edges=edge_out, truncated=row["total"] > p["limit"],
                    total=row["total"])


async def _lineage_chain(tx: AsyncManagedTransaction, p: dict[str, Any]) -> Subgraph:
    row = await (await tx.run(cypher.LINEAGE, p)).single()
    if row is None:
        return _EMPTY
    found = [row["center"]]
    for hop in row["hops"]:
        found += sorted(hop, key=lambda n: n["p"]["uid"])
    kept = found[:p["limit"]]
    uids = {n["p"]["uid"] for n in kept}
    edges = {e["p"]["uid"]: e for e in row["edges"] if e["src"] in uids and e["dst"] in uids}
    nodes, edge_out = await _annotate(tx, p, kept, sorted(
        edges.values(), key=lambda e: (e["type"], e["p"]["uid"])))
    return Subgraph(nodes=nodes, edges=edge_out, truncated=len(found) > p["limit"],
                    total=len(found))


async def _revisions(tx: AsyncManagedTransaction, p: dict[str, Any]) -> list[RevisionOut]:
    return [RevisionOut(**r["revision"]) for r in await _rows(tx, cypher.REVISIONS_BEFORE, p)]


async def _current_node(tx: AsyncManagedTransaction, p: dict[str, Any]) -> NodeOut | None:
    row = await (await tx.run(cypher.NODE_AT, p)).single()
    if row is None:
        return None
    (node,), _ = await _annotate(tx, p, [row["node"]], [])
    return node


def _version_ref(kind: str, stored: dict[str, Any], props: dict[str, Any]) -> VersionRef:
    return VersionRef(uid=stored["uid"], kind=kind, name=stored.get("name") or "",
                      content_hash=stored["content_hash"], props=props,
                      text_hash=stored.get("text_hash"))


def _versions(rows: list[dict[str, Any]]) -> list[VersionOut]:
    """``rows`` newest first; each version's changed keys are against the next older one."""
    refs = [_version_ref(r["node"]["label"], r["node"]["p"], public_node_props(r["node"]["p"]))
            for r in rows]
    out = []
    for i, r in enumerate(rows):
        stored = r["node"]["p"]
        older = refs[i + 1] if i + 1 < len(refs) else None
        out.append(VersionOut(
            valid_from=stored["valid_from"], valid_to=outward_valid_to(stored["valid_to"]),
            phase=stored["phase"], actor=r["actor"], at=r["at"], props=refs[i].props,
            changed_keys=changed_keys(older, refs[i]) if older else []))
    return out


async def _node_detail(tx: AsyncManagedTransaction, p: dict[str, Any]) -> NodeDetail | None:
    row = await (await tx.run(cypher.NODE_AT, p)).single()
    if row is None:
        return None
    versions = _versions(await _rows(tx, cypher.NODE_VERSIONS, p))
    about = [r["node"] for r in await _rows(tx, cypher.ABOUT_SOURCES, p)]
    lineage = await _rows(tx, cypher.LINEAGE_EDGES, p)
    about_uids = {n["p"]["uid"] for n in about}
    targeting = [r["node"] for r in await _rows(tx, cypher.CURATIONS_OF, p)
                 if r["node"]["p"]["uid"] not in about_uids]
    others = [r["other"] for r in lineage]
    nodes, edges = await _annotate(tx, p, [row["node"], *about, *targeting, *others],
                                   [r["edge"] for r in lineage])
    node, about_out = nodes[0], nodes[1:1 + len(about)]
    targeting_out = nodes[1 + len(about):1 + len(about) + len(targeting)]
    others_out = nodes[1 + len(about) + len(targeting):]
    curations = [n for n in about_out if n.label == "Curation"] + targeting_out
    curations.sort(key=lambda n: (-n.valid_from, n.uid))
    return NodeDetail(
        node=node, versions=versions, about=about_out, curations=curations,
        lineage=[LineageOut(edge=e, other=o, direction="out" if r["outgoing"] else "in")
                 for r, e, o in zip(lineage, edges, others_out, strict=True)],
    )


async def _diff(tx: AsyncManagedTransaction, p: dict[str, Any]) -> RevisionDiff | None:
    row = await (await tx.run(cypher.REVISION_AT, p)).single()
    if row is None:
        return None

    async def nodes(statement: str) -> list[VersionRef]:
        return [_version_ref(r["node"]["label"], r["node"]["p"],
                             public_node_props(r["node"]["p"]))
                for r in await _rows(tx, statement, p)]

    async def rels(statement: str) -> list[VersionRef]:
        return [_version_ref(r["edge"]["type"], r["edge"]["p"], public_rel_props(r["edge"]["p"]))
                for r in await _rows(tx, statement, p)]

    return RevisionDiff(
        revision=RevisionOut(**row["revision"]),
        nodes=classify(await nodes(cypher.NODES_OPENED_AT), await nodes(cypher.NODES_CLOSED_AT)),
        relationships=classify(await rels(cypher.RELS_OPENED_AT),
                               await rels(cypher.RELS_CLOSED_AT)),
    )


# ------------------------------------------------------------------ apply_revision internals
@dataclass
class _Node:
    scope: str
    node: MemNode  # masked
    content_hash: str
    text_hash: str


@dataclass
class _Rel:
    scope: str
    rel: MemRel  # masked
    content_hash: str


@dataclass
class _Prepared:
    project_id: str
    scopes: list[str]
    nodes: list[_Node]
    rels: list[_Rel]
    refused: int

    @classmethod
    def of(cls, project_id: str, deltas: list[ScopedDelta]) -> _Prepared:
        """Validates the deltas against the closed sets, then masks and hashes every item."""
        scopes = [d.scope for d in deltas]
        if len(set(scopes)) != len(scopes):
            raise ValueError(f"a scope appears in more than one delta: {scopes}")
        nodes: list[_Node] = []
        rels: list[_Rel] = []
        refused = 0
        seen: set[str] = set()
        for d in deltas:
            for n in d.nodes:
                _check_node(n)
                if n.uid in seen:
                    raise ValueError(f"node uid {n.uid!r} appears twice")
                seen.add(n.uid)
                masked, was_refused = mask_node(n)
                refused += was_refused
                nodes.append(_Node(d.scope, masked, node_hash(masked), text_hash(masked.text)))
            for r in d.rels:
                _check_rel(r)
                if r.uid in seen:
                    raise ValueError(f"relationship uid {r.uid!r} appears twice")
                seen.add(r.uid)
                masked_rel = mask_rel(r)
                rels.append(_Rel(d.scope, masked_rel, rel_hash(masked_rel)))
        return cls(project_id, scopes, nodes, rels, refused)

    def params(self) -> dict[str, Any]:
        return {"project_id": self.project_id, "scopes": self.scopes}


def _check_props(where: str, props: dict[str, Any], reserved: frozenset[str]) -> None:
    clash = reserved & props.keys()
    if clash:
        raise ValueError(f"{where}: props may not set {sorted(clash)}")
    for key, value in props.items():
        ok = value is None or isinstance(value, _SCALARS) or (
            isinstance(value, list) and all(isinstance(v, _SCALARS) for v in value)
            and len({type(v) for v in value}) <= 1)
        if not ok:
            raise ValueError(f"{where}: property {key!r} is not a scalar or a list of scalars "
                             "of one type")


def _check_node(n: MemNode) -> None:
    if n.label not in NODE_LABELS:
        raise ValueError(f"unknown node label {n.label!r}")
    _check_props(n.uid, n.props, RESERVED_NODE_KEYS)
    side_of(n.label, n.props)
    if n.embedding is not None and len(n.embedding) != cypher.EMBEDDING_DIMENSIONS:
        raise ValueError(f"{n.uid}: a preset embedding needs {cypher.EMBEDDING_DIMENSIONS} floats")


def _check_rel(r: MemRel) -> None:
    if r.type not in REL_TYPES:
        raise ValueError(f"unknown relationship type {r.type!r}")
    _check_props(r.uid, r.props, RESERVED_REL_KEYS)


@dataclass
class _Current:
    nodes: dict[str, dict[str, Any]]  # uid -> {scope, content_hash, text_hash, vid}
    rels: dict[str, tuple[str, dict[str, Any]]]  # uid -> (type, {scope, content_hash})


async def _read_current(tx: AsyncManagedTransaction,
                        plan: _Prepared) -> tuple[_Current, str | None]:
    current = await _current(tx, plan)
    meta = await (await tx.run(cypher.META_GET)).single()
    return current, (meta["value"] if meta else None)


async def _current(tx: AsyncManagedTransaction, plan: _Prepared) -> _Current:
    """Current versions owned by the delta scopes, and those of every delta uid (index seeks)."""
    p = {**plan.params(), "open": OPEN_VALID_TO}
    nodes: dict[str, dict[str, Any]] = {}
    for statement, extra in ((cypher.CURRENT_IN_SCOPE, {}),
                             (cypher.CURRENT_BY_UID, {"uids": [n.node.uid for n in plan.nodes]})):
        nodes.update((r["uid"], r) for r in await (await tx.run(statement, **p, **extra)).data())
    rels: dict[str, tuple[str, dict[str, Any]]] = {}
    emitted = _by_type(plan.rels)
    for rel_type in REL_TYPES:
        result = await tx.run(cypher.CURRENT_RELS[rel_type], **p)
        rels.update((r["uid"], (rel_type, r)) for r in await result.data())
        uids = [r.rel.uid for r in emitted.get(rel_type, [])]
        if uids:
            result = await tx.run(cypher.CURRENT_RELS_BY_UID[rel_type], **p, uids=uids)
            rels.update((r["uid"], (rel_type, r)) for r in await result.data())
    return _Current(nodes, rels)


@dataclass
class _Diff:
    new_nodes: list[_Node] = field(default_factory=list)
    changed_nodes: list[tuple[_Node, dict[str, Any]]] = field(default_factory=list)  # + previous
    vanished_vids: list[str] = field(default_factory=list)
    new_rels: list[_Rel] = field(default_factory=list)
    changed_rels: list[_Rel] = field(default_factory=list)
    vanished_rels: dict[str, list[str]] = field(default_factory=dict)  # type -> uids
    guarded: int = 0  # emitted items owned by a scope outside this apply, left alone

    def nodes_to_embed(self) -> list[_Node]:
        """New and changed nodes of the embedded labels whose text changed and that carry no
        preset vector (an unchanged text keeps the previous version's vector)."""
        out = [n for n in self.new_nodes if _needs_vector(n)]
        out += [n for n, prev in self.changed_nodes
                if _needs_vector(n) and prev["text_hash"] != n.text_hash]
        return out


def _needs_vector(n: _Node) -> bool:
    return n.node.label in EMBEDDED_LABELS and n.node.embedding is None and bool(n.node.text)


def _settle(item_scope: str, prev: dict[str, Any] | None, content_hash: str,
            scopes: list[str]) -> str:
    """``new``, ``same``, ``changed`` or ``guarded`` for one emitted item and its current
    version. An item current in another scope of this apply is handed over (``changed``: the
    old version closes, the new one opens in the emitting scope); one current in a scope
    outside this apply belongs to that scope and is left alone (``guarded``)."""
    if prev is None:
        return "new"
    if prev["scope"] == item_scope:
        return "same" if prev["content_hash"] == content_hash else "changed"
    return "changed" if prev["scope"] in scopes else "guarded"


def _classify(plan: _Prepared, current: _Current) -> _Diff:
    """Per scope: same hash untouched, other hash changed, absent from every delta vanished,
    unknown new (see ``_settle`` for items moving between scopes)."""
    diff = _Diff()
    node_uids = {n.node.uid for n in plan.nodes}
    rel_uids = {r.rel.uid for r in plan.rels}
    for n in plan.nodes:
        prev = current.nodes.get(n.node.uid)
        state = _settle(n.scope, prev, n.content_hash, plan.scopes)
        if state == "new":
            diff.new_nodes.append(n)
        elif state == "changed":
            diff.changed_nodes.append((n, prev))
        diff.guarded += state == "guarded"
    diff.vanished_vids = [prev["vid"] for uid, prev in current.nodes.items()
                          if prev["scope"] in plan.scopes and uid not in node_uids]
    for r in plan.rels:
        found = current.rels.get(r.rel.uid)
        state = _settle(r.scope, found[1] if found else None, r.content_hash, plan.scopes)
        if state == "new":
            diff.new_rels.append(r)
        elif state == "changed":
            diff.changed_rels.append(r)
        diff.guarded += state == "guarded"
    for uid, (rel_type, prev) in current.rels.items():
        if prev["scope"] in plan.scopes and uid not in rel_uids:
            diff.vanished_rels.setdefault(rel_type, []).append(uid)
    return diff


async def _embed_missing(nodes: list[_Node], vectors: dict[str, list[float]]) -> int:
    """Embeds, in batches, each distinct text of ``nodes`` that ``vectors`` (by text hash)
    does not hold yet; returns how many texts it embedded."""
    texts: dict[str, str] = {}
    for n in nodes:
        if n.text_hash not in vectors:
            texts.setdefault(n.text_hash, n.node.text)
    hashes = list(texts)
    for i in range(0, len(hashes), EMBED_BATCH):
        batch = hashes[i:i + EMBED_BATCH]
        found = await asyncio.to_thread(embeddings.embed, [texts[h] for h in batch])
        vectors.update(zip(batch, found, strict=True))
    return len(hashes)


class _NoChange(Exception):
    """Rolls back the write transaction of a revision that would change nothing."""

    def __init__(self, result: ApplyResult) -> None:
        super().__init__("no change")
        self.result = result


def _batches(items: list[Any]) -> list[list[Any]]:
    return [items[i:i + WRITE_BATCH] for i in range(0, len(items), WRITE_BATCH)]


async def _total(tx: AsyncManagedTransaction, statement: str, key: str, items: list[Any],
                 column: str, **params: Any) -> int:
    """Runs ``statement`` over ``items`` in batches; the sum of ``column`` of every row."""
    total = 0
    for batch in _batches(items):
        result = await tx.run(statement, {key: batch, **params})
        total += sum(r[column] for r in await result.data())
    return total


async def _write(tx: AsyncManagedTransaction, plan: _Prepared, meta: RevisionMeta, backend: str,
                 vectors: dict[str, list[float]]) -> ApplyResult:
    p = {"project_id": plan.project_id, "open": OPEN_VALID_TO}
    ikey = f"{plan.project_id}|{meta.ingest_key}"
    done = await (await tx.run(cypher.REVISION_BY_IKEY, **p, ikey=ikey)).single()
    if done is not None:
        return ApplyResult(revision=done["n"], skipped=True, refused=plan.refused)
    n = (await (await tx.run(cypher.NEXT_REVISION, **p)).single())["n"]
    recorded = (await (await tx.run(cypher.META_SET, value=backend)).single())["value"]
    if recorded != backend:
        raise backend_mismatch(recorded, backend)

    diff = _classify(plan, await _current(tx, plan))  # re-read: the state this tx changes
    to_embed = diff.nodes_to_embed()
    late = await _embed_missing(to_embed, vectors)  # only when the state moved since the read
    if late:
        log.info("memory %s: embedded %d texts inside the write transaction (state moved since "
                 "the first read)", plan.project_id, late)
    res = ApplyResult(revision=n, refused=plan.refused, embedded=len(to_embed))
    w = {**p, "n": n, "phase": meta.phase}

    # 1. In-scope relationships that changed or vanished close first (changed ones re-open in 6).
    for rel_type, uids in diff.vanished_rels.items():
        res.removed += await _total(tx, cypher.CLOSE_RELS[rel_type], "uids", uids, "closed", **w)
    for rel_type, rels in _by_type(diff.changed_rels).items():
        await _total(tx, cypher.CLOSE_RELS[rel_type], "uids", [r.rel.uid for r in rels],
                     "closed", **w)
    # 2. Close the changed and vanished node versions.
    changed_vids = [prev["vid"] for _, prev in diff.changed_nodes]
    closed_vids = changed_vids + diff.vanished_vids
    await _total(tx, cypher.CLOSE_NODES, "vids", closed_vids, "closed", **w)
    res.removed += len(diff.vanished_vids)
    # 3. Open the new versions.
    rows: dict[str, list[dict[str, Any]]] = {}
    for node, prev in [(x, None) for x in diff.new_nodes] + diff.changed_nodes:
        rows.setdefault(node.node.label, []).append(
            _node_row(node, prev, plan.project_id, n, meta.phase, vectors))
    for label, label_rows in rows.items():
        for batch in _batches(label_rows):
            await (await tx.run(cypher.CREATE_NODES[label], rows=batch, **p)).consume()
    res.added += len(diff.new_nodes)
    res.changed += len(diff.changed_nodes)
    # 4. Move every current relationship of a re-versioned node onto its new version.
    for rel_type in REL_TYPES:
        for statement in cypher.REATTACH[rel_type]:
            res.reattached += await _total(tx, statement, "vids", changed_vids, "moved", **w)
    # 5. Relationships still on a closed version lost an endpoint: they close.
    for rel_type in REL_TYPES:
        res.removed += await _total(tx, cypher.CLOSE_RELS_OF_CLOSED[rel_type], "vids",
                                    closed_vids, "closed", **w)
    # 6. Create the new and changed in-scope relationships between current endpoints.
    changed_rel_uids = {r.rel.uid for r in diff.changed_rels}
    for rel_type, rels in _by_type(diff.new_rels + diff.changed_rels).items():
        created: set[str] = set()
        for batch in _batches([_rel_row(r, plan.project_id, n, meta.phase) for r in rels]):
            result = await tx.run(cypher.CREATE_RELS[rel_type], rows=batch, **p)
            created.update(r["uid"] for r in await result.data())
        res.dangling += len(rels) - len(created)
        res.changed += len(created & changed_rel_uids)
        res.added += len(created - changed_rel_uids)

    if res.added + res.changed + res.removed == 0:
        if diff.guarded:
            log.debug("memory %s: no revision; %d emitted items are owned by scopes outside "
                      "this apply and were left alone", plan.project_id, diff.guarded)
        raise _NoChange(ApplyResult(revision=None, dangling=res.dangling, refused=plan.refused))
    await (await tx.run(
        cypher.CREATE_REVISION, **p, rid=f"{plan.project_id}|{n}", ikey=ikey, n=n,
        phase=meta.phase, scopes=plan.scopes, job_id=meta.source_job_id,
        ingest_job_id=meta.ingest_job_id, iteration=meta.iteration,
        at=datetime.now(UTC).isoformat(), actor=meta.actor, added=res.added,
        changed=res.changed, removed=res.removed,
    )).consume()
    return res


def _by_type(rels: list[_Rel]) -> dict[str, list[_Rel]]:
    out: dict[str, list[_Rel]] = {}
    for r in rels:
        out.setdefault(r.rel.type, []).append(r)
    return out


def _node_row(node: _Node, prev: dict[str, Any] | None, project_id: str, n: int, phase: str,
              vectors: dict[str, list[float]]) -> dict[str, Any]:
    m = node.node
    embedding, copy_from = m.embedding, None
    if embedding is None and _needs_vector(node):
        if prev is not None and prev["text_hash"] == node.text_hash:
            copy_from = prev["vid"]
        else:
            embedding = vectors[node.text_hash]
    props = {
        **m.props, "uid": m.uid, "vid": f"{project_id}|{m.uid}|{n}", "project_id": project_id,
        "scope": node.scope, "content_hash": node.content_hash, "text_hash": node.text_hash,
        "valid_from": n, "valid_to": OPEN_VALID_TO, "phase": phase,
        "side": side_of(m.label, m.props), "name": m.name, "text": m.text,
    }
    return {"props": props, "embedding": embedding, "copy_from": copy_from}


def _rel_row(r: _Rel, project_id: str, n: int, phase: str) -> dict[str, Any]:
    props = {**r.rel.props, "uid": r.rel.uid, "project_id": project_id, "scope": r.scope,
             "content_hash": r.content_hash, "valid_from": n, "valid_to": OPEN_VALID_TO,
             "phase": phase}
    return {"src": r.rel.src, "dst": r.rel.dst, "props": props}
