"""Disk cleanup for iteration artefacts (SP6 final review M10)."""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from sqlalchemy import select

from api.app.models import AuditLog, EditSet, Iteration
from api.app.services import artefacts
from api.tests import test_repos as repos_tests
from api.tests.test_repos import PROC, P

gh_settings = repos_tests.gh_settings
app = repos_tests.app
project = repos_tests.project
finished_build = repos_tests.finished_build

HOUR = 3600


def tree(path: Path, age_h: float) -> Path:
    path.mkdir(parents=True)
    (path / "f.txt").write_text("x")
    old = time.time() - age_h * HOUR
    os.utime(path, (old, old))
    return path


async def edit_set(app, root: Path, status: str = "open") -> None:
    async with app.state.sessionmaker() as s:
        it = Iteration(project_id=P, n=int(time.time_ns() % 10**9), process_ids=[PROC], kind="edits", status=status,
                       opened_by="t")
        s.add(it)
        await s.flush()
        s.add(EditSet(iteration_id=it.id, files=[], root_dir=str(root), created_by="t"))
        await s.commit()


async def sweep(app):
    async with app.state.sessionmaker() as s:
        out = await artefacts.sweep(s, app.state.settings)
        await s.commit()
    return out


async def test_unreferenced_old_trees_go_referenced_and_young_ones_stay(app, project, finished_build):
    data = app.state.settings.data_dir.resolve()
    kept = tree(data / "repos" / "r1" / "aaa-111", 48)
    gone = tree(data / "repos" / "r1" / "bbb-222", 48)
    young = tree(data / "repos" / "r1" / "ccc-333", 1)
    old_edit = tree(data / "edits" / "e-old", 72)
    live_edit = tree(data / "edits" / "e-live", 72)
    done_edit = tree(data / "edits" / "e-done", 72)
    await edit_set(app, kept, "building")
    await edit_set(app, live_edit, "open")
    await edit_set(app, done_edit, "closed")
    out = await sweep(app)
    assert kept.is_dir() and live_edit.is_dir() and young.is_dir()
    assert not gone.exists() and not old_edit.exists() and not done_edit.exists()
    assert out["removed"] == 3
    async with app.state.sessionmaker() as s:
        line = (await s.execute(select(AuditLog).where(AuditLog.action == "iteration artefacts swept"))).scalars().one()
    assert line.detail["removed"] == 3 and line.actor_kind == "system"


async def test_a_running_build_of_the_iteration_keeps_its_trees(app, project, finished_build):
    from api.app.models import Build
    data = app.state.settings.data_dir.resolve()
    root = tree(data / "edits" / "e-1", 72)
    await edit_set(app, root, "closed")
    async with app.state.sessionmaker() as s:
        s.add(Build(process_id=PROC, status="running", build_hash="x" * 64, design_hash="d" * 64))
        await s.commit()
    await sweep(app)
    assert root.is_dir()


async def test_scratch_folders_go_after_an_hour(app, project):
    data = app.state.settings.data_dir.resolve()
    old = tree(data / "repos" / "r1" / ".clone-abc", 2)
    fresh = tree(data / "repos" / "r1" / ".clone-def", 0.1)
    out = await sweep(app)
    assert not old.exists() and fresh.is_dir() and out["scratch"] == 1


async def test_symlinks_are_not_followed(app, project, tmp_path):
    data = app.state.settings.data_dir.resolve()
    outside = tree(tmp_path / "outside", 99)
    (data / "repos" / "r1").mkdir(parents=True)
    (data / "repos" / "r1" / "link-1").symlink_to(outside, target_is_directory=True)
    (data / "edits").mkdir(parents=True)
    (data / "edits" / "link-2").symlink_to(outside, target_is_directory=True)
    (data / "repos" / "r2").symlink_to(outside, target_is_directory=True)
    inner = tree(data / "edits" / "e-1", 72)
    (inner / "escape").symlink_to(outside, target_is_directory=True)
    os.utime(inner, (time.time() - 72 * HOUR,) * 2)
    await sweep(app)
    assert (outside / "f.txt").is_file()
    assert (data / "repos" / "r1" / "link-1").is_symlink() and (data / "edits" / "link-2").is_symlink()
    assert not inner.exists()


def test_a_path_outside_data_dir_is_refused(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    outside = tree(tmp_path / "elsewhere", 99)
    with pytest.raises(ValueError):
        artefacts.remove_tree(outside, data)
    with pytest.raises(ValueError):
        artefacts.remove_tree(data, data)
    assert (outside / "f.txt").is_file()
