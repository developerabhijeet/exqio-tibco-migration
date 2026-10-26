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
from api.app.models import AnalysisRun, Job, MemoryEvent, Project, Rule
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
PATTERNS = load_seed_patterns()
LEGACY_MEMORY_ITEMS = 2 + len(PATTERNS)  # two SME items and the seeds
EXISTING_REVISIONS = 0  # the earlier release had no Neo4j: the migration is revision 1
SUMMARY = "Legacy summary of dealer funding."
RULE = "Funding requests above the dealer cap are held."
STRAY = "A chunk of an analysis nobody derives any more."


# ------------------------------------------------------------------ helpers
def dec(pid: str) -> str:
    return f"mem_legacy0dec1_{pid}"


def mapping_id(pid: str) -> str:
    return f"mem_legacy0map1_{pid}"


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


def lose_decision(app, monkeypatch, pid: str):
    """Neo4j loses the project's migrated decision between the apply and the verification."""
    memory = app.state.memory
    real = memory.current_texts

    async def lossy(project_id: str, label: str) -> dict[str, str]:
        found = await real(project_id, label)
        found.pop(dec(pid), None)
        return found

    monkeypatch.setattr(memory, "current_texts", lossy)
    return lambda: setattr(memory, "current_texts", real)


@pytest.fixture
def drop_one_after_apply(memory_app, legacy_project, monkeypatch):
    lose_decision(memory_app, monkeypatch, legacy_project)


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
    decision = await memory.node_detail(pid, dec(pid))
    assert decision.node.props["kind"] == "decision" and decision.node.props["created_by"] == "sme.vicky"
    assert decision.node.props["created_at"].startswith("2026-03-02T10:00:00")
    assert decision.node.props["context"] and '"rule_key": "R1"' in decision.node.props["context"]
    mapping = await memory.node_detail(pid, mapping_id(pid))
    assert mapping.node.props["kind"] == "mapping" and mapping.node.props["created_by"] == "arch.sam"
    assert await memory.current_node(pid, f"seed:{PATTERNS[0]['id']}") is not None
    async with memory_app.state.sessionmaker() as s:
        events = {e.id: e for e in (await s.execute(select(MemoryEvent).where(
            MemoryEvent.project_id == pid))).scalars()}
        stamp = await s.scalar(text("SELECT value FROM knowledge_retired.meta "
                                    "WHERE key = 'neo4j_migrated_at'"))
    assert set(events) == {dec(pid), mapping_id(pid)}  # seeds come from the seed projector
    d = events[dec(pid)]
    assert (d.kind, d.actor, d.context["kind"]) == ("decision", "sme.vicky", "decision")
    assert d.context["about_uid"] == f"process:{DFR}"
    assert events[mapping_id(pid)].context["kind"] == "mapping"
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
    assert n == 2 and await schema_exists(memory_app, "knowledge_retired")


async def test_missing_item_leaves_postgres_untouched(memory_app, legacy_project, drop_one_after_apply):
    with pytest.raises(RuntimeError, match="migration incomplete") as caught:
        await migrate_all(memory_app.state.sessionmaker, memory_app.state.memory, TEST_DSN)
    assert await schema_exists(memory_app, "knowledge")
    assert not await schema_exists(memory_app, "knowledge_retired")
    assert str(caught.value) == f"migration incomplete for {legacy_project}: ['{dec(legacy_project)}']"


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
    assert {"estate", "seed", f"decision:{dec(pid)}", f"decision:{mapping_id(pid)}"} <= scopes
    assert {f"analysis:{p}" for p in procs} | {f"design:{p}" for p in procs} <= scopes
    assert all(j.payload["ingest_key"] == f"reingest:{j.payload['ingest_key'].split(':')[1]}:{j.payload['scope']}"
               for j in jobs)
    finished = [await wait_terminal(memory_app, i) for i in ids]
    assert all(j.status == "done" for j in finished), [j.error for j in finished if j.status != "done"]


async def processes_of(app, pid: str) -> list[str]:
    from api.app.models import Process

    async with app.state.sessionmaker() as s:
        return list((await s.execute(select(Process.id).where(Process.project_id == pid))).scalars())


