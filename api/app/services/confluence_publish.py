"""The ``connector.confluence.publish`` job: render the process's architecture page from stored data
(latest design record, gates, latest finished build, Figma links) and create or update it in
Confluence; the page is recorded as the process's ``confluence_page`` link and audited.

Payload ``{process_id, actor, create_again}`` (no credential: it is read from its mounted file when
the job runs); result ``{page_id, url, version, title}``.

A deleted page follows the one policy of ruling X20: a linked page that answers 404 marks the link
stale and the job is refused; a stale link is refused before any request; only ``create_again``
makes a new page. A page of the same title that this process did not publish is never adopted or
overwritten (refused).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.app.connectors.audit import audit_connector
from api.app.connectors.base import ConnectorDisabled, ConnectorNotConfigured
from api.app.connectors.confluence import PageGone, TitleTaken
from api.app.connectors.confluence_page import PageContext, page_title
from api.app.connectors.http import header_secrets, redact
from api.app.connectors.links import find_link, upsert_link
from api.app.connectors.registry import AtlassianConnector, connector_for
from api.app.jobs.locks import try_advisory_lock
from api.app.jobs.registry import RetryLater
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
NO_URL = "the Atlassian connector has no Confluence URL (connectors.atlassian.confluenceUrl)"
NO_SPACE = "set the Confluence space key in Settings → Connectors"
LOCK_PREFIX = "confluence:"  # the advisory lock and the queue's dedupe key, per process
LOCK_BUSY_DELAY_S = 2.0
DEFAULT_ITERATION = 1  # until iterations exist (sub-project 6)


def dedupe_key(process_id: str, create_again: bool) -> str:
    """The queue's dedupe key: one queued publish per process (and per ``create_again``)."""
    return f"{LOCK_PREFIX}{process_id}" + (":again" if create_again else "")


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
        stack=str((build.stack if build else None) or record.get("stack") or "dotnet"), iteration=DEFAULT_ITERATION,
        figma=[{"name": (f.detail or {}).get("name") or f.remote_id, "url": f.remote_url} for f in figma])


class Refused(Exception):
    """The job cannot run as asked and nothing was sent: audited ``refused``."""


def _secrets(atl: AtlassianConnector | None) -> list[str]:
    """The credential's forms, to redact from an audit message (none when it cannot be read now)."""
    if atl is None:
        return []
    try:
        return header_secrets(atl.auth_header())
    except ConnectorNotConfigured:
        return []


async def publish_job(ctx: JobContext, w: WorkerServices) -> dict[str, Any]:
    """Every outcome is audited: ``refused`` (off, no credential, no URL or space, no design),
    ``failed`` (any error, its redacted text) or ``ok``; an error is then raised to fail the job.
    One publish per process at a time (advisory lock): a second job waits, then updates the page
    the first one linked instead of creating a page of the same title."""
    process_id = ctx.payload["process_id"]
    async with try_advisory_lock(w.sessionmaker, f"{LOCK_PREFIX}{process_id}") as locked:
        if not locked:
            raise RetryLater(LOCK_BUSY_DELAY_S, "another Confluence publish of this process is running",
                             count_attempt=False)
        return await _publish(ctx, w)


async def _publish(ctx: JobContext, w: WorkerServices) -> dict[str, Any]:
    p = ctx.payload
    actor = p.get("actor") or "system"
    state: dict[str, Any] = {"where": {"project_id": None, "process_id": None}, "atl": None,
                             "target": str(p["process_id"]), "detail": {}}

    async def audit(outcome: Literal["ok", "failed", "refused"], detail: dict[str, Any],
                    session: AsyncSession | None = None) -> None:
        async def write(s: AsyncSession) -> None:
            await audit_connector(s, actor, "confluence", "publish", target=state["target"], outcome=outcome,
                                  detail={**state["detail"], **detail}, **state["where"])

        if session is not None:
            await write(session)
            return
        async with w.sessionmaker() as s:
            await write(s)
            await s.commit()

    try:
        async with w.sessionmaker() as s:
            proc = await s.get(Process, p["process_id"])
            if proc is None:
                raise Refused(f"process {p['process_id']} not found")
            state["where"] = {"project_id": proc.project_id, "process_id": proc.id}
            try:
                page = await page_context(s, proc)
            except LookupError as exc:
                raise Refused(str(exc)) from None
            existing = await page_link(s, proc.id)
            atl = await connector_for(s, w.settings, "atlassian")
        if not isinstance(atl, AtlassianConnector):
            raise TypeError(f"the Atlassian connector is served by {type(atl).__name__}")
        state["atl"] = atl
        atl.ready()  # off or no credential since the request was accepted: before any request
        if not atl.config.url("confluence_url"):
            raise Refused(NO_URL)
        app_layer = atl.config.app
        space_key, parent = getattr(app_layer, "space_key", ""), getattr(app_layer, "parent_page_id", "")
        if not space_key:
            raise Refused(NO_SPACE)
        title = page_title(page.app_name, page.process_name)
        state["target"] = f"{space_key}/{title}"
        state["detail"] = {"design_version": page.design_version}
        where = state["where"]
        publisher = atl.confluence

        await ctx.stage("publish")
        await ctx.log(f"publishing design v{page.design_version} to Confluence space {space_key}"
                      + (f" (updating page {existing.remote_id})" if existing else ""))
        try:
            page_id, url, version = await publisher.write_page(page, space_key=space_key, parent_page_id=parent,
                                                               existing=existing,
                                                               create_again=bool(p.get("create_again")))
        except PageGone as exc:
            if existing is not None and not existing.stale:
                async with w.sessionmaker() as s:  # the page is gone: never silently made again
                    gone = await page_link(s, existing.process_id)
                    if gone is not None:
                        gone.stale = True
                    await s.commit()
                await ctx.log(f"page {existing.remote_id} is gone; the link is marked stale")
            state["target"] = existing.remote_url if existing is not None else state["target"]
            raise Refused(exc.message) from None
        except TitleTaken as exc:
            raise Refused(exc.message) from None
        state["target"] = url
        state["detail"] = {**state["detail"], "page_id": page_id, "version": version}
        async with w.sessionmaker() as s:  # linked now: a failed attachment must not lose the page
            await upsert_link(s, **where, kind=LINK_KIND, object_type=LINK_TYPE, local_ref=LOCAL_REF,
                              remote_id=page_id, remote_url=url, remote_version=version, created_by=actor)
            await s.commit()
        await ctx.log(f"page {page_id} is at version {version}: {url}")
        await publisher.attach_design(page_id, page)
        await audit("ok", {"space_key": space_key})
        return {"page_id": page_id, "url": url, "version": version, "title": title}
    except (ConnectorDisabled, ConnectorNotConfigured, Refused) as exc:
        await audit("refused", {"message": str(exc)})
        raise
    except Exception as exc:
        await audit("failed", {"message": redact(str(exc) or type(exc).__name__, _secrets(state["atl"]))})
        raise
