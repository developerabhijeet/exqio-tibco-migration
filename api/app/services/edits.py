"""Hand edits against a build (sub-project 6, spec section 3).

The baseline: every download carries ``EXIQO_BASELINE.json`` naming the build it came from and the
``text_sha256`` of every file in the zip, so an edited copy can be compared with it later. The
manifest only identifies the build; what counts as the baseline is the server's own build folder
(else its ``build_files`` rows) and ``build_files`` origins: a zip is user-edited data, its
manifest included, so a forged manifest can only name a build, never change what it holds.

An edited zip is a security boundary. Before anything is written, every entry is checked
(:func:`check_archive`): absolute paths, ``..`` segments, symbolic links (``uploads``), more than
``MAX_ENTRIES`` entries, a declared size over ``upload_max_bytes``, an entry that expands more than
``MAX_RATIO`` times, and two entries with one name or names that differ only in case are refused.
Extraction then streams under the same byte budget (an entry lying about its size is stopped), and
any refusal removes what was written. Repository and platform folders (``.git``, ``.exiqo``, the
stack's excluded folders) are never edits.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import unicodedata
import zipfile
from collections.abc import Collection
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.app.config import Settings
from api.app.db import new_id
from api.app.models import Build, BuildFile, DesignRecord, EditSet, InfraAsset, Iteration, Process
from api.app.services import gates as gate_svc
from api.app.services import iterations as iteration_svc
from api.app.services.build_tree import DOWNLOAD_EXCLUDED, build_tree_files
from api.app.services.build_tree import download_excluded as stack_download_excluded
from api.app.services.uploads import (
    UploadError,
    UploadTooLarge,
    _entry_parts,
    extract_zip,
    remove_tree,
    single_root,
)

BASELINE_NAME = "EXIQO_BASELINE.json"
NOTE_NAME = "EXPORTED_FROM.md"
BASELINE_FORMAT = 1
#: The stack a build without one (before sub-project 5) was generated for.
DEFAULT_STACK = "dotnet"


def build_stack(build: Build) -> str:
    return getattr(build, "stack", None) or DEFAULT_STACK


def download_excluded(build: Build) -> frozenset[str]:
    """The folders a build's download (and the edit diff) leaves out: its stack's
    ``download_excluded``."""
    return stack_download_excluded(build.stack)


def text_sha256(data: bytes) -> str:
    """sha256 of ``data`` with CRLF read as LF when it is text (UTF-8 without a NUL byte), so a
    checkout or an editor that rewrote the line endings is not an edit; raw bytes otherwise."""
    if b"\x00" not in data:
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            pass
        else:
            data = data.replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def baseline_manifest(build: Build, process: Process | None, files: dict[str, str]) -> dict[str, Any]:
    """``EXIQO_BASELINE.json`` for ``build``: ``files`` maps each zipped path to its ``text_sha256``."""
    return {"format": BASELINE_FORMAT, "project_id": process.project_id if process else None,
            "process_id": build.process_id, "build_id": build.id, "stack": build_stack(build),
            "build_hash": build.build_hash, "design_hash": build.design_hash, "iteration": build.iteration,
            "files": dict(sorted(files.items()))}


def baseline_file(build: Build, process: Process | None, root: Path, excluded: frozenset[str]) -> bytes:
    """``EXIQO_BASELINE.json`` for a copy of ``root`` (a push to a branch): the same manifest the
    download writes, over the files the copy ships."""
    manifest = baseline_manifest(build, process, tree_hashes(root, excluded))
    return (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()


# ------------------------------------------------------------------ the diff
Change = Literal["modified", "added", "deleted"]
ChangeOrigin = Literal["code", "agent", "ai", "infra", "tests", "hand", "new"]
ResumePhase = Literal["build", "design", "infra"]
#: Paths an infrastructure-only edit set touches (spec section 3, "Resume point").
INFRA_FILES = frozenset({"Dockerfile", ".dockerignore", "docker-compose.override.yml", "azure-pipelines.yml"})
INFRA_PREFIXES = ("infra/", ".github/")


class FileChange(BaseModel):
    """One file of an edit set: how it differs from the baseline, the baseline's origin for it
    (``new`` for an added file) and the ``text_sha256`` of the edited file (``None`` when deleted)."""

    path: str
    change: Change
    origin: ChangeOrigin
    sha256: str | None


def _platform_file(path: str) -> bool:
    return path in (NOTE_NAME, BASELINE_NAME)


def tree_hashes(root: Path, excluded: frozenset[str]) -> dict[str, str]:
    """``text_sha256`` of every shipped file under ``root`` (the note and the manifest aside)."""
    return {name: text_sha256((root / name).read_bytes())
            for name in build_tree_files(root, excluded) if not _platform_file(name)}


def diff_tree(root: Path, baseline: dict[str, str], origins: dict[str, str],
              excluded: frozenset[str]) -> list[FileChange]:
    """What changed under ``root`` against ``baseline`` (path → ``text_sha256``), sorted by path;
    unchanged files are left out, and so are ``.git``, the excluded folders, symbolic links,
    ``EXPORTED_FROM.md``, ``EXIQO_BASELINE.json`` and operating-system clutter (``.DS_Store``,
    ``Thumbs.db``, ``desktop.ini``, ``__MACOSX``)."""
    edited = {p: sha for p, sha in tree_hashes(root, excluded).items() if not _clutter(p)}
    out: list[FileChange] = []
    for path, sha in edited.items():
        if path not in baseline:
            out.append(FileChange(path=path, change="added", origin="new", sha256=sha))
        elif baseline[path] != sha:
            out.append(FileChange(path=path, change="modified", origin=_origin(origins, path), sha256=sha))
    for path in baseline.keys() - edited.keys():
        if not _platform_file(path) and not _excluded(path, excluded):
            out.append(FileChange(path=path, change="deleted", origin=_origin(origins, path), sha256=None))
    return sorted(out, key=lambda c: c.path)


#: Files an operating system leaves in a folder it showed: never a person's edit.
CLUTTER_NAMES = frozenset({".ds_store", "thumbs.db", "desktop.ini"})
CLUTTER_DIRS = frozenset({"__macosx"})


def _clutter(path: str) -> bool:
    parts = [p.lower() for p in path.split("/")]
    return parts[-1] in CLUTTER_NAMES or any(p in CLUTTER_DIRS for p in parts[:-1])


def _excluded(path: str, excluded: frozenset[str]) -> bool:
    return any(part in excluded or part.lower() == ".git" for part in path.split("/"))


def _origin(origins: dict[str, str], path: str) -> str:
    origin = origins.get(path, "code")
    return origin if origin in ("code", "agent", "ai", "infra", "tests", "hand") else "code"


def repaired_paths(build: Build | None) -> set[str]:
    """Paths an applied repair diff of ``build`` changed (``Build.repair`` attempts)."""
    from engine.dotnet_gen.repair import PatchError, parse_unified_diff

    out: set[str] = set()
    for attempt in ((build.repair or {}).get("attempts") or []) if build is not None else []:
        if not attempt.get("applied"):
            continue
        try:
            out |= {p.path for p in parse_unified_diff(attempt.get("diff") or "")}
        except PatchError:
            continue
    return out


def infra_path(path: str) -> bool:
    return path in INFRA_FILES or path.startswith(INFRA_PREFIXES)


def decide_resume(changes: list[FileChange], baseline_design_hash: str | None,
                  current_design_hash: str | None) -> ResumePhase:
    """``design`` when the approved design is no longer the one the baseline build was made from
    (or none is approved), ``infra`` when every change is infrastructure, else ``build``."""
    if not current_design_hash or baseline_design_hash != current_design_hash:
        return "design"
    if changes and all(infra_path(c.path) for c in changes):
        return "infra"
    return "build"


# ------------------------------------------------------------------ the archive
MAX_ENTRIES = 20_000
MAX_RATIO = 200
#: Entries smaller than this are not ratio-checked: a short, repetitive file compresses well.
RATIO_MIN_BYTES = 1024 * 1024
MAX_MANIFEST_BYTES = 1024 * 1024
NO_MANIFEST = "no EXIQO_BASELINE.json: upload the zip to /builds/{id}/edits instead"
OTHER_PROJECT = "the baseline names a build of another project"
NO_BUILD = "the baseline names a build that does not exist"
OTHER_PROCESS = "the branch's EXIQO_BASELINE.json names a build of another process"
NO_CHANGES = "no changes in the upload"


class NoChanges(UploadError):
    """The tree is the baseline build's (HTTP 422; a repository pull reports it as nothing new)."""