async def _reembed(app, payload: dict) -> Job:
    job_id = await enqueue(app.state.sessionmaker, REEMBED_KIND, payload,
                           project_id=payload.get("project_id"))
    return await wait_terminal(app, job_id)


async def _recorded(memory) -> str | None:
    return await memory.recorded_backend()


async def test_reembed_of_every_project_records_the_backend_after_the_last(memory_app, legacy_project,
                                                                          monkeypatch):
    pid = legacy_project
    await run_migration(memory_app)
    memory = memory_app.state.memory
    async with memory_app.state.sessionmaker() as s:  # a second project, no memory ingest queued
        other = Project(name="second")
        s.add(other)
        await s.commit()
        other_id = other.id
    from engine.tests.memory_helpers import set_embedding_backend

    await set_embedding_backend(memory, "another-backend")
    seen: list[tuple[str, str | None]] = []
    real = memory.reembed

    async def spying(project_id: str) -> int:
        seen.append((project_id, await _recorded(memory)))
        return await real(project_id)

    monkeypatch.setattr(memory, "reembed", spying)
    job = await _reembed(memory_app, {})
    assert job.status == "done", job.error
    assert sorted(p for p, _ in seen) == sorted([pid, other_id])
    assert {recorded for _, recorded in seen} == {"another-backend"}  # unchanged until the last
    assert job.result["recorded"] is True and job.result["projects"][pid] > 0
    assert await _recorded(memory) == embeddings.backend_name()
    assert await memory.retrieve(pid, "dealer cap review", labels=("Memory",), k=3)


async def test_reembed_of_one_project_never_changes_the_recorded_backend(memory_app, legacy_project):
    pid = legacy_project
    await run_migration(memory_app)
    memory = memory_app.state.memory
    job = await _reembed(memory_app, {"project_id": pid})  # same backend: vectors refreshed
    assert job.status == "done", job.error
    assert job.result["recorded"] is False and job.result["reembedded"] > 0
    from engine.tests.memory_helpers import set_embedding_backend

    await set_embedding_backend(memory, "another-backend")
    job = await _reembed(memory_app, {"project_id": pid})  # another backend: every project first
    assert job.status == "failed" and "re-embed every project" in job.error
    assert await _recorded(memory) == "another-backend"


async def test_concurrent_migration_jobs_one_migrates_the_other_is_a_no_op(memory_app, legacy_project):
    """A second replica's job (queued while the first runs) never fails: it waits for the lock
    and then finds the schema retired."""
    sm = memory_app.state.sessionmaker
    first = await enqueue(sm, MIGRATE_KIND, {}, dedupe_key=MIGRATE_KIND)
    async with sm() as s:  # dedupe ignores a running job: the second replica's enqueue
        second = Job(kind=MIGRATE_KIND, status="queued", payload={"dedupe_key": MIGRATE_KIND})
        s.add(second)
        await s.commit()
        second_id = second.id
    memory_app.state.runner.worker.wake()
    jobs = [await wait_terminal(memory_app, first), await wait_terminal(memory_app, second_id)]
    assert [j.status for j in jobs] == ["done", "done"], [j.error for j in jobs]
    assert sorted(len(j.result["projects"]) for j in jobs) == [0, 1]
    assert await revision_count(memory_app, legacy_project) == 1
    assert await schema_exists(memory_app, "knowledge_retired")


async def test_migration_busy_requeues_and_rechecks_the_schema_under_the_lock(memory_app, legacy_project):
    from api.app.jobs.locks import try_advisory_lock
    from api.tests.test_memory_ingest import fast_forward, get_job

    sm = memory_app.state.sessionmaker
    async with try_advisory_lock(sm, "memory:migrate") as locked:  # another migration runs
        assert locked
        job_id = await enqueue(sm, MIGRATE_KIND, {}, dedupe_key=MIGRATE_KIND)
        async with asyncio.timeout(60):
            while (job := await get_job(memory_app, job_id)).not_before is None:
                await asyncio.sleep(0.1)
        assert (job.status, job.attempts) == ("queued", 0)  # the attempt handed back
        async with sm() as s:  # ... and it retires the schema
            await s.execute(text("ALTER SCHEMA knowledge RENAME TO knowledge_retired"))
            await s.commit()
    await fast_forward(memory_app, job_id)
    job = await wait_terminal(memory_app, job_id)
    assert job.status == "done" and job.result == {"projects": []}
    assert await revision_count(memory_app, legacy_project) == 0


