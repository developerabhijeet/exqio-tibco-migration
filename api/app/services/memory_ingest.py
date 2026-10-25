"""The ``memory.ingest`` job: project one phase's Postgres state into the Neo4j memory graph.

Spec section 2. The job worker queues an ingest in the transaction that marks a phase job
``done`` (:data:`INGEST_AFTER`, :func:`followup_payload`); gate decisions and project creation
queue one with :func:`enqueue_ingest`. Nothing is queued when ``NEO4J_URI`` is empty.

The payload is ``{project_id, phase, scope, source_kind, source_job_id, source, actor,
ingest_key, dedupe_key}``; ``source`` holds ids only. A per-process ``scope`` names the API
process id (``analysis:<process id>``, ruling R18): it is what :func:`dedupe_key` coalesces on;
the projector writes the IR-id scope it owns (``analysis:<ir id>``). Build, verify and deploy
ingests also key on their build or plan, so a newer build's ingest is never folded into an older
one's, which would then be superseded.

:func:`ingest` takes ``pg_try_advisory_lock(hashtext('memory:<project_id>'))`` on its own
connection (busy: back to the queue in 2 s, the attempt not counted), loads the source rows with
the ``load_*`` functions, calls the phase's projector and applies the delta as one revision.
Neo4j being unreachable raises ``RetryLater`` with backoff ``min(600, 15 * 2 ** (attempt - 1))``;
after the last attempt the job fails with ``memory unavailable after <n> attempts: <class>``.
"""
from __future__ import annotations

import hashlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, get_args

from neo4j.exceptions import ServiceUnavailable, SessionExpired, TransientError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.app.jobs.locks import try_advisory_lock
from api.app.jobs.registry import RetryLater, WorkerServices
from api.app.jobs.runner import JobContext, enqueue
from api.app.models import (
    Application,
    Build,
    BuildFile,
    DesignRecord,
    Domain,
    FieldDiff,
    Gate,
    GraphEdge,
    GraphNode,
    Job,
    MemoryEvent,
    Plan,
    Process,
    Scenario,
    TestResult,
)
from api.app.services import evidence as evidence_svc
from api.app.services import gates as gate_svc
from api.app.services.analysis import chunk_entries
from engine.domains.models import Graph
from engine.dotnet_gen.models import DesignRecord as EngineDesign
from engine.ir.models import Application as IrApplication
from engine.knowledge.memory import SEED_FILE, load_seed_patterns
from engine.memory.graph import MemoryGraph, MemoryUnavailable
from engine.memory.model import Phase, RevisionMeta, ScopedDelta
from engine.memory.projectors.analysis import ChunkInput, ProcessRef, project_analysis
from engine.memory.projectors.build import (
    STACK,
    BuildFileInput,
    BuildInput,
    StepInput,
    TestInput,
    project_build,
)
from engine.memory.projectors.deploy import PlanInput, project_deploy
from engine.memory.projectors.design import project_design
from engine.memory.projectors.estate import DomainInput, project_estate
from engine.memory.projectors.events import EventInput, project_event
from engine.memory.projectors.gate import GateInput, project_gate
from engine.memory.projectors.seed import project_seed
from engine.memory.projectors.verify import DefectInput, ParityInput, RunInput, project_verify

if TYPE_CHECKING:
    from api.app.config import Settings

log = logging.getLogger(__name__)

INGEST_KIND = "memory.ingest"
SYSTEM_ACTOR = "system"
LOCK_BUSY_DELAY_S = 2
BACKOFF_BASE_S = 15
BACKOFF_MAX_S = 600
SUPERSEDED = {"superseded": True}
UNAVAILABLE = (ServiceUnavailable, SessionExpired, TransientError, MemoryUnavailable)

