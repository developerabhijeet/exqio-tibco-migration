"""Which files of a build ship, and the provenance note that goes with them.

One answer for every place a build leaves the platform: the zip download, the GitHub push and the
edit-set diff (cross-ruling X3). A shipped file is a regular file under the build root, reached
without following a symbolic link, outside the excluded folders (``bin``, ``obj``, ``TestResults``,
``.exiqo`` for .NET: the build stack's ``download_excluded``, see ``download_excluded``) and outside any ``.git`` folder,
whose config or hooks would otherwise travel with the build.
"""
from __future__ import annotations

import os
import stat
from collections.abc import Iterable
from pathlib import Path

from api.app.models import Build, Process
from engine.stacks import get_stack

DOWNLOAD_EXCLUDED = frozenset({"bin", "obj", "TestResults", ".exiqo"})
#: Never shipped, whatever the stack says: a repository's metadata is not part of a build.
ALWAYS_EXCLUDED = frozenset({".git"})


def is_excluded(rel: str, excluded: Iterable[str]) -> bool:
    """Whether the posix path ``rel`` (relative to the build root) is left out by ``excluded``: a
    plain name (``bin``) excludes any path with that part; an entry with a ``/`` (``target/``,
    ``.exiqo/logs``) excludes that folder or file from the root only, so a package folder named
    ``target`` deeper down still ships. A ``.git`` part is always excluded."""
    parts = rel.split("/")
    for e in excluded:
        if "/" in e:
            anchor = e.strip("/")
            if rel == anchor or rel.startswith(anchor + "/"):
                return True
        elif e in parts:
            return True
    return any(part.lower() in ALWAYS_EXCLUDED for part in parts)  # ``.GIT`` is ``.git`` on macOS


def build_tree_files(root: Path, excluded: Iterable[str] = DOWNLOAD_EXCLUDED) -> list[str]:
    """The posix paths, relative to ``root`` and sorted, of the build's files that ship (see
    ``is_excluded`` for what ``excluded`` leaves out)."""
    skip = frozenset(excluded)

    def rel_of(path: Path) -> str:
        return path.relative_to(root).as_posix()

    out: list[str] = []
    for current, dirs, files in os.walk(root, followlinks=False):
        here = Path(current)
        # os.walk lists a symlink to a folder among ``dirs``; it is not entered, and not kept
        dirs[:] = sorted(d for d in dirs
                         if not is_excluded(rel_of(here / d), skip) and not (here / d).is_symlink())
        for name in files:
            path = here / name
            rel = rel_of(path)
            if is_excluded(rel, skip):
                continue
            try:
                mode = path.lstat().st_mode
            except OSError:
                continue
            if stat.S_ISREG(mode):
                out.append(rel)
    return sorted(out)


def download_excluded(stack_key: str | None) -> frozenset[str]:
    """The build-output folders a download leaves out for ``stack_key`` (the stack's own list)."""
    try:
        return get_stack(stack_key or "dotnet").download_excluded
    except KeyError:
        return DOWNLOAD_EXCLUDED


def stack_label(stack_key: str | None) -> str:
    key = stack_key or "dotnet"
    try:
        return f"{key} ({get_stack(key).label})"
    except KeyError:
        return key


def export_note(build: Build, process: Process | None) -> str:
    """Which build this bundle is, written into it, so a folder on someone's laptop (or a branch)
    can still be traced back to the TIBCO process and the gate record it came from."""
    rows = [
        ("Build", build.id),
        ("Status", build.status),
        ("Build hash", build.build_hash or "not recorded"),
        ("Design hash", build.design_hash or "not recorded"),
        ("TIBCO spec hash", build.spec_hash or "not recorded"),
        ("TIBCO process", f"{process.name} ({process.id})" if process else build.process_id),
        ("Source file", process.source_path if process else "not recorded"),
        ("Target stack", stack_label(build.stack)),
        ("Target runtime", "AKS" if build.runtime == "aks" else "Azure Container Apps"),
        ("Finished", build.finished_at.isoformat() if build.finished_at else "did not finish"),
    ]
    steps = "\n".join(
        f"| {s.get('name')} | {'skipped' if s.get('skipped') else 'passed' if s.get('ok') else 'failed'} |"
        for s in (build.steps or []) if s.get("name"))
    return (
        "# Exported from ExiQO Migrate\n\n"
        "This folder is one build of one migrated TIBCO BusinessWorks process. Start it with\n"
        "`bash run-local.sh` and read `RUN_LOCALLY.md`.\n\n"
        + "| Field | Value |\n| --- | --- |\n"
        + "".join(f"| {k} | {v} |\n" for k, v in rows)
        + ("\n## Pipeline steps as recorded\n\n| Step | Outcome |\n| --- | --- |\n" + steps + "\n" if steps else "")
        + "\nThe status above is what the pipeline recorded for this build. It is the build's own\n"
        "result, not a statement that the migration was approved: the approval is the G4 gate\n"
        "record in ExiQO Migrate, against the build hash above.\n"
    )
