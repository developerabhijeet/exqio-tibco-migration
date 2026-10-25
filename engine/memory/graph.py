"""``MemoryGraph``: the async Neo4j connection of the memory, its schema, the versioned write
path (``apply_revision``) and test reset."""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from neo4j import AsyncDriver, AsyncGraphDatabase, AsyncManagedTransaction

from engine.knowledge import embeddings
from engine.memory import cypher
from engine.memory.model import (
    EMBEDDED_LABELS,
    NODE_LABELS,
    OPEN_VALID_TO,
    REL_TYPES,
    RESERVED_NODE_KEYS,
    RESERVED_REL_KEYS,
    ApplyResult,
    KnowledgeError,
    MemNode,
    MemRel,
    RevisionMeta,
    ScopedDelta,
    mask_node,
    mask_rel,
    node_hash,
    rel_hash,
    side_of,
    text_hash,
)

log = logging.getLogger(__name__)

WRITE_BATCH = 1000  # rows per UNWIND statement
EMBED_BATCH = 256  # texts per embedding call
_SCALARS = (str, int, float, bool)


class MemoryUnavailable(Exception):
    """Neo4j cannot be reached or refused the request; the message is the exception class name."""


class MemoryGraph:
    def __init__(self, driver: AsyncDriver, uri: str, database: str, exact_scan_max: int) -> None:
        self._driver = driver
        self.uri = uri
        self.database = database
        self.exact_scan_max = exact_scan_max

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

    async def reset(self) -> None:
        if not self.uri or self.uri != os.environ.get("NEO4J_TEST_URI"):
            raise ValueError("refusing to reset a Neo4j that is not NEO4J_TEST_URI")
        async with self._driver.session(database=self.database) as session:
            await (await session.run(cypher.RESET)).consume()

    async def counts(self, project_id: str) -> dict[str, int]:
        """Current node versions per label (every label, zero when absent)."""
        async with self._driver.session(database=self.database) as session:
            result = await session.run(cypher.COUNTS, project_id=project_id, open=OPEN_VALID_TO)
            found = {r["label"]: r["n"] async for r in result}
        return {label: found.get(label, 0) for label in NODE_LABELS}

    async def apply_revision(self, meta: RevisionMeta, deltas: list[ScopedDelta]) -> ApplyResult:
        """Apply the projected ``deltas`` as revision n of ``meta.project_id`` in one write
        transaction; no revision when nothing changed, the existing one when ``ingest_key``
        was already applied."""
        plan = _Prepared.of(meta.project_id, deltas)
        backend = embeddings.backend_name()
        async with self._driver.session(database=self.database) as session:
            current, recorded = await session.execute_read(_read_current, plan)
        if recorded is not None and recorded != backend:
            raise _backend_mismatch(recorded, backend)
        vectors: dict[str, list[float]] = {}
        await _embed_missing(_classify(plan, current).nodes_to_embed(), vectors)
        async with self._driver.session(database=self.database) as session:
            try:
                return await session.execute_write(_write, plan, meta, backend, vectors)
            except _NoChange as nothing:
                return nothing.result


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


def _backend_mismatch(recorded: str, active: str) -> KnowledgeError:
    return KnowledgeError(
        f"the memory graph holds vectors from {recorded!r} but the active embedding backend is "
        f"{active!r}; re-embed or reset the memory"
    )


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
        raise _backend_mismatch(recorded, backend)

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
