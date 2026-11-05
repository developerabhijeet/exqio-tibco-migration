# Neo4j Memory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Neo4j the platform's memory: old code, new code, lineage and knowledge as a versioned graph per project, written by `memory.ingest` jobs after every phase, read by agents and Ask through hybrid search, and browsed and curated on a new `/memory` screen.

**Architecture:** Pure projectors in `engine/memory/projectors/` turn each phase's Postgres output into a `ScopedDelta`; `MemoryGraph.apply_revision` (async neo4j driver, one write transaction per revision, constant Cypher only) compares content hashes per scope and opens or closes node and relationship versions under a new `Revision`. A follow-up `memory.ingest` job is queued by the job worker when a phase job finishes, and by the services for gate decisions, SME decisions, seeds and curations (the last two kept in Postgres `memory_events`). Search keeps today's hybrid semantics on Neo4j's full-text and vector functions; the explorer reads `/memory/*` and renders with the already-bundled cytoscape and elkjs.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy async, Alembic, neo4j Python driver 5.x (async), Neo4j 5.26.32 Community, fastembed bge-small (hash fallback), Next.js 15, React 19, cytoscape 3.34.3, elkjs 0.10.0, TanStack Query, Playwright, Helm 3 with helm-unittest.

**Spec:** `docs/superpowers/specs/2026-10-25-neo4j-memory-design.md`

## Global Constraints

- Branch: work and commit on `feat/gmf-deploy` only; never create a branch; never push.
- No outbound hosts: no new external URL in code or bundle; the graph renderer is cytoscape/elkjs from npm (already dependencies); `make egress-audit` must pass.
- Parameterized Cypher only: every Cypher string is a module constant in `engine/memory/cypher.py`, rendered at import from the closed `NodeLabel`/`RelType` sets; request values are always parameters; no f-string or `.format` on Cypher anywhere else.
- `mask_sensitive` on every stored string except identity keys (`uid`, `vid`, `project_id`, `scope`, `content_hash`, `text_hash`, `stack`, `side`, `kind`), in `MemoryGraph.apply_revision`; curation and decision text masked again before Postgres. `guard_text` drops raw TIBCO XML (counted as `refused`).
- Every Cypher statement constrains `project_id: $project_id`; every `/memory` route 404s on a project unknown to Postgres.
- Viewer read-only server-side: curation needs a role in `WRITE_ROLES` (`require_curator`, both auth modes); re-ingest needs `admin`.
- Embeddings stay local: `engine/knowledge/embeddings.py` unchanged (384 dims; `FASTEMBED_VECTOR_FLOOR = 0.66`, `HASH_VECTOR_FLOOR = 0.25` on raw cosine); Neo4j scores `(1+cos)/2` are converted with `2s − 1`.
- Search constants unchanged: `KEYWORD_WEIGHT = 0.5`, `VECTOR_WEIGHT = 0.5`, `CANDIDATES_PER_HALF = 50`, `min_terms = 2`.
- Ingest constants: handler `memory.ingest`; `MEMORY_INGEST_MAX_ATTEMPTS = 10`; backoff `min(600, 15 * 2 ** (attempt - 1))` s; lock busy → requeue after 2 s, attempt not counted; UNWIND batches of 1000 rows; embedding batches of 256; `MEMORY_EXACT_SCAN_MAX = 50000`; ANN candidates `100 * k`.
- Readiness: Neo4j gates `/readyz` when `NEO4J_URI` is set and `NEO4J_GATES_READINESS` is true (default), with `NEO4J_READY_GRACE_SECONDS = 900`.
- Request limits: `q` ≤ 500 chars, `k` ≤ 50, `depth` 1–2, graph `limit` ≤ 400, revisions `limit` ≤ 200, `uid` ≤ 512 chars, curation `reason` 1–2000 chars.
- Test Neo4j: `NEO4J_TEST_URI` (default in `make test`: `bolt://localhost:7688`, user `neo4j`, password `exiqo-test-neo4j`); fixtures skip with reason `NEO4J_TEST_URI is not set: run make test (starts neo4j-test) or point it at a Neo4j 5.26` only when it is unset.
- The existing suite (`.venv/bin/pytest`, 1144 collected today) stays green apart from tests this plan rewrites; `.venv/bin/ruff check .` clean; `cd web && npm run typecheck && npm run lint` clean.
- API tests that do not need memory keep `neo4j_uri=""`, so ingest is not enqueued and behaviour is unchanged.

## Review Focus

