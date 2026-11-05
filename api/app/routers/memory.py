"""The migration memory, read-only: status, search, subgraph, node detail and history,
revisions and their diffs. Every route 404s on a project unknown to Postgres; memory not
configured or unreachable is a 503 (``status`` reports it with 200 instead)."""
from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Path, Query, Request
from sqlalchemy import func, select

from api.app.deps import AdminDep, CuratorDep, SessionDep
from api.app.memory_admin import reingest as reingest_project
from api.app.models import Job, MemoryEvent, Project
from api.app.schemas.memory import (
    CurationIn,
    CurationOut,
    MemoryHit,
    NodeDetail,
    ReingestOut,
    RevisionDiff,
    RevisionsOut,
    SearchOut,
    StatusOut,
    Subgraph,
)
from api.app.services import gates as gate_svc
from api.app.services.knowledge import KnowledgeUnavailable
from api.app.services.memory_ingest import enqueue_ingest
from engine.masking import mask
from engine.memory.model import NODE_LABELS, NodeLabel, Phase

router = APIRouter(prefix="/memory/{project_id}", tags=["memory"])

INGEST_KIND = "memory.ingest"
Uid = Annotated[str, Query(min_length=1, max_length=512)]
Labels = Annotated[list[NodeLabel], Query()]
Side = Literal["old", "new", "knowledge"]


async def _project(session: SessionDep, project_id: str) -> None:
    if await session.get(Project, project_id) is None:
        raise HTTPException(status_code=404, detail="project not found")


async def _read(request: Request, work):
    try:
        return await request.app.state.knowledge.read(work)
    except KnowledgeUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="not found")


@router.get("/status", response_model=StatusOut)
async def status(project_id: str, request: Request, session: SessionDep) -> StatusOut:
    await _project(session, project_id)
    knowledge = request.app.state.knowledge
    if not knowledge.enabled:
        return StatusOut(configured=False, available=False)
    pending = (await session.execute(
        select(func.count()).select_from(Job).where(
            Job.project_id == project_id, Job.kind == INGEST_KIND,
            Job.status.in_(("queued", "running"))))).scalar_one()

    async def work(m):
        return await m.latest_revision(project_id), await m.counts(project_id), m.embedding_backend

    try:
        latest, counts, backend = await knowledge.read(work)
    except KnowledgeUnavailable:
        return StatusOut(configured=True, available=False, pending_ingests=pending)
    return StatusOut(configured=True, available=True, revision=latest.n if latest else None,
                     at=latest.at if latest else None, phase=latest.phase if latest else None,
                     counts=counts, pending_ingests=pending, embedding_backend=backend)


@router.get("/search", response_model=SearchOut)
async def search(project_id: str, request: Request, session: SessionDep,
                 q: Annotated[str, Query(min_length=1, max_length=500)],
                 labels: Labels = [],  # noqa: B006
                 side: Side | None = None, phase: Phase | None = None,
                 as_of: Annotated[int | None, Query(ge=1)] = None,
                 k: Annotated[int, Query(ge=1, le=50)] = 8) -> SearchOut:
    await _project(session, project_id)
    wanted = tuple(labels) or NODE_LABELS
    hits: list[MemoryHit] = await _read(request, lambda m: m.retrieve(
        project_id, q, labels=wanted, side=side, phase=phase, k=k, as_of=as_of))
    return SearchOut(hits=hits)


@router.get("/graph", response_model=Subgraph)
async def graph(project_id: str, request: Request, session: SessionDep,
                uid: Annotated[str | None, Query(max_length=512)] = None,
                depth: Annotated[int, Query(ge=1, le=2)] = 1,
                labels: Labels = [],  # noqa: B006
                side: Side | None = None,
                as_of: Annotated[int | None, Query(ge=1)] = None,
                mode: Literal["neighbourhood", "lineage"] = "neighbourhood",
                limit: Annotated[int, Query(ge=1, le=400)] = 400) -> Subgraph:
    await _project(session, project_id)
    if mode == "lineage":
        if not uid:
            raise HTTPException(status_code=422, detail="uid is required for mode=lineage")
        result: Subgraph = await _read(request, lambda m: m.lineage_chain(
            project_id, uid, as_of, limit=limit))
    else:
        result = await _read(request, lambda m: m.subgraph(
            project_id, uid=uid or None, depth=depth, labels=labels or None, side=side,
            as_of=as_of, limit=limit))
    # filters can exclude the root: 404 only for a node that does not exist
    if (uid and not result.nodes
            and await _read(request, lambda m: m.node_detail(project_id, uid, as_of)) is None):
        raise _not_found()
    return result


@router.get("/node", response_model=NodeDetail)
async def node(project_id: str, request: Request, session: SessionDep, uid: Uid,
               as_of: Annotated[int | None, Query(ge=1)] = None) -> NodeDetail:
    await _project(session, project_id)
    found = await _read(request, lambda m: m.node_detail(project_id, uid, as_of))
    if found is None:
        raise _not_found()
    return found


