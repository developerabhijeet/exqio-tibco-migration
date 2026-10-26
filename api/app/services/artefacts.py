"""Disk cleanup for iteration artefacts (SP6 final review M10).

Repository clones kept as edit-set trees (``DATA_DIR/repos/<repo id>/<commit>-<nonce>``) and
extracted edit zips (``DATA_DIR/edits/<edit set id>``) were never removed. :func:`sweep` deletes a
tree that nothing references once it is older than ``ITERATION_ARTEFACT_TTL_HOURS``, and the
``.clone-*`` scratch folders a crash leaves once they are an hour old. A tree is referenced while an
edit set points into it and its iteration is active or a build of the iteration's processes is
queued or running. It never follows a symbolic link and never touches a path outside ``DATA_DIR``.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import time
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.app.config import Settings
from api.app.models import Build, EditSet, Iteration
from api.app.services import gates as gate_svc
from api.app.services.iterations import ACTIVE_STATUSES

log = logging.getLogger("exiqo.artefacts")

SCRATCH_PREFIX = ".clone-"
SCRATCH_GRACE_S = 3600
SWEEP_INTERVAL_S = 3600
ACTION = "iteration artefacts swept"


def _inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def remove_tree(path: Path, data_dir: Path) -> None:
    """Remove the directory ``path`` (its symbolic links are unlinked, never followed). ``ValueError``
    when it is a link, not a directory, or not inside ``data_dir``."""
    data = data_dir.resolve()
    if path.is_symlink() or not path.is_dir():
        raise ValueError(f"{path} is not a plain directory")
    if not _inside(path.resolve(), data) or path.resolve() == data:
        raise ValueError(f"{path} is outside the data directory")
    shutil.rmtree(path)


async def referenced_roots(session: AsyncSession) -> list[Path]:
    """Resolved ``root_dir`` of the edit sets whose iteration is active or has a build underway."""
    underway = (await session.execute(
        select(Build.id, Build.process_id).where(Build.status.in_(("queued", "running"))))).all()
    building = {b for b, _ in underway}
    running = {p for _, p in underway}
    out: list[Path] = []
    rows = (await session.execute(select(EditSet, Iteration).join(Iteration, Iteration.id == EditSet.iteration_id))).all()
    for edit, iteration in rows:
        if iteration.status in ACTIVE_STATUSES or edit.baseline_build_id in building \
                or running & set(iteration.process_ids or []):
            out.append(Path(edit.root_dir).resolve())
    return out


def _sweep_disk(settings: Settings, keep: list[Path], now: float) -> dict[str, int]:
    data = settings.data_dir.resolve()
    ttl_s = max(0, settings.iteration_artefact_ttl_hours) * 3600
    counts = {"removed": 0, "scratch": 0, "kept": 0, "skipped": 0, "failed": 0}

    def referenced(p: Path) -> bool:
        return any(_inside(k, p) or _inside(p, k) for k in keep)

    def consider(entry: Path, grace_s: float, scratch: bool) -> None:
        try:
            if entry.is_symlink() or not entry.is_dir():
                counts["skipped"] += 1
                return
            real = entry.resolve()
            if not _inside(real, data):
                counts["skipped"] += 1
                return
            if now - entry.lstat().st_mtime < grace_s or referenced(real):
                counts["kept"] += 1
                return
            remove_tree(entry, data)
            counts["scratch" if scratch else "removed"] += 1
        except (OSError, ValueError):
            log.warning("could not remove %s", entry, exc_info=True)
            counts["failed"] += 1

    def children(parent: Path) -> list[Path]:
        # a parent that is a link, or outside DATA_DIR, is not walked
        if parent.is_symlink() or not parent.is_dir() or not _inside(parent.resolve(), data):
            return []
        return sorted(parent.iterdir())

    for entry in children(data / "edits"):
        consider(entry, ttl_s, False)
    for repo_dir in children(data / "repos"):
        for entry in children(repo_dir):
            if entry.name.startswith(SCRATCH_PREFIX):
                consider(entry, SCRATCH_GRACE_S, True)
            else:
                consider(entry, ttl_s, False)
    return counts


async def sweep(session: AsyncSession, settings: Settings, now: float | None = None) -> dict[str, int]:
    """Delete the unreferenced, expired trees. Writes the audit line when anything went (the caller
    commits). The counts: ``removed``, ``scratch``, ``kept``, ``skipped``, ``failed``."""
    keep = await referenced_roots(session)
    counts = await asyncio.to_thread(_sweep_disk, settings, keep, time.time() if now is None else now)
    if counts["removed"] or counts["scratch"] or counts["failed"]:
        gate_svc.audit(session, None, gate_svc.SYSTEM, ACTION, actor_kind=gate_svc.SYSTEM,
                       detail={**counts, "ttl_hours": settings.iteration_artefact_ttl_hours})
    return counts


async def sweep_once(sessionmaker, settings: Settings) -> dict[str, int] | None:
    try:
        async with sessionmaker() as s:
            counts = await sweep(s, settings)
            await s.commit()
        return counts
    except Exception:
        log.exception("iteration artefact sweep failed")
        return None


async def sweep_forever(sessionmaker, settings: Settings, interval_s: float = SWEEP_INTERVAL_S,
                        first_after_s: float = 0) -> None:
    """Sweep ``first_after_s`` after the start, then every ``interval_s``; cancel the task to stop."""
    await asyncio.sleep(first_after_s)
    while True:
        await sweep_once(sessionmaker, settings)
        await asyncio.sleep(interval_s)
