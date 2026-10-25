# ExiQO Migrate memory in Neo4j: versioned knowledge graph, ingest, explorer

Date: 2026-10-25. Status: approved design, sub-project 2 of the roadmap in
`docs/superpowers/specs/2026-10-24-gmf-container-deploy-design.md`. The user approved the
direction (decisions below) and asked for the remaining choices to be made without further
stops; each such choice is recorded as a ruling (`Ruling: ... — why — cost if wrong`).

## Purpose

Today the platform's knowledge lives in Postgres schema `knowledge` (`graph_nodes`,
`graph_edges`, `chunks`, `memory_items`, pgvector). It is a flat snapshot: a rescan replaces the
estate graph, a re-analysis deletes a process's chunks, and nothing links a TIBCO activity to
the .NET method generated from it. Agents retrieve decisions and patterns from it; Ask answers
from it.

This sub-project moves all of that into Neo4j and makes it the platform's memory: the old code
(the TIBCO model), the new code (the generated services), the lineage between them, and the
knowledge about both (summaries, rules, findings, design decisions, gate decisions, errors,
curations), each versioned per revision so the history of how memory changed is kept. People
browse and curate it on a new `/memory` screen; agents and Ask read it; sub-projects 3
(ExiQoian chat) and 4 (inline citations) build on its retrieval interface.

Success criteria:

1. After the seven-step demo, the project's Neo4j memory holds the old code, the new code and
   `MIGRATED_TO` lineage from every mapped activity to its generated method, and every phase
   that ran has its own revision.
2. Re-running a phase whose output did not change creates no new node or relationship
   versions; re-running an ingest job (retry or duplicate) creates no duplicates.
3. "As of revision k" and "what changed in revision k" are answered for any k, for nodes and
   relationships, from the stored versions.
4. Ask and agent retrieval read only from Neo4j, with the same floors and behaviour as today
   (an unrelated question finds nothing), and every hit carries citation metadata (uid, label,
   phase, revision, source artifact).
5. Viewers can search, browse the graph, open node details and the timeline; only gate owners
   and admin can curate, enforced by the API. A curation changes what agents retrieve on the
   next run.
6. No phase fails because Neo4j is down; memory catches up when it returns; the explorer and
   Ask say clearly that memory is unavailable.
7. No Postgres `knowledge.*` table is read or written by the platform after the one-time
   migration, and the migration loses nothing.
8. Nothing calls out of GMF's network: embeddings stay local, the graph library is bundled
   from npm, `make egress-audit` passes.

Decisions taken with the user (binding):

1. Neo4j is the memory. Graph, chunks and memory items move out of Postgres `knowledge.*` into
   Neo4j (native vector indexes and a full-text index). Postgres keeps application and job data
   only. Ask and agent retrieval read from Neo4j. Neo4j becomes required, local development
   runs it in compose, and `/readyz` includes it. Same local embeddings (bge-small, 384
   dimensions, fastembed with the hash fallback).
2. Versioned history. Every ingest creates `(:Revision {project_id, n, phase, job_id,
   iteration, at, actor})`. Every node and relationship has a stable per-project `uid`, a
   `content_hash`, `valid_from` (revision n) and `valid_to` (null = current). Unchanged hash:
   untouched; changed: close the old version (`valid_to = n`) and create a new one; vanished:
   close. As of k: `valid_from <= k AND (valid_to IS NULL OR valid_to > k)`. Changes in
   revision X: versions opened or closed at X. Curations are revisions with phase `curation`.
3. Explorer = browse and curate. Everyone (viewers included) can search, see the graph canvas,
   the timeline and the diff. Gate owners and admin can pin a note on a node, correct or reject
   a TIBCO to .NET mapping, or mark a memory item obsolete; each curation is a versioned memory
   entry that agents use on the next run. Viewers are read-only, enforced by the server.
4. Write path (approach A): after each phase completes (discover, analysis, design, build,
   verify, deploy), each gate decision and each curation, a `memory.ingest` job goes on the
   existing job queue; a worker projects that phase's outputs into Neo4j as a new revision and
   embeds new or changed text. Idempotent, retried; phases never fail because Neo4j is down.
5. Graph model: section 1.
6. Migration: a one-time job copies the Postgres `knowledge.*` content into Neo4j as revision 1
   per project, then the Postgres knowledge tables are retired along a safe path.
7. Sub-projects 3 and 4 read this memory through a retrieval interface that returns citation
   metadata (artifact, phase, uid, revision).

## 1. Graph model

### Labels

Every versioned node carries the label `:Mem` plus exactly one model label from the closed set
below, and the properties `project_id`, `uid`, `vid` (`<project_id>|<uid>|<valid_from>`),
`scope`, `content_hash`, `text_hash`, `valid_from`, `valid_to`, `phase` (of the revision that
opened the version; outside the hash), `side` (`old`, `new`,
`knowledge`), `name`, `text` (the searchable text) and, where listed, `embedding`.