class EditRefused(Exception):
    """The edit set cannot be made or resumed in the current state (HTTP 409)."""


def _declared_size(infos: list[zipfile.ZipInfo]) -> int:
    return sum(i.file_size for i in infos)


def check_archive(archive: Path, dest: Path, max_bytes: int) -> None:
    """Refuse an archive (``UploadError``; ``UploadTooLarge`` for its size) before anything of it
    is written under ``dest``."""
    try:
        zf = zipfile.ZipFile(archive)
    except (zipfile.BadZipFile, OSError) as exc:
        raise UploadError("not a zip file") from exc
    with zf:
        infos = zf.infolist()
        if len(infos) > MAX_ENTRIES:
            raise UploadError(f"the archive has more than {MAX_ENTRIES} entries")
        names: set[str] = set()
        folded: dict[str, str] = {}
        for info in infos:
            parts = _entry_parts(info, dest)  # UnsafePath: absolute, "..", symbolic link, outside
            if not parts or info.is_dir():
                continue
            name = "/".join(parts)
            if name in names:
                raise UploadError(f"{name} appears more than once in the archive")
            names.add(name)
            key = unicodedata.normalize("NFC", name).casefold()
            if key in folded:
                raise UploadError(f"{name} and {folded[key]} differ only in case or Unicode normalisation")
            folded[key] = name
            if info.file_size > RATIO_MIN_BYTES and info.file_size > MAX_RATIO * max(info.compress_size, 1):
                raise UploadError(f"{info.filename} expands more than {MAX_RATIO} times: refused as a "
                                  "possible zip bomb")
        if _declared_size(infos) > max_bytes:
            raise UploadTooLarge("upload is larger than the configured limit")


