"""The ``connector.figma.sync`` job and the read of a process's design references.

The job attaches chosen frames of a Figma file to a process: one ``figma_frame`` link per frame
(``local_ref = "<file key>:<node id>"``) whose ``detail`` is ``{file_key, node_id, name, page, text,
image_path, image_note}``. Running it again for the same frame refreshes the link (name, text,
image) rather than adding another. Every outcome is audited ``connector figma sync``: ``refused``
(off, no credential, no API URL, a file or frame that is not allowed), ``failed`` (any error, its
redacted text) or ``ok`` per frame; an error is then raised to fail the job.

Each frame is written under an advisory lock of its own (``figma:<process id>:<local ref>``).

Payload ``{process_id, file_key, node_ids, actor}`` (no credential: it is read from its mounted
file when the job runs); result ``{frames: [node id], thumbnails: n, thumbnails_note: str|None}``.

:func:`design_references` is what the designer (and later other prompts) read: name, page, text
and link of each live attached frame.
"""
from __future__ import annotations

import contextlib
import hashlib
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.app.connectors.audit import audit_connector
from api.app.connectors.base import ConnectorDisabled, ConnectorNotConfigured
from api.app.connectors.figma import (
    NO_IMAGE_HOSTS,
    FigmaConnector,
    FileNotAllowed,
    frame_url,
    valid_node_id,
)
from api.app.connectors.http import header_secrets, redact
from api.app.connectors.links import find_link, upsert_link
from api.app.connectors.registry import connector_for
from api.app.jobs.locks import advisory_lock
from api.app.models import Process
from api.app.models.connector import ConnectorLink

if TYPE_CHECKING:
    from api.app.jobs.registry import WorkerServices
    from api.app.jobs.runner import JobContext

SYNC_KIND = "connector.figma.sync"
LINK_KIND = "figma"
LINK_TYPE = "figma_frame"
LOCK_PREFIX = "figma:"
MAX_FRAMES = 20
#: starter kinds whose service has screens in front of it (the designer is given the references)
UI_STARTERS = frozenset({"rest", "http"})


class Refused(Exception):
    """The job cannot run as asked and nothing was sent: audited ``refused``."""


def local_ref(file_key: str, node_id: str) -> str:
    return f"{file_key}:{node_id}"


def dedupe_key(process_id: str, file_key: str, node_ids: list[str]) -> str:
    """The same request for the same process while one is still queued is not queued twice."""
    body = file_key + "\n" + "\n".join(sorted(set(node_ids)))
    return f"{LOCK_PREFIX}{process_id}:{hashlib.sha256(body.encode()).hexdigest()[:24]}"


async def frame_links(session: AsyncSession, process_id: str, *, live_only: bool = False) -> list[ConnectorLink]:
    query = select(ConnectorLink).where(ConnectorLink.process_id == process_id,
                                        ConnectorLink.object_type == LINK_TYPE)
    if live_only:
        query = query.where(ConnectorLink.stale.is_(False))
    return list((await session.execute(query.order_by(ConnectorLink.created_at, ConnectorLink.id))).scalars())


def remove_thumbnail(data_dir: Path, stored: object) -> bool:
    """Delete the stored thumbnail at ``stored`` when it lies under ``<data_dir>/figma``; ``True``
    when a file was removed. Anything else (no path, elsewhere, a symlink) is left alone."""
    if not stored:
        return False
    root = Path(data_dir).resolve() / "figma"
    path = Path(str(stored))
    if path.is_symlink() or not path.resolve().is_relative_to(root) or not path.is_file():
        return False
    with contextlib.suppress(OSError):
        path.unlink()
        return True
    return False


async def remove_frame_link(session: AsyncSession, data_dir: Path, link: ConnectorLink) -> None:
    """Delete one attached frame and its thumbnail (flushed, not committed)."""
    remove_thumbnail(data_dir, (link.detail or {}).get("image_path"))
    await session.delete(link)
    await session.flush()


async def purge_thumbnails(session: AsyncSession, data_dir: Path, *, process_ids: list[str] | None = None,
                           project_id: str | None = None) -> int:
    """Delete the thumbnails of every frame link of the given processes or project (the caller is
    deleting them, and the links go with it). Returns how many files were removed."""
    query = select(ConnectorLink).where(ConnectorLink.object_type == LINK_TYPE)
    if project_id is not None:
        query = query.where(ConnectorLink.project_id == project_id)
    if process_ids is not None:
        query = query.where(ConnectorLink.process_id.in_(process_ids))
    return sum(remove_thumbnail(data_dir, (link.detail or {}).get("image_path"))
               for link in (await session.execute(query)).scalars())


def reference_of(link: ConnectorLink) -> dict[str, Any]:
    d = link.detail or {}
    return {"name": d.get("name") or link.remote_id, "page": d.get("page") or "", "text": d.get("text") or "",
            "url": link.remote_url}