- A phase finishes while Neo4j is stopped (nightly dump): the phase job ends `done`, the ingest job is requeued with `not_before` in the future, and after Neo4j returns one retry applies the revision; nothing is duplicated. Test in Task 8.
- Two ingests of the same project claimed by two workers at once: revisions get consecutive numbers with no interleaving and no lost update. Test in Task 8.
- A rescan in which one process vanished and another changed: the vanished process, its activities and all their relationships (including other scopes' `ABOUT` and `MIGRATED_TO`) are closed at the new revision; unchanged nodes keep their version. Test in Task 2.
- A search string with Lucene syntax or Cypher (`foo" OR 1=1 //`, `name:*`, `) MATCH (n) DETACH DELETE n`) returns normally (empty or relevant hits) and deletes nothing. Test in Task 4.
- A first ingest of a large estate (20 000 nodes): completes inside one transaction within the 300 s write timeout on the dev Neo4j. Test (marked `slow`, run in `make test`) in Task 2.

## File Structure

| Path | Responsibility |
| --- | --- |
| `engine/memory/model.py` (create) | labels, rel types, `MemNode`, `MemRel`, `ScopedDelta`, `RevisionMeta`, `ApplyResult`, read models, `Citation`, `MemoryHit`, hashing, uid helpers, masking |
| `engine/memory/diff.py` (create) | classify opened and closed versions into added, changed, removed (re-attachments dropped) |
| `engine/memory/cypher.py` (create) | every Cypher statement, as constants rendered from the closed label and type sets |
| `engine/memory/graph.py` (create) | `MemoryGraph`: driver, schema, reset, `apply_revision`, reads |
| `engine/memory/search.py` (create) | hybrid search on Neo4j, `query_terms`, `MemoryRetriever` |
| `engine/memory/projectors/{estate,analysis,design,build,verify,deploy,gate,events,seed}.py` (create) | pure phase-output → `ScopedDelta` functions |
| `engine/knowledge/{store.py,schema.sql,search.py}` (delete) | replaced |
| `engine/knowledge/ask.py`, `memory.py` (modify) | Ask over a retriever; `MemoryItem` and seed loading stay |
| `api/app/models/memory.py`, `api/alembic/versions/0007_memory_events_job_retry.py` (create) | `memory_events`; `jobs.not_before` |
| `api/app/jobs/{registry,runner}.py` (modify) | `RetryLater`, per-kind `max_attempts`, `not_before`, `enqueue` with dedupe, follow-up ingest hook |
| `api/app/services/memory_ingest.py` (create) | loaders from Postgres, `ingest`, `followup_payload`, `enqueue_ingest` |
| `api/app/services/memory_migrate.py`, `api/app/memory_admin.py` (create) | one-time migration; CLI `migrate`, `reembed`, `reingest` |
| `api/app/services/knowledge.py` (rewrite) | `Knowledge` facade over `MemoryGraph` and `memory_events` |
| `api/app/routers/memory.py`, `api/app/schemas/memory.py` (create) | `/memory/*` |
| `api/app/routers/system.py` (modify) | Neo4j gates readiness with grace |
| `web/app/memory/page.tsx`, `web/components/memory/*`, `web/lib/memory.ts` (create) | the explorer |
| `web/components/shell/TopBar.tsx`, `web/lib/personas.ts`, `api/app/auth/roles.py` (modify) | Memory link; `viewer.val` persona |
| `docker-compose.yml`, `Makefile`, `.env.example`, `web/playwright.config.ts` (modify) | Neo4j dev and test services and targets |
| `deploy/helm/exiqo/**`, `deploy/docker/compose.pilot.yml`, `docs/deploy/*.md` (modify) | settings, schema rules, backup, docs |
| `scripts/demo_check.py` (modify) | `memory` step |

---

### Task 1: Neo4j for development and tests, `MemoryGraph` schema

**Files:**
- Create: `engine/memory/__init__.py`, `engine/memory/model.py` (constants), `engine/memory/cypher.py`, `engine/memory/graph.py`, `engine/tests/test_memory_schema.py`
- Modify: `docker-compose.yml`, `Makefile`, `.env.example`, `api/app/config.py`, `engine/tests/conftest.py`, `api/tests/conftest.py`, `README.md` (environment table)

**Interfaces:**
- Produces `engine/memory/model.py` with the constants only (Task 2 adds the models): `NODE_LABELS` (the 24 labels listed in Task 2), `REL_TYPES` (the 12 types listed in Task 2), `EMBEDDED_LABELS`.
- Produces settings: `neo4j_database: str = "neo4j"`, `neo4j_ready_grace_seconds: int = 900`, `neo4j_gates_readiness: bool = True`, `memory_ingest_max_attempts: int = 10`, `memory_exact_scan_max: int = 50000` (existing `neo4j_uri`, `neo4j_user`, `neo4j_password` kept).
- Produces `class MemoryUnavailable(Exception)` and `class MemoryGraph` in `engine/memory/graph.py`:
  - `MemoryGraph.connect(uri: str, user: str, password: str, database: str = "neo4j", *, exact_scan_max: int = 50000) -> MemoryGraph` (driver with `connection_timeout=5`), `uri: str` attribute.
  - `async close() -> None`, `async ping() -> None` (raises `MemoryUnavailable(f"{type(exc).__name__}")`), `async ensure_schema() -> None`, `async reset() -> None` (raises `ValueError("refusing to reset a Neo4j that is not NEO4J_TEST_URI")` unless `self.uri == os.environ.get("NEO4J_TEST_URI")`; deletes all nodes, keeps schema).
- Produces in `cypher.py`: `SCHEMA: tuple[str, ...]` — constraints `mem_vid` (`Mem.vid`), `rev_rid`, `rev_ikey`, `meta_key` (`MemoryMeta.key`); range indexes `mem_uid (project_id, uid)`, `mem_scope (project_id, scope, valid_to)`, `mem_from (project_id, valid_from)`, `mem_to (project_id, valid_to)`, `mem_target (project_id, target_uid)`, `rev_n (project_id, n)`; per rel type `rel_<type>_from`, `rel_<type>_to`, `rel_<type>_uid`; vector indexes `vec_chunk`, `vec_memory`, `vec_process`, `vec_activity`, `vec_class`, `vec_method` (384, cosine); full-text `mem_text` over all 24 labels `ON EACH [n.text]` with `fulltext.analyzer: 'english'`. All `IF NOT EXISTS`.
- Produces fixtures: `engine/tests/conftest.py::memory_graph` (async, function scope: connect to `NEO4J_TEST_URI`, `ensure_schema`, `reset`, yield, close; skip with the Global Constraints reason) and `api/tests/conftest.py::memory_settings` (the `settings` fixture with `neo4j_uri`/`neo4j_user`/`neo4j_password` from `NEO4J_TEST_*`, same skip) plus `memory_app`, `memory_client` built like `app`/`client`.
- Compose: `neo4j` image `neo4j:5.26.32`, env `NEO4J_server_memory_heap_max__size: 1G`, `NEO4J_dbms_usage__report_enabled: "false"`; new `neo4j-test` (profile `test`, `7688:7687`, `tmpfs: /data`, `NEO4J_AUTH: neo4j/exiqo-test-neo4j`, same healthcheck).
- Makefile: `neo4j` (`docker compose up -d --wait neo4j`, port probe like `db`), `neo4j-test` (`docker compose --profile test up -d --wait neo4j-test`); `test: neo4j-test` running `NEO4J_TEST_URI=$${NEO4J_TEST_URI:-bolt://localhost:7688} NEO4J_TEST_PASSWORD=$${NEO4J_TEST_PASSWORD:-exiqo-test-neo4j} $(PYTEST)`; `e2e` and `demo-check` depend on `neo4j`. Without Docker each prints `neo4j: docker not available: set NEO4J_URI (or NEO4J_TEST_URI) to a Neo4j 5.26` and continues.

- [ ] **Step 1: Write the failing tests** in `engine/tests/test_memory_schema.py`

```python
async def test_ensure_schema_is_idempotent(memory_graph):
    await memory_graph.ensure_schema(); await memory_graph.ensure_schema()
    names = await index_names(memory_graph)          # helper: SHOW INDEXES YIELD name
    assert {"mem_uid", "mem_scope", "rev_n", "mem_text", "vec_chunk", "vec_method"} <= names
    assert {"mem_vid", "rev_rid", "rev_ikey", "meta_key"} <= await constraint_names(memory_graph)

async def test_reset_refuses_a_non_test_uri(memory_graph, monkeypatch):
    monkeypatch.setenv("NEO4J_TEST_URI", "bolt://elsewhere:7687")
    with pytest.raises(ValueError, match="not NEO4J_TEST_URI"):
        await memory_graph.reset()

async def test_ping_on_closed_port_raises_memory_unavailable():
    g = MemoryGraph.connect("bolt://127.0.0.1:1", "neo4j", "x")
    with pytest.raises(MemoryUnavailable, match="ServiceUnavailable"):
        await g.ping()
    await g.close()

def test_cypher_module_holds_only_constant_strings():
    tree = ast.parse(Path(cypher.__file__).read_text())
    assert not [n for n in ast.walk(tree) if isinstance(n, ast.JoinedStr) and not _in_render_fn(n)]
    assert all(isinstance(s, str) for s in cypher.SCHEMA)
```

`_in_render_fn` allows f-strings only inside the module's private `_render_*` functions, which take a label or type from `NODE_LABELS`/`REL_TYPES` and run at import.

- [ ] **Step 2: Run** `make neo4j-test && NEO4J_TEST_URI=bolt://localhost:7688 NEO4J_TEST_PASSWORD=exiqo-test-neo4j .venv/bin/pytest engine/tests/test_memory_schema.py -v` — expect FAIL (`ModuleNotFoundError: engine.memory`).
- [ ] **Step 3: Implement** the settings, `cypher.SCHEMA`, `MemoryGraph.connect/close/ping/ensure_schema/reset`, the fixtures, compose services and Make targets; document the new variables in `.env.example` (`NEO4J_URI=bolt://localhost:7687`, `NEO4J_PASSWORD=exiqo-dev-neo4j`) and the README table.
- [ ] **Step 4: Run** the same command — expect 4 passed; run `NEO4J_TEST_URI= .venv/bin/pytest engine/tests/test_memory_schema.py -v` — expect the three Neo4j tests SKIPPED with the reason text, the constant test PASS.
- [ ] **Step 5: Commit** `feat(memory): Neo4j dev and test services, MemoryGraph schema`.

---

### Task 2: Versioned write path (`apply_revision`)

**Files:**
- Create: `engine/memory/diff.py`, `engine/tests/test_memory_model.py`, `engine/tests/test_memory_apply.py`
- Modify: `engine/memory/model.py`, `engine/memory/cypher.py`, `engine/memory/graph.py`

**Interfaces:**
- Produces in `model.py`:
  - `NODE_LABELS: tuple[str, ...]` = the 24 labels of spec section 1 (`Application`, `Process`, `Activity`, `Contract`, `SharedResource`, `SubstVar`, `Table`, `Queue`, `Topic`, `Endpoint`, `Domain`, `Solution`, `CodeProject`, `CodeFile`, `Class`, `Method`, `ConfigKey`, `MessagingEntity`, `Test`, `Chunk`, `Memory`, `GateDecision`, `Error`, `Curation`); `NodeLabel = Literal[...]` of the same.
  - `REL_TYPES` = `CONTAINS, CALLS, READS, WRITES, PUBLISHES, CONSUMES, EXPOSES, USES, MIGRATED_TO, TESTS, FAILED_AT, ABOUT`; `RelType`.
  - `EMBEDDED_LABELS = ("Chunk", "Memory", "Process", "Activity", "Class", "Method")`, `KNOWLEDGE_LABELS = ("Chunk", "Memory")`, `OLD_LABELS`, `NEW_LABELS`, `KNOWLEDGE_GROUP`; `side_of(label: str, props: dict) -> Literal["old", "new", "knowledge"]` (`Endpoint` reads `props["side"]`).
  - `IDENTITY_KEYS = frozenset({"uid", "vid", "project_id", "scope", "content_hash", "text_hash", "stack", "side", "kind"})`.
  - `Phase = Literal["discover", "analysis", "design", "build", "verify", "deploy", "gate", "decision", "seed", "curation", "migration", "reingest"]`.
  - `MemNode(BaseModel)`: `label: NodeLabel`, `uid: str`, `name: str`, `text: str = ""`, `props: dict[str, Any] = {}`, `embedding: list[float] | None = None` (preset vector, used by the migration).
  - `MemRel(BaseModel)`: `type: RelType`, `src: str`, `dst: str`, `props: dict[str, Any] = {}`; property `uid -> f"{type}:{src}->{dst}"`.
  - `ScopedDelta(BaseModel)`: `scope: str`, `nodes: list[MemNode] = []`, `rels: list[MemRel] = []`.
  - `RevisionMeta(BaseModel)`: `project_id: str`, `phase: Phase`, `ingest_key: str`, `actor: str`, `source_job_id: str | None = None`, `ingest_job_id: str | None = None`, `iteration: int = 1`.
  - `ApplyResult(BaseModel)`: `revision: int | None`, `skipped: bool = False`, `added: int = 0`, `changed: int = 0`, `removed: int = 0`, `reattached: int = 0`, `embedded: int = 0`, `dangling: int = 0`, `refused: int = 0`.
  - `node_hash(n: MemNode) -> str` (sha256 of canonical JSON of label, name, text, props sorted; `embedding` excluded), `text_hash(text: str) -> str`, `rel_hash(r: MemRel) -> str`, `mask_node(n: MemNode) -> tuple[MemNode, bool]` (masked copy; `True` when `guard_text` refused its text, which is then `""`), `mask_rel(r: MemRel) -> MemRel`.
- Produces in `diff.py`: `VersionRef(BaseModel)`: `uid: str`, `kind: str` (label or type), `name: str`, `content_hash: str`, `props: dict[str, Any]`; `DiffSet(BaseModel)`: `added: list[VersionRef]`, `changed: list[ChangedRef]` (`ChangedRef(VersionRef)` + `changed_keys: list[str]`), `removed: list[VersionRef]`; `classify(opened: list[VersionRef], closed: list[VersionRef]) -> DiffSet`.
- Produces `MemoryGraph.apply_revision(meta: RevisionMeta, deltas: list[ScopedDelta]) -> ApplyResult`, algorithm in order:
  1. mask every node and rel; reject a delta whose node labels or rel types are not in the closed sets (`ValueError`);
  2. read (read transaction) the current `uid → (content_hash, text_hash, vid)` for each delta's scope, and the active embedding backend from `MemoryMeta` (refuse a mismatch with `KnowledgeError`, recording it when absent);
  3. embed (batches of 256) only new or changed nodes in `EMBEDDED_LABELS` whose `text_hash` differs and that carry no preset `embedding`;
  4. one `execute_write` transaction (every new version also gets `phase = meta.phase`, which like `vid` is outside the hash): if `Revision {ikey: project_id|ingest_key}` exists → `ApplyResult(revision=n, skipped=True)`; else `n = max(Revision.n) + 1`; close changed and vanished node versions; create new versions (`vid = f"{project_id}|{uid}|{n}"`, labels `:Mem:<Label>`); move every current relationship of a re-versioned node onto the new version (close at n, create with same uid and hash; count `reattached`); close relationships of closed nodes; then per rel type create new or changed in-scope rels whose endpoints are current (else `dangling += 1`) and close vanished in-scope rels; if nothing was added, changed or removed, abort the transaction and return `ApplyResult(revision=None)`; else create `Revision {rid, ikey, project_id, n, phase, scopes, job_id, ingest_job_id, iteration, at, actor, added, changed, removed}`.
  All row writes are `UNWIND $rows` constants per label or type, batched by 1000.

- [ ] **Step 1: Write failing unit tests** (`test_memory_model.py`, no Neo4j)

```python
def test_node_hash_ignores_prop_order_and_embedding():
    a = MemNode(label="Process", uid="process:p1", name="Get", props={"x": 1, "y": 2})
    b = MemNode(label="Process", uid="process:p1", name="Get", props={"y": 2, "x": 1}, embedding=[0.1] * 384)
    assert node_hash(a) == node_hash(b)

def test_mask_node_masks_text_and_props_but_not_identity():
    n, refused = mask_node(MemNode(label="Chunk", uid="chunk:p1:summary", name="s",
                                   text="customer SSN 123-45-6789", props={"kind": "summary", "note": "123-45-6789"}))
    assert "123-45-6789" not in n.text and "123-45-6789" not in n.props["note"]
    assert n.uid == "chunk:p1:summary" and n.props["kind"] == "summary" and refused is False

def test_mask_node_refuses_raw_tibco_xml():
    n, refused = mask_node(MemNode(label="Chunk", uid="c", name="c", text="<pd:activity name='x'/>"))
    assert refused is True and n.text == ""

def test_classify_drops_reattachments_and_reports_changed_keys():
    o = [VersionRef(uid="r1", kind="CALLS", name="", content_hash="h1", props={}),
         VersionRef(uid="n1", kind="Process", name="A", content_hash="h3", props={"band": "high"})]
    c = [VersionRef(uid="r1", kind="CALLS", name="", content_hash="h1", props={}),
         VersionRef(uid="n1", kind="Process", name="A", content_hash="h2", props={"band": "low"}),
         VersionRef(uid="n2", kind="Process", name="B", content_hash="h9", props={})]
    d = classify(o, c)
    assert [x.uid for x in d.added] == [] and [x.uid for x in d.removed] == ["n2"]
    assert [(x.uid, x.changed_keys) for x in d.changed] == [("n1", ["band"])]
```

- [ ] **Step 2: Write failing integration tests** (`test_memory_apply.py`, `memory_graph` fixture; helper `estate(n_processes, changed=set(), vanished=set())` builds a delta of Process→Activity with CONTAINS)

```python
async def test_first_apply_creates_revision_one(memory_graph):
    r = await memory_graph.apply_revision(meta("k1"), [estate(2)])
    assert (r.revision, r.added, r.changed, r.removed) == (1, 2 + 2 * 3 + 2 * 3, 0, 0)  # nodes + rels

async def test_unchanged_reapply_creates_nothing(memory_graph):
    await memory_graph.apply_revision(meta("k1"), [estate(2)])
    r = await memory_graph.apply_revision(meta("k2"), [estate(2)])
    assert r.revision is None and await revision_count(memory_graph, P) == 1

async def test_same_ingest_key_is_skipped(memory_graph):
    await memory_graph.apply_revision(meta("k1"), [estate(2)])
    r = await memory_graph.apply_revision(meta("k1"), [estate(2, changed={"p0"})])
    assert r.skipped is True and r.revision == 1

async def test_change_closes_old_version_and_reattaches_other_scopes(memory_graph):
    await memory_graph.apply_revision(meta("k1"), [estate(2)])
    await memory_graph.apply_revision(meta("k2", phase="analysis"), [about_chunk("p0")])   # Chunk ABOUT process:p0
    r = await memory_graph.apply_revision(meta("k3"), [estate(2, changed={"p0"})])
    assert r.revision == 3 and r.changed == 1 and r.reattached >= 4                    # 3 CONTAINS + 1 ABOUT
    versions = await node_versions(memory_graph, P, "process:p0")
    assert [(v["valid_from"], v["valid_to"]) for v in versions] == [(1, 3), (3, None)]
    assert await current_rel_count(memory_graph, P, "ABOUT", dst="process:p0") == 1

async def test_vanished_node_closes_its_relationships_from_every_scope(memory_graph):
    await memory_graph.apply_revision(meta("k1"), [estate(2)])
    await memory_graph.apply_revision(meta("k2", phase="analysis"), [about_chunk("p1")])
    r = await memory_graph.apply_revision(meta("k3"), [estate(2, vanished={"p1"})])
    assert r.removed >= 1 + 3 + 3 and await current_rel_count(memory_graph, P, "ABOUT", dst="process:p1") == 0

async def test_rel_to_missing_uid_is_counted_dangling(memory_graph):
    r = await memory_graph.apply_revision(meta("k1", phase="analysis"), [about_chunk("nope")])
    assert r.dangling == 1 and r.added == 1

async def test_embedding_recomputed_only_when_text_changes(memory_graph, embed_spy):
    await memory_graph.apply_revision(meta("k1"), [estate(1)])
    embed_spy.reset()
    await memory_graph.apply_revision(meta("k2"), [estate(1, changed={"p0"}, text_same=True)])
    assert embed_spy.texts == []

async def test_masked_text_is_what_neo4j_holds(memory_graph):
    await memory_graph.apply_revision(meta("k1", phase="analysis"), [chunk_with("SSN 123-45-6789")])
    assert "123-45-6789" not in await stored_text(memory_graph, P, "chunk:p0:summary")

async def test_projects_are_isolated(memory_graph):
    await memory_graph.apply_revision(meta("k1"), [estate(2)])
    await memory_graph.apply_revision(meta("k1", project="other"), [estate(1)])
    assert (await memory_graph.counts(P))["Process"] == 2 and (await memory_graph.counts("other"))["Process"] == 1

@pytest.mark.slow
async def test_large_first_ingest_fits_one_transaction(memory_graph):   # Review Focus 5
    r = await asyncio.wait_for(memory_graph.apply_revision(meta("k1"), [estate(700)]), 300)  # ~21 000 nodes
    assert r.revision == 1
```

Also produce `MemoryGraph.counts(project_id: str) -> dict[str, int]` (current nodes per label).

- [ ] **Step 3: Run** `make test PYTEST='.venv/bin/pytest engine/tests/test_memory_model.py engine/tests/test_memory_apply.py -v'` — expect FAIL (names undefined).
- [ ] **Step 4: Implement** `model.py`, `diff.py`, the write constants in `cypher.py` (`CURRENT_IN_SCOPE`, `REVISION_BY_IKEY`, `NEXT_REVISION`, `CLOSE_NODES`, `CREATE_NODES[label]`, `REATTACH[type]`, `CLOSE_RELS_OF_CLOSED[type]`, `CREATE_RELS[type]`, `CLOSE_RELS[type]`, `CREATE_REVISION`, `COUNTS`, `META_GET`, `META_SET`) and `apply_revision`. Register the `slow` marker in `pyproject.toml`.
- [ ] **Step 5: Run** step 3's command — expect all PASS (slow included).
- [ ] **Step 6: Commit** `feat(memory): versioned revisions with per-scope diffing`.

---

### Task 3: Reads: as-of subgraph, node detail, revisions, diff, lineage

**Files:**
- Modify: `engine/memory/model.py`, `engine/memory/cypher.py`, `engine/memory/graph.py`
- Test: `engine/tests/test_memory_reads.py`

**Interfaces:**
- Produces read models in `model.py`: `NodeOut(uid, label, name, side, props, valid_from, valid_to, curated: bool, obsolete: bool)`; `EdgeOut(uid, type, src, dst, props, status: Literal["generated", "corrected", "rejected"] | None)`; `Subgraph(nodes: list[NodeOut], edges: list[EdgeOut], truncated: bool, total: int)`; `VersionOut(valid_from, valid_to, phase, actor, at, props, changed_keys: list[str])`; `LineageOut(edge: EdgeOut, other: NodeOut, direction: Literal["in", "out"])`; `NodeDetail(node: NodeOut, versions: list[VersionOut], about: list[NodeOut], lineage: list[LineageOut], curations: list[NodeOut])`; `RevisionOut(n, phase, scopes: list[str], actor, at, job_id, iteration, added, changed, removed)`; `RevisionDiff(revision: RevisionOut, nodes: DiffSet, relationships: DiffSet)`.
- Produces on `MemoryGraph`:
  - `subgraph(project_id, *, uid: str | None = None, depth: int = 1, labels: Sequence[str] | None = None, side: str | None = None, as_of: int | None = None, limit: int = 400) -> Subgraph` (no uid: Domain, Application, Solution nodes; `total` counts before the limit; `truncated = total > limit`);
  - `lineage_chain(project_id, uid, as_of=None) -> Subgraph` (MIGRATED_TO, TESTS, FAILED_AT, CONTAINS-down-from-Process paths, both directions, depth ≤ 4);
  - `node_detail(project_id, uid, as_of=None) -> NodeDetail | None`;
  - `revisions(project_id, *, before: int | None = None, limit: int = 50) -> list[RevisionOut]` (newest first), `latest_revision(project_id) -> RevisionOut | None`;
  - `diff(project_id, n) -> RevisionDiff | None` (via `classify`);
  - `current_node(project_id, uid) -> NodeOut | None`.
- Validity predicate (nodes and rels) as of k: `valid_from <= $k AND (valid_to IS NULL OR valid_to > $k)`; `as_of=None` means `valid_to IS NULL`.
- Read-time curation rules: an `EdgeOut` of type MIGRATED_TO whose uid is the `target_uid` of a current `Curation {action: "reject_mapping" | "correct_mapping"}` has `status="rejected"`; its own `props.status` otherwise. A `Memory` targeted by a current `Curation {action: "obsolete"}` has `obsolete=True`. `curated=True` for any node that is ABOUT-target of a current Curation.

- [ ] **Step 1: Write failing tests** (`memory_graph`; helpers from Task 2 moved to `engine/tests/memory_helpers.py`)

```python
async def test_as_of_returns_the_graph_of_that_revision(memory_graph):
    await apply(memory_graph, "k1", estate(2)); await apply(memory_graph, "k2", estate(2, vanished={"p1"}))
    assert names(await memory_graph.subgraph(P, uid="process:p1", as_of=1)) >= {"p1"}
    assert await memory_graph.subgraph(P, uid="process:p1") == Subgraph(nodes=[], edges=[], truncated=False, total=0)

async def test_node_detail_lists_versions_with_changed_keys(memory_graph):
    await apply(memory_graph, "k1", estate(1)); await apply(memory_graph, "k2", estate(1, changed={"p0"}))
    d = await memory_graph.node_detail(P, "process:p0")
    assert [v.valid_from for v in d.versions] == [2, 1] and d.versions[0].changed_keys == ["complexity_band"]

async def test_diff_reports_added_changed_removed_without_reattachments(memory_graph):
    await apply(memory_graph, "k1", estate(3))
    await apply(memory_graph, "k2", estate(3, changed={"p0"}, vanished={"p2"}, added={"p9"}))
    d = await memory_graph.diff(P, 2)
    assert [x.uid for x in d.nodes.changed] == ["process:p0"]
    assert "process:p2" in [x.uid for x in d.nodes.removed] and "process:p9" in [x.uid for x in d.nodes.added]
    assert not [r for r in d.relationships.changed if r.uid.startswith("CONTAINS:process:p0")]   # re-attached only

async def test_subgraph_truncates_at_limit(memory_graph):
    await apply(memory_graph, "k1", estate(1, activities=50))
    g = await memory_graph.subgraph(P, uid="process:p0", limit=10)
    assert len(g.nodes) == 10 and g.truncated is True and g.total == 51

async def test_rejected_mapping_reads_as_rejected(memory_graph):
    await apply(memory_graph, "k1", estate(1)); await apply(memory_graph, "k2", build_delta("p0"))
    await apply(memory_graph, "k3", curation_delta("reject_mapping", target="MIGRATED_TO:activity:p0/a0->method:dotnet:p0/Api/Flow.A0"))
    lin = (await memory_graph.node_detail(P, "activity:p0/a0")).lineage
    assert [l.edge.status for l in lin if l.edge.type == "MIGRATED_TO"] == ["rejected"]

async def test_reads_never_cross_projects(memory_graph):
    await apply(memory_graph, "k1", estate(1)); await apply(memory_graph, "k1", estate(1), project="other")
    assert await memory_graph.node_detail("third", "process:p0") is None
```

- [ ] **Step 2: Run** `make test PYTEST='.venv/bin/pytest engine/tests/test_memory_reads.py -v'` — expect FAIL.
- [ ] **Step 3: Implement** the read constants (one per rel type where the type must be in the statement, combined with `UNION ALL` constants rendered at import) and the methods.
- [ ] **Step 4: Run** — expect PASS.
- [ ] **Step 5: Commit** `feat(memory): as-of reads, node history, revision diff, lineage`.

---

### Task 4: Hybrid search and the retrieval interface

**Files:**
- Create: `engine/memory/search.py`, `engine/tests/test_memory_search.py`
- Modify: `engine/memory/model.py`, `engine/memory/cypher.py`, `engine/memory/graph.py`

**Interfaces:**
- Produces in `model.py`: `Citation(BaseModel)`: `uid: str`, `label: str`, `phase: str`, `revision: int`, `artifact_type: Literal["process", "analysis", "design", "build", "build_file", "verify_run", "plan", "gate", "memory", "curation"]`, `artifact_id: str`, `process_id: str | None = None`, `path: str | None = None`; `MemoryHit(BaseModel)`: `uid`, `label`, `kind: str | None`, `text`, `score`, `keyword_score`, `vector_score`, `citation: Citation`.
- Projectors (Tasks 5–6) set node props `artifact_type`, `artifact_id`, `process_id` (API process id), `path`; `Citation.phase` is the version's `phase` property and `Citation.revision` its `valid_from` (Task 2).
- Produces in `search.py`: `query_terms(query: str) -> list[str]` (today's function moved from `engine/knowledge/search.py`, also dropping `LUCENE_STOPWORDS = {"no", "not", "they"}` plus today's `STOPWORDS`; output only `[a-z0-9_]+`); `async hybrid(graph: MemoryGraph, project_id: str, query: str, *, labels: Sequence[str] = KNOWLEDGE_LABELS, side: str | None = None, phase: str | None = None, k: int = 8, as_of: int | None = None, min_terms: int = 2, vector_floor: float | None = None, include_obsolete: bool = False) -> list[MemoryHit]` (`side` and `phase` filter on the version's properties); `class MemoryRetriever(Protocol)` with `retrieve(...)` of the same signature; `MemoryGraph.retrieve = hybrid` bound.
- Keyword half: `UNWIND $terms AS t CALL db.index.fulltext.queryNodes('mem_text', t) YIELD node, score` filtered by project, `labels(node)` ∩ `$labels`, validity; `count(*) AS matched, sum(score) AS rank WHERE matched >= $need`; `need = min(min_terms, len(terms))`; top `CANDIDATES_PER_HALF`.
- Vector half: when current embedded count of each label ≤ `exact_scan_max`, `vector.similarity.cosine(n.embedding, $v)` over `MATCH (n:Mem {project_id: $p})` with label filter; else `db.index.vector.queryNodes('vec_<label>', 100 * k, $v)` post-filtered; raw cosine `2 * s - 1` ≥ floor.
- Combination exactly as `engine/knowledge/search.py::hybrid_rows` today (normalise each half by its best, 0.5/0.5, sort by `(-score, uid)`, top k).

- [ ] **Step 1: Write failing tests** (`memory_graph`, `EMBEDDINGS_BACKEND=hash` set in the module)

```python
async def test_finds_a_rule_by_its_words(memory_graph):
    await seed_chunks(memory_graph, [("rule", "Credit limit above 5000 needs manager approval")])
    hits = await hybrid(memory_graph, P, "credit limit approval")
    assert hits and hits[0].uid == "chunk:p0:rule:R1" and hits[0].citation.artifact_type == "analysis"
    assert hits[0].citation.revision == 1 and hits[0].citation.phase == "analysis"

async def test_unrelated_question_finds_nothing(memory_graph):
    await seed_chunks(memory_graph, [("rule", "Credit limit above 5000 needs manager approval")])
    assert await hybrid(memory_graph, P, "capital of France") == []

async def test_vector_score_is_raw_cosine(memory_graph):
    await seed_chunks(memory_graph, [("summary", "loan payoff quote")])
    hit = (await hybrid(memory_graph, P, "loan payoff quote", min_terms=99))[0]   # vector half only
    assert hit.vector_score == 1.0 and await raw_similarity(memory_graph, "loan payoff quote") > 0.99

async def test_hostile_query_strings_are_data(memory_graph):                # Review Focus 4
    await seed_chunks(memory_graph, [("summary", "loan payoff quote")])
    for q in ['foo" OR 1=1 //', "name:*", ") MATCH (n) DETACH DELETE n //", "\\", "*:*"]:
        await hybrid(memory_graph, P, q)
    assert (await memory_graph.counts(P))["Chunk"] == 1

async def test_other_projects_never_match(memory_graph):
    await seed_chunks(memory_graph, [("summary", "loan payoff quote")], project="other")
    assert await hybrid(memory_graph, P, "loan payoff quote") == []

async def test_obsolete_memory_is_left_out(memory_graph):
    await seed_memory(memory_graph, "mem_1", "Map JDBC Query to Dapper")
    await apply(memory_graph, "k9", curation_delta("obsolete", target="mem_1"), phase="curation")
    assert await hybrid(memory_graph, P, "JDBC Query Dapper", labels=["Memory"]) == []
    assert await hybrid(memory_graph, P, "JDBC Query Dapper", labels=["Memory"], include_obsolete=True)

async def test_as_of_searches_old_text(memory_graph):
    await seed_chunks(memory_graph, [("summary", "loan payoff quote")])
    await seed_chunks(memory_graph, [("summary", "escrow refund schedule")], key="k2")
    assert await hybrid(memory_graph, P, "loan payoff quote") == []
    assert await hybrid(memory_graph, P, "loan payoff quote", as_of=1)

def test_query_terms_keeps_identifiers_and_drops_lucene_stopwords():
    assert query_terms("Is num_of_pulls NOT they OR x") == ["num_of_pulls", "or", "x"]
```

- [ ] **Step 2: Run** `make test PYTEST='.venv/bin/pytest engine/tests/test_memory_search.py -v'` — expect FAIL.
- [ ] **Step 3: Implement** `search.py`, the search constants, `MemoryGraph.retrieve`.
- [ ] **Step 4: Run** — expect PASS.
- [ ] **Step 5: Commit** `feat(memory): hybrid search on Neo4j with citations`.

---

### Task 5: Projectors for old code, analysis and design

**Files:**
- Create: `engine/memory/projectors/__init__.py`, `estate.py`, `analysis.py`, `design.py`, `engine/tests/test_memory_projectors_old.py`

**Interfaces:**
- Consumes: `MemNode`, `MemRel`, `ScopedDelta` (Task 2); `engine.ir.models.Application`; `engine.domains.models.Graph`; `engine.dotnet_gen.models.DesignRecord`.
- Produces:
  - `DomainInput(BaseModel)`: `key`, `name`, `suggested_name`, `ir_process_ids: list[str]`.
  - `ProcessRef(BaseModel)`: `ir_id`, `api_id`, `name`.
  - `project_estate(apps: list[Application], domains: list[DomainInput], graph: Graph, refs: list[ProcessRef]) -> ScopedDelta` — scope `estate`; uids per spec section 1; `Activity.props.starter` true for the starter activity with `starter_kind`; `SharedResource` props `kind`, `name` only; `SubstVar` props `type` only; old `Endpoint` from REST/HTTP/SOAP starter config (`method`, `path`) with `side="old"`; estate `Graph` edges map one to one (`contains`→`CONTAINS`, ...) with graph node ids translated to uids (`process:<id>` already matches; `table:`, `queue:`, `topic:`, `endpoint:` prefixed per section 1; `resource:` stays); process-calls `CALLS`; `USES` Process→Contract for `contracts_used`, Process→SubstVar for substvars referenced in activity config (`%%name%%`). `artifact_type="process"`, `artifact_id=api_id`.
  - `ChunkInput(BaseModel)`: `kind: Literal["summary", "rule", "finding"]`, `key: str` (`"summary"`, rule key, finding key), `text: str`, `activity_ids: list[str] = []`.
  - `project_analysis(ref: ProcessRef, chunks: list[ChunkInput]) -> ScopedDelta` — scope `analysis:<ir_id>`; uid `chunk:<ir_id>:summary` / `:rule:<key>` / `:finding:<key>`; `ABOUT` → `process:<ir_id>` and each `activity:<ir_id>/<activity id>`; `artifact_type="analysis"`.
  - `project_design(ref: ProcessRef, design: DesignRecord, design_id: str) -> ScopedDelta` — scope `design:<ir_id>`; `chunk:<ir_id>:design:narrative` (when narrative) and `chunk:<ir_id>:design:decision:<slug(title)>` with text `"<title>: <choice>. Alternatives: <a, b>. Reason: <reason>"`; ABOUT Process; `artifact_type="design"`, `artifact_id=design_id`.
  - `slug(text: str) -> str` (lowercase, `[a-z0-9]+` joined by `-`, at most 60 chars).

- [ ] **Step 1: Write failing unit tests** using the synthetic sample estate (`samples/` parsed with the existing scanner, as `engine/tests/test_domains.py` does)

```python
def test_estate_has_every_process_and_activity_once():
    d = project_estate(apps, domains, graph, refs)
    uids = [n.uid for n in d.nodes]
    assert len(uids) == len(set(uids)) and d.scope == "estate"
    assert {f"process:{p.id}" for a in apps for p in a.processes} <= set(uids)
    assert all(f"activity:{p.id}/{x.id}" in uids for a in apps for p in a.processes for x in p.activities)

def test_estate_is_deterministic():
    assert project_estate(apps, domains, graph, refs) == project_estate(apps, domains, graph, refs)

def test_starter_activity_is_marked():
    starters = [n for n in project_estate(apps, domains, graph, refs).nodes if n.label == "Activity" and n.props.get("starter")]
    assert starters and all(n.props["starter_kind"] in {"rest", "http", "soap", "jms", "timer", "subprocess"} for n in starters)

def test_resource_settings_and_substvar_values_never_projected():
    d = project_estate(apps, domains, graph, refs)
    assert all(set(n.props) <= {"kind", "name", "artifact_type", "artifact_id", "process_id", "path"} for n in d.nodes if n.label == "SharedResource")
    assert all("value" not in n.props for n in d.nodes if n.label == "SubstVar")

def test_every_estate_rel_endpoint_is_in_the_delta():
    d = project_estate(apps, domains, graph, refs); uids = {n.uid for n in d.nodes}
    assert all(r.src in uids and r.dst in uids for r in d.rels)

def test_analysis_chunks_are_about_process_and_cited_activities():
    d = project_analysis(ref, [ChunkInput(kind="rule", key="R1", text="Rule R1 ...", activity_ids=["a1"])])
    assert d.scope == f"analysis:{ref.ir_id}" and d.nodes[0].uid == f"chunk:{ref.ir_id}:rule:R1"
    assert {r.dst for r in d.rels} == {f"process:{ref.ir_id}", f"activity:{ref.ir_id}/a1"}

def test_design_decisions_become_design_chunks():
    d = project_design(ref, design_record_with_decision("Use Dapper", "Dapper", ["EF Core"], "simple SQL"), "dr1")
    assert [n.uid for n in d.nodes] == [f"chunk:{ref.ir_id}:design:decision:use-dapper"]
    assert d.nodes[0].text == "Use Dapper: Dapper. Alternatives: EF Core. Reason: simple SQL"
```

- [ ] **Step 2: Run** `.venv/bin/pytest engine/tests/test_memory_projectors_old.py -v` — expect FAIL.
- [ ] **Step 3: Implement** the three projectors.
- [ ] **Step 4: Run** — expect PASS.
- [ ] **Step 5: Commit** `feat(memory): estate, analysis and design projectors`.

---

### Task 6: Projectors for new code, verify, deploy, gates, decisions, curations, seeds

**Files:**
- Create: `engine/memory/projectors/build.py`, `verify.py`, `deploy.py`, `gate.py`, `events.py`, `seed.py`, `engine/tests/test_memory_projectors_new.py`

**Interfaces:**
- Consumes: Task 5's `ProcessRef`, `slug`; `engine.dotnet_gen.models.DesignRecord`, `MappingRow`.
- Produces:
  - `BuildFileInput(path, origin, sha256, size)`, `TestInput(name, kind, rule_key, outcome, duration_ms, message)`, `StepInput(name, ok, log)`, `BuildInput(build_id, build_hash, stack="dotnet", design: DesignRecord, files: list[BuildFileInput], tests: list[TestInput], steps: list[StepInput], starter_activity_id: str | None, starter_kind: str | None, starter_config: dict)`.
  - `project_build(ref: ProcessRef, b: BuildInput) -> ScopedDelta` — scope `build:<ir_id>`; Solution (`solution:dotnet:<ir_id>`), CodeProject per `design.solution.projects`, CodeFile per file (`path` relative to the solution root, `codefile:dotnet:<ir_id>/<path>`), Class and Method per `MappingRow` (uids per spec: `class:dotnet:<ir_id>/<project>/<class>`, `method:dotnet:<ir_id>/<project>/<class>.<method>`), record Classes per `ContractMap.record_names` in the `core` project, ConfigKey per `ConfigMap` (props `secret` only), MessagingEntity per `design.messaging`, new Endpoint from the starter config (`endpoint:new:<METHOD> <path>`, EXPOSED by the starter's Method), Test per test result; CONTAINS down the tree; `MIGRATED_TO` Activity→Method (`status="generated"`, `pattern`, `origin`), Process→CodeProject (the `api` project), Process→Class (the starter row's class), Contract→record Class, SubstVar→ConfigKey (`substvar:<app id>/<substvar>`), old Endpoint→new Endpoint; `TESTS` Test→Method where `test.rule_key in row.rule_ids`; Error `error:build:<ir_id>:<step name>` per failed step with `FAILED_AT` the CodeFile whose path matches `r"([\w./-]+\.cs)\((\d+),(\d+)\)"` in the log (else ABOUT the api CodeProject); Error `error:test:<ir_id>:<test name>` per failed test, `FAILED_AT` its tested Method. Message text: first 500 characters.
  - `ParityInput(build_id, runs: list[RunInput(run_id, scenario_key, status, parity, defects: list[DefectInput(field, expected, actual, finding_key, note)])])`; `project_verify(ref, p: ParityInput, starter_method_uid: str | None) -> ScopedDelta` — scope `verify:<ir_id>`; one Chunk `chunk:<ir_id>:verify:<scenario_key>` (`kind="finding"`, text `"Scenario <key>: <status>, parity <p>%"`), one Error `error:parity:<ir_id>:<scenario_key>/<field>` per defect ABOUT Process and `FAILED_AT` `starter_method_uid`; `artifact_type="verify_run"`.
  - `PlanInput(plan_id, status, output, infra_paths: list[str])`; `project_deploy(ref, p: PlanInput) -> ScopedDelta` — scope `deploy:<ir_id>`; on `status == "failed"` one Error `error:deploy:<ir_id>:<plan_id>` `FAILED_AT` the first infra CodeFile whose path appears in `output`, else ABOUT the Solution; otherwise an empty delta (closes an earlier deploy Error).
  - `GateInput(gate, state, actor, note, decided_at, spec_hash, design_hash, build_hash, evidence_hash, gate_row_id)`; `project_gate(ref, g: GateInput) -> ScopedDelta` — scope `gate:<ir_id>:<gate>`, uid `gate:<ir_id>:<gate>`, text `"<gate> <state> by <actor>" + (": <note>" if note)`, ABOUT Process; `artifact_type="gate"`.
  - `EventInput(id, kind: Literal["decision", "curation"], action: str | None, text, context: dict, target_uid: str | None, replacement_uid: str | None, actor, created_at)`; `project_event(e: EventInput, target_label: str | None, target_name: str | None) -> ScopedDelta` — decision: scope `decision:<id>`, Memory uid `<id>`, `kind=context.get("kind", "decision")`, ABOUT the uid in `context["about_uid"]` when present; curation: scope `curation:<id>`, Curation `curation:<id>` ABOUT the target (for mapping actions the source node, read from `target_uid`'s `src` part) plus per spec section 3 the Memory (`note` → kind `note`; reject/correct → kind `mapping` with the exact sentences of the spec) and for `correct_mapping` a `MIGRATED_TO {status: "corrected", curated_by: actor}` source→replacement; obsolete: Curation only.
  - `project_seed(patterns: list[dict]) -> ScopedDelta` — scope `seed`, uid `seed:<pattern id>`, text `engine.knowledge.memory.pattern_text(p)`, `kind=p["kind"]`, `source="seed"`, `artifact_type="memory"`.

- [ ] **Step 1: Write failing unit tests**

```python
def test_every_mapping_row_has_lineage_activity_to_method():
    d = project_build(ref, build_input())
    mig = {(r.src, r.dst) for r in d.rels if r.type == "MIGRATED_TO"}
    assert all((f"activity:{ref.ir_id}/{m.activity_id}",
                f"method:dotnet:{ref.ir_id}/{m.project}/{m.class_name}.{m.method}") in mig for m in build_input().design.mapping)

def test_new_code_nodes_carry_stack_dotnet():
    assert {n.props["stack"] for n in project_build(ref, build_input()).nodes if n.label in NEW_LABELS} == {"dotnet"}

def test_test_links_to_method_by_rule_key():
    d = project_build(ref, build_input(tests=[TestInput(name="T.Flow.R1", kind="rule", rule_key="R1", outcome="passed")]))
    assert any(r.type == "TESTS" and r.src.endswith("/T.Flow.R1") for r in d.rels)

def test_compile_error_fails_at_the_named_file():
    d = project_build(ref, build_input(steps=[StepInput(name="build", ok=False, log="Api/Flow.cs(12,5): error CS1002")]))
    assert ("FAILED_AT", f"codefile:dotnet:{ref.ir_id}/Api/Flow.cs") in {(r.type, r.dst) for r in d.rels}

def test_failed_plan_without_a_file_is_about_the_solution():
    d = project_deploy(ref, PlanInput(plan_id="pl1", status="failed", output="Error: quota", infra_paths=["infra/main.tf"]))
    assert [(r.type, r.dst) for r in d.rels] == [("ABOUT", f"solution:dotnet:{ref.ir_id}")]

def test_gate_uid_is_stable_across_decisions():
    a = project_gate(ref, gate_input(state="approved")); b = project_gate(ref, gate_input(state="rejected"))
    assert a.nodes[0].uid == b.nodes[0].uid == f"gate:{ref.ir_id}:G3" and node_hash(a.nodes[0]) != node_hash(b.nodes[0])

def test_correct_mapping_adds_a_corrected_migrated_to_and_a_mapping_memory():
    d = project_event(event("correct_mapping", target_uid=MIG_UID, replacement_uid=NEW_METHOD), "Activity", "Query")
    assert any(r.type == "MIGRATED_TO" and r.props["status"] == "corrected" and r.dst == NEW_METHOD for r in d.rels)
    assert [n.props["kind"] for n in d.nodes if n.label == "Memory"] == ["mapping"]

def test_seed_projector_is_idempotent_and_uses_pattern_ids():
    d = project_seed(load_seed_patterns())
    assert all(n.uid.startswith("seed:") and ":" not in n.uid[5:] for n in d.nodes)
    assert project_seed(load_seed_patterns()) == d
```

- [ ] **Step 2: Run** `.venv/bin/pytest engine/tests/test_memory_projectors_new.py -v` — expect FAIL.
- [ ] **Step 3: Implement** the projectors.
- [ ] **Step 4: Run** — expect PASS.
- [ ] **Step 5: Commit** `feat(memory): new-code, lineage, verify, deploy, gate, event and seed projectors`.

---

### Task 7: Job queue: delayed retry, per-kind attempts, dedupe, internal kinds; `memory_events`

**Files:**
- Create: `api/alembic/versions/0007_memory_events_job_retry.py`, `api/app/models/memory.py`, `api/tests/test_job_retry.py`
- Modify: `api/app/models/job.py`, `api/app/models/__init__.py`, `api/app/jobs/registry.py`, `api/app/jobs/runner.py`, `api/app/routers/history.py`, `api/app/services/history.py`

**Interfaces:**
- Migration `0007`: `jobs.not_before TIMESTAMPTZ NULL`; index `jobs (status, not_before, created_at)`; table `memory_events (id TEXT PK, project_id TEXT FK projects.id ON DELETE CASCADE, kind TEXT CHECK kind IN ('decision','curation'), action TEXT NULL, text TEXT NOT NULL, context JSONB NOT NULL DEFAULT '{}', target_uid TEXT NULL, replacement_uid TEXT NULL, actor TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now())`, index `(project_id, created_at)`. Downgrade drops both.
- Produces `MemoryEvent` SQLAlchemy model (`IdMixin` with ids `mem_<12 hex>`).
- Produces in `registry.py`: `class RetryLater(Exception)` with `__init__(self, delay_s: float, reason: str, *, count_attempt: bool = True)`; `register(kind, handler, *, max_attempts: int | str | None = None)` and `job(kind, *, max_attempts=None)` (a `str` names a `Settings` field, read when the job finishes); `max_attempts_for(kind: str, settings: Settings) -> int` (falls back to `settings.job_max_attempts`); `INTERNAL_PREFIX = "memory."`.
- Runner: claim adds `AND (not_before IS NULL OR not_before <= now())`; when a handler raises `RetryLater` and `attempts < max_attempts_for(kind, settings)` (or `count_attempt=False`, which also decrements `attempts`), the job goes back to `queued` with `owner=NULL`, `not_before = now() + delay`, and a `log` event `retry in <delay> s: <reason>`; otherwise `failed` with error `<reason> after <attempts> attempts`.
- Produces in `runner.py`: `async enqueue(sessionmaker, kind: str, payload: dict, *, project_id: str | None = None, process_id: str | None = None, dedupe_key: str | None = None) -> str | None` (returns `None` when a `queued` job with `payload->>'dedupe_key' = dedupe_key` exists; one `INSERT ... SELECT ... WHERE NOT EXISTS` statement); `JobRunner.submit` delegates to it.
- `GET /history` (the only job listing; `services/history.py` selects the project's jobs) excludes kinds starting with `INTERNAL_PREFIX` from the timeline and phase totals unless `include_internal=true`. `GET /jobs/{id}` still serves any job.

- [ ] **Step 1: Write failing tests** (`api/tests/test_job_retry.py`, the existing `app` fixture, test-only handlers registered with teardown)

```python
async def test_retry_later_requeues_with_not_before(app):
    calls = []
    async def flaky(ctx, w):
        calls.append(time.monotonic())
        if len(calls) < 3: raise RetryLater(0.2, "neo4j down")
        return {"ok": True}
    register("retry-test", flaky, max_attempts=5)
    job = await wait_terminal(app, await app.state.runner.submit("retry-test", {}))
    assert job.status == "done" and job.attempts == 3 and calls[2] - calls[1] >= 0.2

async def test_retry_gives_up_after_max_attempts(app):
    register("retry-fail", always_retry, max_attempts=2)
    job = await wait_terminal(app, await app.state.runner.submit("retry-fail", {}))
    assert job.status == "failed" and job.error == "neo4j down after 2 attempts"

async def test_uncounted_retry_does_not_use_an_attempt(app):
    register("retry-lock", lock_busy_twice_then_ok, max_attempts=1)
    job = await wait_terminal(app, await app.state.runner.submit("retry-lock", {}))
    assert job.status == "done"

async def test_enqueue_dedupes_on_queued_dedupe_key(app_no_worker):
    sm = app_no_worker.state.sessionmaker
    a = await enqueue(sm, "noop-test", {"dedupe_key": "p|estate"})
    b = await enqueue(sm, "noop-test", {"dedupe_key": "p|estate"})
    assert a is not None and b is None

async def test_internal_jobs_hidden_from_history(client, project_id):
    await enqueue(client.app.state.sessionmaker, "memory.ingest", {"project_id": project_id}, project_id=project_id)
    kinds = lambda body: {e.get("job_kind") for e in body["timeline"] if e["kind"] == "job"}
    assert "memory.ingest" not in kinds((await client.get("/history", params={"project_id": project_id})).json())
    assert "memory.ingest" in kinds((await client.get("/history", params={"project_id": project_id, "include_internal": True})).json())
```

- [ ] **Step 2: Run** `.venv/bin/pytest api/tests/test_job_retry.py -v` — expect FAIL.
- [ ] **Step 3: Implement** the migration, model, registry and runner changes, listing filter.
- [ ] **Step 4: Run** the file and `.venv/bin/pytest api/tests/test_jobs.py api/tests/test_job_worker.py api/tests/test_job_registry.py -q` — expect PASS; `.venv/bin/alembic -c api/alembic.ini upgrade head && .venv/bin/alembic -c api/alembic.ini downgrade -1 && .venv/bin/alembic -c api/alembic.ini upgrade head` — expect no error.
- [ ] **Step 5: Commit** `feat(jobs): delayed retry, per-kind attempts, dedupe; memory_events table`.

---

### Task 8: The `memory.ingest` job and its enqueue points

**Files:**
- Create: `api/app/services/memory_ingest.py`, `api/tests/test_memory_ingest.py`
- Modify: `api/app/jobs/handlers.py`, `api/app/jobs/runner.py` (follow-up in the `done` transaction), `api/app/jobs/registry.py` (`WorkerServices.memory: MemoryGraph | None`), `api/app/main.py`, `api/app/worker.py` (driver lifecycle, `ensure_schema` at start, logged on failure), `api/app/services/gates.py` (`decide` enqueues), `api/app/routers/projects.py` (seed)

**Interfaces:**
- Consumes: projectors (Tasks 5–6), `MemoryGraph.apply_revision` (Task 2), `enqueue`, `RetryLater` (Task 7), `advisory_lock` semantics (`api/app/jobs/locks.py`).
- Produces in `memory_ingest.py`:
  - `INGEST_AFTER: dict[str, tuple[Phase, str]]` = `{"scan": ("discover", "estate"), "discover": ("discover", "estate"), "analyse": ("analysis", "analysis:{process}"), "design": ("design", "design:{process}"), "build": ("build", "build:{process}"), "verify": ("verify", "verify:{process}"), "plan": ("deploy", "deploy:{process}")}` (`{process}` = IR process id resolved by the handler; the dedupe key uses the API process id until then).
  - `followup_payload(kind: str, job_id: str, project_id: str | None, process_id: str | None, payload: dict) -> dict | None` — the ingest payload of spec section 2 (`ingest_key=f"{phase}:{job_id}"`, `dedupe_key=f"{project_id or process_id}|{scope}"`), `None` for other kinds.
  - `async enqueue_ingest(sessionmaker, settings, *, project_id: str, phase: Phase, scope: str, source: dict, actor: str, ingest_key: str) -> str | None` — no-op returning `None` when `settings.neo4j_uri == ""`.
  - `async ingest(ctx: JobContext, w: WorkerServices) -> dict` — resolve the project; `pg_try_advisory_lock(hashtext('memory:' || project_id))` on a dedicated connection (released in `finally`), busy → `RetryLater(2, "memory of this project is being written", count_attempt=False)`; load the source rows and call the phase's projector (`load_estate`, `load_analysis`, `load_design`, `load_build`, `load_verify`, `load_deploy`, `load_gate`, `load_event`, `load_seed`); build/verify/deploy return `{"superseded": True}` when the source is not the latest build or plan of its process; `apply_revision`; Neo4j `ServiceUnavailable`/`SessionExpired`/`TransientError`/`MemoryUnavailable` → `RetryLater(min(600, 15 * 2 ** (attempt - 1)), f"memory unavailable: {cls}")`; returns `ApplyResult.model_dump() | {"phase", "scope"}`.
  - `load_*` functions read Postgres with the job's session and return the projector inputs; `load_analysis` builds `ChunkInput`s with today's `analysis.chunk_items` text and the rule citations' activity ids.
- `RevisionMeta.iteration` = the process's current `DesignRecord.version` (1 when none, and for project-wide scopes); sub-project 6 redefines it.
- Handler: `@job("memory.ingest", max_attempts="memory_ingest_max_attempts")`; `ctx.attempt: int` added to `JobContext` from the claimed row's `attempts`.
- Runner follow-up: in the transaction that marks a job `done`, when `w.settings.neo4j_uri` and `followup_payload(...)` is not `None`, insert the ingest job with `enqueue`'s dedupe statement.
- `gates.decide` → `enqueue_ingest(phase="gate", scope=f"gate:{ir_id}:{gate}", source={"process_id", "gate"}, ingest_key=f"gate:{row.id}:{row.decided_at.isoformat()}")` after commit.
- Project creation → `enqueue_ingest(phase="seed", scope="seed", ingest_key=f"seed:{sha256(seed_patterns.yaml)[:12]}")` (replaces `_seed`'s `Knowledge.seed_patterns` call).

- [ ] **Step 1: Write failing tests** (`memory_app`, `memory_client`; upload flow helpers from `api/tests/test_analysis_gates.py`)

```python
async def test_scan_queues_and_applies_an_estate_revision(memory_client, sample_upload):
    pid = await scan_sample(memory_client, sample_upload)
    ingest = await wait_kind(memory_client.app, pid, "memory.ingest", status="done")
    assert ingest.result["phase"] == "discover" and ingest.result["revision"] == 2   # 1 = seed
    assert (await memory_client.app.state.memory.counts(pid))["Process"] == SAMPLE_PROCESS_COUNT

async def test_rerun_of_the_same_ingest_job_creates_no_duplicates(memory_client, sample_upload):
    pid = await scan_sample(memory_client, sample_upload)
    done = await wait_kind(memory_client.app, pid, "memory.ingest", status="done")
    again = await rerun_job(memory_client.app, done.id)              # requeue the row as-is
    assert again.result["skipped"] is True and await revision_count(memory_client.app, pid) == 2

async def test_phase_succeeds_and_ingest_retries_while_neo4j_is_down(memory_client, sample_upload, neo4j_down):  # Review Focus 1
    pid = await scan_sample(memory_client, sample_upload)              # neo4j_down: driver to a closed port
    assert (await wait_kind(memory_client.app, pid, "scan", status="done")).status == "done"
    ingest = await latest_kind(memory_client.app, pid, "memory.ingest")
    assert ingest.status == "queued" and ingest.not_before > utcnow()
    neo4j_down.restore(); await fast_forward(memory_client.app, ingest.id)
    assert (await wait_terminal(memory_client.app, ingest.id)).result["revision"] == 2

async def test_two_concurrent_ingests_of_one_project_serialise(memory_app, two_workers):  # Review Focus 2
    ids = [await enqueue_ingest_for(memory_app, P, scope=f"analysis:p{i}") for i in range(6)]
    results = [(await wait_terminal(memory_app, i)).result["revision"] for i in ids]
    assert sorted(r for r in results if r) == list(range(2, 2 + len([r for r in results if r])))

async def test_gate_decision_creates_a_gate_revision(memory_client, approved_g1):
    pid, proc = approved_g1
    ingest = await wait_kind(memory_client.app, pid, "memory.ingest", status="done", phase="gate")
    d = await memory_client.app.state.memory.node_detail(pid, f"gate:{proc.ir_id}:G1")
    assert d.node.props["state"] == "approved" and ingest.result["revision"] >= 2

async def test_older_build_ingest_is_superseded(memory_client, two_builds):
    pid, old_build_ingest_payload = two_builds
    out = await run_ingest_directly(memory_client.app, old_build_ingest_payload)
    assert out == {"superseded": True}

async def test_no_neo4j_uri_queues_no_ingest(client, sample_upload):         # the existing app fixture
    pid = await scan_sample(client, sample_upload)
    assert await jobs_of_kind(client.app, pid, "memory.ingest") == []
```

- [ ] **Step 2: Run** `make test PYTEST='.venv/bin/pytest api/tests/test_memory_ingest.py -v'` — expect FAIL.
- [ ] **Step 3: Implement** `memory_ingest.py`, the handler, the follow-up hook, the lifespan and worker wiring (`MemoryGraph.connect(...)` when `neo4j_uri`, `app.state.memory`, `WorkerServices.memory`, closed on shutdown), the gate and seed enqueues.
- [ ] **Step 4: Run** the file — expect PASS; run `.venv/bin/pytest api/tests -q` — expect no regressions.
- [ ] **Step 5: Commit** `feat(memory): memory.ingest job after phases, gates and seeds`.

---

### Task 9: Agents and Ask read Neo4j; decisions go to `memory_events`; Postgres store removed

**Files:**
- Rewrite: `api/app/services/knowledge.py`
- Modify: `engine/knowledge/ask.py`, `engine/knowledge/memory.py`, `api/app/routers/ask.py`, `api/app/schemas/analysis.py` (`HitOut.citation`), `web/components/tabs/validate/AskPanel.tsx` (a 503 whose detail starts with `memory unavailable` shows `Memory is unavailable right now; try again shortly.`; `memory not configured` shows `Memory is not configured for this install.`), `api/app/services/analysis.py` (`reindex`, `_remember`), `api/app/services/domains.py` (`_remember`), `api/app/services/scan.py` (drop `load_graph`), `api/app/services/readiness.py` (counts), `api/app/main.py`, `api/app/worker.py`, `web/lib/types.ts` (optional `citation` on hits)
- Delete: `engine/knowledge/store.py`, `engine/knowledge/schema.sql`, `engine/knowledge/search.py`
- Rewrite tests: `engine/tests/test_knowledge.py` (embeddings, seeds, Ask over a fake retriever; store tests move to Tasks 2–4), the three `knowledge.*` queries in `api/tests/test_analysis_gates.py` (via `memory_client` and `MemoryGraph`), `api/tests/test_db_urls.py::test_knowledge_store_connects_with_an_asyncpg_ssl_url` (deleted with the store)

**Interfaces:**
- Produces `Knowledge(memory: MemoryGraph | None, sessionmaker, settings)` with:
  - `enabled: bool` (`memory is not None`);
  - `async retrieve_memory(project_id, query, k=6) -> list[MemoryItem]` — `Memory` hits converted to `engine.knowledge.memory.MemoryItem` (id = uid, kind, text, context parsed from JSON, source, created_by, created_at, score); raises `KnowledgeUnavailable("memory not configured")` when disabled, `KnowledgeUnavailable(f"memory unavailable: {cls}")` on driver errors;
  - `async record_decision(project_id, text, context, user) -> str` — masks, inserts `memory_events` (kind `decision`, `context["about_uid"]` set from `process_id`/domain key when present), enqueues phase `decision`, returns the event id; works (stores the row) even when memory is disabled;
  - `async reindex_process(project_id, process_id) -> None` — enqueues the analysis scope (replaces `index_process`);
  - `async ask(project_id, question, provider) -> Answer`;
  - `async counts(project_id) -> dict[str, int]`.
- `engine/knowledge/ask.py`: `async ask(retriever: MemoryRetriever, project_id, question, provider=None, *, k=8) -> Answer`; `Answer.citations: list[Hit]` where `Hit` gains `citation: Citation | None = None` and `id = uid`; `NO_ANSWER` unchanged; the prompt's origin field is `"<label> <citation.artifact_type> <citation.phase> r<revision>"`.
- `POST /ask`: disabled → 503 `memory not configured`; unavailable → 503 `memory unavailable: <class>` (detail text starts with `memory`).
- Readiness screen: the knowledge row reads `counts()` (`Chunk`, `Memory`); the gap text when unavailable is `memory unavailable: <class>`.

- [ ] **Step 1: Write failing tests**

```python
# api/tests/test_analysis_gates.py (rewritten parts)
async def test_finding_decision_reaches_agent_retrieval(memory_client, analysed):
    pid, proc, finding = analysed
    await decide_finding(memory_client, finding, "accept", note="known rounding")
    await wait_kind(memory_client.app, pid, "memory.ingest", status="done", phase="decision")
    items = await memory_client.app.state.knowledge.retrieve_memory(pid, f"{proc.name} finding rounding")
    assert any(i.kind == "decision" and "known rounding" in i.text for i in items)

async def test_ask_answers_from_memory_with_citations(memory_client, analysed):
    pid, proc, _ = analysed
    r = await memory_client.post("/ask", json={"project_id": pid, "question": f"rules of {proc.name}"})
    c = r.json()["citations"][0]
    assert r.status_code == 200 and c["citation"]["revision"] >= 1 and c["id"] == c["citation"]["uid"]

async def test_ask_without_memory_is_503_not_configured(client, project_id):
    r = await client.post("/ask", json={"project_id": project_id, "question": "anything"})
    assert r.status_code == 503 and r.json()["detail"] == "memory not configured"

async def test_seed_patterns_are_in_memory_after_project_creation(memory_client):
    pid = (await memory_client.post("/projects", json={"name": "s"})).json()["id"]
    await wait_kind(memory_client.app, pid, "memory.ingest", status="done", phase="seed")
    assert (await memory_client.app.state.memory.counts(pid))["Memory"] == len(load_seed_patterns())

# engine/tests/test_knowledge.py
async def test_ask_without_provider_returns_hits_as_answer():
    a = await ask(FakeRetriever([hit("chunk:p:summary", "loan payoff")]), "p", "loan payoff")
    assert a.text.startswith("Closest knowledge items") and a.citations[0].id == "chunk:p:summary"

def test_no_module_reads_the_postgres_knowledge_schema():
    offenders = [p for p in Path("api/app").rglob("*.py") if "knowledge." in p.read_text() and "knowledge.meta" not in p.read_text()
                 and p.name != "memory_migrate.py"]
    assert offenders == []
```

- [ ] **Step 2: Run** `make test PYTEST='.venv/bin/pytest engine/tests/test_knowledge.py api/tests/test_analysis_gates.py -v'` — expect FAIL.
- [ ] **Step 3: Implement** the facade and call-site changes; delete the Postgres store files; update `engine/agents/provider.py`'s import comment only if it breaks (it imports `MemoryItem` from `engine.knowledge.memory`, which stays).
- [ ] **Step 4: Run** `make test` — expect the whole suite green (count = 1144 − removed store tests + new tests); `.venv/bin/ruff check .` — clean.
- [ ] **Step 5: Commit** `feat(memory): agents and Ask read Neo4j; Postgres knowledge store removed`.

---

### Task 10: One-time migration from Postgres `knowledge.*`; admin commands

**Files:**
- Create: `api/app/services/memory_migrate.py`, `api/app/memory_admin.py`, `api/tests/test_memory_migrate.py`, `api/tests/fixtures/knowledge_legacy.sql` (the old `schema.sql` DDL plus rows, test-only)
- Modify: `api/app/jobs/handlers.py` (`memory.migrate`, `memory.reembed`), `api/app/main.py` (lifespan enqueues `memory.migrate` once)

**Interfaces:**
- Produces `async migrate_project(session, memory: MemoryGraph, project_id: str, conn) -> MigrationReport` and `async migrate_all(sessionmaker, memory, dsn) -> list[MigrationReport]`; `MigrationReport(project_id, memory_items, chunks, orphans, revision, missing: list[str])`.
  - Reads with psycopg (sync, in `asyncio.to_thread`) from `knowledge.memory_items`, `knowledge.chunks`, `knowledge.meta`.
  - Non-seed memory items → `memory_events` rows (`kind='decision'`, `id` kept, `context.kind` = item kind, `actor=created_by`, `created_at` kept) unless the id exists.
  - One `apply_revision(RevisionMeta(phase="migration", ingest_key="migration:pg-knowledge", actor="migration"), deltas)` with the estate, analysis, design, latest build, verify, deploy, gate deltas from the Task 8 loaders, the event deltas and the seed delta; Memory nodes get the old embedding (`MemNode.embedding`) when `knowledge.meta.embedding_backend == backend_name()`.
  - Orphans: each chunk whose `mask_sensitive(text)` equals no projected Chunk text → `MemNode(label="Chunk", uid=f"chunk:migrated:{old id}", ...)` in scope `migration:orphan`, ABOUT `process:<ir id>` resolved from the chunk's `process_id`.
  - Verification: every memory item id (seed ids mapped to `seed:<pattern>`) and every chunk text present in Neo4j, else `missing` non-empty and the job raises `RuntimeError(f"migration incomplete for {project_id}: {missing[:10]}")` before any rename.
  - When every project verified: `ALTER SCHEMA knowledge RENAME TO knowledge_retired`; `INSERT INTO knowledge_retired.meta VALUES ('neo4j_migrated_at', now()::text)`.
- Lifespan: when `neo4j_uri` set and `to_regclass('knowledge.meta')` exists, `enqueue(sm, "memory.migrate", {}, dedupe_key="memory.migrate")`.
- `memory_ingest.ingest` (Task 8) gains a first check: a `memory.migrate` job `queued` or `running` → `RetryLater(5, "waiting for the memory migration", count_attempt=False)`; test `test_ingest_waits_for_a_pending_migration` asserts an ingest queued alongside the migration finishes after it and revision 1 is the migration's.
- CLI `python -m api.app.memory_admin {migrate | reembed --project <id> | reingest --project <id>}`; `reingest` enqueues one ingest per scope (estate; analysis, design, build, verify, deploy and each gate per process; every `memory_events` row; seed) with `ingest_key=f"reingest:{uuid4().hex}:{scope}"`.

- [ ] **Step 1: Write failing tests** (`memory_app`; the fixture SQL loads `knowledge.*` rows for a project created by the upload flow; `run_migration(app)` = `migrate_all(app.state.sessionmaker, app.state.memory, TEST_DSN)`)

```python
async def test_migration_copies_memory_items_and_rederives_chunks(memory_app, legacy_project):
    pid = legacy_project
    reports = await migrate_all(memory_app.state.sessionmaker, memory_app.state.memory, TEST_DSN)
    r = next(x for x in reports if x.project_id == pid)
    assert r.missing == [] and r.revision is not None and r.memory_items == LEGACY_MEMORY_ITEMS
    assert (await memory_app.state.memory.counts(pid))["Memory"] == LEGACY_MEMORY_ITEMS
    assert await schema_exists(memory_app, "knowledge_retired") and not await schema_exists(memory_app, "knowledge")

async def test_chunk_with_no_source_row_becomes_an_orphan(memory_app, legacy_project_with_stray_chunk):
    r = (await run_migration(memory_app))[0]
    assert r.orphans == 1 and (await memory_app.state.memory.node_detail(r.project_id, "chunk:migrated:chunk:stray")) is not None

async def test_migration_is_idempotent(memory_app, legacy_project):
    await run_migration(memory_app); await rename_back(memory_app)             # simulate a second run
    r = (await run_migration(memory_app))[0]
    assert r.missing == [] and await revision_count(memory_app, r.project_id) == 1 + EXISTING_REVISIONS

async def test_missing_item_leaves_postgres_untouched(memory_app, legacy_project, drop_one_after_apply):
    with pytest.raises(RuntimeError, match="migration incomplete"):
        await migrate_all(memory_app.state.sessionmaker, memory_app.state.memory, TEST_DSN)
    assert await schema_exists(memory_app, "knowledge")

async def test_ingest_waits_for_a_pending_migration(memory_app, legacy_project):
    mig = await enqueue(memory_app.state.sessionmaker, "memory.migrate", {}, dedupe_key="memory.migrate")
    ing = await enqueue_ingest_for(memory_app, legacy_project, scope="seed")
    m, i = await wait_terminal(memory_app, mig), await wait_terminal(memory_app, ing)
    assert m.status == i.status == "done" and i.finished_at >= m.finished_at
    assert (await memory_app.state.memory.revisions(legacy_project))[-1].phase == "migration"

async def test_embeddings_are_reused_when_backend_matches(memory_app, legacy_project, embed_spy):
    await run_migration(memory_app)
    assert embed_spy.memory_texts == []
```

- [ ] **Step 2: Run** `make test PYTEST='.venv/bin/pytest api/tests/test_memory_migrate.py -v'` — expect FAIL.
- [ ] **Step 3: Implement** the migration, handlers, lifespan enqueue and CLI.
- [ ] **Step 4: Run** — expect PASS.
- [ ] **Step 5: Commit** `feat(memory): migrate Postgres knowledge into Neo4j and retire it`.

---

### Task 11: Readiness gating, Helm, compose and the guides

**Files:**
- Modify: `api/app/routers/system.py`, `api/tests/test_system.py`, `deploy/helm/exiqo/values.yaml`, `values.schema.json`, `values.gmf.example.yaml`, `templates/configmap.yaml`, `templates/api-deployment.yaml` (comment), `deploy/helm/tests/{schema_test,netpol_test,neo4j_backup_test}.yaml`, `deploy/helm/exiqo/ci/values.kind.yaml`, `deploy/docker/compose.pilot.yml`, `docs/deploy/GMF-INSTALL.md`, `docs/deploy/RUNBOOK.md`, `scripts/tests/test_docs_consistency.py` (if a new values key is named)

**Interfaces:**
- `check_neo4j(settings, state) -> str` returns `disabled`, `ok`, `degraded: <class> (down <s> s)` while `now - state.neo4j_down_since < neo4j_ready_grace_seconds`, else `down: <class>`; `state.neo4j_down_since` resets on `ok`. `GATING_CHECKS = ("postgres", "secrets", "neo4j")` when `neo4j_gates_readiness`, else the old pair; `_ready` accepts `ok`, `disabled`, and values starting with `degraded`.
- Configmap: `NEO4J_DATABASE`, `NEO4J_READY_GRACE_SECONDS` (`neo4j.readyGraceSeconds`, default 900), `NEO4J_GATES_READINESS` (`neo4j.gatesReadiness`, default true).
- Schema: fail unless `neo4j.enabled` or `neo4j.uri`; when `neo4j.enabled`, require `neo4jBackup.enabled: true` or a non-empty `neo4jBackup.disabledReason`. GMF example: `neo4jBackup.enabled: true` with `storageAccount`. Subchart config: `db.memory.transaction.total.max: "2g"`, `dbms.usage_report.enabled: "false"`; `ci/values.kind.yaml` sets `neo4jBackup.disabledReason: "kind CI"`.
- Pilot compose: Neo4j image `neo4j:5.26.32@<digest of the chart's image>`, `NEO4J_db_memory_transaction_total_max: 2g`.
- Guides: GMF-INSTALL section 9 (Neo4j backup is on, window, restore + re-ingest), section 11 (memory unavailable, ingest failing, readiness `degraded`); RUNBOOK "Memory (Neo4j)" (re-ingest, re-embed, migration, rollback `ALTER SCHEMA knowledge_retired RENAME TO knowledge`, reading ingest results).

- [ ] **Step 1: Write failing tests**

```python
async def test_neo4j_down_is_degraded_within_grace(client, monkeypatch):
    monkeypatch.setattr(system, "_probe_neo4j", fail_with(ServiceUnavailable))
    client.app.state.settings = client.app.state.settings.model_copy(update={"neo4j_uri": "bolt://x:1"})
    r = await client.get("/readyz")
    assert r.status_code == 200 and r.json()["neo4j"].startswith("degraded: ServiceUnavailable")

async def test_neo4j_down_past_grace_fails_readiness(client, neo4j_failing):   # fixture = the setup above
    client.app.state.neo4j_down_since = time.monotonic() - 901
    r = await client.get("/readyz")
    assert r.status_code == 503 and r.json()["neo4j"] == "down: ServiceUnavailable"

async def test_gating_off_keeps_ready(client, neo4j_failing):
    client.app.state.settings = client.app.state.settings.model_copy(update={"neo4j_gates_readiness": False})
    client.app.state.neo4j_down_since = time.monotonic() - 901
    assert (await client.get("/readyz")).status_code == 200
```

helm-unittest: `schema_test.yaml` — `neo4j.enabled=false` with empty `uri` fails with `neo4j.enabled or neo4j.uri`; bundled Neo4j without backup and without `disabledReason` fails; `netpol_test.yaml` — api and worker egress to `exiqo-neo4j` port 7687 rendered; `configmap` renders `NEO4J_READY_GRACE_SECONDS: "900"`.

- [ ] **Step 2: Run** `.venv/bin/pytest api/tests/test_system.py -v && make chart-check` — expect FAIL.
- [ ] **Step 3: Implement** the readiness change, chart, pilot compose and guide updates.
- [ ] **Step 4: Run** the same — expect PASS; `.venv/bin/pytest scripts/tests -q` — expect PASS.
- [ ] **Step 5: Commit** `feat(deploy): Neo4j gates readiness with grace; backup on for GMF; memory runbook`.

---

### Task 12: `/memory` read API

**Files:**
- Create: `api/app/routers/memory.py`, `api/app/schemas/memory.py`, `api/tests/test_memory_api.py`
- Modify: `api/app/main.py` (include under `guarded`)

**Interfaces:**
- Consumes: Task 3 reads, Task 4 `hybrid`, `Knowledge.enabled`.
- Produces routes (spec section 3 table), Pydantic out-models mirroring `engine/memory/model.py` (`StatusOut{configured, available, revision, at, phase, counts, pending_ingests, embedding_backend}`, `SearchOut{hits}`, `Subgraph`, `NodeDetail`, `RevisionsOut{revisions}`, `RevisionDiff`).
- Query validation with FastAPI `Query`: `q: str = Query(min_length=1, max_length=500)`, `k: int = Query(8, ge=1, le=50)`, `depth: int = Query(1, ge=1, le=2)`, `limit: int = Query(400, ge=1, le=400)` (revisions `le=200`), `uid: str = Query(max_length=512)`, `labels: list[str] = Query([])` each validated against `NODE_LABELS` (422 otherwise), `side: Literal["old", "new", "knowledge"] | None`, `phase: Phase | None`, `as_of: int | None = Query(None, ge=1)`, `mode: Literal["neighbourhood", "lineage"] = "neighbourhood"`.
- Every route: `await session.get(Project, project_id)` or 404 `project not found`; memory disabled → 503 `memory not configured` (status → 200 `configured: false`); `MemoryUnavailable`/driver errors → 503 `memory unavailable: <class>` (status → 200 `available: false`); node or revision not found → 404 `not found`.
- `pending_ingests` = queued or running `memory.ingest` jobs of the project.

- [ ] **Step 1: Write failing tests** (`memory_client` after the sample scan + one analysis)

```python
async def test_status_reports_revision_and_counts(memory_client, scanned):
    s = (await memory_client.get(f"/memory/{scanned}/status")).json()
    assert s["configured"] and s["available"] and s["revision"] >= 2 and s["counts"]["Process"] > 0

async def test_search_returns_hits_with_citations(memory_client, scanned):
    hits = (await memory_client.get(f"/memory/{scanned}/search", params={"q": SAMPLE_PROCESS_NAME, "labels": ["Process"]})).json()["hits"]
    assert hits[0]["label"] == "Process" and hits[0]["citation"]["artifact_type"] == "process"

async def test_graph_without_uid_returns_top_level_nodes(memory_client, scanned):
    g = (await memory_client.get(f"/memory/{scanned}/graph")).json()
    assert {n["label"] for n in g["nodes"]} <= {"Domain", "Application", "Solution"} and g["nodes"]

async def test_unknown_label_is_422_and_depth_3_is_422(memory_client, scanned):
    assert (await memory_client.get(f"/memory/{scanned}/graph", params={"labels": ["Nope"]})).status_code == 422
    assert (await memory_client.get(f"/memory/{scanned}/graph", params={"depth": 3})).status_code == 422

async def test_unknown_project_is_404(memory_client):
    assert (await memory_client.get("/memory/nope/status")).status_code == 404

async def test_other_projects_uids_never_returned(memory_client, scanned, second_scanned):
    other = {n["uid"] for n in (await memory_client.get(f"/memory/{second_scanned}/graph")).json()["nodes"]}
    for path in ("graph", "revisions"):
        body = (await memory_client.get(f"/memory/{scanned}/{path}")).text
        assert not [u for u in other if u.startswith("application:") and u in body and u not in SHARED_SAMPLE_UIDS]
    assert (await memory_client.get(f"/memory/{scanned}/node", params={"uid": "process:does-not-exist"})).status_code == 404

async def test_diff_of_latest_revision(memory_client, scanned):
    n = (await memory_client.get(f"/memory/{scanned}/status")).json()["revision"]
    d = (await memory_client.get(f"/memory/{scanned}/revisions/{n}/diff")).json()
    assert d["revision"]["n"] == n and (d["nodes"]["added"] or d["nodes"]["changed"] or d["nodes"]["removed"])

async def test_status_when_neo4j_is_down(memory_client, scanned, neo4j_down):
    s = (await memory_client.get(f"/memory/{scanned}/status")).json()
    assert s["available"] is False
    r = await memory_client.get(f"/memory/{scanned}/search", params={"q": "x y"})
    assert r.status_code == 503 and r.json()["detail"].startswith("memory unavailable")
```

Use two uploads of differently named estates for isolation (copy the sample with renamed application ids in the fixture), so no uid is legitimately shared.

- [ ] **Step 2: Run** `make test PYTEST='.venv/bin/pytest api/tests/test_memory_api.py -v'` — expect FAIL.
- [ ] **Step 3: Implement** the router and schemas.
- [ ] **Step 4: Run** — expect PASS.
- [ ] **Step 5: Commit** `feat(api): /memory read endpoints`.

---

### Task 13: Curation and re-ingest API; `viewer.val` persona

**Files:**
- Modify: `api/app/routers/memory.py`, `api/app/schemas/memory.py`, `api/app/deps.py` (`require_curator`, `CuratorDep`), `api/app/auth/roles.py` (`DEV_PERSONAS["viewer.val"] = frozenset({"viewer"})`), `web/lib/personas.ts` (`viewer.val`: role "Viewer", entry `null`, gates `[]`, roleKey `viewer`), `web/e2e/personas.spec.ts` (persona count), `api/tests/test_roles.py`
- Test: `api/tests/test_memory_curation.py`

**Interfaces:**
- `require_curator(principal: PrincipalDep) -> Principal`: 403 `read-only: curating memory needs a gate-owner or admin role` unless `principal.roles & WRITE_ROLES` (both auth modes).
- `CurationIn(BaseModel)`: `action: Literal["note", "reject_mapping", "correct_mapping", "obsolete"]`, `target_uid: str = Field(max_length=512)`, `reason: str = Field(min_length=1, max_length=2000)`, `text: str | None = Field(None, max_length=4000)` (required for `note`), `replacement_uid: str | None` (required for `correct_mapping`).
- `POST /memory/{project_id}/curations` → 202 `{event_id, job_id}`; 422 `note needs text` / `correct_mapping needs replacement_uid`; 404 `target not found` when `current_node` is None; 409 `target has no mapping to curate` (mapping action on a node with no current outgoing MIGRATED_TO; `target_uid` may name the node or the MIGRATED_TO uid — the stored `target_uid` is the rel uid), 409 `obsolete applies to Memory items only`, 409 `replacement must be a Method or Class`; inserts `memory_events` (masked `reason` + `text`), `audit_log` action `memory curated` with detail `{event_id, action, target_uid}`, enqueues phase `curation` scope `curation:<event id>`.
- `POST /memory/{project_id}/reingest` (`AdminDep`) → 202 `{job_ids}` (Task 10's `reingest`).

- [ ] **Step 1: Write failing tests**

```python
async def test_viewer_cannot_curate(memory_client, built):
    r = await memory_client.post(f"/memory/{built.pid}/curations", headers={"X-User": "viewer.val"},
                                 json={"action": "note", "target_uid": built.activity_uid, "reason": "r", "text": "t"})
    assert r.status_code == 403 and r.json()["detail"].startswith("read-only")

async def test_viewer_can_read(memory_client, built):
    assert (await memory_client.get(f"/memory/{built.pid}/status", headers={"X-User": "viewer.val"})).status_code == 200

async def test_note_lands_as_curation_revision_and_agent_memory(memory_client, built):
    r = await memory_client.post(f"/memory/{built.pid}/curations", headers={"X-User": "sme.vicky"},
                                 json={"action": "note", "target_uid": built.activity_uid, "reason": "context", "text": "Rounds half-even"})
    assert r.status_code == 202
    job = await wait_terminal(memory_client.app, r.json()["job_id"])
    assert job.result["phase"] == "curation"
    items = await memory_client.app.state.knowledge.retrieve_memory(built.pid, "rounds half even")
    assert any(i.kind == "note" for i in items)

async def test_reject_then_lineage_reads_rejected(memory_client, built):
    await curate(memory_client, built, "reject_mapping", target=built.activity_uid, reason="wrong pattern")
    d = (await memory_client.get(f"/memory/{built.pid}/node", params={"uid": built.activity_uid})).json()
    assert [l["edge"]["status"] for l in d["lineage"] if l["edge"]["type"] == "MIGRATED_TO"] == ["rejected"]

async def test_correct_mapping_adds_corrected_edge(memory_client, built):
    await curate(memory_client, built, "correct_mapping", target=built.activity_uid, replacement=built.other_method_uid, reason="r")
    d = (await memory_client.get(f"/memory/{built.pid}/node", params={"uid": built.activity_uid})).json()
    assert sorted(l["edge"]["status"] for l in d["lineage"] if l["edge"]["type"] == "MIGRATED_TO") == ["corrected", "rejected"]

async def test_obsolete_on_a_process_is_409(memory_client, built):
    r = await curate(memory_client, built, "obsolete", target=built.process_uid, reason="r")
    assert r.status_code == 409

async def test_curation_text_is_masked_in_postgres(memory_client, built):
    await curate(memory_client, built, "note", target=built.activity_uid, reason="SSN 123-45-6789", text="x")
    assert "123-45-6789" not in await memory_event_text(memory_client.app, built.pid)

async def test_reingest_is_admin_only(memory_client, built):
    assert (await memory_client.post(f"/memory/{built.pid}/reingest", headers={"X-User": "sme.vicky"})).status_code == 403
    assert (await memory_client.post(f"/memory/{built.pid}/reingest", headers={"X-User": "ops.sam"})).status_code == 202
```

Plus in `api/tests/test_roles.py`: an `AUTH_MODE=oidc` principal with roles `{"viewer"}` gets 403 on curation and 200 on `GET /memory/{p}/status` (use the module's existing oidc principal helpers).

- [ ] **Step 2: Run** `make test PYTEST='.venv/bin/pytest api/tests/test_memory_curation.py api/tests/test_roles.py -v'` — expect FAIL.
- [ ] **Step 3: Implement**.
- [ ] **Step 4: Run** — expect PASS; `cd web && npx playwright test e2e/personas.spec.ts` — expect PASS with the new count.
- [ ] **Step 5: Commit** `feat(api): memory curation and re-ingest; read-only viewer persona`.

---

### Task 14: Explorer screen: search, canvas, node drawer

**Files:**
- Create: `web/app/memory/page.tsx`, `web/lib/memory.ts`, `web/components/memory/{MemoryExplorer,SearchRail,MemoryCanvas,NodeDrawer,MemoryStatusPill,MemoryUnavailable}.tsx`, `web/e2e/memory.spec.ts`
- Modify: `web/components/shell/TopBar.tsx`, `web/playwright.config.ts` (API env `NEO4J_URI` default `bolt://localhost:7687`, `NEO4J_PASSWORD` default `exiqo-dev-neo4j`)

**Interfaces:**
- `web/lib/memory.ts`: types mirroring the API (`MemoryStatus`, `MemoryHit`, `Citation`, `MemNodeOut`, `EdgeOut`, `Subgraph`, `NodeDetail`, `RevisionOut`, `RevisionDiff`), and TanStack Query hooks `useMemoryStatus(projectId)`, `useMemorySearch(projectId, q, filters)`, `useMemoryGraph(projectId, {uid, depth, labels, side, asOf, mode})`, `useMemoryNode(projectId, uid, asOf)`, `useRevisions(projectId)`, `useRevisionDiff(projectId, n)`, built on `web/lib/api.ts` `json()`.
- `LABEL_GROUPS = { old: [...], new: [...], knowledge: [...] }` and `GROUP_COLOR = { old: "plum", new: "teal", knowledge: "amber" }` (Tailwind tokens already in the theme; add `teal`/`amber` tokens to `tailwind.config` if absent).
- TopBar `LINKS`: `{ href: "/memory", label: "Memory", icon: Network }` inserted after History.
- URL state with `useSearchParams`: `uid`, `rev`, `q`, `labels`, `side`, `mode`.
- `MemoryCanvas` props: `{ graph: Subgraph; mode: "neighbourhood" | "lineage"; selected?: string; highlight?: { added: string[]; changed: string[]; removed: string[] }; onSelect(uid); onExpand(uid) }`; dynamic `import("cytoscape")` as in `DomainGraph.tsx`; lineage mode positions from `elkjs` (`layered`, direction RIGHT, old left / new right via `elk.partitioning`), passed as cytoscape `preset`. Edge styles: MIGRATED_TO width 3; `status=rejected` dashed red; `corrected` solid green. Selection highlights the lineage chain (`useMemoryGraph(..., mode: "lineage")` for the selected uid) and dims the rest to 25 % opacity.
- `NodeDrawer` tabs Overview, History, Knowledge, Lineage (Curate added in Task 15), `data-testid="node-drawer"`, each tab `role="tab"`.
- Test ids: `memory-search`, `memory-results`, `memory-canvas` (with `data-node-count`), `memory-status`, `memory-unavailable`, `memory-truncated`.

- [ ] **Step 1: Write failing Playwright tests** (`web/e2e/memory.spec.ts`; the upload helper from `upload-discover.spec.ts`)

```ts
test("the Memory link opens the explorer for the selected project", async ({ page }) => {
  await uploadSample(page);
  await page.getByRole("link", { name: "Memory" }).click();
  await expect(page).toHaveURL(/\/memory/);
  await expect(page.getByTestId("memory-status")).toContainText(/Revision \d+/);
});

test("search finds a process and the canvas focuses it", async ({ page }) => {
  await uploadSample(page); await page.goto("/memory");
  await page.getByTestId("memory-search").fill(SAMPLE_PROCESS_NAME);
  await page.getByTestId("memory-results").getByText(SAMPLE_PROCESS_NAME).first().click();
  await expect(page).toHaveURL(/uid=process%3A/);
  await expect(page.getByTestId("memory-canvas")).toHaveAttribute("data-node-count", /^[1-9]\d*$/);
  await expect(page.getByTestId("node-drawer")).toContainText("process:");
});

test("drawer shows history and knowledge tabs", async ({ page }) => {
  await uploadAndAnalyse(page); await openNode(page, SAMPLE_PROCESS_NAME);
  await page.getByRole("tab", { name: "History" }).click();
  await expect(page.getByTestId("node-drawer")).toContainText(/discover/);
  await page.getByRole("tab", { name: "Knowledge" }).click();
  await expect(page.getByTestId("node-drawer")).toContainText(/Summary of|Rule/);
});

test("no request leaves the web or API origin on the explorer", async ({ page }) => {
  const outside: string[] = [];
  page.on("request", (r) => { const u = new URL(r.url()); if (![WEB_ORIGIN, API_ORIGIN].includes(u.origin)) outside.push(r.url()); });
  await page.goto("/memory");
  await page.waitForLoadState("networkidle");
  expect(outside).toEqual([]);
});
```

- [ ] **Step 2: Run** `make neo4j && cd web && npx playwright test e2e/memory.spec.ts` (the config starts the API and web) — expect FAIL.
- [ ] **Step 3: Implement** the page and components; roomy layout per spec section 3 (24 px gutter, left rail 300 px, drawer 440 px, timeline slot 120 px reserved for Task 15).
- [ ] **Step 4: Run** the spec, `npm run typecheck`, `npm run lint` — expect PASS.
- [ ] **Step 5: Commit** `feat(web): memory explorer with search, graph canvas and node drawer`.

---

### Task 15: Explorer timeline, diff, curation and unavailable states

**Files:**
- Create: `web/components/memory/{Timeline,DiffPanel,CurateForm}.tsx`
- Modify: `web/components/memory/{MemoryExplorer,NodeDrawer,MemoryCanvas}.tsx`, `web/lib/memory.ts` (`useCurate`, `useReingest` mutations), `web/e2e/memory.spec.ts`

**Interfaces:**
- `Timeline` props `{ revisions: RevisionOut[]; value: number | null; onChange(n | null); onShowChanges(n) }`; ticks coloured by phase; `data-testid="memory-timeline"`, each tick `data-testid="rev-<n>"` with `aria-label="Revision <n>, <phase>"`; banner `Viewing revision <k> of <latest>` with button `Back to current`.
- `DiffPanel` `data-testid="memory-diff"`, sections "Added", "Changed", "Removed" for nodes and relationships, items clickable; feeds `MemoryCanvas.highlight` (removed nodes fetched with `as_of = n − 1` and drawn as ghosts at 40 % opacity).
- `CurateForm` in the drawer's `Curate` tab, rendered only when `can.curate` (`web/lib/personas.ts`: roles intersect admin, sme, architect, engineer, qa, ops); actions valid for the node (mapping actions when the node has an outgoing MIGRATED_TO; obsolete on Memory); required reason; submit shows `Pending` until the status revision increases, then `Applied in revision <n>`.
- Unavailable: `status.configured === false` → card text `Memory is not configured for this install.`; `status.available === false` → `Memory is unavailable. Neo4j is not reachable from the platform. Phases keep running and memory catches up when it returns.` with a `Retry` button; empty (`revision === null`) → `No memory yet for this project. It fills in as soon as the estate is scanned.`
- Admin header button `Re-ingest project` (ops.sam in dev).

- [ ] **Step 1: Add failing Playwright tests**

```ts
test("timeline as-of and diff highlight", async ({ page }) => {
  await uploadAndAnalyse(page); await page.goto("/memory");
  await page.getByTestId("rev-2").click();
  await expect(page.getByText(/Viewing revision 2 of \d+/)).toBeVisible();
  await page.getByRole("button", { name: "Show changes" }).click();
  await expect(page.getByTestId("memory-diff")).toContainText("Added");
  await page.getByRole("button", { name: "Back to current" }).click();
  await expect(page.getByText(/Viewing revision/)).toHaveCount(0);
});

test("lineage after a build highlights MIGRATED_TO", async ({ page }) => {
  await throughBuild(page); await openNode(page, SAMPLE_ACTIVITY_NAME);
  await page.getByRole("tab", { name: "Lineage" }).click();
  await expect(page.getByTestId("node-drawer")).toContainText("MIGRATED_TO");
  await expect(page.getByTestId("node-drawer")).toContainText("method:dotnet:");
});

test("an SME note goes pending then applied", async ({ page }) => {
  await uploadSample(page); await actAs(page, "sme.vicky"); await openNode(page, SAMPLE_PROCESS_NAME);
  await page.getByRole("tab", { name: "Curate" }).click();
  await page.getByLabel("Note").fill("Owned by the payoff team");
  await page.getByLabel("Reason").fill("context for agents");
  await page.getByRole("button", { name: "Pin note" }).click();
  await expect(page.getByTestId("curate-state")).toHaveText(/Pending|Applied in revision \d+/);
  await expect(page.getByTestId("curate-state")).toHaveText(/Applied in revision \d+/, { timeout: 60_000 });
});

test("a viewer sees no Curate tab", async ({ page }) => {
  await uploadSample(page); await actAs(page, "viewer.val"); await openNode(page, SAMPLE_PROCESS_NAME);
  await expect(page.getByRole("tab", { name: "Curate" })).toHaveCount(0);
});

test("unavailable state when the API reports Neo4j down", async ({ page }) => {
  await page.route("**/memory/*/status", (r) => r.fulfill({ json: { configured: true, available: false, revision: null, counts: {}, pending_ingests: 0 } }));
  await page.goto("/memory");
  await expect(page.getByTestId("memory-unavailable")).toContainText("Memory is unavailable");
  await expect(page.getByTestId("memory-canvas")).toHaveCount(0);
});
```

- [ ] **Step 2: Run** `cd web && npx playwright test e2e/memory.spec.ts` — expect the new tests FAIL.
- [ ] **Step 3: Implement** the components and wiring.
- [ ] **Step 4: Run** the spec, `npm run typecheck`, `npm run lint`, `make e2e` (whole suite) — expect PASS.
- [ ] **Step 5: Commit** `feat(web): memory timeline, revision diff, curation and unavailable states`.

---

### Task 16: Demo check memory step and final verification

**Files:**
- Modify: `scripts/demo_check.py`, `scripts/tests/test_demo_check.py` (if present; else create `scripts/tests/test_demo_check_memory.py`), `README.md` (Memory section: what it is, `make neo4j`, the explorer)

**Interfaces:**
- `Flow.memory()` runs after `evidence` (non-fatal to earlier steps): checks
  1. `GET /memory/{p}/status` → `available` true;
  2. `revision` ≥ number of phase steps that ran (discover + per target analysis, design, build, verify, plan);
  3. for each built target, `GET /memory/{p}/node?uid=process:<ir id>` lineage, or a graph in `lineage` mode, contains at least one `MIGRATED_TO`;
  4. search for each target process name returns a `Process` hit with that name;
  5. the latest revision's diff is non-empty;
  6. `POST /memory/{p}/curations` (note, as `sme.vicky`, on the first target process) → 202, and a new revision with phase `curation` appears within 60 s;
  7. `POST /ask` about the first target cites a uid (`citations[0].citation.uid`).
  Row names: `memory available`, `revisions per phase`, `lineage <target>`, `search <target>`, `latest diff`, `curation applied`, `ask cites memory`.
- Unit test with a fake `httpx.MockTransport`: all seven rows PASS on good responses; `memory available` FAIL stops the step with the others not run.

- [ ] **Step 1: Write the failing unit test**; **Step 2: Run** `.venv/bin/pytest scripts/tests -q` — expect FAIL.
- [ ] **Step 3: Implement** `Flow.memory` and register the step.
- [ ] **Step 4: Run the whole verification:**
  - `make test` — expect all passed (Neo4j tests included, none skipped for `NEO4J_TEST_URI`);
  - `.venv/bin/ruff check .` — expect `All checks passed!`;
  - `cd web && npm run typecheck && npm run lint` — expect no errors;
  - `make e2e` — expect all specs passed;
  - `make demo-check` — expect the `memory` rows PASS;
  - `make egress-audit` — expect exit 0 (no new host);
  - `make chart-check` — expect PASS;
  - `git status` — only intended files changed; branch `feat/gmf-deploy`.
- [ ] **Step 5: Commit** `feat(demo): memory checks in the acceptance run`.