def _tree_root(dest: Path) -> Path:
    """The extracted build: the single top folder when it holds the build (its manifest or a
    solution file), else ``dest``. A zip of one sub-folder such as ``src/`` is not shifted."""
    top = single_root(dest) if dest.is_dir() else ""
    if top:
        folder = dest / top
        if (folder / BASELINE_NAME).is_file() or any(p.suffix == ".sln" for p in folder.iterdir() if p.is_file()):
            return folder
    return dest


def _manifest_build_id(root: Path) -> str:
    path = root / BASELINE_NAME
    if path.is_symlink() or not path.is_file():
        raise UploadError(NO_MANIFEST)
    if path.stat().st_size > MAX_MANIFEST_BYTES:
        raise UploadError(f"{BASELINE_NAME} is larger than {MAX_MANIFEST_BYTES // 1024} KB")
    try:
        data = json.loads(path.read_bytes())
    except (ValueError, UnicodeDecodeError) as exc:
        raise UploadError(f"{BASELINE_NAME} is not valid JSON") from exc
    build_id = data.get("build_id") if isinstance(data, dict) else None
    if not isinstance(build_id, str) or not build_id or len(build_id) > 64:
        raise UploadError(f"{BASELINE_NAME} names no build")
    return build_id


def edits_dir(settings: Settings) -> Path:
    return settings.data_dir.resolve() / "edits"


def baseline_hashes(build: Build, rows: list[BuildFile]) -> dict[str, str]:
    """The baseline build's files: ``text_sha256`` of its own folder (the files its download
    ships), else, when the folder is gone, its ``build_files`` rows (the hash of the stored
    content, written with ``\n`` line endings)."""
    root = Path(build.output_dir) if build.output_dir else None
    if root is not None and root.is_dir():
        return tree_hashes(root, download_excluded(build))
    return {r.path: r.sha256 for r in rows}


async def approved_design_hash(session: AsyncSession, process: Process) -> str | None:
    """The design hash G3 approved for ``process``, ``None`` when G3 is not approved."""
    g3 = (await gate_svc.gate_rows(session, process.id)).get("G3")
    if g3 is None or g3.state != "approved":
        return None
    if g3.design_hash:
        return g3.design_hash
    return await session.scalar(select(DesignRecord.design_hash).where(DesignRecord.process_id == process.id)
                                .order_by(DesignRecord.version.desc()).limit(1))


def change_counts(files: list[dict[str, Any]]) -> dict[str, int]:
    return {k: sum(1 for f in files if f.get("change") == k) for k in ("modified", "added", "deleted")}