| Group | Label | `uid` pattern | Key properties |
| --- | --- | --- | --- |
| Old code | `Application` | `application:<ir app id>` | dialect, modules |
| | `Process` | `process:<ir process id>` | module, package, starter_kind, complexity_band, spec_hash, source_path |
| | `Activity` | `activity:<ir process id>/<activity id>` | type, native_type, starter (bool), starter_kind |
| | `Contract` | `contract:<contract id>` | kind (`xsd`, `wsdl`, `json`), path, root_elements |
| | `SharedResource` | `resource:<resource id>` | kind (`jdbc`, `http`, `jms`, `file`); settings never stored (masked fields dropped) |
| | `SubstVar` | `substvar:<ir app id>/<name>` | type (value never stored) |
| | `Table` | `table:<name>` | |
| | `Queue`, `Topic` | `queue:<name>`, `topic:<name>` | |
| | `Endpoint` | `endpoint:old:<METHOD> <path>` or `endpoint:new:<METHOD> <path>` | method, path, side |
| | `Domain` | `domain:<domain key>` | suggested_name |
| New code | `Solution` | `solution:<stack>:<ir process id>` | stack, build_id, build_hash |
| | `CodeProject` | `codeproject:<stack>:<ir process id>/<project>` | kind (from `ProjectLayout.kind`) |
| | `CodeFile` | `codefile:<stack>:<ir process id>/<path>` | origin (`code`, `agent`, `ai`, `infra`), sha256, size |
| | `Class` | `class:<stack>:<ir process id>/<project>/<class>` | namespace (when known), file |
| | `Method` | `method:<stack>:<ir process id>/<project>/<class>.<method>` | pattern, origin |
| | `ConfigKey` | `configkey:<stack>:<ir process id>/<key>` | secret (bool; value never stored) |
| | `MessagingEntity` | `messaging:<stack>:<ir process id>/<kind>/<name>` | kind, direction |
| | `Test` | `test:<stack>:<ir process id>/<TRX name>` | kind, rule_key, outcome, duration_ms |
| Knowledge | `Chunk` | `chunk:<ir process id>:summary`, `...:rule:<rule_key>`, `...:finding:<finding_key>`, `...:design:narrative`, `...:design:decision:<slug>` | kind (`summary`, `rule`, `finding`, `design`) |
| | `Memory` | `seed:<pattern id>`, or the `memory_events` id (`mem_<12 hex>`, or the migrated memory id kept) | kind (`decision`, `pattern`, `mapping`, `note`, `seed`), source, created_by, created_at, context (JSON string) |
| | `GateDecision` | `gate:<ir process id>:<G1..G6>` | gate, state, actor, note, decided_at, spec_hash, design_hash, build_hash, evidence_hash |
| | `Error` | `error:<phase>:<ir process id>:<key>` | kind (`build`, `test`, `parity`, `deploy`), message (masked, at most 500 characters), at |
| | `Curation` | `curation:<event id>` | action, target_uid, replacement_uid, actor, reason, at |

`stack` is `dotnet` for every new-code node now; sub-project 5 adds `java`, `go`, `python`
values without new labels. Each `GateDecision` uid has one version per decision, so a gate's
decision history is its version history.

Ruling: `SubstVar` is added to the old-code labels (not in the list shown to the user) — the
substvar to `ConfigKey` lineage the user approved needs a source node — cost if wrong: one
label to drop; nothing else depends on it.

Ruling: secrets and settings values never enter the graph (`SharedResource.settings`,
`SubstVar.value`, `ConfigKey` values are not projected) — the graph is browsed by viewers and
fed to models — cost if wrong: an operator wanting values reads them from the build, which they
can already do.

### Relationships

Every relationship has `project_id`, `uid` (`<TYPE>:<src uid>-><dst uid>`), `scope`,
`content_hash`, `valid_from`, `valid_to`, and type-specific properties.

| Type | From → to | Source |
| --- | --- | --- |
| `CONTAINS` | Domain→Process, Application→Process, Process→Activity, Solution→CodeProject, CodeProject→CodeFile, CodeFile→Class, Class→Method | estate graph, solution layout |
| `CALLS` | Process→Process, Activity→Process | estate graph (`ProcessCall`) |
| `READS`, `WRITES` | Activity/Process→Table | estate graph |
| `PUBLISHES`, `CONSUMES` | Activity/Process→Queue/Topic; Method→MessagingEntity | estate graph; `DesignRecord.messaging` |
| `EXPOSES` | Process→Endpoint (old), Method→Endpoint (new) | estate graph; generated controllers |
| `USES` | Process/Activity→SharedResource/Contract/SubstVar; CodeProject→ConfigKey | estate graph, IR, `ConfigMap` |
| `MIGRATED_TO` | Activity→Method, Process→CodeProject, Process→Class (the workflow class), Contract→Class (record), SubstVar→ConfigKey, Endpoint(old)→Endpoint(new) | `MappingRow`, `ContractMap`, `ConfigMap`; property `status` (`generated`, `corrected`), `pattern`, `origin` |
| `TESTS` | Test→Method | `TestResult.rule_key` matched to `MappingRow.rule_ids` |
| `FAILED_AT` | Error→CodeFile/Method | build step logs, test failures, parity defects, plan errors |
| `ABOUT` | Chunk/Memory/GateDecision/Error/Curation→any node | the entity the knowledge describes |

### Vectors and text

- `embedding` (384 floats, bge-small via fastembed or the hash fallback, unchanged from
  `engine/knowledge/embeddings.py`) on `Chunk`, `Memory`, `Process`, `Activity`, `Class`,
  `Method`. One vector index per label (`vec_chunk`, `vec_memory`, ...; cosine, 384).
- `text` on every node; one full-text index `mem_text` over all model labels with the
  `english` analyzer.
- Embedded text per label: Process "`<name>` (`<application>`/`<module>`), `<starter kind>`
  starter: `<first 30 activity names>`"; Activity "`<name>` (`<type>`) in `<process name>`";
  Class "`<namespace>.<class>` in `<project>`"; Method "`<class>.<method>` implements
  `<activity name>` (`<pattern>`)"; Chunk and Memory their text.