# Phase job kind -> (memory phase, payload scope). ``{process}`` is the API process id.
INGEST_AFTER: dict[str, tuple[Phase, str]] = {
    "scan": ("discover", "estate"),
    "discover": ("discover", "estate"),
    "analyse": ("analysis", "analysis:{process}"),
    "design": ("design", "design:{process}"),
    "build": ("build", "build:{process}"),
    "verify": ("verify", "verify:{process}"),
    "plan": ("deploy", "deploy:{process}"),
}
SOURCE_IDS = ("process_id", "build_id", "plan_id", "gate", "event_id")
# Phases whose source is one build or plan: its ingest never coalesces with another one's.
_SOURCE_OF = {"build": "build_id", "verify": "build_id", "deploy": "plan_id"}


def dedupe_key(project_id: str | None, scope: str, source: dict[str, Any] | None = None,
               phase: str | None = None) -> str:
    """``<project_id>|<scope>``, the one dedupe key of every memory ingest (ruling R18); build,
    verify and deploy add ``|<build or plan id>``."""
    key = f"{project_id}|{scope}"
    own = _SOURCE_OF.get(phase or "")
    if own and source and source.get(own):
        key += f"|{source[own]}"
    return key


def _payload(*, project_id: str | None, phase: Phase, scope: str, source: dict[str, Any],
             actor: str, ingest_key: str, source_kind: str, source_job_id: str | None) -> dict[str, Any]:
    return {"project_id": project_id, "phase": phase, "scope": scope, "source_kind": source_kind,
            "source_job_id": source_job_id, "source": source, "actor": actor,
            "ingest_key": ingest_key,
            "dedupe_key": dedupe_key(project_id or source.get("process_id"), scope, source, phase)}


def followup_payload(kind: str, job_id: str, project_id: str | None, process_id: str | None,
                     payload: dict[str, Any]) -> dict[str, Any] | None:
    """The ingest a finished ``kind`` job queues, or ``None`` for kinds that change no memory."""
    spec = INGEST_AFTER.get(kind)
    if spec is None:
        return None
    phase, template = spec
    source = {k: payload[k] for k in SOURCE_IDS if payload.get(k)}
    process = source.get("process_id") or process_id
    if process:
        source["process_id"] = process
    if "{process}" in template and not process:
        log.warning("%s job %s names no process; no memory ingest queued", kind, job_id)
        return None
    scope = template.format(process=process)
    return _payload(project_id=project_id, phase=phase, scope=scope, source=source,
                    actor=SYSTEM_ACTOR, ingest_key=f"{phase}:{job_id}", source_kind=kind,
                    source_job_id=job_id)


async def enqueue_ingest(sessionmaker: async_sessionmaker[AsyncSession], settings: Settings, *,
                         project_id: str, phase: Phase, scope: str, source: dict[str, Any],
                         actor: str, ingest_key: str) -> str | None:
    """Queue an ingest outside a phase job (gates, seeds); ``None`` when memory is not
    configured or a queued ingest of the scope already covers it."""
    if settings.neo4j_uri == "":
        return None
    payload = _payload(project_id=project_id, phase=phase, scope=scope, source=source, actor=actor,
                       ingest_key=ingest_key, source_kind=phase, source_job_id=None)
    return await enqueue(sessionmaker, INGEST_KIND, payload, project_id=project_id,
                         process_id=source.get("process_id"))


def seed_ingest_key() -> str:
    return f"seed:{hashlib.sha256(SEED_FILE.read_bytes()).hexdigest()[:12]}"


async def open_memory(settings: Settings) -> MemoryGraph | None:
    """The process's Neo4j memory graph when ``NEO4J_URI`` is set, with its schema ensured. Neo4j
    being down at start is logged and leaves ``schema_ok`` false: the first ingest or read then
    ensures the schema (``MemoryGraph.ensure_ready``) before it runs (spec section 2, Driver)."""
    if not settings.neo4j_uri:
        return None
    memory = MemoryGraph.connect(settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password,
                                 settings.neo4j_database, exact_scan_max=settings.memory_exact_scan_max)
    try:
        await memory.ensure_schema()
    except Exception as exc:  # noqa: BLE001  start without memory; ingests retry
        log.error("memory schema not ensured (Neo4j unavailable at %s): %s: %s", settings.neo4j_uri,
                  type(exc).__name__, exc)
    return memory