async def create_edit_set(session: AsyncSession, settings: Settings, build_id: str | None, project_id: str,
                          archive: Path, actor: str) -> EditSet:
    """Check and extract ``archive`` under ``DATA_DIR/edits/<edit set id>``, compare it with the
    baseline build (``build_id``, else the one ``EXIQO_BASELINE.json`` names, which must belong to
    ``project_id``), open an iteration ``kind="edits"`` with the resume phase, and add the edit set.
    Flushes; the caller commits. On any refusal nothing is kept on disk."""
    edit_id = new_id()
    dest = edits_dir(settings) / edit_id
    await asyncio.to_thread(check_archive, archive, dest, settings.upload_max_bytes)
    try:
        await asyncio.to_thread(extract_zip, archive, dest, settings.upload_max_bytes)
        root = await asyncio.to_thread(_tree_root, dest)
        return await _edit_set_for_root(session, edit_id, root, build_id, project_id, actor, "edits", {})
    except BaseException:
        remove_tree(dest)
        raise


async def create_edit_set_from_tree(session: AsyncSession, settings: Settings, project_id: str, root: Path,
                                    actor: str, source: dict[str, Any], process_id: str | None = None) -> EditSet:
    """An edit set from a tree already on disk (a cloned repository branch, sub-project 6 Task 9):
    the baseline is the build ``EXIQO_BASELINE.json`` names (at ``root`` or under its one top
    folder; it must belong to ``project_id``), the iteration is ``kind="repo"`` with ``source``
    (``repo_id``, ``branch``, ``commit``). The tree is used where it is (``root_dir``); nothing is
    copied. ``process_id``: the process the repository is connected for; a manifest naming a build of
    another process is refused. Flushes; the caller commits."""
    del settings  # the clone was size-checked by its caller
    tree = root if (root / BASELINE_NAME).is_file() else await asyncio.to_thread(_tree_root, root)
    return await _edit_set_for_root(session, new_id(), tree, None, project_id, actor, "repo", dict(source),
                                    process_id)


async def _edit_set_for_root(session: AsyncSession, edit_id: str, root: Path, build_id: str | None,
                             project_id: str, actor: str, kind: str, source: dict[str, Any],
                             expect_process: str | None = None) -> EditSet:
    """Compare the tree at ``root`` with its baseline build, open the iteration and add the edit set."""
    if build_id is None:
        build_id = await asyncio.to_thread(_manifest_build_id, root)
    elif (root / BASELINE_NAME).is_file():
        named = await asyncio.to_thread(_manifest_build_id, root)
        if named != build_id:
            raise UploadError(f"the zip's {BASELINE_NAME} names build {named}, not {build_id}")
    build = await session.get(Build, build_id)
    process = await session.get(Process, build.process_id) if build is not None else None
    if build is None or process is None:
        raise UploadError(NO_BUILD)
    if process.project_id != project_id:
        raise UploadError(OTHER_PROJECT)
    if expect_process is not None and process.id != expect_process:
        raise UploadError(OTHER_PROCESS)
    if build.status in ("queued", "running"):
        raise EditRefused("the baseline build has not finished")
    rows = list((await session.scalars(select(BuildFile).where(BuildFile.build_id == build.id))).all())
    excluded = download_excluded(build)
    baseline = await asyncio.to_thread(baseline_hashes, build, rows)
    changes = await asyncio.to_thread(diff_tree, root, baseline, {r.path: r.origin for r in rows}, excluded)
    if not changes:
        # nothing to build, and no iteration is opened: opening one would supersede the process's
        # active iteration for nothing (final review I1)
        raise NoChanges(NO_CHANGES)
    phase = decide_resume(changes, build.design_hash, await approved_design_hash(session, process))
    files = [c.model_dump() for c in changes]
    try:
        iteration = await iteration_svc.open_iteration(
            session, project_id, [process.id], kind, actor=actor, resume_phase=phase,
            baseline_build_id=build.id, source={**source, "edit_set_id": edit_id},
            audit_detail={"edit_set_id": edit_id, "changes": change_counts(files)})
    except iteration_svc.IterationBusy as exc:
        raise EditRefused(str(exc)) from exc
    row = EditSet(id=edit_id, iteration_id=iteration.id, baseline_build_id=build.id, files=files,
                  root_dir=str(root), created_by=actor, status="previewed")
    session.add(row)
    await session.flush()
    gate_svc.audit(session, process, actor, "edit set uploaded",
                   detail={"edit_set_id": edit_id, "baseline_build_id": build.id, "iteration": iteration.n,
                           "counts": change_counts(files), **source})
    return row