@router.get("/revisions", response_model=RevisionsOut)
async def revisions(project_id: str, request: Request, session: SessionDep,
                    before: Annotated[int | None, Query(ge=1)] = None,
                    limit: Annotated[int, Query(ge=1, le=200)] = 50,
                    iteration: Annotated[int | None, Query(ge=1)] = None) -> RevisionsOut:
    """Revisions newest first, each with its ``iteration``; ``iteration=<n>`` keeps that one's."""
    await _project(session, project_id)
    found = await _read(request, lambda m: m.revisions(project_id, before=before, limit=limit,
                                                       iteration=iteration))
    return RevisionsOut(revisions=found)


@router.get("/revisions/{n}/diff", response_model=RevisionDiff)
async def revision_diff(project_id: str, n: Annotated[int, Path(ge=1)], request: Request,
                        session: SessionDep) -> RevisionDiff:
    await _project(session, project_id)
    found = await _read(request, lambda m: m.diff(project_id, n))
    if found is None:
        raise _not_found()
    return found


# ------------------------------------------------------------------ curation (writes)
MAPPING_ACTIONS = ("reject_mapping", "correct_mapping")


async def _mapping_target(request: Request, project_id: str, target_uid: str) -> tuple[str, str]:
    """``(source node uid, MIGRATED_TO rel uid)`` to curate. ``target_uid`` names the old side's
    node (its one current, not yet rejected mapping) or the ``MIGRATED_TO:<src>-><dst>`` uid."""
    rel_uid = target_uid if target_uid.startswith("MIGRATED_TO:") else None
    source = target_uid.removeprefix("MIGRATED_TO:").partition("->")[0] if rel_uid else target_uid
    detail = await _read(request, lambda m: m.node_detail(project_id, source, None))
    if detail is None:
        raise HTTPException(status_code=404, detail="target not found")
    edges = [x.edge for x in detail.lineage
             if x.direction == "out" and x.edge.type == "MIGRATED_TO"]
    if rel_uid is not None:
        edges = [e for e in edges if e.uid == rel_uid]
    else:
        edges = [e for e in edges if e.status != "rejected"]
    if not edges:
        raise HTTPException(status_code=409, detail="target has no mapping to curate")
    return source, edges[0].uid


@router.post("/curations", status_code=202, response_model=CurationOut)
async def curate(project_id: str, body: CurationIn, request: Request, session: SessionDep,
                 principal: CuratorDep) -> CurationOut:
    await _project(session, project_id)
    if body.action == "note" and not (body.text or "").strip():
        raise HTTPException(status_code=422, detail="note needs text")
    if body.action == "correct_mapping" and not body.replacement_uid:
        raise HTTPException(status_code=422, detail="correct_mapping needs replacement_uid")
    target = body.target_uid
    if body.action in MAPPING_ACTIONS:
        _, target = await _mapping_target(request, project_id, body.target_uid)
    else:
        node = await _read(request, lambda m: m.current_node(project_id, body.target_uid))
        if node is None:
            raise HTTPException(status_code=404, detail="target not found")
        if body.action == "obsolete" and node.label != "Memory":
            raise HTTPException(status_code=409, detail="obsolete applies to Memory items only")
    if body.action == "correct_mapping":
        repl = await _read(request, lambda m: m.current_node(project_id, body.replacement_uid or ""))
        if repl is None or repl.label not in ("Method", "Class"):
            raise HTTPException(status_code=409, detail="replacement must be a Method or Class")

    actor = principal.actor
    row = MemoryEvent(project_id=project_id, kind="curation", action=body.action,
                      text=mask(body.text or ""),
                      context={"reason": mask(body.reason)}, target_uid=target,
                      replacement_uid=body.replacement_uid, actor=actor)
    session.add(row)
    await session.flush()
    gate_svc.audit(session, None, actor, "memory curated", project_id=project_id,
                   detail={"event_id": row.id, "action": body.action, "target_uid": target})
    event_id = row.id
    await session.commit()
    try:
        job_id = await enqueue_ingest(
            request.app.state.sessionmaker, request.app.state.settings, project_id=project_id,
            phase="curation", scope=f"curation:{event_id}", source={"event_id": event_id},
            actor=actor, ingest_key=f"curation:{event_id}")
    except Exception as exc:  # the row is stored: a re-ingest of the project brings it
        raise HTTPException(status_code=503, detail=f"curation {event_id} stored; memory ingest "
                            f"not queued: {type(exc).__name__}") from exc
    if job_id is None:
        raise HTTPException(status_code=503, detail=f"curation {event_id} stored; memory ingest "
                            "not queued: memory is not configured")
    return CurationOut(event_id=event_id, job_id=job_id)


@router.post("/reingest", status_code=202, response_model=ReingestOut)
async def reingest(project_id: str, request: Request, session: SessionDep,
                   _admin: AdminDep) -> ReingestOut:
    await _project(session, project_id)
    if not request.app.state.knowledge.enabled:
        raise HTTPException(status_code=503, detail="memory is not configured")
    ids = await reingest_project(request.app.state.sessionmaker, request.app.state.settings,
                                 project_id)
    return ReingestOut(job_ids=ids)
