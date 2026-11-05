"""The one-time migration of the Postgres ``knowledge`` schema into the Neo4j memory graph.

Spec section 4, Migration. The API lifespan queues the ``memory.migrate`` job once
(:func:`legacy_pending`); ``python -m api.app.memory_admin migrate`` queues it by hand. This is the
only module that reads the old schema (plain parameterised SQL over psycopg, run in a thread).

Per project, one revision (``phase: migration``, ``ingest_key: migration:pg-knowledge``):

- ``knowledge.memory_items`` other than seeds become ``memory_events`` rows (kind ``decision``,
  the id kept, the item's kind in ``context.kind``, ``actor`` = ``created_by``, ``created_at``
  kept) unless the id is already there, so a later re-ingest rebuilds them;
- the estate, analysis, design, latest build, verify, deploy and gate scopes are projected from
  the Postgres application tables by the ``memory_ingest`` loaders and projectors, with every
  ``memory_events`` row and the seed patterns (``seed:<project>:<pattern>`` becomes
  ``seed:<pattern>``); Memory nodes keep their old vector when ``knowledge.meta`` records the
  active embedding backend and the text is unchanged;
- a ``knowledge.chunks`` row whose masked text no projected Chunk has is copied as
  ``chunk:migrated:<old id>`` in scope ``migration:orphan``, ABOUT its process.

Then every memory item and every chunk text must be current in Neo4j; on any miss the job fails
naming the ids and the schema is left as it is. When every project verified, the schema is
renamed ``knowledge_retired`` (never dropped) and ``neo4j_migrated_at`` recorded in its ``meta``.
Rollback: ``ALTER SCHEMA knowledge_retired RENAME TO knowledge``. Re-running is safe: copied
events are skipped and the revision's ingest key makes a second apply a no-op.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import psycopg
from psycopg.rows import dict_row
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.app.jobs.locks import try_advisory_lock
from api.app.jobs.registry import RetryLater, WorkerServices
from api.app.jobs.runner import JobContext
from api.app.models import Build, Gate, MemoryEvent, Plan, Process, Project
from api.app.services import evidence as evidence_svc
from api.app.services import memory_ingest as mi
from api.app.services.knowledge import _mask_value, about_uid
from engine.knowledge import embeddings
from engine.masking import mask
from engine.memory.graph import MemoryGraph
from engine.memory.model import KnowledgeError, MemNode, MemRel, RevisionMeta, ScopedDelta

log = logging.getLogger(__name__)

MIGRATE_KIND = mi.MIGRATE_KIND
REEMBED_KIND = mi.REEMBED_KIND
INGEST_KEY = "migration:pg-knowledge"
ACTOR = "migration"
ORPHAN_SCOPE = "migration:orphan"
MIGRATE_LOCK = "memory:migrate"
PROJECT_LOCK_WAIT_S = 120
PROJECT_LOCK_POLL_S = 0.5
BUSY_RETRY_S = 5
# The artifact an orphan chunk cites, by its old kind (summary, rule and finding: the analysis).
ORPHAN_ARTIFACT = {"design": "design"}

# The old schema, read only here (the R5 test allows this module alone).
PRESENT_SQL = "SELECT to_regclass('knowledge.meta') IS NOT NULL AS present"
STAMP_SQL = "SELECT value FROM knowledge.meta WHERE key = 'neo4j_migrated_at'"
BACKEND_SQL = "SELECT value FROM knowledge.meta WHERE key = 'embedding_backend'"
PROJECTS_SQL = ("SELECT project_id FROM knowledge.memory_items UNION "
                "SELECT project_id FROM knowledge.chunks ORDER BY 1")
ITEMS_SQL = ("SELECT id, kind, text, context, source, created_by, created_at, "
             "embedding::text AS embedding FROM knowledge.memory_items WHERE project_id = %s "
             "ORDER BY created_at, id")
CHUNKS_SQL = ("SELECT id, process_id, kind, text, embedding::text AS embedding "
              "FROM knowledge.chunks WHERE project_id = %s ORDER BY id")
RETIRE_SQL = "ALTER SCHEMA knowledge RENAME TO knowledge_retired"
RETIRED_STAMP_SQL = ("INSERT INTO knowledge_retired.meta (key, value) "
                     "VALUES ('neo4j_migrated_at', now()::text) "
                     "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value")


class MigrationBusy(RuntimeError):
    """Another migration holds the ``memory:migrate`` lock (a second replica's job)."""


class MigrationReport(BaseModel):
    project_id: str
    memory_items: int
    chunks: int
    orphans: int
    revision: int | None
    missing: list[str]


# ------------------------------------------------------------------ the old schema (sync)
def _connect(dsn: str) -> psycopg.Connection:
    return psycopg.connect(dsn, autocommit=True, row_factory=dict_row, connect_timeout=5)


def _present(conn: psycopg.Connection) -> bool:
    return bool(conn.execute(PRESENT_SQL).fetchone()["present"])


def _projects(conn: psycopg.Connection) -> list[str]:
    return [r["project_id"] for r in conn.execute(PROJECTS_SQL).fetchall()]


def _vector(raw: str | None) -> list[float] | None:
    return [float(x) for x in json.loads(raw)] if raw else None


def _read_project(conn: psycopg.Connection, project_id: str) -> tuple[list[dict[str, Any]],
                                                                    list[dict[str, Any]], str | None]:
    row = conn.execute(BACKEND_SQL).fetchone()
    items = conn.execute(ITEMS_SQL, [project_id]).fetchall()
    chunks = conn.execute(CHUNKS_SQL, [project_id]).fetchall()
    return items, chunks, row["value"] if row else None


def _retire(conn: psycopg.Connection) -> None:
    with conn.transaction():
        conn.execute(RETIRE_SQL)
        conn.execute(RETIRED_STAMP_SQL)


async def legacy_pending(s: AsyncSession) -> bool:
    """Schema ``knowledge`` exists and records no ``neo4j_migrated_at`` (the lifespan's test)."""
    if not await s.scalar(text(PRESENT_SQL)):
        return False
    return await s.scalar(text(STAMP_SQL)) is None


# ------------------------------------------------------------------ one project
def _seed_prefix(project_id: str) -> str:
    return f"seed:{project_id}:"


def _memory_uid(project_id: str, item_id: str) -> str:
    """The Memory uid of an old item: seeds ``seed:<project>:<pattern>`` are ``seed:<pattern>``."""
    prefix = _seed_prefix(project_id)
    return f"seed:{item_id[len(prefix):]}" if item_id.startswith(prefix) else item_id


async def _copy_items(s: AsyncSession, project_id: str, items: list[dict[str, Any]]) -> int:
    """The non-seed items as ``memory_events`` decisions, those whose id is absent; committed."""
    prefix = _seed_prefix(project_id)
    wanted = [i for i in items if not i["id"].startswith(prefix)]
    have = set((await s.execute(select(MemoryEvent.id).where(
        MemoryEvent.id.in_([i["id"] for i in wanted])))).scalars()) if wanted else set()
    added = 0
    for item in wanted:
        if item["id"] in have:
            continue
        context = _mask_value({**(item["context"] or {}), "kind": item["kind"]})
        about = await about_uid(s, context)
        if about and "about_uid" not in context:
            context["about_uid"] = about
        s.add(MemoryEvent(id=item["id"], project_id=project_id, kind="decision",
                          text=mask(item["text"]), context=context,
                          actor=item["created_by"], created_at=item["created_at"]))
        added += 1
    await s.commit()
    return added


async def _phase(s: AsyncSession, memory: MemoryGraph, project_id: str, phase: str,
                 source: dict[str, Any]) -> list[ScopedDelta]:
    try:
        return (await mi.PROJECTORS[phase](s, memory, project_id, source)).deltas
    except mi.Superseded:
        return []


async def _project(s: AsyncSession, memory: MemoryGraph, project_id: str) -> list[ScopedDelta]:
    """Every scope the ingests of the project would write, from its Postgres rows."""
    deltas = await _phase(s, memory, project_id, "discover", {})
    procs = (await s.execute(select(Process).where(Process.project_id == project_id)
                             .order_by(Process.ir_id))).scalars().all()
    for p in procs:
        source = {"process_id": p.id}
        deltas += await _phase(s, memory, project_id, "analysis", source)
        deltas += await _phase(s, memory, project_id, "design", source)
        build = await evidence_svc.latest_build(s, p.id)
        if build is not None:
            deltas += await _phase(s, memory, project_id, "build", {**source, "build_id": build.id})
            deltas += await _phase(s, memory, project_id, "verify", {**source, "build_id": build.id})
        plan_id = await s.scalar(select(Plan.id).join(Build, Build.id == Plan.build_id)
                                 .where(Build.process_id == p.id)
                                 .order_by(Plan.created_at.desc(), Plan.id.desc()).limit(1))
        if plan_id is not None:
            deltas += await _phase(s, memory, project_id, "deploy", {**source, "plan_id": plan_id})
        for gate in (await s.execute(select(Gate.gate).where(Gate.process_id == p.id)
                                     .order_by(Gate.gate))).scalars():
            deltas += await _phase(s, memory, project_id, "gate", {**source, "gate": gate})
    events = (await s.execute(select(MemoryEvent).where(MemoryEvent.project_id == project_id)
                              .order_by(MemoryEvent.created_at, MemoryEvent.id))).scalars().all()
    for e in events:
        deltas += await _phase(s, memory, project_id, e.kind, {"event_id": e.id})
    deltas += await _phase(s, memory, project_id, "seed", {})
    return deltas


def _unique(deltas: list[ScopedDelta]) -> list[ScopedDelta]:
    """Each uid once, in the first scope that emits it: as the ingests applied one after another
    would leave it (a later scope's copy of a node another scope owns is left alone)."""
    seen: set[str] = set()
    out: list[ScopedDelta] = []
    dropped = 0
    for d in deltas:
        nodes = [n for n in d.nodes if n.uid not in seen]
        seen.update(n.uid for n in nodes)
        rels = [r for r in d.rels if r.uid not in seen]
        seen.update(r.uid for r in rels)
        dropped += len(d.nodes) + len(d.rels) - len(nodes) - len(rels)
        out.append(d.model_copy(update={"nodes": nodes, "rels": rels}))
    if dropped:
        log.info("memory migration: %d items emitted by two scopes kept in the first", dropped)
    return out


def _reuse_vectors(deltas: list[ScopedDelta], project_id: str, items: list[dict[str, Any]]) -> None:
    """The old vector on each Memory node whose masked text is the old item's."""
    old = {_memory_uid(project_id, i["id"]): i for i in items if i["embedding"]}
    for d in deltas:
        for n in d.nodes:
            item = old.get(n.uid)
            if n.label == "Memory" and item and mask(item["text"]) == mask(n.text):
                n.embedding = _vector(item["embedding"])


async def _orphans(s: AsyncSession, deltas: list[ScopedDelta], chunks: list[dict[str, Any]],
                   reuse: bool) -> ScopedDelta:
    projected = {mask(n.text) for d in deltas for n in d.nodes if n.label == "Chunk"}
    nodes: list[MemNode] = []
    rels: list[MemRel] = []
    for c in chunks:
        masked = mask(c["text"])
        if masked in projected:
            continue
        uid = f"chunk:migrated:{c['id']}"
        nodes.append(MemNode(label="Chunk", uid=uid, name=f"{c['kind']} (migrated)", text=masked,
                             props={"kind": c["kind"], "source": "migration",
                                    "artifact_type": ORPHAN_ARTIFACT.get(c["kind"], "analysis"),
                                    "artifact_id": c["process_id"],
                                    "process_id": c["process_id"]},
                             embedding=_vector(c["embedding"]) if reuse else None))
        process = await s.get(Process, c["process_id"])
        if process is not None:
            rels.append(MemRel(type="ABOUT", src=uid, dst=f"process:{process.ir_id}"))
    return ScopedDelta(scope=ORPHAN_SCOPE, nodes=nodes, rels=rels)


async def _verify(memory: MemoryGraph, project_id: str, items: list[dict[str, Any]],
                  chunks: list[dict[str, Any]]) -> list[str]:
    """The old ids not found in Neo4j: items by Memory uid, chunks by masked text. A seed whose
    pattern is no longer shipped is not expected (the seed projector writes the shipped ones)."""
    memories = await memory.current_texts(project_id, "Memory")
    texts = set((await memory.current_texts(project_id, "Chunk")).values())
    shipped = {f"seed:{p['id']}" for p in mi.load_seed()}
    prefix = _seed_prefix(project_id)
    missing = []
    for i in items:
        uid = _memory_uid(project_id, i["id"])
        if i["id"].startswith(prefix) and uid not in shipped:
            log.warning("memory migration %s: seed %s is no longer shipped; not migrated",
                        project_id, i["id"])
            continue
        if uid not in memories:
            missing.append(i["id"])
    missing += [c["id"] for c in chunks if mask(c["text"]) not in texts]
    return missing


async def migrate_project(session: AsyncSession, memory: MemoryGraph, project_id: str,
                          conn: psycopg.Connection) -> MigrationReport:
    """One project's revision ``migration:pg-knowledge`` and its verification."""
    items, chunks, backend = await asyncio.to_thread(_read_project, conn, project_id)
    reuse = backend is not None and backend == embeddings.backend_name()
    copied = await _copy_items(session, project_id, items)
    deltas = await _project(session, memory, project_id)
    if reuse:
        _reuse_vectors(deltas, project_id, items)
    orphans = await _orphans(session, deltas, chunks, reuse)
    meta = RevisionMeta(project_id=project_id, phase="migration", ingest_key=INGEST_KEY,
                        actor=ACTOR)
    result = await memory.apply_revision(meta, _unique([*deltas, orphans]))
    missing = await _verify(memory, project_id, items, chunks)
    log.info("memory migration %s: %d items (%d copied to memory_events), %d chunks, %d orphans, "
             "revision %s%s, %d missing", project_id, len(items), copied, len(chunks),
             len(orphans.nodes), result.revision, " (already applied)" if result.skipped else "",
             len(missing))
    return MigrationReport(project_id=project_id, memory_items=len(items), chunks=len(chunks),
                           orphans=len(orphans.nodes), revision=result.revision, missing=missing)


@asynccontextmanager
async def _project_lock(sessionmaker: async_sessionmaker[AsyncSession],
                        project_id: str) -> AsyncIterator[None]:
    """The ingests' per-project lock, waited for (an ingest running when the migration started)."""
    deadline = time.monotonic() + PROJECT_LOCK_WAIT_S
    while True:
        async with try_advisory_lock(sessionmaker, f"memory:{project_id}") as locked:
            if locked:
                yield
                return
        if time.monotonic() > deadline:
            raise RuntimeError(f"memory of project {project_id} stayed locked for "
                               f"{PROJECT_LOCK_WAIT_S} s")
        await asyncio.sleep(PROJECT_LOCK_POLL_S)


async def migrate_all(sessionmaker: async_sessionmaker[AsyncSession], memory: MemoryGraph,
                      dsn: str) -> list[MigrationReport]:
    """Migrate every project of schema ``knowledge``, then retire it; ``[]`` when there is no
    such schema (also when another migration retired it first). Raises :class:`MigrationBusy`
    while another migration runs, and ``RuntimeError`` naming the first incomplete project
    before any rename. Projects already migrated are no-ops, so a re-run resumes."""
    conn = await asyncio.to_thread(_connect, dsn)
    try:
        async with try_advisory_lock(sessionmaker, MIGRATE_LOCK) as locked:
            if not locked:
                raise MigrationBusy("another memory migration is running")
            if not await asyncio.to_thread(_present, conn):  # checked under the lock
                log.info("memory migration: no schema knowledge; nothing to migrate")
                return []
            legacy = await asyncio.to_thread(_projects, conn)
            async with sessionmaker() as s:
                known = set((await s.execute(select(Project.id).where(Project.id.in_(legacy))))
                            .scalars()) if legacy else set()
            for pid in sorted(set(legacy) - known):
                log.warning("memory migration: project %s is in schema knowledge but no longer "
                            "exists; its rows stay in knowledge_retired", pid)
            reports = []
            for pid in sorted(known):
                async with _project_lock(sessionmaker, pid), sessionmaker() as s:
                    reports.append(await migrate_project(s, memory, pid, conn))
            incomplete = next((r for r in reports if r.missing), None)
            if incomplete is not None:
                raise RuntimeError(f"migration incomplete for {incomplete.project_id}: "
                                   f"{incomplete.missing[:10]}")
            await asyncio.to_thread(_retire, conn)
            log.info("memory migration: %d projects verified; schema knowledge renamed "
                     "knowledge_retired", len(reports))
            return reports
    finally:
        await asyncio.to_thread(conn.close)


# ------------------------------------------------------------------ jobs
async def migrate_job(ctx: JobContext, w: WorkerServices) -> dict[str, Any]:
    from api.app.db import psycopg_url

    if w.memory is None:
        raise RuntimeError("memory not configured: NEO4J_URI is empty")
    try:
        reports = await migrate_all(w.sessionmaker, w.memory, psycopg_url(w.settings.database_url))
    except MigrationBusy as exc:  # a second replica's job: back in 5 s, then a no-op
        raise RetryLater(BUSY_RETRY_S, str(exc), count_attempt=False) from exc
    except mi.UNAVAILABLE as exc:
        raise mi.unavailable_retry(ctx.attempt, exc) from exc
    for r in reports:
        await ctx.log(f"project {r.project_id}: {r.memory_items} memory items, {r.chunks} chunks, "
                      f"{r.orphans} orphans, revision {r.revision}")
    return {"projects": [r.model_dump() for r in reports]}


async def _reembed_one(ctx: JobContext, w: WorkerServices, project_id: str) -> int:
    async with try_advisory_lock(w.sessionmaker, f"memory:{project_id}") as locked:
        if not locked:
            raise RetryLater(mi.LOCK_BUSY_DELAY_S, "memory of this project is being written",
                             count_attempt=False)
        n = await w.memory.reembed(project_id)
    await ctx.log(f"re-embedded {n} nodes of project {project_id}")
    return n


async def reembed_job(ctx: JobContext, w: WorkerServices) -> dict[str, Any]:
    """Without ``project_id``: re-embed every project, then record the active backend (only
    after the last project is written, so no project's old vectors pass the backend check).
    With one: refresh that project's vectors under the backend already recorded; refused while
    the graph records another backend (that needs every project re-embedded)."""
    if w.memory is None:
        raise RuntimeError("memory not configured: NEO4J_URI is empty")
    backend = embeddings.backend_name()
    project_id = ctx.payload.get("project_id")
    try:
        if project_id:
            recorded = await w.memory.recorded_backend()
            if recorded is not None and recorded != backend:
                raise KnowledgeError(
                    f"the memory graph records {recorded!r} but the active backend is {backend!r}: "
                    "re-embed every project (memory_admin reembed, no --project)")
            done = {project_id: await _reembed_one(ctx, w, project_id)}
        else:
            async with w.sessionmaker() as s:
                ids = list((await s.execute(select(Project.id).order_by(Project.id))).scalars())
            done = {pid: await _reembed_one(ctx, w, pid) for pid in ids}
            await w.memory.record_backend()
            await ctx.log(f"re-embedded {len(ids)} projects; the graph records {backend}")
    except mi.UNAVAILABLE as exc:
        raise mi.unavailable_retry(ctx.attempt, exc) from exc
    return {"projects": done, "reembedded": sum(done.values()), "backend": backend,
            "recorded": not project_id}