# ------------------------------------------------------------------ loaders
class Superseded(Exception):
    """The source is not the latest build or plan of its process."""


def process_ref(p: Process) -> ProcessRef:
    return ProcessRef(ir_id=p.ir_id, api_id=p.id, name=p.name)


async def _process(s: AsyncSession, source: dict[str, Any]) -> Process:
    process = await s.get(Process, source.get("process_id") or "")
    if process is None:
        raise ValueError(f"process {source.get('process_id')!r} not found")
    return process


async def load_estate(s: AsyncSession, project_id: str) -> tuple[list[IrApplication], list[DomainInput],
                                                                  Graph, list[ProcessRef]]:
    """The parsed applications (their processes from ``processes.ir``), the domains, the estate
    graph and the process refs of a project."""
    procs = (await s.execute(select(Process).where(Process.project_id == project_id)
                             .order_by(Process.ir_id))).scalars().all()
    by_app: dict[str, list[dict[str, Any]]] = {}
    for p in procs:
        by_app.setdefault(p.application_id, []).append(p.ir)
    apps = [IrApplication.model_validate({**a.ir, "processes": by_app.get(a.id, [])})
            for a in (await s.execute(select(Application).where(Application.project_id == project_id)
                                      .order_by(Application.app_key))).scalars()]
    domains = [DomainInput(key=d.key, name=d.name, suggested_name=d.suggested_name,
                           ir_process_ids=list(d.process_ids or []))
               for d in (await s.execute(select(Domain).where(Domain.project_id == project_id)
                                         .order_by(Domain.key))).scalars()]
    nodes = (await s.execute(select(GraphNode).where(GraphNode.project_id == project_id)
                             .order_by(GraphNode.id))).scalars().all()
    edges = (await s.execute(select(GraphEdge).where(GraphEdge.project_id == project_id)
                             .order_by(GraphEdge.id))).scalars().all()
    graph = Graph.model_validate({
        "nodes": [{"id": n.node_id, "kind": n.kind, "name": n.name, "attrs": n.attrs or {}} for n in nodes],
        "edges": [{"src": e.src, "dst": e.dst, "kind": e.kind, "attrs": e.attrs or {}} for e in edges]})
    return apps, domains, graph, [process_ref(p) for p in procs]


async def load_analysis(s: AsyncSession, process: Process) -> list[ChunkInput]:
    """Today's ``analysis.chunk_items`` texts with their keys and cited activity ids."""
    analysis = await gate_svc.latest_analysis(s, process.id)
    summary = (analysis.summary or {}).get("text") if analysis else None
    rules = await gate_svc.load_rules(s, process.id)
    findings = await gate_svc.load_findings(s, process.id)
    return [ChunkInput(kind=kind, key=key, text=text, activity_ids=cited)  # type: ignore[arg-type]
            for kind, key, text, cited in chunk_entries(process.name, summary, rules, findings)]


async def _latest_design(s: AsyncSession, process_id: str) -> DesignRecord | None:
    return await s.scalar(select(DesignRecord).where(DesignRecord.process_id == process_id)
                          .order_by(DesignRecord.version.desc(), DesignRecord.created_at.desc()).limit(1))


def _engine_design(row: DesignRecord) -> EngineDesign:
    design = EngineDesign.model_validate(row.record)
    if not (design.narrative or "").strip() and row.narrative:
        design = design.model_copy(update={"narrative": row.narrative})
    return design


async def load_design(s: AsyncSession, process: Process) -> tuple[EngineDesign, str] | None:
    row = await _latest_design(s, process.id)
    return (_engine_design(row), row.id) if row else None