async def test_deleted_project_is_skipped_and_the_schema_still_retires(memory_app, legacy_project):
    await asyncio.to_thread(_load_legacy, "p-deleted", "proc-deleted", "Gone",
                            embeddings.backend_name(), False)
    reports = await run_migration(memory_app)
    assert [r.project_id for r in reports] == [legacy_project] and reports[0].missing == []
    assert not await schema_exists(memory_app, "knowledge")
    async with memory_app.state.sessionmaker() as s:  # its rows are kept, renamed with the schema
        kept = await s.scalar(text("SELECT count(*) FROM knowledge_retired.memory_items "
                                   "WHERE project_id = 'p-deleted'"))
    assert kept == LEGACY_MEMORY_ITEMS


async def test_seed_no_longer_shipped_is_not_expected(memory_app, legacy_project):
    pid = legacy_project
    with psycopg.connect(TEST_DSN, autocommit=True) as conn:
        conn.execute("INSERT INTO knowledge.memory_items (id, project_id, kind, text, source, "
                     "created_by, embedding) VALUES (%s, %s, 'seed', 'A retired pattern.', 'seed', "
                     "'seed', array_fill(0.01::real, ARRAY[384])::vector)",
                     [f"seed:{pid}:retired-pattern", pid])
    r = (await run_migration(memory_app))[0]
    assert r.missing == [] and r.memory_items == LEGACY_MEMORY_ITEMS + 1
    assert (await memory_app.state.memory.counts(pid))["Memory"] == LEGACY_MEMORY_ITEMS
    assert await memory_app.state.memory.current_node(pid, "seed:retired-pattern") is None
    assert await schema_exists(memory_app, "knowledge_retired")


async def test_a_rerun_resumes_after_only_the_second_project_was_incomplete(
        memory_client, memory_app, legacy_project, sample_upload, monkeypatch):
    second = await _legacy(memory_client, memory_app, sample_upload)
    first, last = sorted([legacy_project, second])
    restore = lose_decision(memory_app, monkeypatch, last)
    with pytest.raises(RuntimeError, match=f"migration incomplete for {last}: "):
        await run_migration(memory_app)
    assert await schema_exists(memory_app, "knowledge")
    done = (await memory_app.state.memory.revisions(first))[0]
    assert done.phase == "migration"
    restore()  # the miss is fixed (see the report's recovery path); run it again
    reports = {r.project_id: r for r in await run_migration(memory_app)}
    assert reports[first].revision == done.n and reports[last].missing == []
    assert await revision_count(memory_app, first) == 1 and await revision_count(memory_app, last) == 1
    assert await schema_exists(memory_app, "knowledge_retired")


async def test_cli_exit_statuses(memory_app, legacy_project, capsys):
    from api.app.jobs.locks import try_advisory_lock
    from api.app.memory_admin import parser, run

    settings = memory_app.state.settings
    assert await run(parser().parse_args(["reingest", "--project", "nope"]), settings) == 2
    assert "project 'nope' not found" in capsys.readouterr().err
    assert await run(parser().parse_args(["reembed", "--project", "nope"]), settings) == 2
    no_neo4j = settings.model_copy(update={"neo4j_uri": ""})
    assert await run(parser().parse_args(["migrate"]), no_neo4j) == 2
    async with try_advisory_lock(memory_app.state.sessionmaker, f"memory:{legacy_project}") as locked:
        assert locked  # the project's memory stays busy: the re-embed cannot finish
        args = parser().parse_args(["reembed", "--project", legacy_project, "--wait",
                                    "--timeout", "1"])
        assert await run(args, settings) == 3
    assert "still running after 1 s" in capsys.readouterr().err