- `(:MemoryMeta {key: 'embedding_backend'})` records the backend; mixing backends is refused
  as today (`KnowledgeError`), and `python -m api.app.memory_admin reembed` rebuilds vectors.

Ruling: the vector half of search scans exactly with `vector.similarity.cosine` over the
project's current nodes, and uses the label's vector index (ANN, 100×k candidates, post-filtered
by project and validity) only when the project has more than `MEMORY_EXACT_SCAN_MAX` (50 000)
current embedded nodes of that label — Neo4j 5.26 Community cannot filter a vector index query
by property before ranking, so on a shared database an ANN top-k across all projects can miss a
small project's items, while an exact scan over a GMF estate (tens of thousands of 384-float
vectors) takes well under a second — cost if wrong: slow searches on a very large project until
the threshold is lowered.

Ruling: Neo4j's cosine scores are `(1 + cos) / 2`; the store converts them back to raw cosine
(`2s − 1`) before applying today's floors (0.66 bge-small, 0.25 hash) — the floors were
calibrated on raw cosine — cost if wrong: a test pins it (Task 4).

### Versioning rules

- A revision is applied by one write transaction: either all of a revision is visible or none.
- For each scope the ingest computes the projected items, reads the current versions in that
  scope (`valid_to IS NULL`), and compares `content_hash` (hash of the label, the masked
  properties and, for relationships, the endpoint uids):
  same hash → untouched; different → old version `valid_to = n`, new version `valid_from = n`;
  absent from the projection → `valid_to = n`; new → `valid_from = n`.
- A node's new version takes over the node's current relationships from every scope: each is
  closed at n and re-created on the new version with the same uid and hash ("re-attached").
  A closed node closes its current relationships.
- The embedding is recomputed only when `text_hash` changed; otherwise it is copied from the
  previous version.
- Diff semantics for revision X: by uid, opened-and-closed at X with the same hash is a
  re-attachment and is not reported; opened only = added; closed only = removed; opened and
  closed with different hashes = changed (with the changed property keys).
- As-of reads apply the validity predicate to nodes and to relationships.

Ruling: a single `:Mem` label plus `vid` uniqueness carries all versioned nodes — Community
edition supports single-property uniqueness constraints on every edition, while node-key and
some composite constraints need Enterprise — cost if wrong: none functionally; one extra label.

Ruling: each node and relationship has exactly one owning `scope`, the first projector that
emits it (scopes: `estate`, `analysis:<pid>`, `design:<pid>`, `build:<pid>`, `verify:<pid>`,
`deploy:<pid>`, `gate:<pid>:<G>`, `decision:<event id>`, `seed`, `curation:<event id>`,
`migration:orphan`); other projectors may only point relationships at it. A relationship
whose endpoint uid does not exist is skipped and counted as `dangling` in the job result; the
next ingest of its scope tries again — vanished-closure must be per scope or an analysis
ingest would close the estate — cost if wrong: a relationship appears one revision late.

