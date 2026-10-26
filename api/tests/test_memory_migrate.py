"""The one-time migration of the Postgres ``knowledge`` schema into Neo4j (Task 10), the
``memory.migrate`` / ``memory.reembed`` jobs and the ``memory_admin`` re-ingest.

``legacy_project`` is a project of the earlier release: scanned by the upload flow, with real
analysis rows, its Neo4j memory wiped (that release had none) and schema ``knowledge`` loaded from
``fixtures/knowledge_legacy.sql`` (the old DDL plus rows) with one seed row per shipped pattern.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import psycopg
import pytest
from sqlalchemy import func, select, text

from api.app.jobs.runner import enqueue
from api.app.main import create_app
from api.app.memory_admin import reingest
from api.app.models import AnalysisRun, Job, MemoryEvent, Rule
from api.app.services import memory_ingest as mi
from api.app.services.memory_migrate import MIGRATE_KIND, REEMBED_KIND, migrate_all
from api.tests.conftest import TEST_DSN, drop_legacy_knowledge
from api.tests.test_memory_ingest import (
    DFR,
    idle,
    jobs_of_kind,
    processes,
    revision_count,
    scan_sample,
    wait_terminal,
)
from engine.knowledge import embeddings
from engine.knowledge.memory import load_seed_patterns, pattern_text

LEGACY_SQL = Path(__file__).with_name("fixtures") / "knowledge_legacy.sql"
LEGACY_DECISIONS = {"mem_legacy0dec1", "mem_legacy0map1"}
PATTERNS = load_seed_patterns()
LEGACY_MEMORY_ITEMS = len(LEGACY_DECISIONS) + len(PATTERNS)
EXISTING_REVISIONS = 0  # the earlier release had no Neo4j: the migration is revision 1
SUMMARY = "Legacy summary of dealer funding."
RULE = "Funding requests above the dealer cap are held."
STRAY = "A chunk of an analysis nobody derives any more."


# ------------------------------------------------------------------ helpers
def _literal(value: str) -> str:
    return value.replace("'", "''")


def _load_legacy(project_id: str, process_id: str, process_name: str, backend: str,
                 stray: bool) -> None:
    sql = LEGACY_SQL.read_text(encoding="utf-8")
    for token, value in (("@PROJECT@", project_id), ("@PROCESS@", process_id),
                         ("@PROCESS_NAME@", process_name), ("@BACKEND@", backend)):
        sql = sql.replace(token, _literal(value))
    with psycopg.connect(TEST_DSN, autocommit=True) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector SCHEMA public")
        conn.execute(sql)
        for p in PATTERNS:  # the earlier release's MigrationMemory.seed_patterns
            conn.execute(
                "INSERT INTO knowledge.memory_items (id, project_id, kind, text, context, source, "
                "created_by, embedding) VALUES (%s, %s, %s, %s, '{}', 'seed', 'seed', "
                "array_fill(0.01::real, ARRAY[384])::vector)",
                [f"seed:{project_id}:{p['id']}", project_id, p["kind"], pattern_text(p)])
        if stray:
            conn.execute(
                "INSERT INTO knowledge.chunks (id, project_id, process_id, kind, text, embedding) "
                "VALUES ('chunk:stray', %s, %s, 'design', %s, "
                "array_fill(0.06::real, ARRAY[384])::vector)", [project_id, process_id, STRAY])


async def schema_exists(app, name: str) -> bool:
    async with app.state.sessionmaker() as s:
        return bool(await s.scalar(text("SELECT count(*) FROM pg_namespace WHERE nspname = :n"),
                                   {"n": name}))


async def rename_back(app) -> None:
    """The RUNBOOK rollback."""
    async with app.state.sessionmaker() as s:
        await s.execute(text("ALTER SCHEMA knowledge_retired RENAME TO knowledge"))
        await s.commit()


async def run_migration(app):
    return await migrate_all(app.state.sessionmaker, app.state.memory, TEST_DSN)


async def enqueue_ingest_for(app, project_id: str, *, scope: str) -> str:
    job_id = await mi.enqueue_ingest(app.state.sessionmaker, app.state.settings,
                                     project_id=project_id, phase=scope, scope=scope, source={},  # type: ignore[arg-type]
                                     actor="test", ingest_key=f"{scope}:after-migration")
    assert job_id is not None
    return job_id


async def _legacy(memory_client, memory_app, sample_upload, *, stray: bool = False) -> str:
    pid = await scan_sample(memory_client, sample_upload)
    await idle(memory_app, pid)
    proc = next(p for p in await processes(memory_client, pid) if p["ir_id"] == DFR)
    async with memory_app.state.sessionmaker() as s:  # the analysis the old chunks came from
        run = AnalysisRun(process_id=proc["id"], summary={"text": SUMMARY})
        s.add(run)
        await s.flush()
        s.add(Rule(process_id=proc["id"], analysis_id=run.id, rule_key="R1", status="approved",
                   text=RULE))
        await s.commit()
    await memory_app.state.memory.reset()  # the earlier release kept memory in Postgres only
    await asyncio.to_thread(_load_legacy, pid, proc["id"], proc["name"], embeddings.backend_name(),
                            stray)
    return pid


@pytest.fixture
async def legacy_project(memory_client, memory_app, sample_upload):
    try:
        yield await _legacy(memory_client, memory_app, sample_upload)
    finally:
        await drop_legacy_knowledge(memory_app.state.engine)


@pytest.fixture
async def legacy_project_with_stray_chunk(memory_client, memory_app, sample_upload):
    try:
        yield await _legacy(memory_client, memory_app, sample_upload, stray=True)
    finally:
        await drop_legacy_knowledge(memory_app.state.engine)


@pytest.fixture
def drop_one_after_apply(memory_app, monkeypatch):
    """Neo4j loses one migrated decision between the apply and the verification."""
    memory = memory_app.state.memory
    real = memory.current_texts

    async def lossy(project_id: str, label: str) -> dict[str, str]:
        found = await real(project_id, label)
        found.pop("mem_legacy0dec1", None)
        return found

    monkeypatch.setattr(memory, "current_texts", lossy)


class EmbedSpy:
    def __init__(self) -> None:
        self.texts: list[str] = []
        self.legacy = {pattern_text(p) for p in PATTERNS}

    @property
    def memory_texts(self) -> list[str]:
        """Embedded texts of legacy memory items (decisions are recognised by their wording)."""
        return [t for t in self.texts if t in self.legacy or "dealer funding: requests" in t
                or "DealerRepository.FindAsync" in t]


@pytest.fixture
def embed_spy(monkeypatch):
    spy = EmbedSpy()
    real = embeddings.embed

    def spying(texts: list[str]) -> list[list[float]]:
        spy.texts += texts
        return real(texts)

    monkeypatch.setattr(embeddings, "embed", spying)
    return spy


# ------------------------------------------------------------------ tests
async def test_migration_copies_memory_items_and_rederives_chunks(memory_app, legacy_project):
    pid = legacy_project
    reports = await migrate_all(memory_app.state.sessionmaker, memory_app.state.memory, TEST_DSN)
    r = next(x for x in reports if x.project_id == pid)
    assert r.missing == [] and r.revision is not None and r.memory_items == LEGACY_MEMORY_ITEMS
    assert r.chunks == 2 and r.orphans == 0  # both chunk texts re-derived from the analysis rows
    assert (await memory_app.state.memory.counts(pid))["Memory"] == LEGACY_MEMORY_ITEMS
    assert await schema_exists(memory_app, "knowledge_retired") and not await schema_exists(memory_app, "knowledge")

    memory = memory_app.state.memory
    rev = (await memory.revisions(pid))[-1]
    assert (rev.n, rev.phase, rev.actor) == (1, "migration", "migration")
    decision = await memory.node_detail(pid, "mem_legacy0dec1")
    assert decision.node.props["kind"] == "decision" and decision.node.props["created_by"] == "sme.vicky"
    assert decision.node.props["created_at"].startswith("2026-03-02T10:00:00")
    assert decision.node.props["context"] and '"rule_key": "R1"' in decision.node.props["context"]
    mapping = await memory.node_detail(pid, "mem_legacy0map1")
    assert mapping.node.props["kind"] == "mapping" and mapping.node.props["created_by"] == "arch.sam"
    assert await memory.current_node(pid, f"seed:{PATTERNS[0]['id']}") is not None
    async with memory_app.state.sessionmaker() as s:
        events = {e.id: e for e in (await s.execute(select(MemoryEvent).where(
            MemoryEvent.project_id == pid))).scalars()}
        stamp = await s.scalar(text("SELECT value FROM knowledge_retired.meta "
                                    "WHERE key = 'neo4j_migrated_at'"))
    assert set(events) == LEGACY_DECISIONS  # seeds come from the seed projector, not events
    dec = events["mem_legacy0dec1"]
    assert (dec.kind, dec.actor, dec.context["kind"]) == ("decision", "sme.vicky", "decision")
    assert dec.context["about_uid"] == f"process:{DFR}"
    assert events["mem_legacy0map1"].context["kind"] == "mapping"
    assert stamp


async def test_chunk_with_no_source_row_becomes_an_orphan(memory_app, legacy_project_with_stray_chunk):
    r = (await run_migration(memory_app))[0]
    assert r.orphans == 1 and (await memory_app.state.memory.node_detail(r.project_id, "chunk:migrated:chunk:stray")) is not None
    uid = "chunk:migrated:chunk:stray"
    detail = await memory_app.state.memory.node_detail(r.project_id, uid)
    assert detail.node.text == STRAY and detail.node.props["kind"] == "design"
    assert detail.node.props["process_id"] and detail.node.side == "knowledge"
    around = await memory_app.state.memory.subgraph(r.project_id, uid=uid)
    assert ("ABOUT", uid, f"process:{DFR}") in {(e.type, e.src, e.dst) for e in around.edges}
    assert r.missing == []


async def test_migration_is_idempotent(memory_app, legacy_project):
    await run_migration(memory_app); await rename_back(memory_app)             # simulate a second run
    r = (await run_migration(memory_app))[0]
    assert r.missing == [] and await revision_count(memory_app, r.project_id) == 1 + EXISTING_REVISIONS
    async with memory_app.state.sessionmaker() as s:
        n = await s.scalar(select(func.count()).select_from(MemoryEvent)
                           .where(MemoryEvent.project_id == r.project_id))
    assert n == len(LEGACY_DECISIONS) and await schema_exists(memory_app, "knowledge_retired")


async def test_missing_item_leaves_postgres_untouched(memory_app, legacy_project, drop_one_after_apply):
    with pytest.raises(RuntimeError, match="migration incomplete") as caught:
        await migrate_all(memory_app.state.sessionmaker, memory_app.state.memory, TEST_DSN)
    assert await schema_exists(memory_app, "knowledge")
    assert not await schema_exists(memory_app, "knowledge_retired")
    assert str(caught.value) == f"migration incomplete for {legacy_project}: ['mem_legacy0dec1']"


async def test_ingest_waits_for_a_pending_migration(memory_app, legacy_project):
    mig = await enqueue(memory_app.state.sessionmaker, "memory.migrate", {}, dedupe_key="memory.migrate")
    ing = await enqueue_ingest_for(memory_app, legacy_project, scope="seed")
    m, i = await wait_terminal(memory_app, mig), await wait_terminal(memory_app, ing)
    assert m.status == i.status == "done" and i.finished_at >= m.finished_at
    assert (await memory_app.state.memory.revisions(legacy_project))[-1].phase == "migration"
    assert m.result["projects"][0]["project_id"] == legacy_project and i.attempts <= 1


async def test_embeddings_are_reused_when_backend_matches(memory_app, legacy_project, embed_spy):
    await run_migration(memory_app)
    assert embed_spy.memory_texts == []
    assert embed_spy.texts  # the re-derived chunks and the estate were embedded


async def test_embeddings_are_recomputed_when_the_backend_differs(memory_app, legacy_project, embed_spy):
    with psycopg.connect(TEST_DSN, autocommit=True) as conn:
        conn.execute("UPDATE knowledge.meta SET value = 'another-backend' WHERE key = 'embedding_backend'")
    r = (await run_migration(memory_app))[0]
    assert r.missing == [] and len(embed_spy.memory_texts) == LEGACY_MEMORY_ITEMS


async def test_no_legacy_schema_is_nothing_to_migrate(memory_app):
    assert await run_migration(memory_app) == []


async def test_lifespan_queues_the_migration_once(memory_settings):
    """With Neo4j configured and schema ``knowledge`` present, each start queues memory.migrate;
    the dedupe key keeps one queued job."""
    from api.app.db import make_engine, reset_schema

    settings = memory_settings.model_copy(update={"exiqo_role": "api"})  # nothing runs the job
    engine = make_engine(settings.database_url)
    await reset_schema(engine)
    await drop_legacy_knowledge(engine)
    try:
        await asyncio.to_thread(_load_legacy, "p-gone", "proc-gone", "Gone", "hash-384", False)
        for _ in range(2):
            app = create_app(settings)
            async with app.router.lifespan_context(app):
                pass
        async with engine.connect() as conn:
            rows = (await conn.execute(select(Job.status).where(Job.kind == MIGRATE_KIND))).all()
        assert [r.status for r in rows] == ["queued"]
        await drop_legacy_knowledge(engine)
        app = create_app(settings)  # no schema knowledge: nothing more is queued
        async with app.router.lifespan_context(app):
            pass
        async with engine.connect() as conn:
            assert len((await conn.execute(select(Job.id).where(Job.kind == MIGRATE_KIND))).all()) == 1
    finally:
        await drop_legacy_knowledge(engine)
        await engine.dispose()


async def test_reingest_queues_one_ingest_per_scope(memory_app, legacy_project):
    pid = legacy_project
    await run_migration(memory_app)
    before = {j.id for j in await jobs_of_kind(memory_app, pid, mi.INGEST_KIND)}
    ids = await reingest(memory_app.state.sessionmaker, memory_app.state.settings, pid)
    jobs = [j for j in await jobs_of_kind(memory_app, pid, mi.INGEST_KIND) if j.id not in before]
    assert sorted(j.id for j in jobs) == sorted(ids)
    scopes = {j.payload["scope"] for j in jobs}
    procs = await processes_of(memory_app, pid)
    assert {"estate", "seed", "decision:mem_legacy0dec1", "decision:mem_legacy0map1"} <= scopes
    assert {f"analysis:{p}" for p in procs} | {f"design:{p}" for p in procs} <= scopes
    assert all(j.payload["ingest_key"] == f"reingest:{j.payload['ingest_key'].split(':')[1]}:{j.payload['scope']}"
               for j in jobs)
    finished = [await wait_terminal(memory_app, i) for i in ids]
    assert all(j.status == "done" for j in finished), [j.error for j in finished if j.status != "done"]


async def processes_of(app, pid: str) -> list[str]:
    from api.app.models import Process

    async with app.state.sessionmaker() as s:
        return list((await s.execute(select(Process.id).where(Process.project_id == pid))).scalars())


async def test_reembed_job_rebuilds_vectors_and_records_the_backend(memory_app, legacy_project):
    from engine.tests.memory_helpers import query, set_embedding_backend

    pid = legacy_project
    await run_migration(memory_app)
    memory = memory_app.state.memory
    await set_embedding_backend(memory, "another-backend")
    job_id = await enqueue(memory_app.state.sessionmaker, REEMBED_KIND, {"project_id": pid},
                           project_id=pid)
    job = await wait_terminal(memory_app, job_id)
    assert job.status == "done", job.error
    assert job.result["reembedded"] > 0 and job.result["backend"] == embeddings.backend_name()
    rows = await query(memory, "MATCH (m:MemoryMeta {key: 'embedding_backend'}) RETURN m.value AS v")
    assert rows == [{"v": embeddings.backend_name()}]
    hits = await memory.retrieve(pid, "dealer cap review", labels=("Memory",), k=3)
    assert hits  # searchable again with the active backend