async def _latest_build(s: AsyncSession, process: Process, build_id: str) -> Build:
    """The build, unless a newer finished build of the process exists whose build job ended
    ``done`` (and so queued an ingest of its own): then :class:`Superseded`. A newer build whose
    job crashed queued no ingest, so it supersedes nothing."""
    build = await s.get(Build, build_id)
    if build is None or build.process_id != process.id:
        raise ValueError(f"build {build_id!r} not found for process {process.id}")
    newer = await s.scalar(
        select(Build.id).join(Job, Job.id == Build.job_id)
        .where(Build.process_id == process.id, Build.id != build.id,
               Build.status.in_(evidence_svc.FINISHED_BUILDS), Job.status == "done",
               Build.created_at > build.created_at)
        .limit(1))
    if newer is not None:
        raise Superseded
    return build


async def _build_design(s: AsyncSession, build: Build) -> EngineDesign | None:
    row = await s.get(DesignRecord, build.design_id) if build.design_id else None
    row = row or await _latest_design(s, build.process_id)
    return _engine_design(row) if row else None


def _starter(process: Process) -> dict[str, Any]:
    return (process.ir or {}).get("starter") or {}


async def load_build(s: AsyncSession, process: Process, build_id: str) -> BuildInput | None:
    """The build, its files, design, test results and steps; raises :class:`Superseded` when it
    is not the process's latest finished build."""
    build = await _latest_build(s, process, build_id)
    design = await _build_design(s, build)
    if design is None:
        return None
    files = (await s.execute(select(BuildFile).where(BuildFile.build_id == build.id)
                             .order_by(BuildFile.path))).scalars().all()
    tests = (await s.execute(select(TestResult).where(TestResult.build_id == build.id)
                             .order_by(TestResult.name))).scalars().all()
    app = await s.get(Application, process.application_id)
    starter = _starter(process)
    return BuildInput(
        build_id=build.id, build_hash=build.build_hash or "", design=design,
        files=[BuildFileInput(path=f.path, origin=f.origin, sha256=f.sha256 or "", size=f.size or 0)
               for f in files],
        tests=[TestInput(name=t.name, kind=t.kind or "other", rule_key=t.rule_key, outcome=t.outcome,
                         duration_ms=int(t.duration_ms or 0), message=t.message or "") for t in tests],
        steps=[StepInput(name=str(st.get("name", "")), ok=bool(st.get("ok")), log=str(st.get("log") or ""))
               for st in build.steps or []],
        starter_activity_id=starter.get("activity_id"), starter_kind=starter.get("kind"),
        starter_config=starter.get("config") or {}, app_id=app.app_key if app else "")


def _text(value: Any) -> str:
    return "" if value is None else value if isinstance(value, str) else str(value)


async def load_verify(s: AsyncSession, process: Process,
                      build_id: str) -> tuple[ParityInput, str | None]:
    """The latest scenario runs of the build with their defect diffs, and the Method uid mapped
    from the starter activity; raises :class:`Superseded` for an older build."""
    build = await _latest_build(s, process, build_id)
    runs = []
    for run in await evidence_svc.latest_verify_runs(s, build.id):
        scenario = await s.get(Scenario, run.scenario_id)
        diffs = (await s.execute(select(FieldDiff).where(FieldDiff.run_id == run.id,
                                                         FieldDiff.classification == "defect")
                                 .order_by(FieldDiff.field, FieldDiff.id))).scalars().all()
        runs.append(RunInput(run_id=run.id, scenario_key=scenario.key if scenario else run.scenario_id,
                             status=run.status, parity=run.parity,
                             defects=[DefectInput(field=d.field, expected=_text(d.expected),
                                                  actual=_text(d.actual), finding_key=d.finding_key or "",
                                                  note=d.note or "") for d in diffs]))
    starter_method = None
    design = await _build_design(s, build)
    activity = _starter(process).get("activity_id")
    if design is not None and activity:
        row = next((r for r in design.mapping if r.activity_id == activity), None)
        if row is not None:  # the build projector's Method uid
            starter_method = f"method:{STACK}:{process.ir_id}/{row.project}/{row.class_name}.{row.method}"
    return ParityInput(build_id=build.id, runs=runs), starter_method


