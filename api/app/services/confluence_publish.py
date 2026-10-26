"""The ``connector.confluence.publish`` job: render the process's architecture page from stored data
(latest design record, gates, latest finished build, Figma links) and create or update it in
Confluence; the page is recorded as the process's ``confluence_page`` link and audited.

Payload ``{process_id, actor}`` (no credential: it is read from its mounted file when the job runs);
result ``{page_id, url, version, title}``.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.app.connectors.audit import audit_connector
from api.app.connectors.base import ConnectorError
from api.app.connectors.confluence_page import PageContext, page_title
from api.app.connectors.links import find_link, upsert_link
from api.app.connectors.registry import AtlassianConnector, connector_for
from api.app.models import Application, Gate, Process
from api.app.models.connector import ConnectorLink
from api.app.services.evidence import latest_build, latest_design

if TYPE_CHECKING:
    from api.app.jobs.registry import WorkerServices
    from api.app.jobs.runner import JobContext

PUBLISH_KIND = "connector.confluence.publish"
LINK_KIND = "atlassian"
LINK_TYPE = "confluence_page"
LOCAL_REF = "architecture"  # one architecture page per process
NO_DESIGN = "design the process before publishing it"
DEFAULT_STACK = "dotnet"  # until builds carry their stack (sub-project 5)
DEFAULT_ITERATION = 1  # until iterations exist (sub-project 6)


def _iso(value: Any) -> str:
    return value.isoformat() if value is not None else ""


async def page_link(session: AsyncSession, process_id: str) -> ConnectorLink | None:
    return await find_link(session, kind=LINK_KIND, object_type=LINK_TYPE, process_id=process_id,
                           local_ref=LOCAL_REF)


async def page_context(session: AsyncSession, proc: Process) -> PageContext:
    """Everything the page shows, from the database; ``LookupError`` without a design record."""
    design = await latest_design(session, proc.id)
    if design is None:
        raise LookupError(NO_DESIGN)
    application = await session.get(Application, proc.application_id)
    gates = (await session.execute(select(Gate).where(Gate.process_id == proc.id).order_by(Gate.gate))).scalars()
    build = await latest_build(session, proc.id)
    figma = (await session.execute(select(ConnectorLink).where(
        ConnectorLink.process_id == proc.id, ConnectorLink.object_type == "figma_frame",
        ConnectorLink.stale.is_(False)).order_by(ConnectorLink.created_at, ConnectorLink.id))).scalars()
    record = dict(design.record or {})
    if design.narrative and not record.get("narrative"):
        record["narrative"] = design.narrative
    record.setdefault("design_hash", design.design_hash)
    record.setdefault("spec_hash", design.spec_hash)
    return PageContext(
        app_name=application.name if application else proc.module or "application",
        process_name=proc.name, ir_id=proc.ir_id, source_path=proc.source_path, design=record,
        design_version=design.version,
        gates=[{"gate": g.gate, "state": g.state, "decided_by": g.decided_by, "decided_at": _iso(g.decided_at),
                "spec_hash": g.spec_hash, "design_hash": g.design_hash, "build_hash": g.build_hash,
                "evidence_hash": g.evidence_hash} for g in gates],
        build=({"id": build.id, "status": build.status, "build_hash": build.build_hash} if build else None),
        stack=str(getattr(build, "stack", None) or DEFAULT_STACK), iteration=DEFAULT_ITERATION,
        figma=[{"name": f.remote_id, "url": f.remote_url} for f in figma])


async def publish_job(ctx: JobContext, w: WorkerServices) -> dict[str, Any]:
    p = ctx.payload
    actor = p.get("actor") or "system"
    async with w.sessionmaker() as s:
        proc = await s.get(Process, p["process_id"])
        if proc is None:
            raise RuntimeError(f"process {p['process_id']} not found")
        page = await page_context(s, proc)
        existing = await page_link(s, proc.id)
        atl = await connector_for(s, w.settings, "atlassian")
    if not isinstance(atl, AtlassianConnector):
        raise TypeError(f"the Atlassian connector is served by {type(atl).__name__}")
    atl.ready()  # off or no credential since the request was accepted: fail before any request
    app_layer = atl.config.app
    space_key, parent = getattr(app_layer, "space_key", ""), getattr(app_layer, "parent_page_id", "")
    where = {"project_id": proc.project_id, "process_id": proc.id}
    title = page_title(page.app_name, page.process_name)
    await ctx.stage("publish")
    await ctx.log(f"publishing design v{page.design_version} to Confluence space {space_key}"
                  + (f" (updating page {existing.remote_id})" if existing else ""))
    try:
        page_id, url, version = await atl.confluence.publish(page, space_key=space_key, parent_page_id=parent,
                                                             existing=existing)
    except (ConnectorError, ValueError) as exc:
        async with w.sessionmaker() as s:
            await audit_connector(s, actor, "confluence", "publish", target=f"{space_key}/{title}",
                                  outcome="failed", detail={"design_version": page.design_version,
                                                            "message": str(exc)}, **where)
            await s.commit()
        raise
    await ctx.log(f"page {page_id} is at version {version}: {url}")
    async with w.sessionmaker() as s:
        await upsert_link(s, **where, kind=LINK_KIND, object_type=LINK_TYPE, local_ref=LOCAL_REF,
                          remote_id=page_id, remote_url=url, remote_version=version, created_by=actor)
        await audit_connector(s, actor, "confluence", "publish", target=url, outcome="ok",
                              detail={"page_id": page_id, "version": version, "space_key": space_key,
                                      "design_version": page.design_version}, **where)
        await s.commit()
    return {"page_id": page_id, "url": url, "version": version, "title": title}