async def design_references(session: AsyncSession, process_id: str) -> list[dict[str, Any]]:
    """The live attached frames of a process as ``{name, page, text, url}``, oldest first. The text
    is already masked and cut when stored. Needs no connector: what was attached stays readable."""
    return [reference_of(link) for link in await frame_links(session, process_id, live_only=True)]


def _secrets(fig: FigmaConnector | None) -> list[str]:
    if fig is None:
        return []
    try:
        return header_secrets(fig.auth_header())
    except ConnectorNotConfigured:
        return []


async def sync_job(ctx: JobContext, w: WorkerServices) -> dict[str, Any]:
    p = ctx.payload
    actor = p.get("actor") or "system"
    file_key = str(p["file_key"])
    node_ids = list(dict.fromkeys(str(n) for n in p.get("node_ids") or []))
    where: dict[str, Any] = {"project_id": None, "process_id": None}
    state: dict[str, Any] = {"fig": None, "target": file_key, "detail": {"file_key": file_key, "node_ids": node_ids}}

    async def audit(outcome: Literal["ok", "failed", "refused"], detail: dict[str, Any]) -> None:
        async with w.sessionmaker() as s:
            await audit_connector(s, actor, "figma", "sync", target=state["target"], outcome=outcome,
                                  detail={**state["detail"], **detail}, **where)
            await s.commit()

    try:
        async with w.sessionmaker() as s:
            proc = await s.get(Process, p["process_id"])
            if proc is None:
                raise Refused(f"process {p['process_id']} not found")
            where.update(project_id=proc.project_id, process_id=proc.id)
            fig = await connector_for(s, w.settings, "figma")
        if not isinstance(fig, FigmaConnector):
            raise TypeError(f"the Figma connector is served by {type(fig).__name__}")
        state["fig"] = fig
        fig.ready()  # off or no credential since the request was accepted: before any request
        if not 1 <= len(node_ids) <= MAX_FRAMES:
            raise Refused(f"choose 1 to {MAX_FRAMES} frames")
        try:
            fig.check_file(file_key)
            for node_id in node_ids:
                if not valid_node_id(node_id):
                    raise ValueError(f"not a Figma node id: {node_id!r}")
        except (FileNotAllowed, ValueError) as exc:
            raise Refused(str(exc)) from None
        await ctx.stage("frames")
        by_id = {f.node_id: f for f in await fig.frames(file_key)}
        missing = [n for n in node_ids if n not in by_id]
        if missing:
            raise Refused(f"not a frame of file {file_key}: {', '.join(missing)}")
        await ctx.stage("text")
        texts = await fig.frame_text(file_key, node_ids)
        await ctx.stage("thumbnails")
        images, notes = await fig.render_with_notes(file_key, node_ids)
        await ctx.stage("links")
        for node_id in node_ids:
            frame = by_id[node_id]
            ref = local_ref(file_key, node_id)
            url = frame_url(fig.web_url, file_key, node_id)
            image = images.get(node_id)
            detail = {"file_key": file_key, "node_id": node_id, "name": frame.name, "page": frame.page,
                      "text": texts.get(node_id, ""), "image_path": str(image) if image else None,
                      "image_note": None if image else notes.get(node_id)}
            state["detail"], state["target"] = {"file_key": file_key, "local_ref": ref}, url or ref
            async with advisory_lock(w.sessionmaker, f"{LOCK_PREFIX}{proc.id}:{ref}"), w.sessionmaker() as s:
                before = await find_link(s, kind=LINK_KIND, object_type=LINK_TYPE, process_id=proc.id,
                                         local_ref=ref)
                old_image = (before.detail or {}).get("image_path") if before else None
                await upsert_link(s, **where, kind=LINK_KIND, object_type=LINK_TYPE, local_ref=ref,
                                  remote_id=node_id, remote_url=url, detail=detail, created_by=actor)
                await audit_connector(s, actor, "figma", "sync", target=url or ref, outcome="ok",
                                      detail={"file_key": file_key, "local_ref": ref, "name": frame.name,
                                              "thumbnail": image is not None}, **where)
                await s.commit()
            if old_image and old_image != detail["image_path"]:  # replaced or dropped: not kept
                remove_thumbnail(w.settings.data_dir, old_image)
            note = "" if image else f" (no thumbnail: {notes.get(node_id)})"
            await ctx.log(f"{ref}: attached {frame.name!r}{note}")
        shown = [n for n in node_ids if images.get(n)]
        return {"frames": node_ids, "thumbnails": len(shown),
                "thumbnails_note": None if shown or not node_ids else (notes.get(node_ids[0]) or NO_IMAGE_HOSTS)}
    except (ConnectorDisabled, ConnectorNotConfigured, Refused) as exc:
        await audit("refused", {"message": str(exc)})
        raise
    except Exception as exc:
        await audit("failed", {"message": redact(str(exc) or type(exc).__name__, _secrets(state["fig"]))})
        raise