async def load_deploy(s: AsyncSession, process: Process, plan_id: str) -> PlanInput:
    """The plan and the infra files of its build; raises :class:`Superseded` when a newer plan of
    the process exists."""
    plan = await s.get(Plan, plan_id)
    if plan is None:
        raise ValueError(f"plan {plan_id!r} not found")
    latest = await s.scalar(select(Plan.id).join(Build, Build.id == Plan.build_id)
                            .where(Build.process_id == process.id)
                            .order_by(Plan.created_at.desc(), Plan.id.desc()).limit(1))
    if latest != plan.id:
        raise Superseded
    infra = (await s.execute(select(BuildFile.path).where(BuildFile.build_id == plan.build_id,
                                                          BuildFile.origin == "infra")
                             .order_by(BuildFile.path))).scalars().all()
    return PlanInput(plan_id=plan.id, status=plan.status, output=plan.output or "",
                     infra_paths=list(infra))


async def load_gate(s: AsyncSession, process: Process, gate: str) -> GateInput | None:
    row = await s.scalar(select(Gate).where(Gate.process_id == process.id, Gate.gate == gate))
    if row is None:
        return None
    return GateInput(gate=row.gate, state=row.state, actor=row.decided_by or SYSTEM_ACTOR,
                     note=row.note or "", decided_at=row.decided_at, spec_hash=row.spec_hash,
                     design_hash=row.design_hash, build_hash=row.build_hash,
                     evidence_hash=row.evidence_hash, gate_row_id=row.id)


async def load_event(s: AsyncSession, memory: MemoryGraph, project_id: str,
                     event_id: str) -> tuple[EventInput, str | None, str | None]:
    """The ``memory_events`` row, with the label and name of the node it is about."""
    row = await s.get(MemoryEvent, event_id)
    if row is None or row.project_id != project_id:
        raise ValueError(f"memory event {event_id!r} not found in project {project_id}")
    event = EventInput(id=row.id, kind=row.kind, action=row.action, text=row.text,  # type: ignore[arg-type]
                       context=row.context or {}, target_uid=row.target_uid,
                       replacement_uid=row.replacement_uid, actor=row.actor, created_at=row.created_at)
    subject = row.target_uid or ""
    if subject.startswith("MIGRATED_TO:"):  # a mapping curation: the old side's node
        subject = subject.removeprefix("MIGRATED_TO:").partition("->")[0]
    node = await memory.current_node(project_id, subject) if subject else None
    return event, node.label if node else None, node.name if node else None


def load_seed() -> list[dict[str, Any]]:
    return load_seed_patterns()


# ------------------------------------------------------------------ the job
@dataclass
class _Projected:
    deltas: list[ScopedDelta]
    process: Process | None = None


Projector = Callable[[AsyncSession, MemoryGraph, str, dict[str, Any]], Awaitable[_Projected]]


async def _discover(s, memory, project_id, source):
    apps, domains, graph, refs = await load_estate(s, project_id)
    # stored paths are already relative to the scan root (services.scan.relativise), so the
    # projector's root is that root's relative name
    return _Projected([project_estate(apps, domains, graph, refs, root=".")])


async def _analysis(s, memory, project_id, source):
    process = await _process(s, source)
    return _Projected([project_analysis(process_ref(process), await load_analysis(s, process))], process)


async def _design(s, memory, project_id, source):
    process = await _process(s, source)
    found = await load_design(s, process)
    ref = process_ref(process)
    delta = project_design(ref, *found) if found else ScopedDelta(scope=f"design:{ref.ir_id}")
    return _Projected([delta], process)


async def _build(s, memory, project_id, source):
    process = await _process(s, source)
    inputs = await load_build(s, process, source["build_id"])
    ref = process_ref(process)
    delta = project_build(ref, inputs) if inputs else ScopedDelta(scope=f"build:{ref.ir_id}")
    return _Projected([delta], process)


async def _verify(s, memory, project_id, source):
    process = await _process(s, source)
    parity, starter_method = await load_verify(s, process, source["build_id"])
    return _Projected([project_verify(process_ref(process), parity, starter_method)], process)


