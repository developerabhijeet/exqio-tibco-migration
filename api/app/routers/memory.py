"""The migration memory, read-only: status, search, subgraph, node detail and history,
revisions and their diffs. Every route 404s on a project unknown to Postgres; memory not
configured or unreachable is a 503 (``status`` reports it with 200 instead)."""
from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Path, Query, Request
from sqlalchemy import func, select

from api.app.deps import SessionDep
from api.app.models import Job, Project
from api.app.schemas.memory import (
    MemoryHit,
    NodeDetail,
    RevisionDiff,
    RevisionsOut,
    SearchOut,
    StatusOut,
    Subgraph,
)
from api.app.services.knowledge import KnowledgeUnavailable
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
    if uid and not result.nodes:
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
                    limit: Annotated[int, Query(ge=1, le=200)] = 50) -> RevisionsOut:
    await _project(session, project_id)
    found = await _read(request, lambda m: m.revisions(project_id, before=before, limit=limit))
    return RevisionsOut(revisions=found)


@router.get("/revisions/{n}/diff", response_model=RevisionDiff)
async def revision_diff(project_id: str, n: Annotated[int, Path(ge=1)], request: Request,
                        session: SessionDep) -> RevisionDiff:
    await _project(session, project_id)
    found = await _read(request, lambda m: m.diff(project_id, n))
    if found is None:
        raise _not_found()
    return found
