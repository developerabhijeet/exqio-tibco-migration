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
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.app.config import Settings
from api.app.db import new_id
from api.app.models import Build, BuildFile, DesignRecord, EditSet, Process
from api.app.services import gates as gate_svc
from api.app.services import iterations as iteration_svc
from api.app.services.build_tree import build_tree_files
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
    ``EXPORTED_FROM.md`` and ``EXIQO_BASELINE.json``."""
    edited = tree_hashes(root, excluded)
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


def _excluded(path: str, excluded: frozenset[str]) -> bool:
    return any(part in excluded or part.lower() == ".git" for part in path.split("/"))


def _origin(origins: dict[str, str], path: str) -> str:
    origin = origins.get(path, "code")
    return origin if origin in ("code", "agent", "ai", "infra", "tests", "hand") else "code"


def _infra_path(path: str) -> bool:
    return path in INFRA_FILES or path.startswith(INFRA_PREFIXES)


def decide_resume(changes: list[FileChange], baseline_design_hash: str | None,
                  current_design_hash: str | None) -> ResumePhase:
    """``design`` when the approved design is no longer the one the baseline build was made from
    (or none is approved), ``infra`` when every change is infrastructure, else ``build``."""
    if not current_design_hash or baseline_design_hash != current_design_hash:
        return "design"
    if changes and all(_infra_path(c.path) for c in changes):
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
                raise UploadError(f"{name} and {folded[key]} differ only in case")
            folded[key] = name
            if info.file_size > RATIO_MIN_BYTES and info.file_size > MAX_RATIO * max(info.compress_size, 1):
                raise UploadError(f"{info.filename} expands more than {MAX_RATIO} times: refused as a "
                                  "possible zip bomb")
        if _declared_size(infos) > max_bytes:
            raise UploadTooLarge("upload is larger than the configured limit")


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
        top = single_root(dest) if dest.is_dir() else ""
        root = dest / top if top else dest
        if build_id is None:
            build_id = await asyncio.to_thread(_manifest_build_id, root)
        build = await session.get(Build, build_id)
        process = await session.get(Process, build.process_id) if build is not None else None
        if build is None or process is None:
            raise UploadError(NO_BUILD)
        if process.project_id != project_id:
            raise UploadError(OTHER_PROJECT)
        if build.status in ("queued", "running"):
            raise EditRefused("the baseline build has not finished")
        rows = list((await session.scalars(select(BuildFile).where(BuildFile.build_id == build.id))).all())
        excluded = download_excluded(build)
        baseline = await asyncio.to_thread(baseline_hashes, build, rows)
        changes = await asyncio.to_thread(diff_tree, root, baseline, {r.path: r.origin for r in rows}, excluded)
        phase = decide_resume(changes, build.design_hash, await approved_design_hash(session, process))
        files = [c.model_dump() for c in changes]
        try:
            iteration = await iteration_svc.open_iteration(
                session, project_id, [process.id], "edits", actor=actor, resume_phase=phase,
                baseline_build_id=build.id, source={"edit_set_id": edit_id},
                audit_detail={"edit_set_id": edit_id, "changes": change_counts(files)})
        except iteration_svc.IterationBusy as exc:
            raise EditRefused(str(exc)) from exc
        row = EditSet(id=edit_id, iteration_id=iteration.id, baseline_build_id=build.id, files=files,
                      root_dir=str(root), created_by=actor, status="previewed")
        session.add(row)
        await session.flush()
    except BaseException:
        remove_tree(dest)
        raise
    return row