async def _deploy(s, memory, project_id, source):
    process = await _process(s, source)
    return _Projected([project_deploy(process_ref(process), await load_deploy(s, process, source["plan_id"]))],
                      process)


async def _gate(s, memory, project_id, source):
    process = await _process(s, source)
    ref = process_ref(process)
    found = await load_gate(s, process, source["gate"])
    delta = project_gate(ref, found) if found else ScopedDelta(scope=f"gate:{ref.ir_id}:{source['gate']}")
    return _Projected([delta], process)


async def _event(s, memory, project_id, source):
    return _Projected([project_event(*await load_event(s, memory, project_id, source["event_id"]))])


async def _seed(s, memory, project_id, source):
    return _Projected([project_seed(load_seed())])


PROJECTORS: dict[str, Projector] = {
    "discover": _discover, "analysis": _analysis, "design": _design, "build": _build,
    "verify": _verify, "deploy": _deploy, "gate": _gate, "decision": _event, "curation": _event,
    "seed": _seed,
}
assert set(PROJECTORS) <= set(get_args(Phase))


async def _project_id(s: AsyncSession, payload: dict[str, Any]) -> str:
    if payload.get("project_id"):
        return payload["project_id"]
    process = await s.get(Process, (payload.get("source") or {}).get("process_id") or "")
    if process is None:
        raise ValueError("memory ingest names no project and no known process")
    return process.project_id


async def _iteration(s: AsyncSession, process: Process | None) -> int:
    """The process's current DesignRecord version (1 without one, and for project-wide scopes)."""
    if process is None:
        return 1
    return await s.scalar(select(func.max(DesignRecord.version))
                          .where(DesignRecord.process_id == process.id)) or 1


def _backoff(attempt: int) -> float:
    return min(BACKOFF_MAX_S, BACKOFF_BASE_S * 2 ** (max(1, attempt) - 1))


async def ingest(ctx: JobContext, w: WorkerServices) -> dict[str, Any]:
    p = ctx.payload
    if w.memory is None:
        raise RuntimeError("memory not configured: NEO4J_URI is empty")
    phase: Phase = p["phase"]
    projector = PROJECTORS.get(phase)
    if projector is None:
        raise ValueError(f"memory phase {phase!r} has no projector")
    source = p.get("source") or {}
    async with w.sessionmaker() as s:
        project_id = await _project_id(s, p)
    async with try_advisory_lock(w.sessionmaker, f"memory:{project_id}") as locked:
        if not locked:
            raise RetryLater(LOCK_BUSY_DELAY_S, "memory of this project is being written",
                             count_attempt=False)
        try:
            async with w.sessionmaker() as s:
                try:
                    projected = await projector(s, w.memory, project_id, source)
                except Superseded:
                    await ctx.log(f"{phase} source {source} is not the latest of its process; "
                                  "superseded")
                    return dict(SUPERSEDED)
                iteration = await _iteration(s, projected.process)
            meta = RevisionMeta(project_id=project_id, phase=phase, ingest_key=p["ingest_key"],
                                actor=p.get("actor") or SYSTEM_ACTOR,
                                source_job_id=p.get("source_job_id"), ingest_job_id=ctx.job_id,
                                iteration=iteration)
            await w.memory.ensure_ready()  # a start with Neo4j down left the schema to this ingest
            result = await w.memory.apply_revision(meta, projected.deltas)
        except UNAVAILABLE as exc:
            cls = type(exc).__name__
            raise RetryLater(_backoff(ctx.attempt), f"memory unavailable: {cls}",
                             give_up=f"memory unavailable after {{attempts}} attempts: {cls}") from exc
    scopes = [d.scope for d in projected.deltas]
    out = result.model_dump() | {"phase": phase, "scope": scopes[0] if len(scopes) == 1 else scopes}
    await ctx.log(f"memory {phase} ({out['scope']}): revision {result.revision}"
                  + (" (already applied)" if result.skipped else "")
                  + f", {result.added} added, {result.changed} changed, {result.removed} removed")
    return out