# ------------------------------------------------------------------ building from an edit set
@dataclass
class EditBuild:
    """What a build with ``edit_set_id`` needs: the changes, the baseline build's folder (``None``
    when it is gone), the edited tree, and whether only infrastructure changed."""

    edit_set_id: str
    baseline_build_id: str | None
    changes: list[FileChange]
    baseline_root: Path | None
    edited_root: Path
    infra_only: bool
    excluded: frozenset[str] = DOWNLOAD_EXCLUDED
    baseline_files: list[dict[str, Any]] = field(default_factory=list)  # the baseline's build_files rows
    baseline_design_hash: str | None = None
    baseline_spec_hash: str | None = None
    #: the baseline's infrastructure asset paths (its infrastructure generation's files)
    infra_paths: frozenset[str] = frozenset()
    #: baseline ``agent`` files a model changed anyway (an applied repair diff): merged like ``ai``
    model_written: frozenset[str] = frozenset()

    def is_infra(self, change: FileChange) -> bool:
        return infra_change(change, self.infra_paths)

    def scaffold_changed(self, design_hash: str | None, spec_hash: str | None) -> bool:
        """Whether this build generates from another design or spec than the baseline did, so a
        model-filled file may sit on a different scaffold (X23)."""
        return (self.baseline_design_hash, self.baseline_spec_hash) != (design_hash, spec_hash)


def infra_change(change: FileChange, infra_paths: Collection[str] = frozenset()) -> bool:
    """A change the infrastructure generation owns (merged after it, not before): an
    infrastructure path, or a file the baseline's infrastructure generation wrote, whatever its
    origin now (an earlier edit build makes it ``hand``)."""
    return change.origin == "infra" or infra_path(change.path) or change.path in infra_paths


async def load_edit_build(session: AsyncSession, build: Build) -> EditBuild:
    row = await session.get(EditSet, build.edit_set_id)
    if row is None:
        raise ValueError(f"edit set {build.edit_set_id} not found")
    iteration = await session.get(Iteration, row.iteration_id)
    baseline = await session.get(Build, row.baseline_build_id) if row.baseline_build_id else None
    edited = Path(row.root_dir)
    if not edited.is_dir():
        raise ValueError("the edit set's files are no longer on disk: upload the edits again")
    root = Path(baseline.output_dir) if baseline is not None and baseline.output_dir else None
    rows = [] if baseline is None else list((await session.scalars(
        select(BuildFile).where(BuildFile.build_id == baseline.id).order_by(BuildFile.path))).all())
    changes = [FileChange.model_validate(f) for f in row.files or []]
    # A baseline that is itself an edit build: its hand files nobody touched again are carried
    # over as they are (a merge would regenerate and refill them), and stay protected.
    listed = {c.path for c in changes}
    for r in rows:
        if r.origin == "hand" and r.path not in listed and (edited / r.path).is_file():
            changes.append(FileChange(path=r.path, change="modified", origin="hand", sha256=r.sha256))
    infra = [] if baseline is None else list((await session.scalars(
        select(InfraAsset.path).where(InfraAsset.build_id == baseline.id))).all())
    return EditBuild(
        edit_set_id=row.id, baseline_build_id=row.baseline_build_id,
        changes=sorted(changes, key=lambda c: c.path),
        infra_paths=frozenset(infra), model_written=frozenset(repaired_paths(baseline)),
        baseline_root=root if root is not None and root.is_dir() else None, edited_root=edited,
        infra_only=iteration is not None and iteration.resume_phase == "infra",
        excluded=download_excluded(baseline if baseline is not None else build),
        baseline_design_hash=baseline.design_hash if baseline is not None else None,
        baseline_spec_hash=baseline.spec_hash if baseline is not None else None,
        baseline_files=[{"path": r.path, "origin": r.origin, "sha256": r.sha256, "size": r.size,
                         "process_id": r.process_id, "tibco_ref": r.tibco_ref} for r in rows])