Ruling: new code is owned by the build scope (the build's `DesignRecord` supplies the mapping),
not the design scope; the design scope holds only design chunks and design decisions — "new
code" means what was generated, and a design that is never built leaves no phantom classes —
cost if wrong: the explorer shows new code only after Build, which matches the stepper.

Ruling: an ingest that projects no change creates no `Revision` — the timeline must show
change, not job traffic — cost if wrong: "when did the build ingest run" is answered by the
jobs list, not the timeline.

## 2. Ingest, retrieval and the driver

### The `memory.ingest` job

- **Handler** `memory.ingest` in `api/app/jobs/handlers.py`, registered with
  `max_attempts = MEMORY_INGEST_MAX_ATTEMPTS` (default 10).
- **Payload**: `{project_id, phase, scope, source_kind, source_job_id, source, actor,
  ingest_key, dedupe_key}`. `source` holds ids only (`process_id`, `build_id`, `plan_id`,
  `gate`, `event_id`), never text. `ingest_key` identifies the source event
  (`<phase>:<source_job_id>`, `gate:<gate row id>:<decided_at>`, `decision:<event id>`,
  `curation:<event id>`, `seed:<sha of seed_patterns.yaml>`, `migration:pg-knowledge`).
- **Enqueue points**.
  - The job worker, in the transaction that marks a job `done`, inserts the follow-up ingest
    for kinds `scan` and `discover` (phase `discover`, scope `estate`), `analyse` (`analysis`),
    `design` (`design`), `build` (`build`), `verify` (`verify`) and `plan` (`deploy`), copying
    the job's `project_id` and the payload ids.
  - `gates.decide` after commit (phase `gate`); the SME decisions that today call
    `Knowledge.record_decision` (finding decided, rule edited, domain renamed or merged) store a
    `memory_events` row and enqueue phase `decision`; a finding decision also re-ingests
    its analysis scope (today's `reindex`); project creation (phase `seed`); curations
    (phase `curation`).
  - Nothing is enqueued when `NEO4J_URI` is empty (development without Neo4j; the chart always
    sets it).
- **Dedupe**: an enqueue is skipped when a `queued` `memory.ingest` with the same `dedupe_key`
  (`<project_id>|<scope>`) exists. Projectors read the current Postgres state when they run, so
  one queued ingest covers any number of events for its scope. Gate, decision and curation
  scopes are unique per event and never coalesce.
- **Locking**: the handler takes `pg_try_advisory_lock(hashtext('memory:<project_id>'))`; if
  another ingest of the project holds it, the job is requeued after 2 s without counting an
  attempt. Revision numbers are therefore assigned one at a time per project; a uniqueness
  constraint on `Revision.rid` backs that up.
- **Idempotency**: inside the write transaction, a `Revision` with the same `ikey`
  (`<project_id>|<ingest_key>`) means the event was applied; the job returns
  `{revision: n, skipped: true}`. Neo4j's managed-transaction retries and the job queue's
  retries are both safe because the check is inside the transaction.
- **Supersession**: build, verify and deploy projectors refuse a source that is not the
  latest for its process (an older build's ingest after a newer one's) and return
  `{superseded: true}`.
- **Transaction size**: embeddings are computed before the transaction opens (batches of 256
  texts), then one write transaction runs pre-rendered `UNWIND $rows` statements in batches of
  1000 rows per label or relationship type, plus the `Revision` node.
- **Failure**: `neo4j.exceptions.ServiceUnavailable`, `SessionExpired` and `TransientError`
  raise `RetryLater` with backoff `15 s × 2^(attempt−1)`, capped at 600 s (about 1.5 hours over
  10 attempts); after the last attempt the job fails with
  `memory unavailable after 10 attempts: <exception class>`. Because projectors read current
  state, the next ingest of the scope catches up; an admin can also re-ingest everything.
- **Result**: `{revision, phase, scope, added, changed, removed, reattached, embedded,
  dangling, refused}` (`refused`: texts dropped by the raw-XML guard).

Ruling: the follow-up ingest is inserted by the job worker in the job's final transaction, not
by each service — one place, and a phase that completed always has its ingest queued — cost if
wrong: a phase whose output is written outside its job (none today) needs an explicit enqueue.

Ruling: one write transaction per revision — no partial revision is ever visible and retries
need no cleanup — cost if wrong: a very large first estate ingest needs more transaction
memory; the install guide sets `db.memory.transaction.total.max` to 2 GiB for the bundled Neo4j
and the plan's Review Focus measures a 20 000-node estate.

Ruling: the job queue gains `not_before` (delayed requeue) and per-kind `max_attempts` — the
queue retried only on worker drain; ingest needs backoff — cost if wrong: small; other kinds
keep their behaviour (`max_attempts` default stays `JOB_MAX_ATTEMPTS`).

Ruling: `memory.*` jobs are internal: the project history (`GET /history`, the only job
listing) leaves them out unless `include_internal=true`, and `GET /jobs/{id}` still serves them;
the explorer shows a pending count — the history timeline and phase totals would otherwise fill
with ingest rows — cost if wrong: one query parameter.

### Projectors

Pure functions in `engine/memory/projectors/`, each returning a `ScopedDelta` (scope, nodes,
relationships). The API side (`api/app/services/memory_ingest.py`) loads the Postgres rows and
calls them.

| Phase | Scope | Reads | Produces |
| --- | --- | --- | --- |
| discover | `estate` | applications, processes (IR), domains, `api.graph_nodes`/`graph_edges` | Application, Process, Activity, Contract, SharedResource, SubstVar, Table, Queue, Topic, Endpoint (old), Domain and their relationships |
| analysis | `analysis:<pid>` | latest analysis summary, rules, findings with decisions | Chunk (summary, rule, finding) ABOUT Process, and ABOUT each cited Activity |
| design | `design:<pid>` | latest `DesignRecord` | Chunk (design narrative; one per design decision with alternatives and reason) ABOUT Process |
| build | `build:<pid>` | the build, its files, its `DesignRecord`, test results, steps | Solution, CodeProject, CodeFile, Class, Method, ConfigKey, MessagingEntity, Endpoint (new), Test; CONTAINS, MIGRATED_TO, TESTS, USES, PUBLISHES/CONSUMES, EXPOSES; Error (failed steps, failed tests) FAILED_AT |
| verify | `verify:<pid>` | latest scenario runs of the build and their field diffs | Error (parity, one per defect diff) ABOUT Process, FAILED_AT the Method mapped from the starter activity; a Chunk (`finding`) summarising parity per scenario |
| deploy | `deploy:<pid>` | the plan | Error (deploy) FAILED_AT the infra CodeFile named in the output, else ABOUT Solution |
| gate | `gate:<pid>:<G>` | the gate row | GateDecision ABOUT Process |
| decision | `decision:<event id>` | the `memory_events` row | Memory (decision) ABOUT the process or domain in its context |
| seed | `seed` | `seed_patterns.yaml` | Memory (seed/pattern) |
| curation | `curation:<event id>` | the `memory_events` row | Curation and, per action, a Memory or a MIGRATED_TO (below) |

Ruling: parity defects attach to the method mapped from the process's starter activity, not to
the field-level code — the field diffs do not carry code positions, and the entry method is
where the response is assembled — cost if wrong: coarse `FAILED_AT` for parity, refined later
when the generator records field-to-member mapping.

Ruling: a failed build step's `FAILED_AT` targets the CodeFile whose path appears in the
compiler output (`<path>(<line>,<col>)`), else the Error is ABOUT the CodeProject — exact
positions are free in dotnet output; anything else would be guessing — cost if wrong: some
errors attach to the project instead of the file.

### Write-path security

- Every string property except identity keys (`uid`, `vid`, `project_id`, `scope`, hashes,
  `stack`, `side`, `kind`) passes `mask_sensitive` in `MemoryGraph.apply_revision` before
  hashing and storing, a single choke point, as `Knowledge` does today.
- `guard_text` (raw TIBCO process XML refused) applies to every `text`; a refused text is
  dropped from that node and counted in `refused`, not a job failure.
- Cypher text lives only in `engine/memory/cypher.py` as module constants. Labels and
  relationship types cannot be Cypher parameters, so statements per label and type are
  rendered at import time from the closed `NodeLabel` and `RelType` enums, never from input.
  Every value from a request is a parameter. A test asserts the module holds only constant
  strings and that every endpoint passes user input as parameters only.
- Every statement filters on `project_id: $project_id`; there is no cross-project query.

### Retrieval

`engine/memory/search.py` keeps today's hybrid search semantics on Neo4j:

- Keyword half: `query_terms(query)` (today's function, also dropping Lucene's English
  stopwords) and one full-text query per term,
  `CALL db.index.fulltext.queryNodes('mem_text', $term)`, filtered by project, label set and
  validity; a node must match at least `min(min_terms, len(terms))` terms (default 2), its
  rank is the sum of term scores. Terms contain only `[a-z0-9_]`, so no Lucene syntax reaches
  the query.
- Vector half: exact cosine (or ANN, per the ruling above) with the backend's floor.
- Each half normalised by its best score, combined 0.5/0.5, ties by uid, top k — unchanged.
- Default reads are current (`valid_to IS NULL`); `as_of` applies the validity predicate.
- A `Memory` targeted by a current obsolete `Curation` is excluded from agent retrieval and
  Ask; the explorer shows it struck through.

Interface for agents, Ask, ExiQoian (sub-project 3) and inline citations (sub-project 4):

```python
class Citation(BaseModel):
    uid: str; label: str; phase: str; revision: int     # valid_from of the version cited
    artifact_type: str   # process|analysis|design|build|build_file|verify_run|plan|gate|memory|curation
    artifact_id: str     # the Postgres id of that artifact (process id, build id, ...)
    process_id: str | None; path: str | None             # API process id; file path for code

class MemoryHit(BaseModel):
    uid: str; label: str; kind: str | None; text: str
    score: float; keyword_score: float; vector_score: float
    citation: Citation

class MemoryRetriever(Protocol):
    async def retrieve(self, project_id: str, query: str, *, labels: Sequence[str] = KNOWLEDGE_LABELS,
                       k: int = 8, as_of: int | None = None, min_terms: int = 2,
                       vector_floor: float | None = None) -> list[MemoryHit]: ...
```

`MemoryGraph` implements it. `Knowledge.retrieve_memory` returns `MemoryItem`s (unchanged
model, so the agents are untouched) built from `Memory` hits. Ask gathers `Chunk` and `Memory`
hits; `HitOut` gains an optional `citation`, with `id` = uid so the web keeps working.

### Driver

- The `neo4j` Python package (already a dependency), async driver: one
  `AsyncGraphDatabase.driver` per process, created in the API lifespan and in the worker
  entrypoint, held by `MemoryGraph` (`app.state.memory`, `WorkerServices.memory`) and closed on
  shutdown. Sessions per operation on `NEO4J_DATABASE` (default `neo4j`; Community has one
  user database), `execute_read`/`execute_write` managed transactions, `connection_timeout` 5 s,
  read query timeout 30 s, write 300 s.
- `MemoryGraph.ensure_schema()` creates every constraint and index with `IF NOT EXISTS` at
  startup of the API and the worker; if Neo4j is down it logs and the first operation retries
  it.

Constraints and indexes: unique `Mem.vid`, `Revision.rid`, `Revision.ikey`, `MemoryMeta.key`;
range indexes `Mem(project_id, uid)`, `Mem(project_id, scope, valid_to)`,
`Mem(project_id, valid_from)`, `Mem(project_id, valid_to)`, `Mem(project_id, target_uid)`,
`Revision(project_id, n)`; per
relationship type `(project_id, valid_from)`, `(project_id, valid_to)`, `(project_id, uid)`;
the six vector indexes; the `mem_text` full-text index.

Ruling: the API's Postgres advisory lock serialises ingests per project, not a Neo4j lock —
the job queue already uses Postgres locks and Neo4j Community has no advisory locks — cost if
wrong: none while Postgres is the queue.

Ruling: the async driver is used directly from the engine's `MemoryGraph`, not wrapped in
threads like today's psycopg store — API and worker are asyncio, and the driver is
async-native — cost if wrong: an engine script needing memory runs it under `asyncio.run`.

## 3. The `/memory` explorer

A secondary screen like Settings or History, linked from the top bar (`Memory`, lucide
`Network` icon, between History and Tokenomics). It works on the project picked in the
`ProjectSelector`. URL state: `/memory?uid=<uid>&rev=<n>&q=<query>&labels=...&side=...`, so a
view can be shared.

Layout, enterprise and roomy: a 24 px page gutter, white cards on the app's neutral
background, 13.5–15 px type, generous row heights, the same plum and lilac accents as the
stepper.

- **Header row**: title "Memory", a status pill "Revision 42 · discover · 3 min ago"
  (`GET /memory/{p}/status`), a muted "3 ingests pending" when the queue has some, and for admin
  a "Re-ingest project" button.
- **Left rail (300 px)**: search box (debounced 250 ms, `GET /memory/{p}/search`); filters:
  label checkboxes grouped Old code, New code, Knowledge; side segmented control All / Old /
  New; phase select (any, discover, analysis, design, build, verify, deploy, gate, curation);
  results list with label chip, name, a two-line snippet and the score bar. Clicking a result
  focuses the canvas on it.
- **Canvas (flexible)**: the focused node's neighbourhood (depth 1 or 2,
  `GET /memory/{p}/graph`), at most 400 nodes, with a "Showing 400 of 1 312: narrow the filters"
  banner when truncated. Node colour by group (old code plum, new code teal, knowledge amber),
  shape by label family, a halo on curated nodes. Two modes: **Neighbourhood** (cytoscape
  `cose`) and **Lineage** (elkjs layered, old code left to new code right, `MIGRATED_TO` edges
  thick; rejected mappings dashed red; corrected ones solid green). Selecting a node highlights
  its lineage chain (MIGRATED_TO, TESTS, FAILED_AT in both directions) and dims the rest.
  Zoom, fit and "expand node" (double-click) controls.
- **Detail drawer (440 px, right)** for the selected node, tabs:
  - Overview: label, uid (copy), properties table, the revision it was last changed in.
  - History: every version (revision, phase, actor, time) with the changed properties of each
    against the previous one.
  - Knowledge: the Chunk, Memory, GateDecision, Error and Curation nodes ABOUT it, newest
    first, obsolete ones struck through.
  - Lineage: incoming and outgoing MIGRATED_TO/TESTS/FAILED_AT, each with status.
  - Curate (curators only; a viewer sees no tab, and the API refuses them): "Pin a note",
    "Reject mapping" and "Correct mapping" (on an Activity, Process, Contract, SubstVar with
    MIGRATED_TO; correcting picks the right Method or Class from a search box), "Mark obsolete"
    (on a Memory). Each takes a required reason; submitted curations show "pending" until their
    revision lands.
- **Timeline (bottom, 120 px)**: one tick per revision coloured by phase, labelled on hover
  (revision, phase, actor, time, counts). Dragging the handle sets `rev` (as-of): the canvas,
  drawer and search re-read as of that revision, and a banner "Viewing revision 17 of 42 ·
  Back to current" appears. "Show changes" opens the **diff panel** for the selected revision:
  added, changed and removed nodes and relationships grouped by label, each clickable; the
  canvas outlines added green, changed amber, removed red (removed shown as ghosts from the
  previous revision).
- **Unavailable state**: when `status.available` is false, the page shows a single card:
  "Memory is unavailable. Neo4j is not reachable from the platform. Phases keep running and
  memory catches up when it returns." with a Retry button; no canvas, no errors in the console.
  When `NEO4J_URI` is not set: "Memory is not configured for this install."
- **Empty state**: "No memory yet for this project. It fills in as soon as the estate is
  scanned."

Ruling: cytoscape.js (3.34.3, already bundled and used by `DomainGraph`) with elkjs (0.10.0,
already bundled) — no new dependency, canvas rendering handles a few thousand elements, typed,
no CDN, passes the egress audit as it does today; sigma.js and react-force-graph would add
WebGL dependency trees (graphology, d3, three) for scale we cap at 400 nodes per view — cost if
wrong: swapping the renderer touches one component (`MemoryCanvas`).

### API (`api/app/routers/memory.py`, all under the router guard)

| Method and path | Who | Returns |
| --- | --- | --- |
| `GET /memory/{project_id}/status` | any signed-in | `{configured, available, revision, at, phase, counts: {label: n}, pending_ingests, embedding_backend}` |
| `GET /memory/{project_id}/search?q=&labels=&side=&phase=&as_of=&k=` | any | `{hits: [MemoryHit]}` (k ≤ 50) |
| `GET /memory/{project_id}/graph?uid=&depth=&labels=&side=&as_of=&mode=&limit=` | any | `{nodes, edges, truncated, total}` (depth 1–2, limit ≤ 400; without uid: the project's top-level nodes: Domains, Applications, Solutions) |
| `GET /memory/{project_id}/node?uid=&as_of=` | any | `{node, versions, about, lineage, curations}` |
| `GET /memory/{project_id}/revisions?before=&limit=` | any | `{revisions: [...]}` (limit ≤ 200) |
| `GET /memory/{project_id}/revisions/{n}/diff` | any | `{revision, nodes: {added, changed, removed}, relationships: {added, changed, removed}}` |
| `POST /memory/{project_id}/curations` | gate owner role or admin | 202 `{event_id, job_id}` |
| `POST /memory/{project_id}/reingest` | admin | 202 `{job_ids}` |

All take the project from the path and 404 when the project does not exist in Postgres. `uid`
is a query parameter (uids contain `/` and `:`). Neo4j down: reads answer 503
`{"detail": "memory unavailable: <exception class>"}`, and `status` answers 200 with
`available: false`.

Curation request: `{action: "note" | "reject_mapping" | "correct_mapping" | "obsolete",
target_uid, reason (1–2000 characters), text? (note body), replacement_uid? (correct_mapping)}`.
The API validates the target exists and is current and fits the action (a mapping action on a
node with an outgoing `MIGRATED_TO`; `obsolete` on a Memory; the replacement a Method or Class
of the same project), stores a `memory_events` row in Postgres (kind `curation`, masked text,
actor, time),
writes an `audit_log` entry `memory curated`, and enqueues the ingest. The projector then
creates:

- note → `Curation` ABOUT target and `Memory {kind: note, source: curation}` ABOUT target;
- reject_mapping → `Curation {target_uid: <MIGRATED_TO uid>}` ABOUT the source node, and
  `Memory {kind: mapping}` "Do not map `<activity>` to `<method>`: `<reason>`"; lineage reads
  mark that relationship `rejected`;
- correct_mapping → as reject, plus a new `MIGRATED_TO {status: corrected, curated_by}` to the
  replacement (owned by the curation scope) and `Memory {kind: mapping}` "Map `<activity>` to
  `<replacement>`: `<reason>`";
- obsolete → `Curation {target_uid: <memory uid>}` ABOUT the Memory; reads treat a Memory
  targeted by a current obsolete curation as `obsolete` (retrieval and Ask leave it out).

Ruling: SME decisions and curations are stored in Postgres (`memory_events`: id, project_id,
kind `decision`|`curation`, action, text, context, target_uid, replacement_uid, actor,
created_at; text masked) as well as projected — they are human decisions with no other
Postgres source, belong with the audit trail, and must survive Neo4j being down or restored
from an older dump; every projector then reads Postgres only, so a re-ingest rebuilds all of
memory — cost if wrong: one small table.

Ruling: a curation never edits another scope's items; rejections and obsolete marks are
recorded on the `Curation` node and applied at read time — keeps the one-owner-per-scope invariant, so the next
build ingest cannot silently undo a curation — cost if wrong: lineage queries join curations
(cheap, indexed by project).

Ruling: curator = any role in `WRITE_ROLES` (admin and the five gate-owner roles), enforced by
a `require_curator` dependency in both auth modes (unlike other dev-mode writes); a `viewer.val`
dev persona (role `viewer`) is added so the read-only path is exercisable locally and in
Playwright — cost if wrong: the persona menu grows by one.

## 4. Migration, failure handling, security, delivery, testing

### Migration from Postgres `knowledge.*`

- `memory.migrate` job (and `python -m api.app.memory_admin migrate`) runs once per install:
  the API lifespan enqueues it when schema `knowledge` exists and `knowledge.meta` has no
  `neo4j_migrated_at`. While it is queued or running, `memory.ingest` jobs requeue themselves
  (5 s, attempt not counted), so the migration is each project's revision 1.
- Per project it builds one revision (`phase: migration`, `ikey: migration:pg-knowledge`):
  - `knowledge.memory_items` other than seeds are copied into `memory_events` (kind
    `decision`, id kept, the item's own kind in `context.kind`) and projected by the decision
    projector as `Memory` nodes whose uid is that id, so a re-ingest rebuilds them; seed items
    (`seed:<project>:<pattern>`) are produced by the seed projector as `seed:<pattern>`.
    Kind, text, context, source, created_by and created_at are kept; embeddings are reused when
    `knowledge.meta.embedding_backend` equals the active backend, else re-embedded.
  - The estate graph and chunks are produced by the estate, analysis, design, build, verify,
    deploy and gate projectors from the Postgres application tables, which they are derived
    from; each `knowledge.chunks` row is then checked: if no projected Chunk has its (masked)
    text, it is copied as a `Chunk {scope: migration:orphan}` ABOUT its process.
- Verification before retiring: per project, every memory item and every chunk text is found
  in Neo4j; on any miss the job fails, names the missing ids, and leaves Postgres untouched.
- Retire: on success the job runs `ALTER SCHEMA knowledge RENAME TO knowledge_retired` and
  records `neo4j_migrated_at` in `knowledge_retired.meta`. No code reads either schema
  afterwards. Rollback to the previous release: `ALTER SCHEMA knowledge_retired RENAME TO
  knowledge` (RUNBOOK). A later release drops `knowledge_retired`.
- `engine/knowledge/store.py`, `schema.sql` and `search.py` are deleted; `embeddings.py`,
  `memory.py` (`MemoryItem`, seed loading) and `ask.py` stay, rewired. Alembic `0005` already
  guards on `to_regclass` and keeps working.

Ruling: chunks and graph are re-derived, memory items copied — re-derived uids reconcile with
later ingests (copied hash-based chunk ids would duplicate on the next analysis), while memory
items have no other source; the orphan check makes the "nothing lost" claim verifiable — cost
if wrong: an orphan chunk shows under `migration:orphan` until its process is re-analysed.

Ruling: retire by renaming, not dropping — reversible for one release — cost if wrong: one
schema of dead rows until the next release.

### Failure handling

- **Neo4j down during a phase**: the phase finishes; its follow-up ingest retries with backoff
  (section 2); agent retrieval returns no memory and logs "migration memory unavailable" in
  the job log, as today.
- **Ask**: 503 `memory unavailable: <class>`; the web shows "Memory is unavailable right now;
  try again shortly." Not configured: 503 `memory not configured`.
- **Explorer**: the unavailable card (section 3).
- **`/readyz`**: Neo4j now gates readiness when `NEO4J_URI` is set, with a grace: a failing
  check reports `degraded: <class> (down <s> s)` and still passes for
  `NEO4J_READY_GRACE_SECONDS` (default 900) after the first failure seen by that pod, then
  `down: <class>` fails readiness. `/system/status` shows the same.
- **Backup**: the stop-dump-start CronJob from sub-project 1 is the strategy (Community has no
  online backup). `values.gmf.example.yaml` enables it; the chart's schema requires either
  `neo4jBackup.enabled` or a non-empty `neo4jBackup.disabledReason` when the bundled Neo4j is
  on. Ingests during the window retry; the readiness grace covers the window.
- **Restore from an older dump**: memory is behind Postgres; `POST /memory/{p}/reingest`
  (admin) re-projects every scope as one revision, curations included (from Postgres).

Ruling: Neo4j gates readiness with a grace instead of immediately — the user asked for it to
gate now that it is load-bearing, but every API pod shares it, so an immediate gate would turn
the nightly dump into a full outage; the grace keeps short outages degraded and long ones
visible to Kubernetes — cost if wrong: during a long Neo4j outage all API pods go unready
together, which is the stated intent; `neo4j.gatesReadiness: false` in values turns gating
off.

### Security

- `mask_sensitive` on every stored string (write path above); masked again for curation text
  before Postgres.
- Parameterized Cypher only; labels and types from closed enums; no string-built Cypher from
  input (test-enforced).
- Per-project isolation: every Cypher statement constrains `project_id`; every route checks
  the project exists; a test seeds two projects and asserts no endpoint returns the other's
  uids.
- Viewers read-only, server-side (`require_curator`, `require_admin` for re-ingest).
- Request limits: `q` ≤ 500 characters, `k` ≤ 50, `depth` ≤ 2, `limit` ≤ 400, uid ≤ 512
  characters.
- Neo4j credentials as in sub-project 1 (`NEO4J_PASSWORD_FILE`); no value logged.

### Delivery

- `docker-compose.yml`: `neo4j` image aligned to `neo4j:5.26.32` (the chart's version), with
  `NEO4J_server_memory_heap_max__size` 1G and usage reporting off; new `neo4j-test` service
  (profile `test`, port 7688, tmpfs data, password `exiqo-test-neo4j`).
- `Makefile`: `neo4j` (start the dev Neo4j), `neo4j-test`; `make test` starts `neo4j-test` and
  exports `NEO4J_TEST_URI=bolt://localhost:7688` and `NEO4J_TEST_PASSWORD` unless set; `make e2e`
  and `make demo-check` start `neo4j`. Without Docker they print how to point at a Neo4j.
- `.env.example`: `NEO4J_URI=bolt://localhost:7687`, `NEO4J_PASSWORD=exiqo-dev-neo4j`,
  `NEO4J_DATABASE`, `NEO4J_READY_GRACE_SECONDS`, `MEMORY_INGEST_MAX_ATTEMPTS`,
  `MEMORY_EXACT_SCAN_MAX`.
- Helm: configmap gains `NEO4J_DATABASE`, `NEO4J_READY_GRACE_SECONDS`,
  `NEO4J_GATES_READINESS`; schema requires `neo4j.enabled` or `neo4j.uri`; the backup rule
  above; the Neo4j subchart config sets `db.memory.transaction.total.max: 2g` and
  `dbms.usage_report.enabled: false`. NetworkPolicies already allow api and worker to Neo4j 7687
  (sub-project 1); a helm-unittest pins it. No new egress.
- `compose.pilot.yml`: Neo4j image aligned to 5.26.32, same settings.
- Docs: `GMF-INSTALL.md` sections 9 (backup and restore) and 11 (troubleshooting: memory
  unavailable, ingest failing) updated; `RUNBOOK.md` gains "Memory (Neo4j)": re-ingest,
  re-embed, the migration and its rollback, reading ingest job results.

### Testing

- How tests get Neo4j: `NEO4J_TEST_URI` (+ `NEO4J_TEST_USER`, `NEO4J_TEST_PASSWORD`).
  Fixtures skip with the reason "NEO4J_TEST_URI is not set: run make test (starts neo4j-test)
  or point it at a Neo4j 5.26" only when it is unset; `make test` and `make kind-e2e` provide
  it. `MemoryGraph.reset()` refuses unless its URI equals `NEO4J_TEST_URI`. API tests that do
  not need memory keep `NEO4J_URI` empty, so the existing suite's behaviour is unchanged.
- Unit (no Neo4j): projectors (each phase, uids, hashes stable across runs, masking, guard),
  diff classification, query term handling, Cypher-constant audit.
- Integration (real Neo4j): schema idempotence, apply/no-op/change/vanish/re-attach, as-of,
  diff, idempotent ingest key, concurrent ingests of one project serialised, hybrid search
  floors and isolation, migration with verification and rename, curations end to end, retry
  on Neo4j down (driver pointed at a closed port).
- API: every `/memory` route, viewer 403 on curation, admin-only re-ingest, 404 on unknown
  project, cross-project isolation, `/readyz` grace.
- Web e2e (`web/e2e/memory.spec.ts`): TopBar link, search finds a process after the upload
  flow, canvas renders nodes, drawer tabs, lineage highlight after a build, timeline diff,
  curation as `sme.vicky` shows pending then applied, `viewer.val` sees no Curate tab, no
  request leaves the origin.
- `scripts/demo_check.py` gains a `memory` step after `evidence`: status available, revision
  ≥ the number of phases run, at least one `MIGRATED_TO` per built target, search for each
  target process name returns it, the latest revision's diff is non-empty, a note curation by
  `sme.vicky` lands in a new revision within 60 s, and Ask cites a uid.

## Out of scope

- ExiQoian chat (sub-project 3) and inline citations (sub-project 4) — they consume
  `MemoryRetriever`.
- Java, Go and Python stacks (sub-project 5) — only the `stack` property is prepared.
- Re-entry iterations (sub-project 6) — `Revision.iteration` is the design record version for
  now; sub-project 6 defines it.
- Neo4j Enterprise features (clustering, online backup, multiple databases).
- Dropping `knowledge_retired` (next release) and removing pgvector from the Postgres
  prerequisites (it stays harmless until then).
- Graph analytics (GDS), community detection, or editing the graph beyond the four curations.
